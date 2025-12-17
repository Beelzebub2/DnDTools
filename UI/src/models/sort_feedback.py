import json
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.models.appdirs import get_output_dir

logger = logging.getLogger(__name__)
_SORT_FEEDBACK_MANAGER_LOCK = threading.Lock()

_FEATURE_NAMES: List[str] = [
    "stash_fill_ratio",
    "inventory_free_ratio",
    "plan_density",
    "largest_item_ratio",
    "workspace_prep_ratio",
    "buffer_ratio",
    "park_ratio",
    "workspace_failure_ratio",
    "pack_mode",
    "stack_mode",
]


@dataclass
class SortFeedbackHandle:
    manager: "SortFeedbackManager"
    record: Dict[str, Any]
    prediction: Optional[float] = None
    _finalized: bool = False

    def increment(self, key: str, amount: float = 1.0) -> None:
        metrics = self.record.setdefault("metrics", {})
        metrics[key] = float(metrics.get(key, 0.0) + amount)

    def set_metric(self, key: str, value: float) -> None:
        metrics = self.record.setdefault("metrics", {})
        metrics[key] = float(value)

    def set_feature(self, key: str, value: float) -> None:
        features = self.record.setdefault("features", {})
        features[key] = float(value)

    def recommended_workspace_cells(self, base_min: int = 6, max_extra: int = 12) -> int:
        risk = max(0.0, min(1.0, self.prediction or 0.0))
        extra = math.ceil(max_extra * risk)
        workspace = base_min + extra
        self.record.setdefault("features", {})["workspace_target"] = workspace
        return workspace

    def finalize(self, *, success: bool, cancelled: bool, failure_reason: Optional[str]) -> Dict[str, Any]:
        if self._finalized:
            return self.record.get("summary", {})

        self.record["completed_at"] = time.time()
        self.record["duration_ms"] = int((self.record["completed_at"] - self.record["started_at"]) * 1000)
        self.record["success"] = bool(success)
        self.record["cancelled"] = bool(cancelled)
        self.record["failure_reason"] = failure_reason
        self.record["auto_label"] = False if cancelled else bool(success)

        summary = {
            "sessionId": self.record["session_id"],
            "predictedRisk": self.prediction,
            "workspaceTarget": self.record.get("features", {}).get("workspace_target"),
            "durationMs": self.record.get("duration_ms"),
            "success": bool(success),
            "cancelled": bool(cancelled),
        }
        self.record["summary"] = summary
        self.manager._persist_record(self.record)
        self.manager._schedule_training()
        self._finalized = True
        return summary


