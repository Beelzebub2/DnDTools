import random

from src.models.market_patterns import (
    Listing, analyze, below_vendor, extra_good_roll_factor, good_roll_counts, percentile, rarity_steps,
    roll_ranges, stat_premiums,
)


def _market(seed=7):
    """Synthetic shields: Luck is valuable (+60% at best roll), Vigor worthless; each good roll adds value."""
    rng = random.Random(seed)
    rows = []
    for item in ("ShieldA_5001", "ShieldB_5001", "ShieldC_5001"):
        base_price = rng.choice((200, 400, 800))
        for _ in range(40):
            luck, vigor, power = rng.randint(10, 20), rng.randint(1, 5), rng.randint(1, 4)
            q_luck, q_power = (luck - 10) / 10, (power - 1) / 3
            price = base_price * (1 + 0.6 * q_luck) * (1 + 0.3 * q_power) * rng.uniform(0.95, 1.05)
            rows.append(Listing(item, 5, int(price), 1, (), (("Luck", luck), ("Vigor", vigor), ("Power", power))))
    return rows


def test_roll_ranges_and_percentile():
    ranges = roll_ranges([Listing("A_5001", 5, 100, 1, (), (("Luck", 10),)),
                          Listing("A_5001", 5, 100, 1, (), (("Luck", 20),))])
    assert ranges["A_5001"]["Luck"] == (10, 20, 2)
    assert percentile(17, 10, 20) == 0.7
    assert percentile(5, 5, 5) == 1.0


def test_stat_premiums_find_the_valuable_stat():
    premiums = stat_premiums(_market())
    assert premiums["Luck"]["per_quality"] > 40          # planted +60%
    assert abs(premiums["Vigor"]["per_quality"]) < 10     # planted 0
    assert list(premiums)[0] == "Luck"                    # sorted most valuable first


def test_more_good_rolls_are_worth_more():
    counts = good_roll_counts(_market())
    uplifts = [counts[k]["median_uplift"] for k in sorted(counts) if counts[k]["support"] >= 8]
    assert uplifts == sorted(uplifts)
    factor = extra_good_roll_factor(counts)
    assert factor is not None and 0 < factor <= 1


def test_rarity_steps_between_consecutive_rarities():
    rows = [Listing(f"Axe_{r}001", r, p, 1, (), ()) for r, p in ((4, 100), (4, 110), (4, 90), (5, 300), (5, 310), (5, 290))]
    assert rarity_steps(rows)["4->5"]["median_ratio"] == 3.0


def test_below_vendor_deals():
    rows = [Listing("GoldCrown_4001", 4, 80, 1, (), ()), Listing("GoldCrown_4001", 4, 150, 1, (), ())]
    assert below_vendor(rows, {"GoldCrown_4001": 100}) == [
        {"item": "GoldCrown_4001", "price": 80, "vendor": 100, "gain": 20}]


def test_analyze_runs_end_to_end():
    report = analyze(_market(), {"ShieldA_5001": 10})
    assert report["listings"] == 120 and report["items"] == 3
    assert report["pair_synergies"] and report["lowball_share"].endswith("%")
