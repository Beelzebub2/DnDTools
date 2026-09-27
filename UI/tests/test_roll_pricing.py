from src.models.market_rules import ListerRules
from src.models.roll_pricing import MarketRow, price_from_market

RULES = ListerRules(min_price=50, min_listings=1)
OURS_BASE = (("ArmorRating", 30),)
OURS_ROLLS = (("Luck", 17), ("MagicalPower", 2))


def _row(price, rolls=OURS_ROLLS, base=OURS_BASE, item="HeaterShield_5001"):
    return MarketRow(item, price, tuple(base), tuple(rolls))


def _price(rows, rolls=OURS_ROLLS, base=OURS_BASE, vendor=10, same=()):
    return price_from_market("HeaterShield_5001", base, rolls, vendor, list(same), list(rows), RULES)


def test_prices_under_cheapest_listing_with_equal_or_better_rolls():
    rows = [
        _row(200, rolls=(("Luck", 10), ("MagicalPower", 2))),   # worse roll — ignored
        _row(400, rolls=(("Luck", 17), ("MagicalPower", 3))),   # better — sets the price
        _row(500, rolls=(("Luck", 20), ("MagicalPower", 3))),
    ]
    result = _price(rows)
    assert (result.ok, result.price, result.flag) == (True, 360, "")
    assert result.compared == "3 listings with the same rolls; cheapest at least as good: 400g"


def test_flags_when_ours_beats_every_similar_listing():
    rows = [_row(200, rolls=(("Luck", 10), ("MagicalPower", 1))), _row(300, rolls=(("Luck", 12), ("MagicalPower", 2)))]
    result = _price(rows)
    assert result.ok and result.price == 300
    assert "better than every" in result.flag


def test_falls_back_to_all_rolls_with_flag_when_no_similar_attributes():
    rows = [_row(300, rolls=(("Strength", 2),)), _row(350, rolls=(("Agility", 1),))]
    result = _price(rows)
    assert (result.ok, result.price) == (True, 270)
    assert "no listings with the same rolls" in result.flag


def test_items_without_rolls_use_plain_cheapest_price():
    rows = [MarketRow("GoldBand_3001", 75, (), ()), MarketRow("GoldBand_3001", 125, (), ())]
    result = price_from_market("GoldBand_3001", (), (), 10, [], rows, RULES)
    assert (result.ok, result.price, result.flag) == (True, 67, "")


def test_base_stats_count_toward_equal_or_better():
    rows = [_row(250, base=(("ArmorRating", 25),)), _row(420, base=(("ArmorRating", 31),))]
    result = _price(rows)
    assert result.price == 378  # the 250g one has worse armor, so 420g sets the price


def test_rows_for_other_items_are_ignored_and_no_rows_skips():
    result = _price([_row(100, item="Other_1")])
    assert (result.ok, result.reason) == (False, "nobody is selling this right now")


def test_vendor_floor_still_applies():
    result = _price([_row(60)], vendor=100)
    assert (result.ok, result.reason) == (False, "vendor pays more")


def test_same_attribute_search_results_are_used_too():
    same = [_row(900, rolls=(("Luck", 18), ("MagicalPower", 2)))]
    result = _price([_row(300, rolls=(("Strength", 2),))], same=same)
    assert (result.price, result.flag) == (810, "")


def test_a_lone_lowball_listing_does_not_set_the_price():
    # Live example: an axe listed at 100g while the rest start at 199g.
    rows = [_row(p, rolls=(("Strength", 1),)) for p in (100, 199, 222, 290, 300, 300, 311, 311, 350, 350)]
    result = _price(rows)
    assert result.price == 179  # 199 * 0.9 — the 100g outlier is ignored
    assert "cheapest 199g" in result.compared


def test_duplicate_rows_from_both_searches_are_counted_once():
    row = _row(400, rolls=(("Luck", 17), ("MagicalPower", 3)))
    result = _price([row], same=[_row(400, rolls=(("Luck", 17), ("MagicalPower", 3)))])
    assert result.compared.startswith("1 listings with the same rolls")


def test_best_single_roll_prices_items_without_an_exact_match():
    # Nobody sells Luck+MagicalPower together, but single-roll listings exist.
    rows = [
        _row(450, rolls=(("Luck", 18), ("Strength", 1))),       # Luck >= 17 -> supports 450
        _row(300, rolls=(("Luck", 12), ("Agility", 2))),        # Luck too low — doesn't count
        _row(180, rolls=(("MagicalPower", 2), ("Vigor", 1))),   # MagicalPower >= 2 -> supports 180
        _row(120, rolls=(("Strength", 3),)),                    # no shared roll
    ]
    result = _price(rows)
    assert result.price == 405  # best roll (Luck) listing 450g, undercut 10%
    assert result.compared == "matched on your best roll: Luck 17 (cheapest listing with it at least as good: 450g)"
    assert "best single roll" in result.flag


def test_best_single_roll_ignores_listings_with_worse_base_stats():
    rows = [_row(450, rolls=(("Luck", 18),), base=(("ArmorRating", 20),)),
            _row(200, rolls=(("Luck", 17),), base=(("ArmorRating", 30),))]
    result = _price(rows)
    assert result.price == 180
