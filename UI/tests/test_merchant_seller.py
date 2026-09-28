from src.market_lister import PlanEntry
from src.models.market_rules import is_merchant_reason
from src.models.merchant_seller import (
    SELL_BOX_COLUMNS, SELL_BOX_ROWS, merchant_value, pack_sell_box, resolve_sell_entries, sale_outcome,
)


def _item(uid, slot, **kw):
    item = {"name": f"Item {uid}", "itemId": f"Id_{uid}", "itemUniqueId": uid, "slotId": slot,
            "itemCount": 1, "rarity": 3, "width": 1, "height": 1, "pp": [], "sp": [],
            "vendor_price": 10, "max_stack_size": 1, "originalData": {"tradable": 1}}
    item.update(kw)
    return item


def _entry(uid, width=1, height=1, vendor=10, quantity=1, stash="4", slot=0):
    return PlanEntry(uid, f"Item {uid}", 3, stash, slot, width, height, 0, 0, vendor, quantity=quantity)


def test_merchant_reasons_cover_cheap_and_vendor_better_items():
    assert is_merchant_reason("vendor pays more")
    assert is_merchant_reason("below min price")
    assert is_merchant_reason("below minimum rarity")
    assert is_merchant_reason("a merchant sells it for 3g")


def test_other_skip_reasons_are_not_merchant_bound():
    for reason in ("gold is never listed", "already listed", "on your never-sell list", "no market data",
                   "nobody is selling this right now", "not priced — the pricing run stopped first", ""):
        assert not is_merchant_reason(reason)


def test_resolve_finds_items_by_unique_id_at_their_current_slot():
    stashes = {"4": [_item("a", 34, vendor_price=250)], "20": [_item("b", 154, width=1, height=2, vendor_price=100)]}
    entries, refused = resolve_sell_entries(stashes, ["b", "a"], ["4", "20"])
    assert [(e.unique_id, e.stash_id, e.slot_id, e.width, e.height, e.vendor_price) for e in entries] == [
        ("b", "20", 154, 1, 2, 100), ("a", "4", 34, 1, 1, 250)]
    assert refused == []


def test_resolve_keeps_stack_size_for_the_merchant_value():
    stashes = {"4": [_item("eyes", 142, itemCount=2, vendor_price=25, max_stack_size=5)]}
    entries, _ = resolve_sell_entries(stashes, ["eyes"], ["4"])
    assert entries[0].quantity == 2
    assert merchant_value(entries[0]) == 50


def test_resolve_refuses_gold_missing_items_and_tabs_not_chosen():
    stashes = {"4": [_item("gold", 1, itemId="GoldCoins", itemCount=25), _item("pouch", 2, itemId="GoldCoinPouch")],
               "21": [_item("gem", 3)]}
    entries, refused = resolve_sell_entries(stashes, ["gold", "pouch", "gone", "gem"], ["4"])
    assert entries == []
    assert refused == [("gold", "gold is never sold"), ("pouch", "gold is never sold"),
                       ("gone", "not found in the chosen stash tabs"),
                       ("gem", "not found in the chosen stash tabs")]


def test_resolve_keeps_items_that_are_not_tradable():
    # Quest items such as Huntress' emblems carry no tradable flag (verified in game: "Non-tradable").
    emblem = _item("emblem", 12, originalData={"permittedAreaArray": [{"type": 3}]}, vendor_price=25)
    entries, refused = resolve_sell_entries({"21": [emblem, _item("gem", 13)]}, ["emblem", "gem"], ["21"])
    assert [e.unique_id for e in entries] == ["gem"]
    assert refused[0][0] == "emblem" and refused[0][1].startswith("not tradable")


def test_resolve_ignores_duplicate_ids():
    entries, refused = resolve_sell_entries({"4": [_item("a", 0)]}, ["a", "a"], ["4"])
    assert [e.unique_id for e in entries] == ["a"]
    assert refused == []


def test_pack_places_items_left_to_right_in_one_batch():
    batches, too_big = pack_sell_box([_entry("a"), _entry("b", height=2), _entry("c", 2, 2)])
    assert too_big == ()
    assert [[(p.entry.unique_id, p.col, p.row) for p in batch] for batch in batches] == [
        [("a", 0, 0), ("b", 1, 0), ("c", 2, 0)]]


def test_pack_moves_what_does_not_fit_to_the_next_batch():
    singles = [_entry(str(i)) for i in range(SELL_BOX_COLUMNS * SELL_BOX_ROWS + 3)]
    batches, too_big = pack_sell_box(singles)
    assert [len(b) for b in batches] == [SELL_BOX_COLUMNS * SELL_BOX_ROWS, 3]
    assert too_big == ()
    last = batches[0][-1]
    assert (last.col, last.row) == (SELL_BOX_COLUMNS - 1, SELL_BOX_ROWS - 1)


def test_pack_never_overlaps_tall_items():
    tall = [_entry(f"t{i}", 1, 3) for i in range(25)]
    batches, _ = pack_sell_box(tall)
    for batch in batches:
        cells = [(p.col + dx, p.row + dy) for p in batch for dx in range(p.entry.width)
                 for dy in range(p.entry.height)]
        assert len(cells) == len(set(cells))
        assert all(0 <= c < SELL_BOX_COLUMNS and 0 <= r < SELL_BOX_ROWS for c, r in cells)
    assert sum(len(b) for b in batches) == 25


def test_pack_reports_items_bigger_than_the_box():
    batches, too_big = pack_sell_box([_entry("huge", 1, SELL_BOX_ROWS + 1), _entry("a")])
    assert [e.unique_id for e in too_big] == ["huge"]
    assert [[p.entry.unique_id for p in b] for b in batches] == [["a"]]


def test_pack_of_nothing_is_empty():
    assert pack_sell_box([]) == ([], ())


def test_sale_outcome_splits_sold_not_taken_and_unexpected():
    staged = [_entry("a"), _entry("b"), _entry("c")]
    outcome = sale_outcome(staged, ["c", "a", "zzz"])
    assert [e.unique_id for e in outcome.sold] == ["a", "c"]
    assert [e.unique_id for e in outcome.not_taken] == ["b"]
    assert outcome.unexpected == ("zzz",)


def test_sale_outcome_accepts_numeric_ids():
    outcome = sale_outcome([_entry("123")], [123])
    assert [e.unique_id for e in outcome.sold] == ["123"]
    assert outcome.unexpected == ()
