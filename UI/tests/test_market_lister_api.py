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

ENTRY = {"unique_id": "a", "name": "Gloves", "rarity": 5, "stash_id": "2", "slot_id": 0,
         "width": 1, "height": 1, "price": 900, "fee": 45, "vendor_price": 10}


class FakeRunner:
    def __init__(self, event):
        self.event = event

    def run(self, entries, dry_run=False, on_progress=None):
        results = [ItemResult(e.unique_id, e.name, "dry_run" if dry_run else "listed") for e in entries]
        for r in results:
            on_progress(r)
        return RunReport(tuple(results), None)


def _wait_done(job):
    for _ in range(100):
        if not job.is_running():
            return
        time.sleep(0.01)


@pytest.fixture
def client_and_deps():
    settings = {}
    state = MarketplaceState()
    state.handle_my_item_list(MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(totalItemCount=2))
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
    assert data["listings"] == {"seen": True, "used": 2, "age_s": 0}


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

    def run(self, entries, dry_run=False, on_progress=None):
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
    state.handle_my_item_list(MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(totalItemCount=3))
    deps.state = state
    clock["t"] += 42.4
    assert client.get("/api/market-lister/status").get_json()["listings"] == {"seen": True, "used": 3, "age_s": 42}
    plan = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()
    assert plan["listings"]["age_s"] == 42


def test_listings_age_is_none_without_snapshot(client_and_deps):
    client, deps, _ = client_and_deps
    deps.state = MarketplaceState()
    assert client.get("/api/market-lister/status").get_json()["listings"] == {"seen": False, "used": None, "age_s": None}
