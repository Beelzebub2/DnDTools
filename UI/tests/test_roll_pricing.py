from src.models.market_rules import ListerRules
from src.models.roll_pricing import MarketRow, price_from_market

RULES = ListerRules(min_price=50, min_listings=1)
OURS_BASE = (("ArmorRating", 30),)
OURS_ROLLS = (("Luck", 17), ("MagicalPower", 2))


def _row(price, rolls=OURS_ROLLS, base=OURS_BASE, item="HeaterShield_5001", listing_id=""):
    return MarketRow(item, price, tuple(base), tuple(rolls), listing_id)


def _price(rows, rolls=OURS_ROLLS, base=OURS_BASE, vendor=10, same=(), extra_share=0.5):
    return price_from_market("HeaterShield_5001", base, rolls, vendor, list(same), list(rows), RULES,
                             extra_share=extra_share)


def test_capped_by_a_copy_that_is_at_least_as_good_on_every_stat():
    rows = [
        _row(200, rolls=(("Luck", 10), ("MagicalPower", 2))),
        _row(400, rolls=(("Luck", 17), ("MagicalPower", 3))),
        _row(500, rolls=(("Luck", 20), ("MagicalPower", 3))),
    ]
    result = _price(rows)
    assert (result.ok, result.price, result.flag, result.confidence) == (True, 360, "", "high")


def test_roll_between_two_ladder_rungs_is_interpolated():
    rows = [_row(450, rolls=(("Luck", 18), ("Strength", 1))), _row(300, rolls=(("Luck", 12), ("Agility", 2))),
            _row(180, rolls=(("MagicalPower", 2), ("Vigor", 1))), _row(120, rolls=(("Strength", 3),))]
    result = _price(rows)
    assert result.price == 382  # Luck 17 is 5/6 of the way from Luck 12 (300g) to Luck 18 (450g) -> 425
    assert "Luck 17 sits between Luck 12 (300g) and Luck 18 (450g)" in result.compared


def test_extra_good_rolls_add_a_share_of_their_premium():
    rows = [_row(400, rolls=(("Luck", 17), ("Strength", 1))), _row(300, rolls=(("MagicalPower", 2), ("Strength", 1))),
            _row(100, rolls=(("Strength", 1),)), _row(110, rolls=(("Strength", 2),)), _row(120, rolls=(("Strength", 3),))]
    with_bonus = _price(rows, extra_share=0.5)
    # Luck 400g + half of Magical Power's 190g premium over the cheapest real plain copy (110g;
    # the 100g one is under half the average ask, so it counts as a lowball) -> 495 -> 445 after 10%
    assert with_bonus.price == 445
    assert "extra good rolls (MagicalPower 2)" in with_bonus.compared
    assert _price(rows, extra_share=0).price == 360


def test_flags_when_our_best_roll_beats_everything_listed():
    rows = [_row(200, rolls=(("Luck", 10), ("MagicalPower", 1))), _row(300, rolls=(("Luck", 12), ("MagicalPower", 2)))]
    result = _price(rows)
    assert result.ok and "beat everything" in result.flag


def test_no_listing_with_any_of_our_rolls_falls_back_to_all_rolls():
    rows = [_row(300, rolls=(("Strength", 2),)), _row(350, rolls=(("Agility", 1),))]
    result = _price(rows)
    assert (result.ok, result.price, result.confidence) == (True, 270, "low")
    assert "no listings with any of your rolls" in result.flag


def test_items_without_rolls_use_plain_cheapest_price():
    rows = [MarketRow("GoldBand_3001", 75, (), ()), MarketRow("GoldBand_3001", 125, (), ())]
    result = price_from_market("GoldBand_3001", (), (), 10, [], rows, RULES)
    assert (result.ok, result.price, result.flag) == (True, 67, "")


def test_base_stats_within_tolerance_count_as_equal():
    assert _price([_row(300, base=(("ArmorRating", 29),))]).price == 270   # 29 vs our 30 is close enough
    rows = [_row(250, base=(("ArmorRating", 25),)), _row(420, base=(("ArmorRating", 31),))]
    assert _price(rows).price == 378  # 25 armour is clearly worse, so the 420g copy sets the price


def test_rows_for_other_items_are_ignored_and_no_rows_skips():
    result = _price([_row(100, item="Other_1")])
    assert (result.ok, result.reason) == (False, "nobody is selling this right now")


def test_vendor_floor_still_applies():
    assert _price([_row(60)], vendor=100).reason == "vendor pays more"


def test_same_attribute_search_results_are_used_too():
    same = [_row(900, rolls=(("Luck", 18), ("MagicalPower", 2)))]
    result = _price([_row(300, rolls=(("Strength", 2),))], same=same)
    assert (result.price, result.flag) == (810, "")


def test_a_lone_lowball_listing_does_not_set_the_price():
    rows = [_row(p, rolls=(("Strength", 1),)) for p in (100, 199, 222, 290, 300, 300, 311, 311, 350, 350)]
    result = _price(rows)
    assert result.price == 179 and "cheapest 199g" in result.compared


def test_duplicate_rows_from_both_searches_count_once():
    row = _row(400, rolls=(("Luck", 17), ("MagicalPower", 3)), listing_id="42")
    assert _price([row], same=[row]).price == _price([row]).price


def test_closest_stronger_roll_is_preferred_over_a_god_roll():
    rows = [_row(450, rolls=(("Luck", 30),)), _row(300, rolls=(("Luck", 19),))]
    assert _price(rows).price == 270


def test_only_much_better_rolls_scale_the_price_down_by_roll_strength():
    result = _price([_row(450, rolls=(("Luck", 30),))])
    assert result.price == 229 and "scaled" in result.compared and result.confidence == "low"


def test_a_weaker_roll_listing_anchors_the_low_end():
    rows = [_row(450, rolls=(("Luck", 30),)), _row(300, rolls=(("Luck", 12),))]
    assert _price(rows).price == 307  # Luck 17 is 5/18 of the way from 300g (Luck 12) to 450g (Luck 30)


def test_worse_base_stats_do_not_count_on_the_ladder():
    rows = [_row(450, rolls=(("Luck", 18),), base=(("ArmorRating", 20),)),
            _row(200, rolls=(("Luck", 17),), base=(("ArmorRating", 30),))]
    assert _price(rows).price == 180
