import pytest

from src.market_lister import MAX_LISTING_PRICE, PlanEntry, PlanError, build_plan
from src.models.market_rules import ListerRules

MAPPING = [4, 20, 5, 6, 7, 8, 9, 30]


def _item(uid, slot, **kw):
    item = {"name": f"Item {uid}", "itemId": f"Id_{uid}", "itemUniqueId": uid, "slotId": slot,
            "itemCount": 1, "rarity": 5, "width": 1, "height": 1, "pp": [], "sp": [],
            "vendor_price": 10, "max_stack_size": 1}
    item.update(kw)
    return item


def _ok(price=1000):
    return {"success": True, "has_data": True, "avg_price": price, "lowest_ask": price, "num_listings": 10}


def _plan(stashes, lookup, rules=None, **overrides):
    kwargs = {"tab_mapping": MAPPING, "free_spots": 38, "data_age_s": 10.0, **overrides}
    return build_plan(stashes, rules or ListerRules(source_stash_ids=("2", "4")), lookup, **kwargs)


def test_build_plan_prices_and_orders_entries():
    stashes = {"2": [_item("a", 3)], "4": [_item("b", 0, width=2, height=3)]}
    plan = _plan(stashes, lambda item: _ok())
    assert [e.unique_id for e in plan.entries] == ["a", "b"]
    b = plan.entries[1]
    assert (b.stash_id, b.slot_id, b.width, b.height, b.price, b.fee) == ("4", 0, 2, 3, 900, 45)
    assert plan.warnings == ()


def test_build_plan_records_price_skips():
    stashes = {"2": [_item("a", 0), _item("b", 1)]}
    plan = _plan(stashes, lambda item: _ok() if item["itemUniqueId"] == "a" else {"success": True, "has_data": False})
    assert [e.unique_id for e in plan.entries] == ["a"]
    assert [(s.name, s.reason) for s in plan.skipped] == [("Item b", "no market data")]


def test_build_plan_caps_to_free_spots_and_max_items():
    stashes = {"2": [_item(str(i), i) for i in range(6)]}
    plan = _plan(stashes, lambda item: _ok(), free_spots=2)
    assert len(plan.entries) == 2
    assert any("free listing spots" in w for w in plan.warnings)
    rules = ListerRules(source_stash_ids=("2",), max_items_per_run=3)
    assert len(_plan(stashes, lambda item: _ok(), rules=rules, free_spots=None).entries) == 3


def test_build_plan_skips_unmapped_tabs():
    stashes = {"9": [_item("x", 0)]}
    rules = ListerRules(source_stash_ids=("9",))
    plan = _plan(stashes, lambda item: _ok(), rules=rules, tab_mapping=[4, 20, 5, 6, 7, 8, 0, 30])
    assert plan.entries == ()
    assert plan.skipped[0].reason == "stash tab not mapped in DnDTools settings"


def test_build_plan_explains_unmapped_tabs_and_empty_plan():
    stashes = {"4": [_item("x", 0)]}
    rules = ListerRules(source_stash_ids=("4",))
    plan = _plan(stashes, lambda item: _ok(), rules=rules, tab_mapping=[0] * 8)
    assert plan.entries == ()
    assert any("Stash Tab Mapping" in w for w in plan.warnings)
    assert any("No items to list" in w for w in plan.warnings)


def test_build_plan_has_no_empty_warning_when_items_found():
    plan = _plan({"2": [_item("a", 0)]}, lambda item: _ok())
    assert not any("No items to list" in w for w in plan.warnings)


def test_build_plan_warns_on_stale_data():
    plan = _plan({"2": []}, lambda item: _ok(), data_age_s=900.0)
    assert any("minutes old" in w for w in plan.warnings)


def test_build_plan_missing_key_raises():
    with pytest.raises(PlanError) as exc:
        _plan({"2": [_item("a", 0)]}, lambda item: {"success": False, "error_code": "missing_api_key"})
    assert exc.value.code == "missing_api_key"


def test_build_plan_rate_limited_returns_partial():
    calls = []

    def lookup(item):
        calls.append(item["itemUniqueId"])
        return _ok() if len(calls) == 1 else {"success": False, "error_code": "rate_limited"}

    plan = _plan({"2": [_item("a", 0), _item("b", 1), _item("c", 2)]}, lookup)
    assert [e.unique_id for e in plan.entries] == ["a"]
    assert calls == ["a", "b"]
    assert any("rate limit" in w.lower() for w in plan.warnings)


def test_plan_entry_round_trip_and_validation():
    entry = PlanEntry("a", "Item", 5, "2", 3, 1, 1, 900, 45, 10)
    assert PlanEntry.from_dict(entry.to_dict()) == entry
    for bad in (0, -5, "abc", MAX_LISTING_PRICE + 1, None, 12.5):
        with pytest.raises(ValueError):
            PlanEntry.from_dict({**entry.to_dict(), "price": bad})
    with pytest.raises(ValueError):
        PlanEntry.from_dict({**entry.to_dict(), "slot_id": -1})