class SortFeedbackManager:
    def __init__(self, base_dir: Optional[Path] = None, min_samples: int = 25) -> None:
        output_dir = Path(base_dir or get_output_dir())
        self.base_dir = output_dir / "sort_feedback"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.base_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.base_dir / "model.json"
        self.min_samples = int(min_samples)
        self._lock = threading.RLock()
        self._model: Optional[Dict[str, Any]] = None
        self._training_thread: Optional[threading.Thread] = None
        self._record_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._load_model()

    def begin_session(
        self,
        *,
        character_id: Optional[str],
        stash_id: Optional[int],
        pack_mode: bool,
        stack_mode: bool,
        features: Dict[str, float],
    ) -> SortFeedbackHandle:
        session_id = uuid.uuid4().hex
        record: Dict[str, Any] = {
            "session_id": session_id,
            "character_id": character_id,
            "stash_id": stash_id,
            "pack_mode": bool(pack_mode),
            "stack_mode": bool(stack_mode),
            "started_at": time.time(),
            "features": dict(features or {}),
            "metrics": {},
        }
        prediction = self._predict_probability(record)
        record["prediction"] = prediction
        return SortFeedbackHandle(manager=self, record=record, prediction=prediction)

    def record_user_feedback(self, session_id: str, success: bool, note: Optional[str] = None) -> bool:
        try:
            with self._lock:
                record = self._load_record(session_id)
                if not record:
                    return False
                record["user_feedback"] = bool(success)
                if note:
                    record["user_note"] = str(note)[:500]
                self._write_record(record)
            self._schedule_training()
            return True
        except Exception as exc:
            logger.warning("Failed to record user feedback for %s: %s", session_id, exc, exc_info=True)
            return False

    def _predict_probability(self, record: Dict[str, Any]) -> Optional[float]:
        if not self._model:
            return None
        vector = self._build_feature_vector(record)
        if not vector:
            return None
        try:
            logits = sum(c * v for c, v in zip(self._model["coefficients"], vector)) + self._model["intercept"]
            return 1.0 / (1.0 + math.exp(-logits))
        except Exception as exc:
            logger.debug("Prediction failed: %s", exc, exc_info=True)
            return None

    def _persist_record(self, record: Dict[str, Any]) -> None:
        listeners: List[Callable[[Dict[str, Any]], None]] = []
        with self._lock:
            self._write_record(record)
            if self._record_listeners:
                listeners = list(self._record_listeners)

        if listeners:
            for listener in listeners:
                try:
                    listener(dict(record))
                except Exception as exc:
                    logger.debug("Sort feedback listener failed: %s", exc, exc_info=True)

    def _session_file(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def _write_record(self, record: Dict[str, Any]) -> None:
        path = self._session_file(record["session_id"])
        with path.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)

    def _load_record(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = self._session_file(session_id)
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def load_session_record(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._load_record(session_id)
        return record

    def get_session_path(self, session_id: str) -> Path:
        return self._session_file(session_id)

    def register_record_listener(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        if not callable(listener):
            raise ValueError("Listener must be callable")
        with self._lock:
            if listener in self._record_listeners:
                return
            self._record_listeners.append(listener)

    def _schedule_training(self) -> None:
        with self._lock:
            if self._training_thread and self._training_thread.is_alive():
                return
            thread = threading.Thread(target=self._train_worker, name="SortFeedbackTrainer", daemon=True)
            self._training_thread = thread
            thread.start()

    def _train_worker(self) -> None:
        try:
            rows: List[Dict[str, Any]] = []
            for file_path in self.sessions_dir.glob("*.json"):
                try:
                    with file_path.open("r", encoding="utf-8") as handle:
                        rows.append(json.load(handle))
                except Exception:
                    continue
            dataset = self._build_training_dataset(rows)
            if not dataset:
                return
            self._train_model(dataset)
        except Exception as exc:
            logger.debug("Training worker failed: %s", exc, exc_info=True)

    def _build_training_dataset(self, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        vectors: List[List[float]] = []
        labels: List[int] = []
        for record in rows:
            label = self._resolve_label(record)
            if label is None:
                continue
            vector = self._build_feature_vector(record)
            if not vector:
                continue
            vectors.append(vector)
            labels.append(int(label))

        if len(vectors) < self.min_samples:
            return None
        return {"X": vectors, "y": labels}

    def _resolve_label(self, record: Dict[str, Any]) -> Optional[bool]:
        if "user_feedback" in record:
            return bool(record["user_feedback"])
        if record.get("cancelled"):
            return False
        if "success" in record:
            return bool(record.get("success"))
        return None

    def _build_feature_vector(self, record: Dict[str, Any]) -> Optional[List[float]]:
        features = record.get("features", {})
        metrics = record.get("metrics", {})
        try:
            stash_total = max(1.0, float(features.get("stash_total_cells") or 1.0))
            stash_occupied = float(features.get("stash_occupied_cells") or 0.0)
            inventory_total = max(1.0, float(features.get("inventory_total_cells") or 1.0))
            inventory_free = float(features.get("inventory_free_cells") or 0.0)
            plan_size = float(metrics.get("plan_size") or 0.0)
            largest_area = float(metrics.get("largest_item_area") or features.get("largest_item_area") or 0.0)
            workspace_prep_moves = float(metrics.get("workspace_preparation_moves") or 0.0)
            buffered_items = float(metrics.get("buffered_items") or 0.0)
            park_attempts = float(metrics.get("park_attempts") or 0.0)
            workspace_attempts = float(metrics.get("workspace_creation_attempts") or 0.0)
            workspace_failures = float(metrics.get("workspace_creation_failures") or 0.0)

            plan_norm = max(1.0, plan_size) if plan_size else 1.0
            workspace_attempts_norm = max(1.0, workspace_attempts)

            vector = [
                stash_occupied / stash_total,
                inventory_free / inventory_total,
                plan_size / stash_total,
                largest_area / stash_total,
                workspace_prep_moves / plan_norm,
                buffered_items / plan_norm,
                park_attempts / plan_norm,
                workspace_failures / workspace_attempts_norm,
                1.0 if record.get("pack_mode") else 0.0,
                1.0 if record.get("stack_mode") else 0.0,
            ]
            return vector
        except Exception as exc:
            logger.debug("Failed to build feature vector: %s", exc, exc_info=True)
            return None

    def _train_model(self, dataset: Dict[str, Any]) -> None:
        try:
            from sklearn.linear_model import LogisticRegression  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:
            logger.debug("scikit-learn unavailable; skipping training: %s", exc)
            return

        X = np.array(dataset["X"], dtype=float)
        y = np.array(dataset["y"], dtype=int)
        if len(set(y.tolist())) < 2:
            return

        model = LogisticRegression(max_iter=200)
        model.fit(X, y)
        score = float(model.score(X, y))
        payload = {
            "trained_at": time.time(),
            "samples": int(len(y)),
            "coefficients": model.coef_[0].tolist(),
            "intercept": float(model.intercept_[0]),
            "feature_names": list(_FEATURE_NAMES),
            "training_score": score,
        }
        with self._lock:
            self._model = payload
            with self.model_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        logger.info("Updated sort reliability model with %s samples (score=%.3f)", len(y), score)

    def _load_model(self) -> None:
        if not self.model_path.is_file():
            return
        try:
            with self.model_path.open("r", encoding="utf-8") as handle:
                self._model = json.load(handle)
        except Exception as exc:
            logger.debug("Failed to load reliability model: %s", exc, exc_info=True)
            self._model = None


def get_sort_feedback_manager() -> SortFeedbackManager:
    global _DEFAULT_SORT_FEEDBACK_MANAGER
    try:
        manager = _DEFAULT_SORT_FEEDBACK_MANAGER
    except NameError:
        manager = None

    if manager is not None:
        return manager

    with _SORT_FEEDBACK_MANAGER_LOCK:
        try:
            manager = _DEFAULT_SORT_FEEDBACK_MANAGER
        except NameError:
            manager = None
        if manager is None:
            manager = SortFeedbackManager()
            globals()["_DEFAULT_SORT_FEEDBACK_MANAGER"] = manager
        return manager
