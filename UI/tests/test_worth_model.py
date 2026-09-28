import math
import random

from src.models.market_patterns import Listing
from src.models.worth_model import WorthModel, evaluate, roll_quality, similar, train

META = {"Helm_5001": "Head", "Boots_5001": "Foot", "Cap_5001": "Head", "GoldBand_3001": "Ring"}
STR_EFFECT = {1: 1.0, 2: 1.3, 3: 1.6}
JUNK_EFFECT = 0.8
BASE = {"Helm_5001": 500, "Boots_5001": 800, "Cap_5001": 400}
FILLER = ("Knowledge", "Will", "Agility", "Luck")


def _market(seed=1, n=320, pair_bonus=1.0, lowballs=0):
    """Synthetic Epic market: price = base x Strength effect x junk effect x (Dex+Vigor pair) x noise."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        item = rng.choice(list(BASE))
        strength, rolls, price = rng.choice((1, 2, 3)), [], BASE[item]
        rolls.append(("Strength", strength))
        price *= STR_EFFECT[strength]
        if rng.random() < 0.4:
            rolls.append(("UndeadDamageMod", rng.randint(20, 40)))
            price *= JUNK_EFFECT
        else:
            rolls.append((rng.choice(FILLER), rng.randint(1, 3)))
        if rng.random() < 0.35:
            rolls[-1] = ("Dexterity", 2)
            rolls.append(("Vigor", 2))
            price *= pair_bonus
        else:
            rolls.append((rng.choice(FILLER), rng.randint(1, 3)))
        price *= math.exp(rng.gauss(0, 0.08))
        rows.append(Listing(item, 5, max(int(price), 1), 1, (), tuple(rolls)))
    for i in range(lowballs):
        rows.append(Listing("Helm_5001", 5, 40, 1, (), (("Strength", 3), ("Luck", 2), ("Will", 1))))
    return rows


def _train(rows, **kw):
    return train(rows, META, min_pair_support=kw.pop("min_pair_support", 30), **kw)


def test_strong_rolls_raise_and_junk_rolls_lower_the_prediction():
    model = _train(_market())
    weak = model.predict("Helm_5001", (("Strength", 1), ("Luck", 2), ("Will", 2)))
    strong = model.predict("Helm_5001", (("Strength", 3), ("Luck", 2), ("Will", 2)))
    junk = model.predict("Helm_5001", (("Strength", 3), ("UndeadDamageMod", 30), ("Will", 2)))
    assert abs(strong.value / weak.value - 1.6) < 0.15
    assert abs(junk.value / strong.value - JUNK_EFFECT) < 0.1
    assert abs(strong.value - 500 * 1.6) / (500 * 1.6) < 0.15
    effects = {c.stat: c.effect_pct for c in junk.rolls}
    assert effects["Strength"] > 0 > effects["UndeadDamageMod"]


def _pair_market(seed=5, n=800, bonus=1.3):
    """Dexterity and Vigor also appear apart, so a bonus for having both is identifiable."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        item = rng.choice(list(BASE))
        kind = rng.random()
        extra = ([("Dexterity", 2), ("Vigor", 2)] if kind < 0.3 else
                 [("Dexterity", 2), (rng.choice(FILLER), 2)] if kind < 0.5 else
                 [("Vigor", 2), (rng.choice(FILLER), 2)] if kind < 0.7 else
                 [(rng.choice(FILLER), 1), (rng.choice(FILLER[:2]), 3)])
        price = BASE[item] * (bonus if kind < 0.3 else 1.0) * math.exp(rng.gauss(0, 0.08))
        rows.append(Listing(item, 5, int(price), 1, (), tuple([("Strength", 2)] + extra)))
    return rows


def test_stat_pairs_that_sell_together_are_worth_more():
    model = _train(_pair_market())
    both = model.predict("Boots_5001", (("Strength", 2), ("Dexterity", 2), ("Vigor", 2)))
    dex = model.predict("Boots_5001", (("Strength", 2), ("Dexterity", 2), ("Luck", 2)))
    vig = model.predict("Boots_5001", (("Strength", 2), ("Vigor", 2), ("Luck", 2)))
    assert abs(both.value / dex.value - 1.3) < 0.1 and abs(both.value / vig.value - 1.3) < 0.1
    assert max(both.pairs, key=lambda p: p.effect_pct).stats == ("Dexterity", "Vigor")


def test_unseen_items_fall_back_to_their_slot_and_rarity():
    model = _train(_market())
    guess = model.predict("NewHelm_5001", (("Strength", 2), ("Luck", 2), ("Will", 2)), slot="Head")
    assert guess.confidence == "low"
    assert 300 < guess.value < 1000   # between the two known Epic helmets' levels