def test_build_plan_skips_already_listed_unique_ids():
    stashes = {"2": [_item("a", 0), _item("b", 1)]}
    looked_up = []

    def lookup(item):
        looked_up.append(item["itemUniqueId"])
        return _ok()

    plan = _plan(stashes, lookup, exclude_unique_ids=frozenset({"a"}))
    assert [e.unique_id for e in plan.entries] == ["b"]
    assert [(s.name, s.reason) for s in plan.skipped] == [("Item a", "already listed")]
    assert looked_up == ["b"]


def test_build_plan_without_price_lookup_defers_pricing_to_the_game():
    plan = _plan({"2": [_item("a", 3)]}, None)
    entry = plan.entries[0]
    assert (entry.unique_id, entry.price, entry.fee, entry.item_id) == ("a", 0, 0, "Id_a")
    assert any("in-game market" in w for w in plan.warnings)


def test_apply_game_prices_undercuts_cheapest_listing():
    from src.market_lister import apply_game_prices
    unpriced = _plan({"2": [_item("a", 0), _item("b", 1), _item("c", 2)]}, None).entries
    from src.models.roll_pricing import MarketRow

    def rows(item, *prices):
        return [MarketRow(item, p, (), ()) for p in prices]
    rows = {
        "a": {"same": [], "all": rows("Id_a", 300, 310, 333, 350)},
        "b": {"same": [], "all": []},                 # nobody selling it
        "c": {"same": [], "all": rows("Id_other", 50) + rows("Id_c", 1000, 1000, 1100)},
    }
    plan = apply_game_prices(unpriced, rows, ListerRules(source_stash_ids=("2",)))
    assert [(e.unique_id, e.price, e.fee) for e in plan.entries] == [("a", 270, 15), ("c", 900, 45)]
    assert [(s.name, s.reason) for s in plan.skipped] == [("Item b", "nobody is selling this right now")]


def test_apply_game_prices_marks_unsearched_items_not_priced():
    from src.market_lister import apply_game_prices
    unpriced = _plan({"2": [_item("a", 0)]}, None).entries
    plan = apply_game_prices(unpriced, {}, ListerRules(source_stash_ids=("2",)))
    assert [(s.name, s.reason) for s in plan.skipped] == [("Item a", "not priced — the pricing run stopped first")]


def test_apply_game_prices_merges_history_rows_and_records_confidence():
    from src.market_lister import apply_game_prices
    from src.models.roll_pricing import MarketRow
    unpriced = _plan({"2": [_item("a", 0)]}, None).entries
    history = {"Id_a": [MarketRow("Id_a", p, (), ()) for p in (300, 320, 340)]}
    plan = apply_game_prices(unpriced, {"a": {"same": [], "all": []}}, ListerRules(source_stash_ids=("2",)),
                             extra_rows=lambda item_id: history.get(item_id, []))
    assert [(e.unique_id, e.price, e.confidence) for e in plan.entries] == [("a", 270, "high")]


def test_darkerdb_pricing_never_prices_a_stack_as_one_unit():
    stashes = {"2": [_item("pots", 0, itemCount=5, max_stack_size=5)]}
    rules = ListerRules(source_stash_ids=("2",), allow_stacks=True)
    plan = _plan(stashes, lambda item: _ok(100), rules=rules)
    assert plan.entries == ()
    assert "Price from game" in plan.skipped[0].reason


def test_apply_game_prices_excludes_our_own_listings_and_records_recommended():
    from src.market_lister import apply_game_prices
    from src.models.roll_pricing import MarketRow
    unpriced = _plan({"2": [_item("a", 0)]}, None).entries
    market = {"a": {"same": [], "all": [MarketRow("Id_a", 90, (), (), "mine"),
                                        MarketRow("Id_a", 300, (), (), "x1"), MarketRow("Id_a", 310, (), (), "x2")]}}
    plan = apply_game_prices(unpriced, market, ListerRules(source_stash_ids=("2",)),
                             exclude_listing_ids=frozenset({"mine"}))
    entry = plan.entries[0]
    assert (entry.price, entry.recommended) == (270, 270)   # our own 90g listing was ignored


def test_apply_game_prices_skips_prices_above_the_game_maximum():
    from src.market_lister import MAX_LISTING_PRICE, apply_game_prices
    from src.models.roll_pricing import MarketRow
    unpriced = _plan({"2": [_item("a", 0)]}, None).entries
    market = {"a": {"same": [], "all": [MarketRow("Id_a", MAX_LISTING_PRICE * 3, (), ())]}}
    plan = apply_game_prices(unpriced, market, ListerRules(source_stash_ids=("2",)))
    assert plan.entries == () and "maximum" in plan.skipped[0].reason


def test_darkerdb_prices_remember_the_recommendation_so_edits_are_kept():
    plan = _plan({"2": [_item("a", 3)]}, lambda item: _ok())
    assert (plan.entries[0].price, plan.entries[0].recommended) == (900, 900)
