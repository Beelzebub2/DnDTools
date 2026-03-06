"""
SortObserver — replaces the old SortFeedbackManager.

Hooks into StashSorter.sort() to:
  1. Predict risk at sort start (delegates to SortAdaptiveModel)
  2. Record SORT_STARTED / SORT_COMPLETED events to SortEventStore
  3. Trigger incremental training after each completed session

Backward-compatible public surface:
    get_sort_feedback_manager()   — returns a SortObserver (same call-site in sort.py)
    SortFeedbackHandle            — same interface used by StashSorter
"""

import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.models.sort_model import (
    SortAdaptiveModel,
    RISK_FEATURE_NAMES,
    get_sort_adaptive_model,
)
from src.models.sort_event_store import (
    SortEventStore,
    get_event_store,
)

logger = logging.getLogger(__name__)

# Keep the old name list for backward compat / payload building
_FEATURE_NAMES: List[str] = list(RISK_FEATURE_NAMES)


@dataclass
class SortFeedbackHandle:
    """
    Per-session handle handed to StashSorter.  Same public interface as before
    so existing call-sites (increment, set_metric, finalize, etc.) keep working.
    """
    observer: "SortObserver"
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
        workspace = self.observer.model.recommended_workspace_cells(
            risk=risk, base_min=base_min, max_extra=max_extra,
        )
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

        summary = {
            "sessionId": self.record["session_id"],
            "predictedRisk": self.prediction,
            "workspaceTarget": self.record.get("features", {}).get("workspace_target"),
            "durationMs": self.record.get("duration_ms"),
            "success": bool(success),
            "cancelled": bool(cancelled),
        }
        self.record["summary"] = summary

        # Record the completion event
        self.observer._record_completion(self.record)

        # Trigger incremental risk training
        self.observer._schedule_risk_training()

        self._finalized = True
        return summary


class SortObserver:
    """
    Thin observer that delegates prediction to SortAdaptiveModel
    and persistence to SortEventStore.

    This replaces SortFeedbackManager.  The public API is nearly identical
    so wiring changes in sort.py are minimal.
    """

    def __init__(
        self,
        model: Optional[SortAdaptiveModel] = None,
        event_store: Optional[SortEventStore] = None,
    ) -> None:
        self.model = model or get_sort_adaptive_model()
        self.event_store = event_store or get_event_store()
        self._lock = threading.RLock()
        self._training_thread: Optional[threading.Thread] = None

    # ── Session lifecycle (called by StashSorter) ────────────────────

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

        # Build risk features from the base features
        risk_features = self._build_risk_features(record)
        prediction = self.model.predict_risk(risk_features)
        record["prediction"] = prediction

        # Record the start event
        try:
            self.event_store.record_sort_started(
                session_id=session_id,
                features=risk_features,
                metadata={
                    "character_id": character_id,
                    "stash_id": stash_id,
                    "pack_mode": bool(pack_mode),
                    "stack_mode": bool(stack_mode),
                    "prediction": prediction,
                },
            )
        except Exception as exc:
            logger.debug("Failed to record SORT_STARTED: %s", exc, exc_info=True)

        return SortFeedbackHandle(observer=self, record=record, prediction=prediction)

    # ── Delegated by the handle ──────────────────────────────────────

    def _record_completion(self, record: Dict[str, Any]) -> None:
        """Called by SortFeedbackHandle.finalize()."""
        risk_features = self._build_risk_features(record)
        try:
            self.event_store.record_sort_completed(
                session_id=record["session_id"],
                features=risk_features,
                success=bool(record.get("success")),
                failure_reason=record.get("failure_reason"),
                metrics=record.get("metrics"),
                duration_ms=record.get("duration_ms"),
            )
        except Exception as exc:
            logger.debug("Failed to record SORT_COMPLETED: %s", exc, exc_info=True)

    def _schedule_risk_training(self) -> None:
        """Train the risk model after each session completes."""
        with self._lock:
            if self._training_thread and self._training_thread.is_alive():
                return
            thread = threading.Thread(target=self._train_risk_worker, name="SortRiskTrain", daemon=True)
            self._training_thread = thread
            thread.start()

    def _train_risk_worker(self) -> None:
        try:
            events = self.event_store.get_risk_training_data(limit=5000)
            if not events:
                return
            self.model.train_risk(events, async_=False)
        except Exception as exc:
            logger.debug("Risk training worker failed: %s", exc, exc_info=True)

    # ── Backward-compat stubs ────────────────────────────────────────

    def get_model_version(self) -> Optional[str]:
        return self.model.get_risk_version()

    def apply_remote_model(self, payload: Dict[str, Any]) -> bool:
        return self.model.apply_remote_risk_model(payload)

    @property
    def base_dir(self):
        return self.model.base_dir

    @property
    def sessions_dir(self):
        return self.model.base_dir

    def record_user_feedback(self, session_id: str, success: bool, note: Optional[str] = None) -> bool:
        """Record explicit user feedback for a completed session."""
        if not session_id:
            return False
        try:
            self.event_store.record_sort_completed(
                session_id=session_id,
                features={},
                success=success,
                failure_reason=note if (not success and note) else None,
                metrics={"user_feedback": 1.0, "user_success": 1.0 if success else 0.0},
                duration_ms=None,
            )
            logger.info("Recorded user feedback for session %s: success=%s", session_id, success)
            return True
        except Exception as exc:
            logger.debug("Failed to record user feedback: %s", exc, exc_info=True)
            return False

    def register_record_listener(self, listener) -> None:
        """No-op for backward compatibility — sync now uses the event store."""
        pass

    # ── Feature engineering ──────────────────────────────────────────

    def _build_risk_features(self, record: Dict[str, Any]) -> Dict[str, float]:
        features = record.get("features") or {}
        metrics = record.get("metrics") or {}

        stash_total = max(1.0, float(features.get("stash_total_cells") or 1.0))
        stash_occupied = float(features.get("stash_occupied_cells") or 0.0)
        inventory_total = max(1.0, float(features.get("inventory_total_cells") or 1.0))
        inventory_free = float(features.get("inventory_free_cells") or 0.0)
        plan_size = float(metrics.get("plan_size") or 0.0)
        largest_area = float(
            metrics.get("largest_item_area") or features.get("largest_item_area") or 0.0
        )
        workspace_prep_moves = float(metrics.get("workspace_preparation_moves") or 0.0)
        buffered_items = float(metrics.get("buffered_items") or 0.0)
        park_attempts = float(metrics.get("park_attempts") or 0.0)
        workspace_attempts = float(metrics.get("workspace_creation_attempts") or 0.0)
        workspace_failures = float(metrics.get("workspace_creation_failures") or 0.0)

        plan_norm = max(1.0, plan_size)
        workspace_attempts_norm = max(1.0, workspace_attempts)

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


# ── Module-level singleton (backward compat with get_sort_feedback_manager) ──

_LOCK = threading.Lock()
_OBSERVER: Optional[SortObserver] = None


def get_sort_feedback_manager() -> SortObserver:
    """Drop-in replacement: returns a SortObserver with the same public API."""
    global _OBSERVER
    if _OBSERVER is not None:
        return _OBSERVER
    with _LOCK:
        if _OBSERVER is None:
            _OBSERVER = SortObserver()
        return _OBSERVER