def test_unrolled_items_are_priced_per_unit():
    rows = _market() + [Listing("GoldBand_3001", 3, p, c, (), ()) for p, c in
                        ((100, 1), (110, 1), (95, 1), (300, 3), (105, 1), (98, 1), (102, 1), (97, 1))]
    model = _train(rows)
    assert abs(model.predict("GoldBand_3001", ()).value - 100) < 10
    assert abs(model.predict("GoldBand_3001", (), quantity=3).value - 300) < 30


def test_lowball_outliers_do_not_drag_the_prediction_down():
    model = _train(_market(lowballs=12))
    value = model.predict("Helm_5001", (("Strength", 3), ("Luck", 2), ("Will", 1))).value
    assert abs(value - 800) / 800 < 0.15


def test_model_survives_a_json_round_trip():
    model = _train(_market())
    again = WorthModel.from_dict(model.to_dict())
    rolls = (("Strength", 2), ("UndeadDamageMod", 25), ("Knowledge", 3))
    assert abs(again.predict("Cap_5001", rolls).value - model.predict("Cap_5001", rolls).value) < 1e-6


def test_explanation_adds_up_to_the_value():
    model = _train(_market())
    est = model.predict("Helm_5001", (("Strength", 3), ("UndeadDamageMod", 30), ("Will", 2)))
    total = est.typical * math.prod(1 + c.effect_pct / 100 for c in est.rolls) \
        * math.prod(1 + p.effect_pct / 100 for p in est.pairs)
    assert abs(total - est.value) / est.value < 0.03   # tiny pair effects aren't itemised
    assert est.low < est.value < est.high


def test_roll_quality_is_measured_within_the_items_range():
    model = _train(_market())
    est = model.predict("Helm_5001", (("Strength", 2), ("UndeadDamageMod", 40), ("Will", 1)))
    quality = {c.stat: c.quality for c in est.rolls}
    assert quality["Strength"] == 0.5 and quality["UndeadDamageMod"] == 1.0 and quality["Will"] == 0.0
    assert roll_quality(5, 5, 5) == 0.5


def test_holdout_evaluation_beats_the_item_median_baseline():
    result = evaluate(_market(n=500), META, holdout=0.25, seed=3, min_pair_support=30)
    assert result["model_mdape"] < result["baseline_mdape"]
    assert result["model_within_25"] > result["baseline_within_25"]
    assert result["tested"] > 100


def test_similar_listings_share_stats_and_have_close_rolls():
    rows = [Listing("Helm_5001", 5, 900, 1, (), (("Strength", 3), ("Vigor", 2), ("Luck", 1))),
            Listing("Helm_5001", 5, 400, 1, (), (("Knowledge", 1), ("Will", 1), ("Luck", 1))),
            Listing("Helm_5001", 5, 700, 1, (), (("Strength", 2), ("Vigor", 2), ("Will", 1))),
            Listing("Cap_5001", 5, 999, 1, (), (("Strength", 3), ("Vigor", 2), ("Luck", 1)))]
    model = _train(_market())
    found = similar(model, "Helm_5001", (("Strength", 3), ("Vigor", 2), ("Will", 2)), rows, limit=2)
    assert [r.price for r in found] == [700, 900]   # same three stats beats two exact matches + a stranger


def test_values_are_for_a_fresh_listing_because_old_listings_are_the_overpriced_ones():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Aged:
        item_id: str
        rarity: int
        price: int
        item_count: int
        base: tuple
        rolls: tuple
        age_days: float

    rng = random.Random(9)
    rows = []
    for _ in range(400):
        age = rng.choice((0.2, 6.0))
        price = 600 * (1.2 if age > 5 else 1.0) * math.exp(rng.gauss(0, 0.05))
        rows.append(Aged("Helm_5001", 5, int(price), 1, (), (("Strength", 2), ("Luck", 2), ("Will", 2)), age))
    model = _train(rows)
    fresh = model.predict("Helm_5001", (("Strength", 2), ("Luck", 2), ("Will", 2)))
    old = model.predict("Helm_5001", (("Strength", 2), ("Luck", 2), ("Will", 2)), age_days=6)
    assert abs(fresh.value - 600) / 600 < 0.06 and abs(old.value / fresh.value - 1.2) < 0.06


def test_items_from_a_group_with_no_market_data_get_no_value():
    model = _train(_market())
    guess = model.predict("Bandage_2001", (), slot="utility")
    assert guess.confidence == "unknown"
