"""
SortEventStore — SQLite-backed event log for sort ML training.

Replaces the scattered JSONL / per-session JSON files with a single,
queryable, replayable event store.  Three event types:

  SORT_STARTED      — features + plan snapshot at sort begin
  SORT_COMPLETED    — outcome (success/fail) + metrics at sort end
  ITEM_PLACEMENT    — per-item placement record (candidate features + outcome)
  USER_CORRECTION   — detected manual move after sort (before/after positions)

Each event carries a session_id, timestamp, and full feature snapshot.

Usage:
    from src.models.sort_event_store import get_event_store
    store = get_event_store()
    store.record_sort_started(session_id, features)
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from src.models.appdirs import get_output_dir

logger = logging.getLogger(__name__)

EVENT_SCHEMA_VERSION = 1

# ── Event type constants ─────────────────────────────────────────────────

SORT_STARTED = "SORT_STARTED"
SORT_COMPLETED = "SORT_COMPLETED"
ITEM_PLACEMENT = "ITEM_PLACEMENT"
USER_CORRECTION = "USER_CORRECTION"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    timestamp   REAL    NOT NULL,
    features    TEXT,
    label       INTEGER,
    weight      REAL    DEFAULT 1.0,
    metadata    TEXT,
    synced      INTEGER DEFAULT 0,
    created_at  REAL    NOT NULL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);",
    "CREATE INDEX IF NOT EXISTS idx_events_synced ON events (synced);",
    "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);",
]


class SortEventStore:
    """
    Thread-safe SQLite event store for sort ML events.

    Designed for append-heavy workloads with periodic batch reads
    for training and sync.
    """

    MAX_EVENTS = 50_000  # auto-prune oldest synced events beyond this

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        output_dir = Path(base_dir or get_output_dir())
        self.base_dir = output_dir / "sort_model"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "events.db"
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._init_db()

    # ── Connection management ────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    @contextmanager
    def _cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(_CREATE_TABLE)
            for idx_sql in _CREATE_INDEXES:
                conn.execute(idx_sql)
            conn.commit()
    def close(self) -> None:
        """Close the thread-local SQLite connection if open."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
    # ── Public: record events ────────────────────────────────────────

    def record_sort_started(
        self,
        session_id: str,
        features: Dict[str, float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        return self._insert_event(
            event_type=SORT_STARTED,
            session_id=session_id,
            features=features,
            label=None,
            weight=1.0,
            metadata=metadata,
        )

    def record_sort_completed(
        self,
        session_id: str,
        features: Dict[str, float],
        *,
        success: bool,
        failure_reason: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
    ) -> int:
        meta = dict(metrics or {})
        if failure_reason:
            meta["failure_reason"] = failure_reason
        if duration_ms is not None:
            meta["duration_ms"] = duration_ms
        meta["success"] = bool(success)
        return self._insert_event(
            event_type=SORT_COMPLETED,
            session_id=session_id,
            features=features,
            label=0 if success else 1,  # 1 = failure for risk model
            weight=1.0 if success else self._failure_weight(),
            metadata=meta,
        )

    def record_item_placement(
        self,
        session_id: str,
        features: Dict[str, float],
        *,
        success: bool,
        item_id: Optional[str] = None,
        item_name: Optional[str] = None,
        target_x: Optional[int] = None,
        target_y: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> int:
        meta: Dict[str, Any] = {}
        if item_id:
            meta["item_id"] = item_id
        if item_name:
            meta["item_name"] = item_name
        if target_x is not None:
            meta["target_x"] = target_x
        if target_y is not None:
            meta["target_y"] = target_y
        if reason:
            meta["reason"] = reason
        return self._insert_event(
            event_type=ITEM_PLACEMENT,
            session_id=session_id,
            features=features,
            label=1 if success else 0,
            weight=1.0,
            metadata=meta,
        )

    def record_user_correction(
        self,
        session_id: str,
        *,
        item_id: str,
        planned_features: Dict[str, float],
        corrected_features: Dict[str, float],
        planned_pos: Dict[str, int],
        corrected_pos: Dict[str, int],
    ) -> List[int]:
        """
        Records TWO events for a single user correction:
          1. Negative sample at the planned position (label=0, weight=CORRECTION_WEIGHT)
          2. Positive sample at the corrected position (label=1, weight=CORRECTION_WEIGHT)
        """
        ids: List[int] = []
        weight = self._correction_weight()

        # Negative: the planned position was wrong
        ids.append(self._insert_event(
            event_type=USER_CORRECTION,
            session_id=session_id,
            features=planned_features,
            label=0,
            weight=weight,
            metadata={
                "item_id": item_id,
                "correction_role": "planned_failure",
                "position": planned_pos,
            },
        ))

        # Positive: the user's chosen position
        ids.append(self._insert_event(
            event_type=USER_CORRECTION,
            session_id=session_id,
            features=corrected_features,
            label=1,
            weight=weight,
            metadata={
                "item_id": item_id,
                "correction_role": "manual_success",
                "position": corrected_pos,
            },
        ))
        return ids

    # ── Public: query events ─────────────────────────────────────────

    def get_risk_training_data(self, limit: int = 5000) -> List[Dict[str, Any]]:
        """Return SORT_COMPLETED events formatted for risk model training."""
        return self._query_training_events(SORT_COMPLETED, limit)

    def get_item_training_data(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """Return ITEM_PLACEMENT + USER_CORRECTION events for item model training."""
        sql = """
            SELECT features, label, weight, metadata
            FROM events
            WHERE event_type IN (?, ?)
            AND label IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        """
        with self._cursor() as cur:
            cur.execute(sql, (ITEM_PLACEMENT, USER_CORRECTION, limit))
            return [self._row_to_training_dict(row) for row in cur.fetchall()]

    def get_unsynced_events(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Return events not yet uploaded to the server."""
        sql = """
            SELECT id, event_type, session_id, timestamp, features, label, weight, metadata
            FROM events
            WHERE synced = 0
            ORDER BY timestamp ASC
            LIMIT ?
        """
        with self._cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
        return [self._row_to_full_dict(row) for row in rows]

    def mark_synced(self, event_ids: List[int]) -> None:
        """Mark events as successfully uploaded."""
        if not event_ids:
            return
        placeholders = ",".join("?" for _ in event_ids)
        sql = f"UPDATE events SET synced = 1 WHERE id IN ({placeholders})"
        with self._write_lock:
            with self._cursor() as cur:
                cur.execute(sql, event_ids)

    def count_events(self, event_type: Optional[str] = None) -> int:
        if event_type:
            sql = "SELECT COUNT(*) FROM events WHERE event_type = ?"
            params: tuple = (event_type,)
        else:
            sql = "SELECT COUNT(*) FROM events"
            params = ()
        with self._cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def get_last_sync_cursor(self) -> Optional[float]:
        """Return the timestamp of the most recent synced event."""
        sql = "SELECT MAX(timestamp) FROM events WHERE synced = 1"
        with self._cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None

    def get_session_ids(self, limit: int = 100) -> List[str]:
        sql = "SELECT DISTINCT session_id FROM events ORDER BY timestamp DESC LIMIT ?"
        with self._cursor() as cur:
            cur.execute(sql, (limit,))
            return [row[0] for row in cur.fetchall()]

    # ── Public: maintenance ──────────────────────────────────────────

    def prune(self, keep: int = 30_000) -> int:
        """Remove oldest synced events to keep the database manageable."""
        total = self.count_events()
        if total <= keep:
            return 0
        excess = total - keep
        sql = """
            DELETE FROM events WHERE id IN (
                SELECT id FROM events
                WHERE synced = 1
                ORDER BY timestamp ASC
                LIMIT ?
            )
        """
        with self._write_lock:
            with self._cursor() as cur:
                cur.execute(sql, (excess,))
                deleted = cur.rowcount
        if deleted:
            logger.info("Pruned %d old synced events", deleted)
        return deleted

    # ── Internal ─────────────────────────────────────────────────────

    def _insert_event(
        self,
        *,
        event_type: str,
        session_id: str,
        features: Optional[Dict[str, float]],
        label: Optional[int],
        weight: float,
        metadata: Optional[Dict[str, Any]],
    ) -> int:
        now = time.time()
        features_json = json.dumps(features, separators=(",", ":")) if features else None
        metadata_json = json.dumps(metadata, separators=(",", ":")) if metadata else None
        sql = """
            INSERT INTO events (event_type, session_id, timestamp, features, label, weight, metadata, synced, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """
        with self._write_lock:
            with self._cursor() as cur:
                cur.execute(sql, (event_type, session_id, now, features_json, label, weight, metadata_json, now))
                row_id = cur.lastrowid
        # Periodic prune
        if row_id and row_id % 1000 == 0:
            try:
                self.prune(self.MAX_EVENTS)
            except Exception:
                pass
        return row_id or 0

    def _query_training_events(self, event_type: str, limit: int) -> List[Dict[str, Any]]:
        sql = """
            SELECT features, label, weight, metadata
            FROM events
            WHERE event_type = ? AND label IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        """
        with self._cursor() as cur:
            cur.execute(sql, (event_type, limit))
            return [self._row_to_training_dict(row) for row in cur.fetchall()]

    @staticmethod
    def _row_to_training_dict(row: sqlite3.Row) -> Dict[str, Any]:
        features = json.loads(row["features"]) if row["features"] else {}
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        return {
            "features": features,
            "label": row["label"],
            "weight": row["weight"] or 1.0,
            "metadata": metadata,
        }

    @staticmethod
    def _row_to_full_dict(row: sqlite3.Row) -> Dict[str, Any]:
        features = json.loads(row["features"]) if row["features"] else {}
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "session_id": row["session_id"],
            "timestamp": row["timestamp"],
            "features": features,
            "label": row["label"],
            "weight": row["weight"] or 1.0,
            "metadata": metadata,
        }

    @staticmethod
    def _failure_weight() -> float:
        return 2.0

    @staticmethod
    def _correction_weight() -> float:
        return 3.0


# ── Module-level singleton ───────────────────────────────────────────────

_STORE_LOCK = threading.Lock()
_STORE: Optional[SortEventStore] = None


def get_event_store() -> SortEventStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = SortEventStore()
        return _STORE
