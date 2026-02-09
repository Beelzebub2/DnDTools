"""
SortLearningManager — Unified item-placement learning.

Replaces the old SortLearningManager that used JSONL files and a separate
logistic-regression model.  Now delegates to:
  - SortAdaptiveModel for scoring
  - SortEventStore for persistence
  - Correction detection writes weighted negative/positive events

Backward-compatible public surface:
    get_sort_learning_manager()        — returns a SortLearningManager
    get_item_priority_feature_names()  — returns feature name list
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from src.models.sort_model import (
    SortAdaptiveModel,
    ITEM_FEATURE_NAMES,
    get_sort_adaptive_model,
)
from src.models.sort_event_store import (
    SortEventStore,
    get_event_store,
)

logger = logging.getLogger(__name__)

_ITEM_FEATURE_NAMES = list(ITEM_FEATURE_NAMES)
_SORT_LEARNING_MANAGER_LOCK = threading.Lock()


class SortLearningManager:
    """
    Scores items via SortAdaptiveModel, records placement events to
    SortEventStore, and detects user corrections.
    """

    def __init__(
        self,
        model: Optional[SortAdaptiveModel] = None,
        event_store: Optional[SortEventStore] = None,
    ) -> None:
        self.model = model or get_sort_adaptive_model()
        self.event_store = event_store or get_event_store()
        self._lock = threading.RLock()
        self._pending_plans: Dict[str, Dict[str, Any]] = {}
        self._pending_features: Dict[str, Dict[str, Dict[str, float]]] = {}
        self._training_thread: Optional[threading.Thread] = None

    # ── Scoring (used by LayoutPlanner) ──────────────────────────────

    def score_item(self, features: Dict[str, float]) -> Optional[float]:
        """Score an item-slot pair. Returns None when no model is available."""
        return self.model.score_item_slot(features)

    # ── Recording placements (used by LayoutPlanner + StashSorter) ───

    def record_priority_sample(
        self,
        *,
        session_id: Optional[str],
        item: Any,
        features: Dict[str, float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a candidate placement (not yet confirmed)."""
        # We now record during outcome instead of at candidate time.
        # This no-ops for backward compatibility — the real recording
        # happens in record_priority_outcome.
        pass

    def record_priority_outcome(
        self,
        *,
        session_id: Optional[str],
        item: Any,
        success: bool,
        reason: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record the outcome of an item placement."""
        if not session_id:
            return

        # Extract features from metadata if available
        features: Dict[str, float] = {}
        if isinstance(metadata, dict):
            if "features" in metadata and isinstance(metadata["features"], dict):
                features = metadata["features"]

        item_id = str(getattr(item, "item_id", "") or "")
        item_name = str(getattr(item, "name", "") or "")
        target = metadata.get("target") if isinstance(metadata, dict) else None
        target_x = target.get("x") if isinstance(target, dict) else None
        target_y = target.get("y") if isinstance(target, dict) else None

        try:
            self.event_store.record_item_placement(
                session_id=session_id,
                features=features,
                success=success,
                item_id=item_id,
                item_name=item_name,
                target_x=target_x,
                target_y=target_y,
                reason=reason,
            )
        except Exception as exc:
            logger.debug("Failed to record item placement: %s", exc, exc_info=True)

    # ── Pending plan registration (for correction detection) ─────────

    def register_pending_plan(
        self,
        session_id: str,
        plan_map: Dict[str, Tuple[int, int]],
        feature_map: Dict[str, Dict[str, float]],
    ) -> None:
        if not session_id or not plan_map:
            return
        with self._lock:
            now = time.time()
            # Cleanup old plans (1 hour)
            cutoff = now - 3600
            expired = [
                sid for sid, data in self._pending_plans.items()
                if data.get("timestamp", 0) < cutoff
            ]
            for sid in expired:
                self._pending_plans.pop(sid, None)
                self._pending_features.pop(sid, None)

            self._pending_plans[session_id] = {
                "timestamp": now,
                "map": plan_map,
            }
            self._pending_features[session_id] = feature_map

    # ── Correction detection (called from StashManager) ──────────────

    def check_corrections(self, items: List[Any]) -> int:
        """
        Compare current item positions against the last plan.
        Any deviation is treated as a user correction → writes weighted
        negative + positive events to the event store.
        """
        corrections_found = 0
        with self._lock:
            if not self._pending_plans:
                return 0

            # Find most recent session
            latest_session_id = None
            latest_ts = 0.0
            for sid, data in self._pending_plans.items():
                ts = data.get("timestamp", 0)
                if ts > latest_ts:
                    latest_ts = ts
                    latest_session_id = sid

            if not latest_session_id:
                return 0

            plan_data = self._pending_plans.get(latest_session_id)
            if not plan_data:
                return 0

            plan_map = plan_data.get("map", {})
            feature_map = self._pending_features.get(latest_session_id, {})

        for item in items:
            item_id = str(getattr(item, "item_id", "") or "")
            if item_id not in plan_map:
                continue

            pos = getattr(item, "position", None)
            if not pos:
                continue

            try:
                current_x, current_y = int(pos.x), int(pos.y)
            except (ValueError, TypeError):
                continue

            planned_x, planned_y = plan_map[item_id]

            if current_x != planned_x or current_y != planned_y:
                corrections_found += 1
                original_features = feature_map.get(item_id, {})

                # Build planned-position features
                planned_features = dict(original_features)
                planned_features["slot_x"] = float(planned_x)
                planned_features["slot_y"] = float(planned_y)

                # Build corrected-position features
                corrected_features = dict(original_features)
                corrected_features["slot_x"] = float(current_x)
                corrected_features["slot_y"] = float(current_y)

                try:
                    self.event_store.record_user_correction(
                        session_id=latest_session_id,
                        item_id=item_id,
                        planned_features=planned_features,
                        corrected_features=corrected_features,
                        planned_pos={"x": planned_x, "y": planned_y},
                        corrected_pos={"x": current_x, "y": current_y},
                    )
                except Exception as exc:
                    logger.debug("Failed to record correction for %s: %s", item_id, exc, exc_info=True)

        if corrections_found > 0:
            logger.info("Recorded %d corrections for session %s", corrections_found, latest_session_id)
            self._schedule_item_training()

        return corrections_found

    # ── Model exchange (backward compat) ─────────────────────────────

    def apply_model(self, payload: Dict[str, Any]) -> bool:
        return self.model.apply_remote_item_model(payload)

    def get_model_version(self) -> Optional[str]:
        return self.model.get_item_version()

    def get_model_payload(self) -> Optional[Dict[str, Any]]:
        return self.model.get_item_model_payload()

    # ── Training ─────────────────────────────────────────────────────

    def _schedule_item_training(self) -> None:
        with self._lock:
            if self._training_thread and self._training_thread.is_alive():
                return
            thread = threading.Thread(
                target=self._train_item_worker, name="SortItemTrain", daemon=True
            )
            self._training_thread = thread
            thread.start()

    def _train_item_worker(self) -> None:
        try:
            events = self.event_store.get_item_training_data(limit=10000)
            if not events:
                return
            self.model.train_items(events, async_=False)
        except Exception as exc:
            logger.debug("Item training worker failed: %s", exc, exc_info=True)


# ── Module-level singleton ───────────────────────────────────────────────


def get_sort_learning_manager() -> SortLearningManager:
    global _DEFAULT_SORT_LEARNING_MANAGER
    try:
        manager = _DEFAULT_SORT_LEARNING_MANAGER
    except NameError:
        manager = None

    if manager is not None:
        return manager

    with _SORT_LEARNING_MANAGER_LOCK:
        try:
            manager = _DEFAULT_SORT_LEARNING_MANAGER
        except NameError:
            manager = None
        if manager is None:
            manager = SortLearningManager()
            globals()["_DEFAULT_SORT_LEARNING_MANAGER"] = manager
        return manager


def get_item_priority_feature_names() -> List[str]:
    return list(_ITEM_FEATURE_NAMES)
