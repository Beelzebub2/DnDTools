import sys
import threading
import time

import pytest

import networking.protos

# Generated *_pb2 modules import siblings by bare name (e.g. `import _Item_pb2`).
_PROTOS_PATH = str(next(iter(networking.protos.__path__)))
if _PROTOS_PATH not in sys.path:
    sys.path.insert(0, _PROTOS_PATH)

from flask import Flask
from networking.protos import MarketPlace_pb2

from src.market_lister_api import ListerDeps, create_market_lister_blueprint
from src.market_lister_job import ListerJob
from src.models.marketplace_runner import ItemResult, RunReport
from src.models.marketplace_state import MarketplaceState
from src.models.roll_pricing import MarketRow

ENTRY = {"unique_id": "a", "name": "Gloves", "rarity": 5, "stash_id": "2", "slot_id": 0,
         "width": 1, "height": 1, "price": 900, "fee": 45, "vendor_price": 10}


class FakeRunner:
    def __init__(self, event):
        self.event = event

    def run(self, entries, dry_run=False, on_progress=None, reprice=None):
        results = [ItemResult(e.unique_id, e.name, "dry_run" if dry_run else "listed") for e in entries]
        for r in results:
            on_progress(r)
        return RunReport(tuple(results), None)

    def crawl_market(self, pages, on_progress=None, **options):
        result = ItemResult("crawl", "market", "crawled", f"{pages} pages, {pages * 10} listings")
        on_progress(result)
        return RunReport((result,), None)

    def collect_payouts(self, on_progress=None):
        result = ItemResult("1", "GreatHelm_3001", "collected", "200g collected")
        on_progress(result)
        return RunReport((result,), None)

    def price_all(self, entries, on_progress=None):
        rows = {e.unique_id: {"same": [], "all": [MarketRow(e.item_id, p, (), ()) for p in (300, 320, 340)]}
                for e in entries}
        results = [ItemResult(e.unique_id, e.name, "priced") for e in entries]
        for r in results:
            on_progress(r)
        return rows, RunReport(tuple(results), None)


def _wait_done(job):
    for _ in range(100):
        if not job.is_running():
            return
        time.sleep(0.01)


@pytest.fixture
def client_and_deps():
    settings = {}
    state = MarketplaceState()
    state.handle_my_item_list(MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(availableOrderIndexes=range(2, 40)))
    job = ListerJob(runner_factory=FakeRunner, hover_factory=lambda event: (lambda: None))
    item = {"name": "Gloves", "itemId": "G_1", "itemUniqueId": "a", "slotId": 0, "itemCount": 1,
            "rarity": 5, "width": 1, "height": 1, "pp": [], "sp": [], "vendor_price": 10, "max_stack_size": 1}
    deps = ListerDeps(
        get_stashes=lambda cid, ids: {"2": [item]} if cid == "c1" else {},
        get_data_age=lambda cid: 5.0,
        price_lookup=lambda it: {"success": True, "has_data": True, "avg_price": 1000,
                                 "lowest_ask": 1000, "num_listings": 9},
        state=state,
        settings_get=lambda key, default=None: settings.get(key, default),
        settings_update=lambda updates: settings.update(updates),
        tab_mapping=lambda: [4, 20, 5, 6, 7, 8, 9, 30],
        resolution_key=lambda: "1920x1080",
        job=job,
        pause=lambda: None,
        is_sort_running=lambda: False,
    )
    app = Flask(__name__)
    app.register_blueprint(create_market_lister_blueprint(deps))
    return app.test_client(), deps, settings


def test_rules_round_trip(client_and_deps):
    client, _, settings = client_and_deps
    assert client.get("/api/market-lister/rules").get_json()["undercut_pct"] == 10.0
    client.post("/api/market-lister/rules", json={"undercut_pct": 25})
    assert settings["marketListerRules"]["undercut_pct"] == 25.0


def test_plan_returns_entries_and_listing_info(client_and_deps):
    client, _, _ = client_and_deps
    data = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()
    assert data["success"] is True
    assert data["plan"]["entries"][0]["price"] == 900
    assert data["listings"] == {"seen": True, "free": 38, "age_s": 0}


def test_plan_requires_character(client_and_deps):
    client, _, _ = client_and_deps
    assert client.post("/api/market-lister/plan", json={}).status_code == 400


