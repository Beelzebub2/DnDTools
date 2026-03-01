"""
SortAdaptiveModel — Unified ML model for stash sorting.

Replaces the old two-model system (SortFeedbackManager logistic regression +
SortLearningManager logistic regression) with a single adaptive model that:

  1. Predicts sort-session risk  (will this sort fail?)
  2. Scores item-slot candidates  (where should this item go?)
  3. Learns from failures and user corrections with weighted samples

The model uses gradient-boosted trees (via scikit-learn's
HistGradientBoosting) when available, falling back to logistic regression,
and finally to a pure-Python sigmoid scorer when no ML library is present.

Wire-up:
    from src.models.sort_model import get_sort_adaptive_model
    model = get_sort_adaptive_model()

All model artefacts live under  <output_dir>/sort_model/.
"""

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.models.appdirs import get_output_dir

logger = logging.getLogger(__name__)

# ── Feature schemas ──────────────────────────────────────────────────────

RISK_FEATURE_NAMES: List[str] = [
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

ITEM_FEATURE_NAMES: List[str] = [
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

MODEL_SCHEMA_VERSION = 2

# ── Helpers ──────────────────────────────────────────────────────────────

def _sigmoid(x: float) -> float:
    if x > 20.0:
        return 1.0
    if x < -20.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _linear_score(coefficients: List[float], intercept: float, vector: List[float]) -> float:
    return sum(c * v for c, v in zip(coefficients, vector)) + intercept


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


# ── Model payload helpers ────────────────────────────────────────────────

def validate_model_payload(payload: Dict[str, Any], expected_features: List[str]) -> bool:
    """Check that a serialized model dict has the right shape."""
    if not isinstance(payload, dict):
        return False
    coefficients = payload.get("coefficients")
    if not isinstance(coefficients, list) or len(coefficients) != len(expected_features):
        return False
    if "intercept" not in payload:
        return False
    feature_names = payload.get("feature_names")
    if feature_names and list(feature_names) != expected_features:
        return False
    return True


# ── Adaptive Model ───────────────────────────────────────────────────────

class SortAdaptiveModel:
    """
    Unified model for sort-risk prediction and item-slot scoring.

    Supports three tiers:
      1. HistGradientBoosting (scikit-learn ≥ 1.0) — learns non-linear interactions
      2. LogisticRegression (scikit-learn)          — linear fallback
      3. Pure-Python sigmoid scorer                  — zero-dependency fallback

    Both the *risk* and *item* heads share the same lifecycle but maintain
    independent weight sets and training datasets.
    """

    MIN_RISK_SAMPLES = 25
    MIN_ITEM_SAMPLES = 30
    COLD_START_RISK = 0.15  # default risk when no model is loaded
    CORRECTION_WEIGHT = 3.0  # extra weight given to user-correction samples
    FAILURE_WEIGHT = 2.0     # extra weight given to failure samples

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        output_dir = Path(base_dir or get_output_dir())
        self.base_dir = output_dir / "sort_model"
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self._risk_model_path = self.base_dir / "risk_model.json"
        self._item_model_path = self.base_dir / "item_model.json"
        self._meta_path = self.base_dir / "model_meta.json"

        self._lock = threading.RLock()

        # Runtime model state (serializable payloads)
        self._risk_payload: Optional[Dict[str, Any]] = None
        self._item_payload: Optional[Dict[str, Any]] = None

        # Fitted sklearn objects (not serialized)
        self._risk_estimator: Any = None
        self._item_estimator: Any = None

        # Metadata
        self._meta: Dict[str, Any] = {
            "schema_version": MODEL_SCHEMA_VERSION,
            "risk_version": None,
            "item_version": None,
            "last_risk_train": None,
            "last_item_train": None,
            "total_risk_samples_seen": 0,
            "total_item_samples_seen": 0,
        }

        self._training_thread: Optional[threading.Thread] = None
        self._load()

    # ── Public API: prediction ───────────────────────────────────────

    def predict_risk(self, features: Dict[str, float]) -> float:
        """
        Return the predicted probability (0-1) that a sort session with
        the given features will fail.  Higher = more likely to fail.
        """
        vector = self._risk_vector(features)
        if vector is None:
            return self.COLD_START_RISK

        # Try sklearn estimator first
        estimator = self._risk_estimator
        if estimator is not None:
            try:
                import numpy as np
                proba = estimator.predict_proba(np.array([vector]))[0]
                # Class 1 = failure
                return float(proba[1]) if len(proba) > 1 else float(proba[0])
            except Exception:
                pass

        # Fall back to linear payload
        payload = self._risk_payload
        if payload and validate_model_payload(payload, RISK_FEATURE_NAMES):
            logit = _linear_score(payload["coefficients"], payload["intercept"], vector)
            return _sigmoid(logit)

        return self.COLD_START_RISK

    def score_item_slot(self, features: Dict[str, float]) -> Optional[float]:
        """
        Score an (item, slot) candidate pair.  Higher = better placement.
        Returns None when no model is available (caller should use heuristics).
        """
        vector = self._item_vector(features)
        if vector is None:
            return None

        estimator = self._item_estimator
        if estimator is not None:
            try:
                import numpy as np
                proba = estimator.predict_proba(np.array([vector]))[0]
                return float(proba[1]) if len(proba) > 1 else float(proba[0])
            except Exception:
                pass

        payload = self._item_payload
        if payload and validate_model_payload(payload, ITEM_FEATURE_NAMES):
            return _linear_score(payload["coefficients"], payload["intercept"], vector)

        return None

    def recommended_workspace_cells(
        self,
        risk: Optional[float] = None,
        base_min: int = 6,
        max_extra: int = 12,
    ) -> int:
        """Translate a risk score into a recommended workspace buffer."""
        r = max(0.0, min(1.0, risk if risk is not None else self.COLD_START_RISK))
        extra = math.ceil(max_extra * r)
        return base_min + extra

    # ── Public API: training ─────────────────────────────────────────

    def train_risk(self, events: List[Dict[str, Any]], *, async_: bool = True) -> None:
        """
        Train the risk head from a list of event dicts, each containing:
            features: Dict[str, float]
            label: 0 | 1                (0 = success, 1 = failure)
            weight: float               (optional, default 1.0)
        """
        if async_:
            self._schedule_train("risk", events)
        else:
            self._do_train_risk(events)

    def train_items(self, events: List[Dict[str, Any]], *, async_: bool = True) -> None:
        """
        Train the item head from a list of event dicts, each containing:
            features: Dict[str, float]
            label: 0 | 1                (0 = bad placement, 1 = good)
            weight: float               (optional, default 1.0)
        """
        if async_:
            self._schedule_train("items", events)
        else:
            self._do_train_items(events)

    # ── Public API: model exchange ───────────────────────────────────

    def apply_remote_risk_model(self, payload: Dict[str, Any]) -> bool:
        if not validate_model_payload(payload, RISK_FEATURE_NAMES):
            return False
        payload = dict(payload)
        payload.setdefault("source", "remote")
        payload.setdefault("received_at", time.time())
        self._save_risk_payload(payload)
        logger.info("Applied remote risk model v%s", payload.get("version"))
        return True

    def apply_remote_item_model(self, payload: Dict[str, Any]) -> bool:
        if not validate_model_payload(payload, ITEM_FEATURE_NAMES):
            return False
        payload = dict(payload)
        payload.setdefault("source", "remote")
        payload.setdefault("received_at", time.time())
        self._save_item_payload(payload)
        logger.info("Applied remote item model v%s", payload.get("version"))
        return True

    def get_risk_model_payload(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._risk_payload) if self._risk_payload else None

    def get_item_model_payload(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._item_payload) if self._item_payload else None

    def get_risk_version(self) -> Optional[str]:
        with self._lock:
            p = self._risk_payload
            return str(p["version"]) if p and "version" in p else None

    def get_item_version(self) -> Optional[str]:
        with self._lock:
            p = self._item_payload
            return str(p["version"]) if p and "version" in p else None

    def get_meta(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._meta)

    # ── Feature vectorization ────────────────────────────────────────

    def _risk_vector(self, features: Dict[str, float]) -> Optional[List[float]]:
        try:
            return [_safe_float(features.get(name)) for name in RISK_FEATURE_NAMES]
        except Exception:
            return None

    def _item_vector(self, features: Dict[str, float]) -> Optional[List[float]]:
        try:
            return [_safe_float(features.get(name)) for name in ITEM_FEATURE_NAMES]
        except Exception:
            return None

    # ── Training internals ───────────────────────────────────────────

    def _schedule_train(self, head: str, events: List[Dict[str, Any]]) -> None:
        with self._lock:
            if self._training_thread and self._training_thread.is_alive():
                return
            target = self._do_train_risk if head == "risk" else self._do_train_items
            thread = threading.Thread(
                target=target, args=(events,), name=f"SortModelTrain-{head}", daemon=True
            )
            self._training_thread = thread
            thread.start()

    def _do_train_risk(self, events: List[Dict[str, Any]]) -> None:
        vectors, labels, weights = self._prepare_dataset(events, RISK_FEATURE_NAMES, self.MIN_RISK_SAMPLES)
        if vectors is None:
            return
        try:
            payload, estimator = self._fit(vectors, labels, weights, RISK_FEATURE_NAMES, "risk")
            if payload:
                self._save_risk_payload(payload)
                with self._lock:
                    self._risk_estimator = estimator
                    self._meta["last_risk_train"] = time.time()
                    self._meta["total_risk_samples_seen"] += len(labels)
                self._save_meta()
                logger.info(
                    "Risk model trained: %d samples, score=%.3f",
                    payload.get("samples", 0),
                    payload.get("training_score", 0),
                )
        except Exception as exc:
            logger.debug("Risk training failed: %s", exc, exc_info=True)

    def _do_train_items(self, events: List[Dict[str, Any]]) -> None:
        vectors, labels, weights = self._prepare_dataset(events, ITEM_FEATURE_NAMES, self.MIN_ITEM_SAMPLES)
        if vectors is None:
            return
        try:
            payload, estimator = self._fit(vectors, labels, weights, ITEM_FEATURE_NAMES, "item")
            if payload:
                self._save_item_payload(payload)
                with self._lock:
                    self._item_estimator = estimator
                    self._meta["last_item_train"] = time.time()
                    self._meta["total_item_samples_seen"] += len(labels)
                self._save_meta()
                logger.info(
                    "Item model trained: %d samples, score=%.3f",
                    payload.get("samples", 0),
                    payload.get("training_score", 0),
                )
        except Exception as exc:
            logger.debug("Item training failed: %s", exc, exc_info=True)

    def _prepare_dataset(
        self,
        events: List[Dict[str, Any]],
        feature_names: List[str],
        min_samples: int,
    ) -> Tuple[Optional[List[List[float]]], Optional[List[int]], Optional[List[float]]]:
        """Extract (vectors, labels, weights) from event dicts."""
        vectors: List[List[float]] = []
        labels: List[int] = []
        weights: List[float] = []
        for ev in events:
            features = ev.get("features")
            label = ev.get("label")
            if features is None or label is None:
                continue
            try:
                vec = [_safe_float(features.get(name)) for name in feature_names]
            except Exception:
                continue
            vectors.append(vec)
            labels.append(int(bool(label)))
            weights.append(max(0.1, _safe_float(ev.get("weight"), 1.0)))

        if len(vectors) < min_samples:
            logger.debug("Not enough samples (%d < %d)", len(vectors), min_samples)
            return None, None, None
        if len(set(labels)) < 2:
            logger.debug("Need both positive and negative labels")
            return None, None, None
        return vectors, labels, weights

    def _holdout_score(self, X, y, w, model_type):
        """Weighted accuracy on a stratified 80/20 held-out test split.

        Returns the score as a float, or None if the dataset is too small
        for a meaningful split (caller should fall back).
        """
        import numpy as np

        try:
            from sklearn.model_selection import train_test_split

            _, counts = np.unique(y, return_counts=True)
            if min(counts) < 2 or len(y) < 10:
                return None

            X_tr, X_te, y_tr, y_te, w_tr, w_te = train_test_split(
                X, y, w, test_size=0.2, random_state=42, stratify=y
            )

            if model_type == "hgb":
                from sklearn.ensemble import HistGradientBoostingClassifier

                ev = HistGradientBoostingClassifier(
                    max_iter=150,
                    max_depth=4,
                    learning_rate=0.1,
                    min_samples_leaf=5,
                    l2_regularization=0.01,
                    random_state=42,
                )
            else:
                from sklearn.linear_model import LogisticRegression

                ev = LogisticRegression(max_iter=300, C=1.0, solver="lbfgs")

            ev.fit(X_tr, y_tr, sample_weight=w_tr)
            preds = ev.predict(X_te)
            w_correct = np.sum(w_te[preds == y_te])
            return float(w_correct / np.sum(w_te))
        except Exception:
            return None

    def _fit(
        self,
        vectors: List[List[float]],
        labels: List[int],
        weights: List[float],
        feature_names: List[str],
        head_name: str,
    ) -> Tuple[Optional[Dict[str, Any]], Any]:
        """
        Attempt to fit in this order:
          1. HistGradientBoostingClassifier
          2. LogisticRegression
          3. Return None (caller keeps existing model)
        Returns (serializable_payload, fitted_estimator).
        """
        import numpy as np

        X = np.array(vectors, dtype=np.float64)
        y = np.array(labels, dtype=np.int32)
        w = np.array(weights, dtype=np.float64)

        estimator = None
        model_type = "none"

        # Tier 1: gradient boosted trees
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier
            est = HistGradientBoostingClassifier(
                max_iter=150,
                max_depth=4,
                learning_rate=0.1,
                min_samples_leaf=5,
                l2_regularization=0.01,
                random_state=42,
            )
            est.fit(X, y, sample_weight=w)
            estimator = est
            model_type = "hgb"
        except Exception:
            pass

        # Tier 2: logistic regression
        if estimator is None:
            try:
                from sklearn.linear_model import LogisticRegression
                est = LogisticRegression(max_iter=300, C=1.0, solver="lbfgs")
                est.fit(X, y, sample_weight=w)
                estimator = est
                model_type = "lr"
            except Exception as exc:
                logger.debug("sklearn unavailable: %s", exc)
                return None, None

        # Compute held-out weighted accuracy (falls back to weighted training accuracy)
        score = self._holdout_score(X, y, w, model_type)
        if score is None:
            score = float(estimator.score(X, y, sample_weight=w))

        # Build serializable payload (always store linear coefficients for cross-platform portability)
        coefficients: List[float] = [0.0] * len(feature_names)
        intercept: float = 0.0
        if model_type == "lr":
            coefficients = estimator.coef_[0].tolist()
            intercept = float(estimator.intercept_[0])
        elif model_type == "hgb":
            # For HGB we can't extract linear coefficients directly.
            # Store dummy coefficients but mark model_type so the desktop
            # uses the sklearn estimator at runtime and the server can still
            # serve a linear approximation via a future endpoint.
            try:
                from sklearn.linear_model import LogisticRegression
                lr = LogisticRegression(max_iter=200, C=1.0)
                lr.fit(X, y, sample_weight=w)
                coefficients = lr.coef_[0].tolist()
                intercept = float(lr.intercept_[0])
            except Exception:
                pass

        payload: Dict[str, Any] = {
            "schema_version": MODEL_SCHEMA_VERSION,
            "version": f"local-{head_name}-{int(time.time())}",
            "trained_at": time.time(),
            "samples": len(labels),
            "coefficients": coefficients,
            "intercept": intercept,
            "feature_names": list(feature_names),
            "training_score": score,
            "model_type": model_type,
            "source": "local",
        }
        return payload, estimator

    # ── Persistence ──────────────────────────────────────────────────

    def _save_risk_payload(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._risk_payload = payload
            self._meta["risk_version"] = payload.get("version")
            self._write_json(self._risk_model_path, payload)

    def _save_item_payload(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._item_payload = payload
            self._meta["item_version"] = payload.get("version")
            self._write_json(self._item_model_path, payload)

    def _save_meta(self) -> None:
        with self._lock:
            self._write_json(self._meta_path, self._meta)

    def _load(self) -> None:
        self._risk_payload = self._read_json(self._risk_model_path)
        self._item_payload = self._read_json(self._item_model_path)
        meta = self._read_json(self._meta_path)
        if meta:
            self._meta.update(meta)

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        tmp = path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(path)
        except Exception as exc:
            logger.debug("Failed to write %s: %s", path.name, exc, exc_info=True)
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.debug("Failed to read %s: %s", path.name, exc, exc_info=True)
            return None


# ── Module-level singleton ───────────────────────────────────────────────

_SINGLETON_LOCK = threading.Lock()
_SINGLETON: Optional[SortAdaptiveModel] = None


def get_sort_adaptive_model() -> SortAdaptiveModel:
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = SortAdaptiveModel()
        return _SINGLETON
