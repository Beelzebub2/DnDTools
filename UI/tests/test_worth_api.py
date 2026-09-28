import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_worth_model import META, _market  # noqa: E402

from src.models.market_rules import ListerRules  # noqa: E402
from src.models.roll_pricing import MarketRow  # noqa: E402
from src.models.worth_model import train  # noqa: E402
from src.worth_api import WorthDeps, create_worth_blueprint, verdict  # noqa: E402

HELM = {"itemId": "Helm_5001", "itemUniqueId": "h1", "name": "Helm", "slotId": 3, "itemCount": 1,
        "vendor_price": 20, "sp": [["Strength", 3], ["Luck", 2], ["Will", 2]]}
GOLD = {"itemId": "GoldCoinBag", "itemUniqueId": "g1", "name": "Gold Coin Bag", "slotId": 0, "itemCount": 1,
        "vendor_price": 0, "sp": []}
LIVE = [MarketRow("Helm_5001", 850, (), (("Strength", 3), ("Luck", 1), ("Will", 2)), "1"),
        MarketRow("Helm_5001", 500, (), (("Strength", 1), ("Luck", 2), ("Will", 2)), "2"),
        MarketRow("Helm_5001", 900, (), (("Strength", 3), ("Luck", 2), ("Will", 3)), "3")]


def _client(model="trained", trained_calls=None):
    model = train(_market(), META, min_pair_support=30) if model == "trained" else model
    deps = WorthDeps(
        model=lambda: model,
        stashes=lambda cid: {"2": [HELM, GOLD]} if cid == "c1" else {},
        live_rows=lambda item_id: [r for r in LIVE if r.item_id == item_id],
        rules=lambda: ListerRules(min_price=50),
        sold_rows=lambda item_id: LIVE[:1],
        train=lambda: (trained_calls.append(1) if trained_calls is not None else None) or {"model_mdape": 20.0},
        info=lambda: {"listings": 320},
    )
    app = Flask(__name__)
    app.register_blueprint(create_worth_blueprint(deps))
    return app.test_client()


def test_character_worth_values_items_and_leaves_gold_out():
    data = _client().get("/api/worth/character/c1").get_json()
    items = {i["unique_id"]: i for i in data["stashes"]["2"]["items"]}
    assert items["g1"]["currency"] is True and "value" not in items["g1"]
    helm = items["h1"]
    assert 650 < helm["value"] < 950 and helm["known"] is True and helm["verdict"] == "list"
    assert data["value"] == helm["value"] == data["stashes"]["2"]["value"]


def test_character_worth_needs_a_trained_model():
    response = _client(model=None).get("/api/worth/character/c1")
    assert response.status_code == 409 and "Analyze market data" in response.get_json()["error"]


def test_item_worth_explains_the_value_and_suggests_a_price():
    data = _client().post("/api/worth/item", json={"item_id": "Helm_5001", "vendor_price": 20,
                                                   "rolls": [["Strength", 3], ["Luck", 2], ["Will", 2]]}).get_json()
    est = data["estimate"]
    assert {r["stat"]: r["quality"] for r in est["rolls"]}["Strength"] == 1.0
    assert data["lowest_ask"] == 500 and data["market"] == {"live": 3, "likely_sold": 1}
    assert data["suggested"] <= est["value"]   # never above what the rolls are worth
    assert [s["price"] for s in data["similar"]] == [850, 900, 500]   # one step off each, cheaper first


def test_item_worth_rejects_requests_without_an_item():
    assert _client().post("/api/worth/item", json={"rolls": []}).status_code == 400


def test_verdict_prefers_the_merchant_when_it_pays_at_least_the_listing_net():
    assert verdict(100, 90) == "merchant"      # 100 - 15g fee = 85 < 90
    assert verdict(1000, 100) == "list"
    assert verdict(None, 0) == "unknown"


def test_model_info_and_retraining():
    calls = []
    client = _client(trained_calls=calls)
    assert client.get("/api/worth/model").get_json() == {"success": True, "trained": True, "listings": 320}
    assert client.post("/api/worth/train").get_json()["accuracy"] == {"model_mdape": 20.0} and calls == [1]
