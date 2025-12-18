import json
import logging
import math
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
    MODEL_REFRESH_INTERVAL = 60 * 60  # 60 minutes
    MODEL_TYPE = "reliability"

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
        self._model_url = f"{self._base_url}/model"
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
            if self._enabled:
                self.trigger_sync(immediate=True)

    @property
    def client_id(self) -> str:
        return str(self._state.get("client_id"))

    @property
    def base_url(self) -> str:
        return self._base_url

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
        if self._should_fetch_model(now, force_download):
            if self._fetch_remote_model():
                self._state["last_model_pull"] = now
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
                transformed = self._convert_record_for_upload(record)
                if transformed:
                    samples.append(transformed)
            except Exception as exc:
                self._logger.debug("Unable to read pending record %s: %s", path.name, exc, exc_info=True)
        return {
            "clientId": self.client_id,
            "schemaVersion": self.SCHEMA_VERSION,
            "appVersion": self._app_version,
            "modelType": self.MODEL_TYPE,
            "samples": samples,
        }

    def _convert_record_for_upload(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not record or not isinstance(record, dict):
            return None

        session_id = str(record.get("session_id") or record.get("sessionId") or "").strip()
        if not session_id:
            return None

        sanitized: Dict[str, Any] = {"session_id": session_id}

        for key in (
            "schema_version",
            "app_version",
            "started_at",
            "completed_at",
            "duration_ms",
            "prediction",
            "success",
            "cancelled",
            "failure_reason",
            "auto_label",
        ):
            if key in record and record[key] is not None:
                sanitized[key] = record[key]

        summary = record.get("summary") if isinstance(record.get("summary"), dict) else None
        if summary:
            sanitized["summary"] = summary

        sanitized["pack_mode"] = bool(record.get("pack_mode"))
        sanitized["stack_mode"] = bool(record.get("stack_mode"))

        base_features = record.get("features") if isinstance(record.get("features"), dict) else {}
        normalized_features: Dict[str, float] = {}
        for key, value in base_features.items():
            coerced = self._coerce_float(value)
            if coerced is not None:
                normalized_features[key] = coerced

        derived = self._build_training_features(record)
        normalized_features.update(derived)
        sanitized["features"] = normalized_features

        metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else None
        if metrics:
            metric_snapshot: Dict[str, float] = {}
            for key in (
                "plan_size",
                "largest_item_area",
                "workspace_preparation_moves",
                "buffered_items",
                "park_attempts",
                "workspace_creation_attempts",
                "workspace_creation_successes",
                "workspace_creation_failures",
            ):
                value = self._coerce_float(metrics.get(key))
                if value is not None:
                    metric_snapshot[key] = value
            if metric_snapshot:
                sanitized["metrics"] = metric_snapshot

        return sanitized

    def _build_training_features(self, record: Dict[str, Any]) -> Dict[str, float]:
        features = record.get("features") if isinstance(record.get("features"), dict) else {}
        metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}

        stash_total = self._coerce_float(features.get("stash_total_cells"))
        if stash_total is None or stash_total < 1.0:
            stash_total = 1.0
        stash_occupied = self._coerce_float(features.get("stash_occupied_cells")) or 0.0

        inventory_total = self._coerce_float(features.get("inventory_total_cells"))
        if inventory_total is None or inventory_total < 1.0:
            inventory_total = 1.0
        inventory_free = self._coerce_float(features.get("inventory_free_cells")) or 0.0

        plan_size = self._coerce_float(metrics.get("plan_size")) or 0.0
        largest_area = (
            self._coerce_float(metrics.get("largest_item_area"))
            or self._coerce_float(features.get("largest_item_area"))
            or 0.0
        )
        workspace_prep_moves = self._coerce_float(metrics.get("workspace_preparation_moves")) or 0.0
        buffered_items = self._coerce_float(metrics.get("buffered_items")) or 0.0
        park_attempts = self._coerce_float(metrics.get("park_attempts")) or 0.0
        workspace_attempts = self._coerce_float(metrics.get("workspace_creation_attempts")) or 0.0
        workspace_failures = self._coerce_float(metrics.get("workspace_creation_failures"))
        if workspace_failures is None:
            successes = self._coerce_float(metrics.get("workspace_creation_successes")) or 0.0
            workspace_failures = max(0.0, workspace_attempts - successes)

        plan_norm = plan_size if plan_size and plan_size >= 1.0 else 1.0
        workspace_attempts_norm = workspace_attempts if workspace_attempts and workspace_attempts >= 1.0 else 1.0

        return {
            "stash_fill_ratio": stash_occupied / stash_total,
            "inventory_free_ratio": inventory_free / inventory_total,
            "plan_density": plan_size / stash_total,
            "largest_item_ratio": largest_area / stash_total,
            "workspace_prep_ratio": workspace_prep_moves / plan_norm,
            "buffer_ratio": buffered_items / plan_norm,
            "park_ratio": park_attempts / plan_norm,
            "workspace_failure_ratio": workspace_failures / workspace_attempts_norm,
            "pack_mode": 1.0 if record.get("pack_mode") else 0.0,
            "stack_mode": 1.0 if record.get("stack_mode") else 0.0,
        }

    def _coerce_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number

    def _pull_remote_sessions(self) -> bool:
        params = {
            "clientId": self.client_id,
            "schemaVersion": self.SCHEMA_VERSION,
            "modelType": self.MODEL_TYPE,
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

    def _should_fetch_model(self, now: float, force: bool) -> bool:
        if not self._model_url:
            return False
        if force:
            return True
        last_pull = float(self._state.get("last_model_pull", 0))
        return (now - last_pull) >= self.MODEL_REFRESH_INTERVAL

    def _fetch_remote_model(self) -> bool:
        params = {
            "clientId": self.client_id,
            "schemaVersion": self.SCHEMA_VERSION,
            "appVersion": self._app_version,
            "modelType": self.MODEL_TYPE,
        }
        try:
            current_version = self._manager.get_model_version()  # type: ignore[attr-defined]
        except Exception:
            current_version = None
        if current_version:
            params["currentVersion"] = current_version
        try:
            response = self._session.get(self._model_url, params=params, timeout=self.REQUEST_TIMEOUT)
        except Exception as exc:
            self._logger.debug("Sort feedback model fetch failed: %s", exc, exc_info=True)
            return False

        if response.status_code in {204, 304}:
            return False

        if not response.ok:
            self._logger.info("Sort feedback model fetch rejected (status %s)", response.status_code)
            return False

        try:
            payload = response.json()
        except Exception as exc:
            self._logger.debug("Sort feedback model response invalid JSON: %s", exc)
            return False

        model_payload = payload.get("model") if isinstance(payload, dict) else None
        if model_payload is None and isinstance(payload, dict):
            model_payload = payload
        if isinstance(payload, dict):
            model_type = payload.get("modelType")
            if model_type and str(model_type).lower() != self.MODEL_TYPE:
                return False
        if not isinstance(model_payload, dict):
            return False
        model_payload = dict(model_payload)
        model_payload.pop("modelType", None)

        applied = False
        try:
            applied = bool(self._manager.apply_remote_model(model_payload))  # type: ignore[attr-defined]
        except Exception as exc:
            self._logger.debug("Remote model rejected: %s", exc, exc_info=True)
            return False

        if applied:
            version = model_payload.get("version") or model_payload.get("modelVersion")
            checksum = model_payload.get("checksum") or model_payload.get("sha256")
            if version:
                self._state["last_model_version"] = str(version)
            if checksum:
                self._state["last_model_checksum"] = str(checksum)
            return True

        return False

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
