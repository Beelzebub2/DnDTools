"""Flask blueprint for the auto market lister page."""
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from src.market_lister import PlanEntry, PlanError, build_plan
from src.models.market_rules import ListerRules
from src.models.marketplace_layout import BASE_LENGTHS, BASE_POINTS

RULES_KEY = "marketListerRules"
CALIBRATION_KEY = "marketplaceCalibrationOverride"
MAX_CALIBRATION_PX = 400
TOTAL_SPOTS = 40
SORT_RUNNING_ERROR = "An inventory sort is running."
STALE_AFTER_RUN_WARNING = "Stash data is older than your last listing run — reopen your character to refresh."


@dataclass
class ListerDeps:
    get_stashes: Callable[[str, list], dict]
    get_data_age: Callable[[str], Any]
    price_lookup: Callable[[dict], dict]
    state: Any
    settings_get: Callable[..., Any]
    settings_update: Callable[[dict], Any]
    tab_mapping: Callable[[], list]
    resolution_key: Callable[[], str]
    job: Any
    pause: Callable[[], None]
    is_sort_running: Callable[[], bool]


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def _valid_delta(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return False
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) <= MAX_CALIBRATION_PX for v in value)


def _clean_calibration(payload):
    points = payload.get("points") or {}
    lengths = payload.get("lengths") or {}
    if not isinstance(points, dict) or not isinstance(lengths, dict):
        raise ValueError("points and lengths must be objects")
    for key, value in points.items():
        if key not in BASE_POINTS or not _valid_delta(value, 2):
            raise ValueError(f"invalid point offset: {key}")
    for key, value in lengths.items():
        if key not in BASE_LENGTHS or not _valid_delta([value], 1):
            raise ValueError(f"invalid length offset: {key}")
    return {"points": {k: list(v) for k, v in points.items()}, "lengths": dict(lengths)}


def _listings_info(state):
    snapshot = state.snapshot()
    if snapshot is None:
        return {"seen": False, "used": None, "age_s": None}
    age = max(state.now() - snapshot.received_at, 0)
    return {"seen": True, "used": snapshot.used, "age_s": round(age)}


def _unique_entries(entries):
    seen, unique = set(), []
    for entry in entries:
        if entry.unique_id not in seen:
            seen.add(entry.unique_id)
            unique.append(entry)
    return unique


def _data_predates_last_run(job, data_age_s):
    finished_at = job.last_list_finished_at
    if data_age_s is None or finished_at is None:
        return False
    return time.time() - data_age_s < finished_at


def create_market_lister_blueprint(deps: ListerDeps) -> Blueprint:
    bp = Blueprint("market_lister", __name__)

    def current_rules():
        return ListerRules.from_dict(deps.settings_get(RULES_KEY) or {})

    @bp.get("/api/market-lister/rules")
    def get_rules():
        return jsonify(current_rules().to_dict())

    @bp.post("/api/market-lister/rules")
    def save_rules():
        rules = ListerRules.from_dict(request.get_json(silent=True) or {})
        deps.settings_update({RULES_KEY: rules.to_dict()})
        return jsonify(rules.to_dict())

    @bp.post("/api/market-lister/plan")
    def plan():
        payload = request.get_json(silent=True) or {}
        character_id = str(payload.get("character_id") or "").strip()
        if not character_id:
            return _error("Pick a character first.")
        rules = ListerRules.from_dict(payload["rules"]) if isinstance(payload.get("rules"), dict) else current_rules()
        snapshot = deps.state.snapshot()
        free = None if snapshot is None else max(TOTAL_SPOTS - snapshot.used, 0)
        data_age_s = deps.get_data_age(character_id)
        try:
            result = build_plan(
                deps.get_stashes(character_id, list(rules.source_stash_ids)), rules, deps.price_lookup,
                tab_mapping=deps.tab_mapping(), free_spots=free,
                data_age_s=data_age_s, pause=deps.pause, exclude_unique_ids=deps.state.listed_ids(),
            )
        except PlanError as exc:
            return _error(str(exc), 424)
        if _data_predates_last_run(deps.job, data_age_s):
            result = replace(result, warnings=result.warnings + (STALE_AFTER_RUN_WARNING,))
        return jsonify({"success": True, "plan": result.to_dict(), "listings": _listings_info(deps.state)})

    @bp.post("/api/market-lister/start")
    def start():
        payload = request.get_json(silent=True) or {}
        raw = payload.get("entries")
        if not isinstance(raw, list) or not raw:
            return _error("Nothing to list.")
        if len(raw) > TOTAL_SPOTS:
            return _error(f"At most {TOTAL_SPOTS} items can be listed at once.")
        try:
            entries = _unique_entries(PlanEntry.from_dict(e) for e in raw)
        except ValueError as exc:
            return _error(str(exc))
        if deps.is_sort_running():
            return _error(SORT_RUNNING_ERROR, 409)
        if not deps.job.start(entries, bool(payload.get("dry_run"))):
            return _error("The lister is already running.", 409)
        return jsonify({"success": True})

    @bp.post("/api/market-lister/cancel")
    def cancel():
        return jsonify({"success": deps.job.cancel()})

    @bp.get("/api/market-lister/status")
    def status():
        return jsonify({**deps.job.status(), "listings": _listings_info(deps.state)})

    @bp.get("/api/market-lister/calibration")
    def get_calibration():
        overrides = deps.settings_get(CALIBRATION_KEY) or {}
        key = deps.resolution_key()
        return jsonify({"resolution": key, "calibration": overrides.get(key) or {"points": {}, "lengths": {}}})

    @bp.post("/api/market-lister/calibration")
    def save_calibration():
        try:
            cleaned = _clean_calibration(request.get_json(silent=True) or {})
        except ValueError as exc:
            return _error(str(exc))
        overrides = dict(deps.settings_get(CALIBRATION_KEY) or {})
        overrides[deps.resolution_key()] = cleaned
        deps.settings_update({CALIBRATION_KEY: overrides})
        return jsonify({"success": True})

    @bp.post("/api/market-lister/hover-test")
    def hover_test():
        if deps.is_sort_running():
            return _error(SORT_RUNNING_ERROR, 409)
        if not deps.job.hover_test():
            return _error("The lister is already running.", 409)
        return jsonify({"success": True})

    return bp
