import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


class SortFeedbackSyncService:
    """Shares anonymized sort feedback with the central training API."""

    DEFAULT_BASE_URL = "https://dndtools.rrmtools.uk/api/ai/training"
    SCHEMA_VERSION = 1
    REQUEST_TIMEOUT = 12.0
    UPLOAD_BATCH_SIZE = 25
    MIN_PULL_INTERVAL = 60 * 30  # 30 minutes

    def __init__(
        self,
        *,
        feedback_manager,
        settings_manager,
        app_version: str,
        logger: Optional[logging.Logger] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._manager = feedback_manager
        self._settings = settings_manager
        self._app_version = app_version
        self._lock = threading.RLock()
        self._sync_event = threading.Event()
        self._stop_event = threading.Event()
        self._last_sync: Optional[float] = None

        resolved_url = base_url or os.environ.get("SORT_FEEDBACK_SYNC_URL")
        if not resolved_url:
            resolved_url = os.environ.get("DNDTOOLS_TRAINING_URL")
        resolved_url = (resolved_url or self.DEFAULT_BASE_URL).strip()
        if resolved_url.endswith("/"):
            resolved_url = resolved_url[:-1]
        self._base_url = resolved_url
        self._available = bool(self._base_url)

        self._sync_dir = self._manager.base_dir / "sync"
        self._pending_dir = self._sync_dir / "pending"
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._sync_dir / "state.json"
        self._state = self._load_state()
        if "client_id" not in self._state:
            self._state["client_id"] = uuid.uuid4().hex
            self._save_state()

        self._session = requests.Session()
        self._enabled = bool(self._settings.get("sortFeedbackSyncEnabled", False))
        self._worker: Optional[threading.Thread] = None

        if self._available:
            try:
                self._manager.register_record_listener(self._handle_local_record)
            except Exception as exc:
                self._logger.debug("Unable to attach sync listener: %s", exc, exc_info=True)
                self._available = False

        if self._available:
            self._ensure_worker()

    @property
    def client_id(self) -> str:
        return str(self._state.get("client_id"))

    def stop(self) -> None:
        self._stop_event.set()
        self._sync_event.set()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=2.0)

    def apply_settings(self, settings: Dict[str, Any]) -> None:
        enabled = bool(settings.get("sortFeedbackSyncEnabled", False))
        with self._lock:
            previous = self._enabled
            self._enabled = enabled
        if self._available and enabled and not previous:
            self._logger.info("Sort feedback sync enabled; queueing immediate upload")
            self.trigger_sync(immediate=True)
        elif not enabled and previous:
            self._logger.info("Sort feedback sync disabled; pausing uploads")

    def trigger_sync(self, *, immediate: bool = False) -> None:
        if not self._available or not self._enabled:
            return
        self._sync_event.set()
        if immediate:
            self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        worker = threading.Thread(target=self._worker_loop, name="SortFeedbackSync", daemon=True)
        self._worker = worker
        worker.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            triggered = self._sync_event.wait(timeout=self.MIN_PULL_INTERVAL)
            self._sync_event.clear()
            if self._stop_event.is_set():
                break
            if not self._enabled or not self._available:
                continue
            try:
                self._perform_sync_cycle(force_download=triggered)
            except Exception as exc:  # pragma: no cover - defensive
                self._logger.debug("Unexpected sync cycle failure: %s", exc, exc_info=True)

    def _perform_sync_cycle(self, *, force_download: bool = False) -> None:
        uploads_sent = self._flush_pending_uploads()
        now = time.time()
        last_pull = float(self._state.get("last_pull", 0))
        should_pull = force_download or (now - last_pull) >= self.MIN_PULL_INTERVAL
        if should_pull:
            fetched = self._pull_remote_sessions()
            if fetched:
                self._state["last_pull"] = now
                self._save_state()
        if uploads_sent:
            self._last_sync = now

    def _handle_local_record(self, record: Dict[str, Any]) -> None:
        if not self._available:
            return
        session_id = record.get("session_id") or uuid.uuid4().hex
        target = self._pending_dir / f"{session_id}.json"
        try:
            with target.open("w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2)
        except Exception as exc:
            self._logger.debug("Failed to queue session %s for sync: %s", session_id, exc, exc_info=True)
            return
        if self._enabled:
            self.trigger_sync()

    def _pending_files(self) -> List[Path]:
        return sorted(self._pending_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)

    def _flush_pending_uploads(self) -> bool:
        files = self._pending_files()
        if not files:
            return False
        uploaded_any = False
        while files:
            batch_paths = files[: self.UPLOAD_BATCH_SIZE]
            payload = self._build_upload_payload(batch_paths)
            if not payload["samples"]:
                break
            try:
                response = self._session.post(
                    self._base_url,
                    json=payload,
                    timeout=self.REQUEST_TIMEOUT,
                )
            except Exception as exc:
                self._logger.debug("Sort feedback upload failed: %s", exc, exc_info=True)
                break

            if not response.ok:
                self._logger.info("Sort feedback upload rejected (status %s)", response.status_code)
                break

            for path in batch_paths:
                try:
                    if path.exists():
                        path.unlink()
                except Exception as exc:
                    self._logger.debug("Failed to drop pending file %s: %s", path.name, exc, exc_info=True)
            uploaded_any = True
            files = self._pending_files()

        return uploaded_any

    def _build_upload_payload(self, batch_paths: Iterable[Path]) -> Dict[str, Any]:
        samples: List[Dict[str, Any]] = []
        for path in batch_paths:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    record = json.load(handle)
                record.pop("user_note", None)
                record.pop("character_id", None)
                record.pop("stash_id", None)
                samples.append(record)
            except Exception as exc:
                self._logger.debug("Unable to read pending record %s: %s", path.name, exc, exc_info=True)
        return {
            "clientId": self.client_id,
            "schemaVersion": self.SCHEMA_VERSION,
            "appVersion": self._app_version,
            "samples": samples,
        }

    def _pull_remote_sessions(self) -> bool:
        params = {
            "clientId": self.client_id,
            "schemaVersion": self.SCHEMA_VERSION,
        }
        cursor = self._state.get("last_pull_token")
        if cursor:
            params["since"] = cursor
        try:
            response = self._session.get(self._base_url, params=params, timeout=self.REQUEST_TIMEOUT)
        except Exception as exc:
            self._logger.debug("Sort feedback download failed: %s", exc, exc_info=True)
            return False

        if not response.ok:
            self._logger.info("Sort feedback download rejected (status %s)", response.status_code)
            return False

        try:
            payload = response.json()
        except Exception as exc:
            self._logger.debug("Sort feedback download not JSON: %s", exc)
            return False

        samples = payload.get("samples") or payload.get("sessions") or []
        if not isinstance(samples, list):
            samples = []

        imported = self._store_remote_samples(samples)

        next_cursor = payload.get("nextSince") or payload.get("cursor")
        if next_cursor:
            self._state["last_pull_token"] = str(next_cursor)
            self._save_state()

        if imported:
            try:
                self._manager._schedule_training()  # type: ignore[attr-defined]
            except Exception:
                pass

        return imported

    def _store_remote_samples(self, samples: Iterable[Dict[str, Any]]) -> bool:
        imported_any = False
        for sample in samples:
            session_id = sample.get("sessionId") or sample.get("session_id")
            if not session_id:
                session_id = uuid.uuid4().hex
            safe_id = "".join(ch for ch in str(session_id) if ch.isalnum() or ch in {"-", "_"}) or uuid.uuid4().hex
            filename = f"remote-{safe_id}.json"
            path = self._manager.sessions_dir / filename
            if path.exists():
                continue
            record = dict(sample)
            record["session_id"] = str(session_id)
            record["remote_source"] = True
            record.setdefault("auto_label", True)
            record.setdefault("completed_at", time.time())
            record.setdefault("schema_version", self.SCHEMA_VERSION)
            try:
                with path.open("w", encoding="utf-8") as handle:
                    json.dump(record, handle, indent=2)
            except Exception as exc:
                self._logger.debug("Failed to store remote sample %s: %s", session_id, exc, exc_info=True)
                continue
            imported_any = True
        return imported_any

    def _load_state(self) -> Dict[str, Any]:
        if not self._state_path.is_file():
            return {}
        try:
            with self._state_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            self._logger.debug("Failed to read sync state: %s", exc, exc_info=True)
            return {}

    def _save_state(self) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(self._state, handle, indent=2)
            tmp.replace(self._state_path)
        except Exception as exc:
            self._logger.debug("Failed to persist sync state: %s", exc, exc_info=True)
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
