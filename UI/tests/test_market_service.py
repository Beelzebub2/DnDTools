import time

from src import market_service


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, json_error=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self._json_error = json_error
        self.ok = 200 <= status_code < 300

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def test_price_check_request_uses_v2_exact_endpoint_header_key_and_roll_filters(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({
            "url": url,
            "params": params,
            "headers": headers,
            "timeout": timeout,
        })
        return FakeResponse(payload={
            "timestamp": "2026-08-11T09:01:00+00:00",
            "body": {
                "item": {"item_id": "id.item.frost_amulet_6001", "name": "Frost Amulet", "rarity": "legendary"},
                "valuation": {"fair_value": 123, "low": 100, "high": 150, "quick_list": 119, "confidence": "high"},
                "market": {"active_listings": 5, "sales_30d": 20},
                "similar_sales": [{"price": 120}, {"price": 126}],
            },
        })

    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")
    monkeypatch.setattr(market_service.requests, "get", fake_get)
    market_service.clear_market_cache()

    result = market_service.fetch_price_check(
        "Frost Amulet",
        "Legend",
        pp=[["AdditionalMoveSpeed", 1.5]],
        sp=[["Physical Power", "3:"]],
        item_id="FrostAmulet_6001",
    )

    assert result["success"] is True
    assert result["avg_price"] == 123
    assert calls[0]["url"] == "https://api.darkerdb.com/v2/price-checks"
    assert "key" not in calls[0]["params"]
    assert calls[0]["headers"]["X-Api-Key"] == "test-key"
    assert calls[0]["headers"]["X-API-Version"] == market_service.DARKERDB_API_VERSION
    assert calls[0]["params"]["item_id"] == "id.item.frost_amulet_6001"
    assert "has_sold" not in calls[0]["params"]
    assert calls[0]["params"]["attributes[additional_move_speed]"] == 1.5
    assert calls[0]["params"]["attributes[physical_power]"] == "3:"
    assert calls[0]["headers"]["User-Agent"].startswith("DnDTools-MarketProxy")


def test_price_check_marks_missing_key_without_calling_api(monkeypatch):
    monkeypatch.delenv("DARKERDB_API_KEY", raising=False)
    monkeypatch.delenv("DNDTOOLS_DARKERDB_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    market_service.clear_market_cache()

    def fail_get(*args, **kwargs):
        raise AssertionError("API should not be called without a key")

    monkeypatch.setattr(market_service.requests, "get", fail_get)

    result = market_service.fetch_price_check("Gold Coin Bag", "Rare")

    assert result["success"] is False
    assert result["error_code"] == "missing_api_key"
    assert result["status"] == "disabled"


def test_price_check_normalizes_body_confidence_and_price_fields(monkeypatch):
    now = 1_775_000_000.0
    monkeypatch.setattr(market_service.time, "time", lambda: now)
    market_service.clear_market_cache()

    payload = {
        "body": {
            "market_price": 456.7,
            "num_similar_sold_recently": 2,
            "quality": 82,
            "relative_quality": 91,
            "updated_at": "2026-07-09T12:00:00+00:00",
        }
    }

    result = market_service.normalize_price_check_response(
        payload,
        item_name="Frost Amulet",
        rarity="Epic",
        fetched_at=now,
        rate_limit={"remaining": "57", "reset": "60"},
    )

    assert result["has_data"] is True
    assert result["avg_price"] == 457
    assert result["recent_price"] == 457
    assert result["num_listings"] == 2
    assert result["confidence"] == "low"
    assert result["quality"] == 82
    assert result["relative_quality"] == 91
    assert result["freshness"] == "fresh"
    assert result["source"] == "DarkerDB"
    assert result["rate_limit"]["remaining"] == "57"


def test_price_check_caches_successful_results(monkeypatch):
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse(payload={
            "timestamp": "2026-08-11T09:12:00+00:00",
            "body": {
                "item": {"name": "Gold Coin Bag", "rarity": "rare"},
                "valuation": {"fair_value": 10, "low": 8, "high": 12, "quick_list": 9, "confidence": "high"},
                "similar_sales": [{"price": 10} for _ in range(12)],
            },
        })

    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")
    monkeypatch.setattr(market_service.requests, "get", fake_get)
    market_service.clear_market_cache()

    first = market_service.fetch_price_check("Gold Coin Bag", "Rare", item_id="GoldCoinBag_4001")
    second = market_service.fetch_price_check("Gold Coin Bag", "Rare", item_id="GoldCoinBag_4001")

    assert calls == 1
    assert first["avg_price"] == second["avg_price"] == 10
    assert first["num_listings"] == second["num_listings"] == 12
    assert second["cache"] == "hit"


def test_price_cache_separates_same_name_and_rarity_by_exact_item_id(monkeypatch):
    calls = []

    def fake_get(_url, params=None, **_kwargs):
        item_id = params["item_id"]
        calls.append(item_id)
        price = 100 if item_id.endswith("poison_vial_2001") else 275
        return FakeResponse(payload={
            "timestamp": "2026-08-23T12:01:00+00:00",
            "body": {
                "item": {"item_id": item_id, "name": "Poison Vial", "rarity": "common"},
                "valuation": {"fair_value": price, "confidence": "medium"},
                "similar_sales": [{"price": price}],
            },
        })

    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")
    monkeypatch.setattr(market_service.requests, "get", fake_get)
    market_service.clear_market_cache()

    first = market_service.fetch_price_check(
        "Poison Vial", "Common", item_id="PoisonVial_2001"
    )
    second = market_service.fetch_price_check(
        "Poison Vial", "Common", item_id="PoisoncloudVial_2001"
    )

    assert first["avg_price"] == 100
    assert second["avg_price"] == 275
    assert calls == ["id.item.poison_vial_2001", "id.item.poisoncloud_vial_2001"]


def test_market_cache_is_bounded_and_prunes_expired_entries(monkeypatch):
    monkeypatch.setattr(market_service, "MARKET_CACHE_MAX_ENTRIES", 3)
    monkeypatch.setattr(market_service, "MARKET_CACHE_DURATION", 10)
    market_service.clear_market_cache()

    for index in range(5):
        market_service._cache_market_result(
            f"item-{index}", {"success": True, "value": index}, 100 + index
        )

    assert len(market_service._market_price_cache) == 3
    assert set(market_service._market_price_cache) == {"item-2", "item-3", "item-4"}

    assert market_service._get_cached_market_result("item-4", 200) is None
    assert market_service._market_price_cache == {}


def test_market_listing_request_uses_v2_endpoint_and_envelope(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        return FakeResponse(payload={
            "body": [
                {
                    "id": "listing-1",
                    "item_id": "id.item.potion_health",
                    "name": "Potion of Healing",
                    "price": 25,
                    "created_at": "2026-07-09T12:00:00+00:00",
                }
            ],
            "pagination": {"total": 1},
        })

    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")
    monkeypatch.setattr(market_service.requests, "get", fake_get)

    result = market_service.fetch_market_listings(item_id="id.item.potion_health", rarity="Rare", limit=5)

    assert result["success"] is True
    assert calls[0][0] == "https://api.darkerdb.com/v2/market"
    assert "key" not in calls[0][1]
    assert calls[0][1]["item_id"] == "id.item.potion_health"
    assert calls[0][1]["listing_state"] == "sold"
    assert "has_sold" not in calls[0][1]
    assert calls[0][1]["limit"] == 5
    assert result["pagination"]["total"] == 1
    assert result["listings"][0]["price"] == 25


def test_price_check_uses_v2_exact_quote_for_dndtools_item_id(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        return FakeResponse(payload={
            "timestamp": "2026-08-23T12:01:00+00:00",
            "body": {
                "item": {"item_id": "id.item.adventurer_boots_2001", "name": "Adventurer Boots", "rarity": "common"},
                "valuation": {"fair_value": 120, "low": 100, "high": 140, "quick_list": 115, "lowest_ask": 110, "confidence": "medium"},
                "market": {"active_listings": 3, "sales_30d": 9},
                "similar_sales": [{"price": 100}, {"price": 140}],
            },
        })

    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")
    monkeypatch.setattr(market_service.requests, "get", fake_get)
    market_service.clear_market_cache()

    result = market_service.fetch_price_check(
        "Adventurer Boots",
        "Common",
        item_id="AdventurerBoots_2001",
    )

    assert result["success"] is True
    assert result["has_data"] is True
    assert result["avg_price"] == 120
    assert result["min_price"] == 100
    assert result["max_price"] == 140
    assert result["num_listings"] == 2
    assert len(calls) == 1
    assert result["recent_price"] == 115
    assert calls[0][0] == "https://api.darkerdb.com/v2/price-checks"
    assert calls[0][1]["item_id"] == "id.item.adventurer_boots_2001"
    assert calls[0][2]["X-Api-Key"] == "test-key"


def test_exact_price_check_falls_back_to_v2_market_for_unknown_variant(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/v2/price-checks"):
            return FakeResponse(status_code=404, payload={"status": "error"})
        return FakeResponse(payload={
            "body": [{"price": 75, "created_at": "2026-08-23T12:00:00+00:00"}],
        })

    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")
    monkeypatch.setattr(market_service.requests, "get", fake_get)
    market_service.clear_market_cache()

    result = market_service.fetch_price_check("Unknown Boots", "Rare", item_id="UnknownBoots_4001")

    assert result["success"] is True
    assert result["avg_price"] == 75
    assert calls == [
        "https://api.darkerdb.com/v2/price-checks",
        "https://api.darkerdb.com/v2/market",
    ]


def test_listing_estimate_filters_extreme_high_outliers():
    payload = {
        "body": [
            {"price": 100, "created_at": "2026-07-09T12:00:00+00:00"},
            {"price": 110, "created_at": "2026-07-09T12:01:00+00:00"},
            {"price": 120, "created_at": "2026-07-09T12:02:00+00:00"},
            {"price": 130, "created_at": "2026-07-09T12:03:00+00:00"},
            {"price": 53000, "created_at": "2026-07-09T12:04:00+00:00"},
        ]
    }

    result = market_service.normalize_market_listings_response(
        payload,
        item_name="Adventurer Boots",
        rarity="Common",
        fetched_at=1_775_000_000.0,
    )

    assert result["avg_price"] == 115
    assert result["recent_price"] == 130
    assert result["max_price"] == 130
    assert result["num_listings"] == 4
    assert result["outliers_filtered"] == 1


def test_bulk_status_reports_missing_key_when_every_result_is_disabled():
    results = {
        "Gold Coin Bag|Rare": {
            "success": False,
            "status": "disabled",
            "error_code": "missing_api_key",
            "error": "DarkerDB API key is not configured.",
        },
        "Frost Amulet|Legendary": {
            "success": False,
            "status": "disabled",
            "error_code": "missing_api_key",
            "error": "DarkerDB API key is not configured.",
        },
    }

    status = market_service.summarize_bulk_price_results(results)

    assert status["success"] is False
    assert status["status"] == "disabled"
    assert status["error_code"] == "missing_api_key"


def test_cache_key_is_canonical_for_roll_order_rarity_alias_and_identity():
    key = market_service.build_cache_key(
        "Frost Amulet",
        "Legend",
        pp=[["Strength", 1], ["MoveSpeed", 2]],
        sp=[["Physical Power", 3]],
        item_id="FrostAmulet_6001",
    )

    assert key == (
        "Frost Amulet|Legendary|id:frostamulet_6001"
        "|p:move_speed=2|p:strength=1|s:physical_power=3"
    )


def test_price_check_handles_success_with_malformed_json(monkeypatch):
    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")
    monkeypatch.setattr(
        market_service.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(json_error=ValueError("bad json")),
    )
    market_service.clear_market_cache()

    result = market_service.fetch_price_check(
        "Frost Amulet", "Epic", item_id="FrostAmulet_5001"
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_response"


def test_market_listings_rejects_invalid_success_envelope(monkeypatch):
    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")
    monkeypatch.setattr(
        market_service.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(payload={"body": {"not": "a list"}}),
    )

    result = market_service.fetch_market_listings(item_id="id.item.frost_amulet_5001")

    assert result["success"] is False
    assert result["error_code"] == "invalid_response"


def test_price_check_rejects_malformed_or_unbounded_rolls_before_network(monkeypatch):
    monkeypatch.setenv("DARKERDB_API_KEY", "test-key")

    def fail_get(*_args, **_kwargs):
        raise AssertionError("invalid rolls must not reach the network")

    monkeypatch.setattr(market_service.requests, "get", fail_get)

    malformed = market_service.fetch_price_check(
        "Frost Amulet", "Epic", pp=[{"name": "Strength"}], item_id="FrostAmulet_5001"
    )
    oversized = market_service.fetch_price_check(
        "Frost Amulet",
        "Epic",
        sp=[["Strength", 1]] * 65,
        item_id="FrostAmulet_5001",
    )

    assert malformed["error_code"] == "invalid_request"
    assert oversized["error_code"] == "invalid_request"