def test_start_runs_job_and_reports_status(client_and_deps):
    client, deps, _ = client_and_deps
    resp = client.post("/api/market-lister/start", json={"entries": [ENTRY], "dry_run": True})
    assert resp.status_code == 200
    _wait_done(deps.job)
    status = client.get("/api/market-lister/status").get_json()
    assert status["state"] == "done" and status["mode"] == "dry_run"
    assert status["results"][0]["status"] == "dry_run"


def test_start_rejects_invalid_price(client_and_deps):
    client, _, _ = client_and_deps
    for bad in (-1, "abc", 5_000_000):
        resp = client.post("/api/market-lister/start", json={"entries": [{**ENTRY, "price": bad}]})
        assert resp.status_code == 400


class SlowRunner:
    def __init__(self, gate):
        self.gate = gate

    def run(self, entries, dry_run=False, on_progress=None, reprice=None):
        self.gate.wait(2)
        return RunReport((), None)


def test_start_rejects_empty_and_busy(client_and_deps):
    client, deps, _ = client_and_deps
    assert client.post("/api/market-lister/start", json={"entries": []}).status_code == 400
    gate = threading.Event()
    deps.job._runner_factory = lambda event: SlowRunner(gate)
    client.post("/api/market-lister/start", json={"entries": [ENTRY]})
    assert client.post("/api/market-lister/start", json={"entries": [ENTRY]}).status_code == 409
    gate.set()
    _wait_done(deps.job)


def test_calibration_saved_per_resolution(client_and_deps):
    client, _, settings = client_and_deps
    client.post("/api/market-lister/calibration", json={"points": {"price_field": [3, -2]}, "lengths": {"cell": 0.5}})
    assert settings["marketplaceCalibrationOverride"]["1920x1080"]["points"]["price_field"] == [3, -2]
    assert client.get("/api/market-lister/calibration").get_json()["calibration"]["lengths"]["cell"] == 0.5


def test_calibration_rejects_unknown_keys(client_and_deps):
    client, _, _ = client_and_deps
    assert client.post("/api/market-lister/calibration", json={"points": {"evil": [1, 1]}}).status_code == 400


def test_listings_report_snapshot_age(client_and_deps):
    client, deps, _ = client_and_deps
    clock = {"t": 500.0}
    state = MarketplaceState(clock=lambda: clock["t"])
    state.handle_my_item_list(MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(availableOrderIndexes=range(3, 40)))
    deps.state = state
    clock["t"] += 42.4
    assert client.get("/api/market-lister/status").get_json()["listings"] == {"seen": True, "free": 37, "age_s": 42}
    plan = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()
    assert plan["listings"]["age_s"] == 42


def test_listings_age_is_none_without_snapshot(client_and_deps):
    client, deps, _ = client_and_deps
    deps.state = MarketplaceState()
    assert client.get("/api/market-lister/status").get_json()["listings"] == {"seen": False, "free": None, "age_s": None}


STALE_AFTER_RUN_WARNING = "Stash data is older than your last listing run — reopen your character to refresh."


def test_plan_skips_items_already_in_my_listings(client_and_deps):
    client, deps, _ = client_and_deps
    item = {**deps.get_stashes("c1", ["2"])["2"][0], "itemUniqueId": 777}
    deps.get_stashes = lambda cid, ids: {"2": [item]}
    msg = MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(totalItemCount=1)
    msg.myItemInfos.add().itemInfo.item.itemUniqueId = 777
    deps.state.handle_my_item_list(msg)
    data = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()
    assert data["plan"]["entries"] == []
    assert data["plan"]["skipped"][0]["reason"] == "already listed"


def test_plan_warns_when_stash_data_predates_last_list_run(client_and_deps):
    client, deps, _ = client_and_deps
    assert STALE_AFTER_RUN_WARNING not in client.post(
        "/api/market-lister/plan", json={"character_id": "c1"}).get_json()["plan"]["warnings"]
    client.post("/api/market-lister/start", json={"entries": [ENTRY]})
    _wait_done(deps.job)
    assert deps.job.last_finished_at is not None
    deps.get_data_age = lambda cid: 60.0  # captured before the run finished
    warnings = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()["plan"]["warnings"]
    assert STALE_AFTER_RUN_WARNING in warnings


def test_plan_does_not_warn_after_dry_run_only(client_and_deps):
    client, deps, _ = client_and_deps
    client.post("/api/market-lister/start", json={"entries": [ENTRY], "dry_run": True})
    _wait_done(deps.job)
    deps.get_data_age = lambda cid: 60.0
    warnings = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()["plan"]["warnings"]
    assert STALE_AFTER_RUN_WARNING not in warnings


