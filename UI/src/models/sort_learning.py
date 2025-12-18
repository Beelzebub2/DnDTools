import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.models.appdirs import get_output_dir

logger = logging.getLogger(__name__)

_ITEM_FEATURE_NAMES = [
    "width",
    "height",
    "area",
    "max_side",
    "rarity",
    "slot_x",
    "slot_y",
    "slot_x_norm",
    "slot_y_norm",
    "pack_mode",
    "stack_mode",
    "free_ratio",
]

_SORT_LEARNING_MANAGER_LOCK = threading.Lock()


class SortLearningManager:
    """Stores per-item training samples and scores items with an optional model."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        output_dir = Path(base_dir or get_output_dir())
        self.base_dir = output_dir / "sort_learning"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.samples_path = self.base_dir / "item_priority_samples.jsonl"
        self.model_path = self.base_dir / "item_priority_model.json"
        self._lock = threading.RLock()
        self._model: Optional[Dict[str, Any]] = None
        self._load_model()

    def score_item(self, features: Dict[str, float]) -> Optional[float]:
        model = self._model
        if not model:
            return None
        coefficients = model.get("coefficients")
        if not isinstance(coefficients, list) or len(coefficients) != len(_ITEM_FEATURE_NAMES):
            return None
        intercept = float(model.get("intercept", 0.0))
        try:
            score = intercept
            for weight, name in zip(coefficients, _ITEM_FEATURE_NAMES):
                score += float(weight) * float(features.get(name, 0.0))
            return score
        except Exception as exc:
            logger.debug("Item scoring failed: %s", exc, exc_info=True)
            return None

    def record_priority_sample(
        self,
        *,
        session_id: Optional[str],
        item: Any,
        features: Dict[str, float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "type": "candidate",
            "timestamp": time.time(),
            "sessionId": session_id,
            "itemId": getattr(item, "item_id", None),
            "itemName": getattr(item, "name", None),
            "rarity": getattr(item, "rarity", None),
            "features": dict(features or {}),
            "metadata": metadata or {},
        }
        self._append_sample(payload)

    def record_priority_outcome(
        self,
        *,
        session_id: Optional[str],
        item: Any,
        success: bool,
        reason: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "type": "outcome",
            "timestamp": time.time(),
            "sessionId": session_id,
            "itemId": getattr(item, "item_id", None),
            "itemName": getattr(item, "name", None),
            "rarity": getattr(item, "rarity", None),
            "success": bool(success),
            "reason": reason,
            "metadata": metadata or {},
        }
        self._append_sample(payload)

    def apply_model(self, payload: Dict[str, Any]) -> bool:
        if not self._validate_model_payload(payload):
            return False
        payload = dict(payload)
        payload.setdefault("received_at", time.time())
        with self._lock:
            self._model = payload
            with self.model_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        logger.info("Applied item priority model %s", payload.get("version"))
        return True

    def get_model_version(self) -> Optional[str]:
        with self._lock:
            model = self._model
            if not model:
                return None
            version = model.get("version")
        return str(version) if version else None

    def get_model_payload(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._model:
                return None
            return dict(self._model)

    def _append_sample(self, payload: Dict[str, Any]) -> None:
        if not payload:
            return
        try:
            line = json.dumps(payload, separators=(",", ":"))
            with self._lock:
                with self.samples_path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.write("\n")
        except Exception as exc:
            logger.debug("Unable to record priority sample: %s", exc, exc_info=True)

    def _validate_model_payload(self, payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        coefficients = payload.get("coefficients")
        if not isinstance(coefficients, list) or len(coefficients) != len(_ITEM_FEATURE_NAMES):
            return False
        if "intercept" not in payload:
            return False
        return True

    def _load_model(self) -> None:
        if not self.model_path.is_file():
            return
        try:
            with self.model_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if self._validate_model_payload(payload):
                with self._lock:
                    self._model = payload
        except Exception as exc:
            logger.debug("Failed to load item priority model: %s", exc, exc_info=True)
            self._model = None


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
