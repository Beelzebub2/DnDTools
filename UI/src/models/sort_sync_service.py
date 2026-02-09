"""
SortSyncService — Unified bidirectional sync with the website backend.

Replaces both SortFeedbackSyncService and SortLearningTrainer with a single
background service that:

  1. Uploads event batches from SortEventStore via POST /api/ai/training/events
  2. Downloads global risk + item models via GET /api/ai/training/model
  3. Uses delta sync — only sends events since the last sync cursor
  4. Uses ETag caching for model downloads

Wire-up (in app.py):
    from src.models.sort_sync_service import SortSyncService
    sync = SortSyncService(
        settings_manager=settings_manager,
        app_version=APP_VERSION,
    )
    sync.start()
"""

import json
import logging
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.models.sort_model import (
    SortAdaptiveModel,
    get_sort_adaptive_model,
)
from src.models.sort_event_store import (
    SortEventStore,
    get_event_store,
)

logger = logging.getLogger(__name__)


class SortSyncService:
    """
    Single background service for all ML sync with the website.

    Replaces:
      - SortFeedbackSyncService   (reliability sessions upload/download)
      - SortLearningTrainer       (item-priority samples upload/download + local training)
    """

    DEFAULT_BASE_URL = "https://dndtools.rrmtools.uk/api/ai/training"
    SCHEMA_VERSION = 2
    REQUEST_TIMEOUT = 15.0
    UPLOAD_BATCH_SIZE = 200
    SYNC_INTERVAL = 60 * 30         # 30 minutes
    MODEL_REFRESH_INTERVAL = 60 * 60  # 1 hour
    TRAIN_AFTER_SYNC = True

    def __init__(
        self,
        *,
        settings_manager,
        app_version: str,
        model: Optional[SortAdaptiveModel] = None,
        event_store: Optional[SortEventStore] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._settings = settings_manager
        self._app_version = app_version
        self.model = model or get_sort_adaptive_model()
        self.event_store = event_store or get_event_store()
        import requests
        self._session = requests.Session()

        # Resolve API URL
        resolved = base_url or os.environ.get("DNDTOOLS_TRAINING_URL") or self.DEFAULT_BASE_URL
        resolved = resolved.strip().rstrip("/")
        self._base_url = resolved
        self._events_url = f"{self._base_url}/events"
        self._model_url = f"{self._base_url}/model"

        # State
        self._state_path = self.model.base_dir / "sync_state.json"
        self._state = self._load_state()
        if "client_id" not in self._state:
            self._state["client_id"] = uuid.uuid4().hex
            self._save_state()

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._sync_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

    @property
    def client_id(self) -> str:
        return str(self._state.get("client_id", ""))

    @property
    def base_url(self) -> str:
        return self._base_url

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._enabled():
            logger.info("Sort sync service disabled via settings")
            return
        self._ensure_worker()
        self.trigger_sync(immediate=True)

    def stop(self) -> None:
        self._stop_event.set()
        self._sync_event.set()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=3.0)
        try:
            self._session.close()
        except Exception:
            pass

    def apply_settings(self, settings: Dict[str, Any]) -> None:
        was_enabled = self._enabled()
        if "sortFeedbackSyncEnabled" in settings or "sortLearningAutoTrain" in settings:
            if self._enabled() and not was_enabled:
                logger.info("Sync re-enabled; queueing immediate sync")
                self.start()

    def trigger_sync(self, *, immediate: bool = False) -> None:
        if not self._enabled():
            return
        self._sync_event.set()
        if immediate:
            self._ensure_worker()

    # ── Worker ───────────────────────────────────────────────────────

    def _enabled(self) -> bool:
        try:
            sync_enabled = bool(self._settings.get("sortFeedbackSyncEnabled", False))
            train_enabled = bool(self._settings.get("sortLearningAutoTrain", True))
            return sync_enabled or train_enabled
        except Exception:
            return False

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        worker = threading.Thread(target=self._worker_loop, name="SortSyncService", daemon=True)
        self._worker = worker
        worker.start()

    def _worker_loop(self) -> None:
        # Initial delay
        self._stop_event.wait(5.0)

        while not self._stop_event.is_set():
            triggered = self._sync_event.wait(timeout=self.SYNC_INTERVAL)
            self._sync_event.clear()
            if self._stop_event.is_set():
                break
            if not self._enabled():
                continue
            try:
                self._perform_sync_cycle()
            except Exception as exc:
                logger.debug("Sync cycle failed: %s", exc, exc_info=True)

    def _perform_sync_cycle(self) -> None:
        # 1. Upload unsynced events
        uploaded = self._upload_events()

        # 2. Fetch remote models
        now = time.time()
        last_model_pull = float(self._state.get("last_model_pull", 0))
        if (now - last_model_pull) >= self.MODEL_REFRESH_INTERVAL:
            risk_updated = self._fetch_remote_model("reliability")
            item_updated = self._fetch_remote_model("itemPriority")
            self._state["last_model_pull"] = now
            self._save_state()

        # 3. Trigger local training if we uploaded new data
        if uploaded and self.TRAIN_AFTER_SYNC:
            self._trigger_local_training()

    # ── Upload ───────────────────────────────────────────────────────

    def _upload_events(self) -> bool:
        events = self.event_store.get_unsynced_events(limit=self.UPLOAD_BATCH_SIZE)
        if not events:
            return False

        # Strip local-only fields
        upload_events = []
        event_ids = []
        for ev in events:
            event_ids.append(ev["id"])
            upload_events.append({
                "event_type": ev["event_type"],
                "session_id": ev["session_id"],
                "timestamp": ev["timestamp"],
                "features": ev["features"],
                "label": ev["label"],
                "weight": ev["weight"],
                "metadata": ev["metadata"],
            })

        payload = {
            "clientId": self.client_id,
            "schemaVersion": self.SCHEMA_VERSION,
            "appVersion": self._app_version,
            "events": upload_events,
        }

        try:
            response = self._session.post(
                self._events_url,
                json=payload,
                timeout=self.REQUEST_TIMEOUT,
            )
        except Exception as exc:
            logger.debug("Event upload failed: %s", exc, exc_info=True)
            return False

        if response.ok:
            self.event_store.mark_synced(event_ids)
            self._state["last_upload"] = time.time()
            self._state["last_upload_count"] = len(event_ids)
            self._save_state()
            logger.info("Uploaded %d events to server", len(event_ids))
            return True
        else:
            logger.info("Event upload rejected (status %s)", response.status_code)
            return False

    # ── Download models ──────────────────────────────────────────────

    def _fetch_remote_model(self, model_type: str) -> bool:
        params = {
            "clientId": self.client_id,
            "schemaVersion": self.SCHEMA_VERSION,
            "appVersion": self._app_version,
            "modelType": model_type,
        }

        # Send current version for 204/304 optimization
        if model_type == "reliability":
            current = self.model.get_risk_version()
        else:
            current = self.model.get_item_version()
        if current:
            params["currentVersion"] = current

        # ETag caching
        etag_key = f"etag_{model_type}"
        stored_etag = self._state.get(etag_key)
        headers = {}
        if stored_etag:
            headers["If-None-Match"] = stored_etag

        try:
            response = self._session.get(
                self._model_url,
                params=params,
                headers=headers,
                timeout=self.REQUEST_TIMEOUT,
            )
        except Exception as exc:
            logger.debug("Model fetch for %s failed: %s", model_type, exc, exc_info=True)
            return False

        if response.status_code in {204, 304}:
            return False

        if not response.ok:
            logger.info("Model fetch for %s rejected (status %s)", model_type, response.status_code)
            return False

        try:
            data = response.json()
        except Exception:
            return False

        model_payload = data.get("model") if isinstance(data, dict) else None
        if model_payload is None and isinstance(data, dict):
            model_payload = data
        if not isinstance(model_payload, dict):
            return False

        model_payload = dict(model_payload)
        model_payload.pop("modelType", None)

        applied = False
        if model_type == "reliability":
            applied = self.model.apply_remote_risk_model(model_payload)
        else:
            applied = self.model.apply_remote_item_model(model_payload)

        if applied:
            # Store ETag
            new_etag = response.headers.get("ETag")
            if new_etag:
                self._state[etag_key] = new_etag
            self._state[f"last_{model_type}_version"] = model_payload.get("version")
            self._save_state()
            logger.info("Applied remote %s model v%s", model_type, model_payload.get("version"))

        return applied

    # ── Local training trigger ───────────────────────────────────────

    def _trigger_local_training(self) -> None:
        """Train both heads from the event store after a sync cycle."""
        try:
            risk_events = self.event_store.get_risk_training_data(limit=5000)
            if risk_events:
                self.model.train_risk(risk_events, async_=True)
        except Exception as exc:
            logger.debug("Post-sync risk training failed: %s", exc, exc_info=True)

        try:
            item_events = self.event_store.get_item_training_data(limit=10000)
            if item_events:
                self.model.train_items(item_events, async_=True)
        except Exception as exc:
            logger.debug("Post-sync item training failed: %s", exc, exc_info=True)

    # ── State persistence ────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        if not self._state_path.is_file():
            return {}
        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            tmp.replace(self._state_path)
        except Exception as exc:
            logger.debug("Failed to persist sync state: %s", exc, exc_info=True)
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
