from src.models.market_rules import (
    Candidate, ListerRules, compute_price, listing_fee, rarity_id, select_candidates,
)


def _item(**overrides):
    item = {
        "name": "Riveted Gloves", "itemId": "RivetedGloves_5001", "itemUniqueId": "111",
        "slotId": 3, "itemCount": 1, "rarity": 5, "width": 2, "height": 2,
        "pp": [], "sp": [], "vendor_price": 20, "max_stack_size": 1,
    }
    item.update(overrides)
    return item


def _check(**overrides):
    check = {"success": True, "has_data": True, "avg_price": 1000, "lowest_ask": 900, "num_listings": 12}
    check.update(overrides)
    return check


def test_listing_fee_has_15_gold_minimum():
    assert listing_fee(100) == 15
    assert listing_fee(500) == 25
    assert listing_fee(301) == 16  # ceil(15.05)


def test_rarity_id_accepts_names_and_ints():
    assert rarity_id(5) == 5
    assert rarity_id("Epic") == 5
    assert rarity_id("legend") == 6
    assert rarity_id(None) == 0
    assert rarity_id("nonsense") == 0


def test_rules_from_dict_clamps_and_defaults():
    rules = ListerRules.from_dict({"undercut_pct": 150, "max_items_per_run": 0, "min_rarity": "Rare",
                                   "source_stash_ids": [2, "4"], "exclude_item_ids": ["A"]})
    assert rules.undercut_pct == 90.0
    assert rules.max_items_per_run == 1
    assert rules.min_rarity == 4
    assert rules.source_stash_ids == ("2", "4")
    assert rules.exclude_item_ids == frozenset({"A"})
    assert ListerRules.from_dict(rules.to_dict()) == rules


def test_select_candidates_filters_with_reasons():
    stashes = {
        "2": [_item(), _item(itemUniqueId="222", rarity=2, slotId=5),
              _item(itemUniqueId="333", max_stack_size=5, slotId=7)],
        "4": [_item(itemUniqueId="444", itemId="Excluded_1", slotId=0)],
        "5": [_item(itemUniqueId="555")],
    }
    rules = ListerRules(source_stash_ids=("2", "4"), exclude_item_ids=frozenset({"Excluded_1"}))
    candidates, skipped = select_candidates(stashes, rules)
    assert candidates == [Candidate("2", stashes["2"][0])]
    reasons = {s.slot_id: s.reason for s in skipped}
    assert reasons == {5: "below minimum rarity", 7: "stackable items not supported yet", 0: "on your never-sell list"}


def test_select_candidates_orders_by_source_then_slot():
    stashes = {"4": [_item(itemUniqueId="b", slotId=9), _item(itemUniqueId="a", slotId=1)],
               "2": [_item(itemUniqueId="c", slotId=4)]}
    rules = ListerRules(source_stash_ids=("2", "4"))
    candidates, _ = select_candidates(stashes, rules)
    assert [c.item["itemUniqueId"] for c in candidates] == ["c", "a", "b"]


def test_compute_price_undercuts_lower_reference():
    decision = compute_price(_check(), vendor_price=20, rules=ListerRules())
    assert decision.ok and decision.price == 810 and decision.fee == 41  # 900 * 0.9


def test_compute_price_skips_without_data():
    assert compute_price(None, 0, ListerRules()).reason == "no market data"
    assert compute_price(_check(success=False), 0, ListerRules()).reason == "no market data"
    assert compute_price(_check(num_listings=1), 0, ListerRules()).reason == "not enough market data"


def test_compute_price_ignores_zero_reference_prices():
    assert compute_price(_check(lowest_ask=0, avg_price=None), 0, ListerRules()).reason == "no market data"
    assert compute_price(_check(lowest_ask=0), 0, ListerRules()).price == 900  # falls back to avg 1000


def test_compute_price_skip_reasons():
    rules = ListerRules(min_price=100)
    assert compute_price(_check(lowest_ask=90, avg_price=90), 0, rules).reason == "below min price"
    assert compute_price(_check(), vendor_price=900, rules=rules).reason == "vendor pays more"
    cheap = ListerRules(min_price=1, min_net_ratio=0.5)
    assert compute_price(_check(lowest_ask=30, avg_price=30), 0, cheap).reason == "fee too high"  # 27 - 15