def test_plan_still_warns_when_dry_run_follows_list_run(client_and_deps):
    client, deps, _ = client_and_deps
    client.post("/api/market-lister/start", json={"entries": [ENTRY]})
    _wait_done(deps.job)
    client.post("/api/market-lister/start", json={"entries": [ENTRY], "dry_run": True})
    _wait_done(deps.job)
    deps.get_data_age = lambda cid: 60.0
    warnings = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()["plan"]["warnings"]
    assert STALE_AFTER_RUN_WARNING in warnings


def test_start_and_hover_refused_while_sort_running(client_and_deps):
    client, deps, _ = client_and_deps
    deps.is_sort_running = lambda: True
    for path, body in (("/start", {"entries": [ENTRY]}), ("/hover-test", {})):
        resp = client.post(f"/api/market-lister{path}", json=body)
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "An inventory sort is running."
    assert not deps.job.is_running()


def test_start_rejects_more_than_40_entries(client_and_deps):
    client, deps, _ = client_and_deps
    entries = [{**ENTRY, "unique_id": str(i)} for i in range(41)]
    assert client.post("/api/market-lister/start", json={"entries": entries}).status_code == 400
    assert not deps.job.is_running()


def test_start_drops_duplicate_unique_ids(client_and_deps):
    client, deps, _ = client_and_deps
    entries = [ENTRY, {**ENTRY, "price": 500}, {**ENTRY, "unique_id": "b"}]
    assert client.post("/api/market-lister/start", json={"entries": entries}).status_code == 200
    _wait_done(deps.job)
    results = client.get("/api/market-lister/status").get_json()["results"]
    assert [r["unique_id"] for r in results] == ["a", "b"]


def test_plan_falls_back_to_game_pricing_without_darkerdb_key(client_and_deps):
    client, deps, _ = client_and_deps
    deps.price_lookup = lambda item: {"success": False, "error_code": "missing_api_key"}
    data = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()
    assert data["success"] is True and data["needs_game_pricing"] is True
    assert data["plan"]["entries"][0]["price"] == 0


def test_price_endpoint_prices_from_game_and_returns_plan(client_and_deps):
    client, deps, _ = client_and_deps
    unpriced = {**ENTRY, "price": 0, "fee": 0, "item_id": "G_1"}
    assert client.post("/api/market-lister/price", json={"entries": [unpriced]}).status_code == 200
    _wait_done(deps.job)
    status = client.get("/api/market-lister/status").get_json()
    assert status["mode"] == "price" and status["state"] == "done"
    assert status["plan"]["entries"][0]["price"] == 270


def test_price_endpoint_rejects_bad_entries(client_and_deps):
    client, _, _ = client_and_deps
    assert client.post("/api/market-lister/price", json={"entries": []}).status_code == 400
    assert client.post("/api/market-lister/price", json={"entries": [{**ENTRY, "price": -1}]}).status_code == 400


def test_crawl_and_collect_endpoints_run_jobs(client_and_deps):
    client, deps, _ = client_and_deps
    assert client.post("/api/market-lister/crawl", json={"pages": 5}).status_code == 200
    _wait_done(deps.job)
    status = client.get("/api/market-lister/status").get_json()
    assert status["mode"] == "crawl" and status["results"][0]["message"] == "5 pages, 50 listings"
    assert client.post("/api/market-lister/collect").status_code == 200
    _wait_done(deps.job)
    assert client.get("/api/market-lister/status").get_json()["results"][0]["status"] == "collected"


def test_crawl_rejects_bad_page_counts(client_and_deps):
    client, _, _ = client_and_deps
    for bad in (0, -1, 10_000, "x", True):
        assert client.post("/api/market-lister/crawl", json={"pages": bad}).status_code == 400


def test_history_summary_endpoint(client_and_deps):
    client, deps, _ = client_and_deps
    deps.history_summary = lambda: {"listings": 12, "items": 3, "vanished": 1, "my_sold": 1}
    assert client.get("/api/market-lister/history").get_json()["listings"] == 12


def test_crawl_backfill_skips_the_incremental_stop(client_and_deps):
    client, deps, _ = client_and_deps
    calls = []
    deps.old_page_detector = lambda: calls.append("made") or (lambda rows: True)
    client.post("/api/market-lister/crawl", json={"pages": 2, "incremental": False})
    _wait_done(deps.job)
    assert calls == []
    client.post("/api/market-lister/crawl", json={"pages": 2})
    _wait_done(deps.job)
    assert calls == ["made"]
