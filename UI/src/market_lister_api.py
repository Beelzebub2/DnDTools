"""Flask blueprint for the auto market lister page."""
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from src.market_lister import PlanEntry, PlanError, apply_game_prices, build_plan
from src.models.market_rules import ListerRules
from src.models.marketplace_layout import BASE_LENGTHS, BASE_POINTS

RULES_KEY = "marketListerRules"
CALIBRATION_KEY = "marketplaceCalibrationOverride"
MAX_CALIBRATION_PX = 400
TOTAL_SPOTS = 40
DEFAULT_CRAWL_PAGES = 100
MIN_PRICE_QUERY = 2
MAX_PRICE_RESULTS = 60
MAX_CRAWL_PAGES = 6000
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
    history_summary: Callable[[], dict] = lambda: {}
    history_rows: Callable[[str], list] = lambda item_id: []
    extra_roll_share: Callable[[], float] = lambda: 0.5
    old_page_detector: Callable[[], Any] = lambda: None
    price_search: Callable[[str], list] = lambda query: []
    analyze_market: Callable[[], dict] = lambda: {}


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
        return {"seen": False, "free": None, "age_s": None, "payouts": 0}
    age = max(state.now() - snapshot.received_at, 0)
    return {"seen": True, "free": snapshot.free, "age_s": round(age), "payouts": len(snapshot.payouts)}


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
        free = None if snapshot is None else snapshot.free
        data_age_s = deps.get_data_age(character_id)
        stashes = deps.get_stashes(character_id, list(rules.source_stash_ids))

        def build(price_lookup):
            return build_plan(
                stashes, rules, price_lookup, tab_mapping=deps.tab_mapping(), free_spots=free,
                data_age_s=data_age_s, pause=deps.pause, exclude_unique_ids=deps.state.listed_ids(),
            )

        needs_game_pricing = False
        try:
            result = build(deps.price_lookup)
        except PlanError as exc:
            if exc.code != "missing_api_key":
                return _error(str(exc), 424)
            result, needs_game_pricing = build(None), True  # no DarkerDB key: price from the game
        if _data_predates_last_run(deps.job, data_age_s):
            result = replace(result, warnings=result.warnings + (STALE_AFTER_RUN_WARNING,))
        return jsonify({"success": True, "plan": result.to_dict(), "listings": _listings_info(deps.state),
                        "needs_game_pricing": needs_game_pricing})

    @bp.post("/api/market-lister/price")
    def price_from_game():
        payload = request.get_json(silent=True)
        raw = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            return _error("Nothing to price.")
        if len(raw) > TOTAL_SPOTS:
            return _error(f"At most {TOTAL_SPOTS} items can be priced at once.")
        try:
            entries = _unique_entries(PlanEntry.from_dict(e, allow_unpriced=True) for e in raw)
        except ValueError as exc:
            return _error(str(exc))
        if deps.is_sort_running():
            return _error("An inventory sort is running.", 409)
        rules = current_rules()
        share = deps.extra_roll_share()
        if not deps.job.price(entries, lambda es, rows: apply_game_prices(
                es, rows, rules, extra_rows=deps.history_rows, extra_share=share)):
            return _error("The lister is already running.", 409)
        return jsonify({"success": True})

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
        reprice = _repricer(current_rules()) if payload.get("recheck", True) else None
        if not deps.job.start(entries, bool(payload.get("dry_run")), reprice):
            return _error("The lister is already running.", 409)
        return jsonify({"success": True})

    @bp.post("/api/market-lister/crawl")
    def crawl():
        payload = request.get_json(silent=True)
        pages = payload.get("pages", DEFAULT_CRAWL_PAGES) if isinstance(payload, dict) else DEFAULT_CRAWL_PAGES
        if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= MAX_CRAWL_PAGES:
            return _error(f"pages must be 1-{MAX_CRAWL_PAGES}")
        incremental = payload.get("incremental", True) if isinstance(payload, dict) else True
        detector = deps.old_page_detector() if incremental else None  # backfills read past known pages
        return _launch_job(lambda: deps.job.crawl(pages, detector))

    @bp.post("/api/market-lister/collect")
    def collect():
        return _launch_job(deps.job.collect)

    @bp.get("/api/market-lister/prices")
    def prices():
        query = str(request.args.get("q") or "").strip()[:64]
        if len(query) < MIN_PRICE_QUERY:
            return _error(f"Type at least {MIN_PRICE_QUERY} letters of an item name.")
        return jsonify({"success": True, "items": deps.price_search(query)[:MAX_PRICE_RESULTS]})

    @bp.post("/api/market-lister/analyze")
    def analyze():
        return jsonify({"success": True, "report": deps.analyze_market()})

    @bp.get("/api/market-lister/history")
    def history():
        return jsonify(deps.history_summary())

    def _repricer(rules):
        """Re-price one entry from a fresh market search just before it is listed."""
        share = deps.extra_roll_share()

        def reprice(entry, market):
            plan = apply_game_prices([replace(entry, price=0, fee=0)], {entry.unique_id: market}, rules,
                                     extra_rows=deps.history_rows, extra_share=share)
            return plan.entries[0].price if plan.entries else None
        return reprice

    def _launch_job(start):
        if deps.is_sort_running():
            return _error("An inventory sort is running.", 409)
        if not start():
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
