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


def test_roll_between_two_rungs_is_priced_at_the_low_rung_not_inside_the_range():
    rows = [_row(450, rolls=(("Luck", 18), ("Strength", 1))), _row(300, rolls=(("Luck", 12), ("Agility", 2))),
            _row(180, rolls=(("MagicalPower", 2), ("Vigor", 1))), _row(120, rolls=(("Strength", 3),))]
    result = _price(rows, extra_share=0)
    assert result.price == 270  # the cheapest real listing at Luck 12 or better (300g), -10%
    assert "cheapest listing with Luck ≥ 12 asks 300g" in result.compared
    assert result.confidence == "high"


def test_extra_good_rolls_add_a_share_of_their_premium():
    rows = [_row(400, rolls=(("Luck", 17), ("Strength", 1))), _row(300, rolls=(("MagicalPower", 2), ("Strength", 1))),
            _row(700, rolls=(("Luck", 25), ("Strength", 1))),
            _row(100, rolls=(("Strength", 1),)), _row(110, rolls=(("Strength", 2),)), _row(120, rolls=(("Strength", 3),))]
    with_bonus = _price(rows, extra_share=0.5)
    # Luck 400g + half of Magical Power's 200g premium over the cheapest plain copy (100g) -> 500 -> 450
    assert with_bonus.price == 450
    assert "extra rolls (MagicalPower 2)" in with_bonus.compared
    assert _price(rows, extra_share=0).price == 360


def test_flags_when_our_best_roll_beats_everything_listed():
    rows = [_row(200, rolls=(("Luck", 10), ("MagicalPower", 1))), _row(300, rolls=(("Luck", 12), ("MagicalPower", 2)))]
    result = _price(rows)
    assert result.ok and "beat everything" in result.flag


def test_no_listing_with_any_of_our_rolls_falls_back_to_all_rolls():
    rows = [_row(300, rolls=(("Strength", 2),)), _row(350, rolls=(("Agility", 1),))]
    result = _price(rows)
    assert (result.ok, result.price, result.confidence) == (True, 270, "low")
    assert "no listings with rolls like yours" in result.flag


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


def test_only_much_stronger_rolls_do_not_set_the_price():
    rows = [_row(450, rolls=(("Luck", 30),)), _row(200, rolls=(("Strength", 2),))]
    result = _price(rows)  # Luck 30 is no comparison for our Luck 17: priced as a copy of any roll
    assert (result.price, result.confidence) == (180, "low") and "rolls like yours" in result.flag


def test_a_close_weaker_roll_anchors_the_price():
    rows = [_row(450, rolls=(("Luck", 30),)), _row(300, rolls=(("Luck", 12),))]
    assert _price(rows).price == 270  # Luck 12 is within 1.5x of our 17: its 300g sets the price


def test_a_much_weaker_roll_does_not_drag_the_price_down():
    rows = [_row(100, rolls=(("Luck", 5),)), _row(500, rolls=(("Luck", 18),))]
    assert _price(rows).price == 450  # Luck 5 is no comparison for Luck 17; Luck 18 at 500g is


def test_a_cheaper_stronger_copy_undercuts_similar_ones():
    rows = [_row(500, rolls=(("Luck", 18),)), _row(350, rolls=(("Luck", 30),))]
    assert _price(rows).price == 315  # nobody pays 500g for Luck 17 while Luck 30 costs 350g


def test_cheap_weak_rolls_are_a_real_price_level_not_lowballs():
    rows = [_row(800, rolls=(("Luck", 12),)), _row(900, rolls=(("Luck", 13),)), _row(2000, rolls=(("Luck", 17),)),
            _row(3000, rolls=(("Luck", 18),)), _row(3200, rolls=(("Luck", 18),))]
    assert _price(rows, rolls=(("Luck", 12),)).price == 720  # not 1800: expensive high rolls don't make 800g a lowball


def test_a_strong_roll_dumped_far_below_weaker_copies_is_a_lowball():
    rows = [_row(800, rolls=(("Luck", 17),)), _row(850, rolls=(("Luck", 17),)), _row(900, rolls=(("Luck", 18),)),
            _row(200, rolls=(("Luck", 24),))]
    assert _price(rows).price == 720  # the 200g Luck 24 is a lowball: ignored, not undercut


def test_worse_base_stats_do_not_count_on_the_ladder():
    rows = [_row(450, rolls=(("Luck", 18),), base=(("ArmorRating", 20),)),
            _row(200, rolls=(("Luck", 17),), base=(("ArmorRating", 30),))]
    assert _price(rows).price == 180


def test_stacks_are_priced_per_unit_times_our_quantity():
    rows = [MarketRow("Bandage_2001", 300, (), (), "1", 3),    # 100 each
            MarketRow("Bandage_2001", 110, (), (), "2", 1),    # 110 each
            MarketRow("Bandage_2001", 500, (), (), "3", 4)]    # 125 each
    result = price_from_market("Bandage_2001", (), (), 5, [], rows, RULES, quantity=3)
    assert (result.ok, result.price) == (True, 270)   # 100/unit, -10%, x3
    assert "per unit" in result.compared


def test_one_expensive_listing_does_not_inflate_the_price():
    rows = [MarketRow("GoldBand_3001", p, (), ()) for p in (1000, 1050, 1100, 1200, 15000)]
    result = price_from_market("GoldBand_3001", (), (), 10, [], rows, RULES)
    assert result.price == 900


def test_two_listings_far_apart_use_the_cheaper_one():
    rows = [MarketRow("GoldBand_3001", 1000, (), ()), MarketRow("GoldBand_3001", 50000, (), ())]
    assert price_from_market("GoldBand_3001", (), (), 10, [], rows, RULES).price == 900


def test_rolled_item_ignores_a_wild_high_ask():
    rows = [_row(900), _row(950), _row(99999)]
    assert _price(rows).price == 810


def test_equal_roll_values_break_ties_on_the_cheaper_listing():
    # We beat both listings on Luck; they have the same value, so the cheaper ask is the reference.
    rows = [_row(500, rolls=(("Luck", 12),)), _row(50000, rolls=(("Luck", 12),))]
    assert _price(rows).price < 1000


def test_extra_roll_bonus_never_exceeds_the_most_expensive_comparable_listing():
    rows = [_row(1000, rolls=(("Luck", 17), ("Strength", 1))), _row(900, rolls=(("MagicalPower", 2), ("Strength", 1))),
            _row(100, rolls=(("Strength", 1),)), _row(110, rolls=(("Strength", 2),)), _row(120, rolls=(("Strength", 3),))]
    result = _price(rows, extra_share=1.0)
    assert result.price <= 900  # capped at the dearest listing (1000g) before the 10% undercut


def test_learned_stat_pair_synergy_sets_the_extra_roll_bonus():
    rows = [_row(400, rolls=(("Luck", 17), ("Strength", 1))), _row(300, rolls=(("MagicalPower", 2), ("Strength", 1))),
            _row(900, rolls=(("Luck", 25), ("Strength", 1))),
            _row(100, rolls=(("Strength", 1),)), _row(110, rolls=(("Strength", 2),)), _row(120, rolls=(("Strength", 3),))]
    synergies = {frozenset({"Luck", "MagicalPower"}): 20.0}
    result = price_from_market("HeaterShield_5001", OURS_BASE, OURS_ROLLS, 10, [], rows, RULES,
                               extra_share=0.0, synergies=synergies)
    assert result.price == 432   # Luck 400g +20% for the Luck + MagicalPower pair -> 480 -> 432
    assert "pair" in result.compared
