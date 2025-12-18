import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.models.sort_learning import (
    SortLearningManager,
    get_item_priority_feature_names,
    get_sort_learning_manager,
)


class SortLearningTrainer:
    """Trains the item-priority model locally and syncs anonymized samples."""

    MIN_LOCAL_SAMPLES = 40
    MAX_UPLOAD_SAMPLES = 250
    REQUEST_TIMEOUT = 12.0
    SCHEMA_VERSION = 1
    STATE_FILENAME = "trainer_state.json"
    MODEL_TYPE = "itemPriority"

    def __init__(
        self,
        *,
        settings_manager,
        app_version: str,
        learning_manager: Optional[SortLearningManager] = None,
        base_url: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._settings = settings_manager
        self._app_version = app_version
        self._learning_manager = learning_manager or get_sort_learning_manager()
        self._feature_names = get_item_priority_feature_names()
        self._session = requests.Session()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        base_dir = getattr(self._learning_manager, "base_dir", None)
        self._base_dir = Path(base_dir) if base_dir else Path(os.getcwd())
        self._state_path = self._base_dir / self.STATE_FILENAME
        self._state = self._load_state()
        if "client_id" not in self._state:
            self._state["client_id"] = uuid.uuid4().hex
            self._save_state()

        resolved_base = base_url or os.environ.get("SORT_LEARNING_SYNC_URL")
        if not resolved_base:
            resolved_base = os.environ.get("DNDTOOLS_TRAINING_URL")
        self._base_url = (resolved_base or "").strip()
        if self._base_url.endswith("/"):
            self._base_url = self._base_url[:-1]
        if not self._base_url:
            from src.models.sort_feedback_sync import SortFeedbackSyncService

            self._base_url = SortFeedbackSyncService.DEFAULT_BASE_URL
        self._model_url = f"{self._base_url}/model"

    def start(self) -> None:
        if not self._enabled():
            self._logger.info("Sort learning trainer disabled via settings")
            return
        if self._thread and self._thread.is_alive():
            return
        thread = threading.Thread(target=self._run_once, name="SortLearningTrainer", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        try:
            self._session.close()
        except Exception:
            pass

    def _enabled(self) -> bool:
        try:
            return bool(self._settings.get("sortLearningAutoTrain", True))
        except Exception:
            return True

    def _run_once(self) -> None:
        if self._stop_event.is_set():
            return
        try:
            dataset = self._load_dataset()
            total_rows = len(dataset)
            if total_rows < self.MIN_LOCAL_SAMPLES:
                self._logger.debug("Skipping item-priority training (%s samples)", total_rows)
                self._state["lastSkippedSampleCount"] = total_rows
                self._save_state()
                return
            model_payload = self._train_local_model(dataset)
            if model_payload:
                self._learning_manager.apply_model(model_payload)
                self._state["lastLocalTrain"] = time.time()
                self._state["lastLocalSampleCount"] = total_rows
                self._save_state()
            if self._should_upload_samples():
                self._upload_dataset(dataset)
                self._maybe_fetch_remote_model(model_payload)
        except Exception as exc:
            self._logger.debug("Sort learning trainer failed: %s", exc, exc_info=True)

    def _load_dataset(self) -> List[Dict[str, Any]]:
        path = getattr(self._learning_manager, "samples_path", None)
        if not path or not Path(path).is_file():
            return []
        dataset: List[Dict[str, Any]] = []
        candidate_cache: Dict[tuple, Dict[str, Any]] = {}
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    entry_type = record.get("type")
                    session_id = record.get("sessionId")
                    item_id = record.get("itemId") or record.get("itemName")
                    if not session_id or not item_id:
                        continue
                    key = (session_id, item_id)
                    if entry_type == "candidate":
                        candidate_cache[key] = record
                    elif entry_type == "outcome":
                        candidate = candidate_cache.get(key)
                        if not candidate:
                            continue
                        features = candidate.get("features") or {}
                        if not isinstance(features, dict):
                            continue
                        dataset.append(
                            {
                                "sessionId": session_id,
                                "itemId": item_id,
                                "features": features,
                                "label": 1 if record.get("success") else 0,
                                "metadata": self._merge_metadata(candidate, record),
                                "timestamp": record.get("timestamp") or candidate.get("timestamp"),
                            }
                        )
                        candidate_cache.pop(key, None)
        except Exception as exc:
            self._logger.debug("Failed reading item-priority samples: %s", exc, exc_info=True)
        return dataset

    def _merge_metadata(self, candidate: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if isinstance(candidate.get("metadata"), dict):
            payload.update(candidate["metadata"])
        if isinstance(outcome.get("metadata"), dict):
            payload.update(outcome["metadata"])
        payload.setdefault("itemName", candidate.get("itemName"))
        payload.setdefault("rarity", candidate.get("rarity"))
        return payload

    def _train_local_model(self, dataset: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        try:
            from sklearn.linear_model import LogisticRegression  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:
            self._logger.debug("scikit-learn unavailable; skipping local item-priority training: %s", exc)
            return None

        vectors: List[List[float]] = []
        labels: List[int] = []
        for row in dataset:
            features = row.get("features") or {}
            vector = [float(features.get(name, 0.0) or 0.0) for name in self._feature_names]
            vectors.append(vector)
            labels.append(int(1 if row.get("label") else 0))

        if len(set(labels)) < 2:
            self._logger.debug("Training skipped: need both positive and negative samples")
            return None

        X = np.array(vectors, dtype=float)
        y = np.array(labels, dtype=int)
        model = LogisticRegression(max_iter=250)
        model.fit(X, y)
        score = float(model.score(X, y))
        payload = {
            "trained_at": time.time(),
            "samples": int(len(labels)),
            "coefficients": model.coef_[0].tolist(),
            "intercept": float(model.intercept_[0]),
            "feature_names": list(self._feature_names),
            "training_score": score,
            "version": f"local-{int(time.time())}",
            "source": "local_startup_trainer",
        }
        self._logger.info(
            "Updated local item-priority model with %s samples (score=%.3f)",
            len(labels),
            score,
        )
        return payload

    def _should_upload_samples(self) -> bool:
        if not self._base_url:
            return False
        try:
            return bool(self._settings.get("sortFeedbackSyncEnabled", False))
        except Exception:
            return False

    def _upload_dataset(self, dataset: List[Dict[str, Any]]) -> None:
        if not dataset:
            return
        samples = dataset[-self.MAX_UPLOAD_SAMPLES :]
        payload = {
            "clientId": self._state.get("client_id"),
            "schemaVersion": self.SCHEMA_VERSION,
            "appVersion": self._app_version,
            "samples": [],  # keeps API contract compatible
            "itemPrioritySamples": [self._sanitize_sample(row) for row in samples],
            "modelType": self.MODEL_TYPE,
        }
        try:
            response = self._session.post(self._base_url, json=payload, timeout=self.REQUEST_TIMEOUT)
        except Exception as exc:
            self._logger.debug("Item-priority sample upload failed: %s", exc, exc_info=True)
            return

        if response.ok:
            self._state["lastUpload"] = time.time()
            self._state["lastUploadCount"] = len(samples)
            self._save_state()
        else:
            self._logger.info(
                "Item-priority sample upload rejected (status %s)", response.status_code
            )

    def _sanitize_sample(self, row: Dict[str, Any]) -> Dict[str, Any]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return {
            "sessionId": row.get("sessionId"),
            "itemId": row.get("itemId"),
            "label": int(1 if row.get("label") else 0),
            "features": {name: float(row.get("features", {}).get(name, 0.0) or 0.0) for name in self._feature_names},
            "metadata": metadata,
            "timestamp": row.get("timestamp"),
        }

    def _maybe_fetch_remote_model(self, local_payload: Optional[Dict[str, Any]]) -> None:
        params = {
            "clientId": self._state.get("client_id"),
            "schemaVersion": self.SCHEMA_VERSION,
            "appVersion": self._app_version,
            "modelType": self.MODEL_TYPE,
        }
        current_version = self._learning_manager.get_model_version()
        if current_version:
            params["currentVersion"] = current_version
        try:
            response = self._session.get(self._model_url, params=params, timeout=self.REQUEST_TIMEOUT)
        except Exception as exc:
            self._logger.debug("Item-priority model fetch failed: %s", exc, exc_info=True)
            return

        if response.status_code in {204, 304}:
            return
        if not response.ok:
            self._logger.info(
                "Item-priority model fetch rejected (status %s)", response.status_code
            )
            return
        try:
            payload = response.json()
        except Exception as exc:
            self._logger.debug("Item-priority model response invalid JSON: %s", exc)
            return
        model_payload = payload.get("model") if isinstance(payload, dict) else None
        if model_payload is None and isinstance(payload, dict):
            model_payload = payload
        if not isinstance(model_payload, dict):
            return
        if local_payload and model_payload.get("version") == local_payload.get("version"):
            return
        if not self._validate_remote_model(model_payload):
            return
        if self._learning_manager.apply_model(model_payload):
            self._state["lastRemoteVersion"] = model_payload.get("version")
            self._state["lastRemoteFetchedAt"] = time.time()
            self._save_state()

    def _validate_remote_model(self, payload: Dict[str, Any]) -> bool:
        coefficients = payload.get("coefficients")
        if not isinstance(coefficients, list) or len(coefficients) != len(self._feature_names):
            return False
        return "intercept" in payload

    def _load_state(self) -> Dict[str, Any]:
        if not self._state_path.is_file():
            return {}
        try:
            with self._state_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _save_state(self) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(self._state, handle, indent=2)
            tmp.replace(self._state_path)
        except Exception as exc:
            self._logger.debug("Failed to persist trainer state: %s", exc, exc_info=True)
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass


def start_sort_learning_trainer(*, settings_manager, app_version: str, base_url: Optional[str] = None) -> Optional[SortLearningTrainer]:
    trainer = SortLearningTrainer(
        settings_manager=settings_manager,
        app_version=app_version,
        base_url=base_url,
    )
    trainer.start()
    return trainer
