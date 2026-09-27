"""Cross-item pattern analysis over the local market history (pure, no I/O).

Prices are compared *within* an item: each listing's value is its log price relative to
the median log price of the same item and rarity. That cancels out how expensive the item
is in general and leaves what its rolls add. From that we learn:

- roll ranges per item/stat (so a roll can be judged as a percentile of what's possible)
- how much each stat adds when present, and per unit of roll quality
- how much a 2nd and 3rd *good* roll adds on top of the best one
- stat pairs that are worth more together than apart
- rarity price steps, price-point habits, lowball frequency, below-vendor deals
"""
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass

GOOD_ROLL_PERCENTILE = 0.7   # a roll in the top 30% of its observed range counts as "good"
MIN_ITEM_LISTINGS = 8        # items with fewer listings are too thin to compare within
MODEL_PAIRS = 40             # stat pairs kept for pricing (the report shows the top ones)
MIN_STAT_SUPPORT = 8         # stats / pairs need this many listings before we trust them
LOWBALL_RATIO = 0.5


@dataclass(frozen=True)
class Listing:
    item_id: str
    rarity: int
    price: int
    item_count: int
    base: tuple
    rolls: tuple
    seller: str = ""


def _group_by_item(listings):
    groups = defaultdict(list)
    for listing in listings:
        if listing.item_count == 1 and listing.price > 0:
            groups[listing.item_id].append(listing)
    return {item: rows for item, rows in groups.items() if len(rows) >= MIN_ITEM_LISTINGS}


def roll_ranges(listings) -> dict:
    """{item_id: {stat: (min, max, count)}} over random rolls."""
    values = defaultdict(lambda: defaultdict(list))
    for listing in listings:
        for stat, value in listing.rolls:
            values[listing.item_id][stat].append(value)
    return {item: {stat: (min(v), max(v), len(v)) for stat, v in stats.items()} for item, stats in values.items()}


def percentile(value, low, high) -> float:
    return 1.0 if high <= low else max(0.0, min(1.0, (value - low) / (high - low)))


def _relative_prices(groups):
    """[(listing, log price minus the median log price of the *other* listings of that item)]

    Leaving the listing itself out keeps small groups from pinning results to exactly 0.
    """
    out = []
    for rows in groups.values():
        logs = [math.log(r.price) for r in rows]
        for i, (r, lp) in enumerate(zip(rows, logs)):
            others = logs[:i] + logs[i + 1:]
            out.append((r, lp - statistics.median(others)))
    return out


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def stat_premiums(listings) -> dict:
    """{stat: {"present": %, "per_quality": %, "support": n}} as percent price changes.

    "present": median price uplift of listings carrying the stat vs the item's median.
    "per_quality": extra uplift going from the worst to the best observed roll of it.
    """
    groups = _group_by_item(listings)
    ranges = roll_ranges([r for rows in groups.values() for r in rows])
    by_stat = defaultdict(list)
    for listing, rel in _relative_prices(groups):
        for stat, value in listing.rolls:
            low, high, _ = ranges[listing.item_id][stat]
            by_stat[stat].append((rel, percentile(value, low, high)))
    result = {}
    for stat, points in by_stat.items():
        if len(points) < MIN_STAT_SUPPORT:
            continue
        rels = [p[0] for p in points]
        quals = [p[1] for p in points]
        mq, mr = _mean(quals), _mean(rels)
        var = sum((q - mq) ** 2 for q in quals)
        slope = sum((q - mq) * (r - mr) for r, q in points) / var if var > 1e-9 else 0.0
        result[stat] = {
            "present": round((math.exp(statistics.median(rels)) - 1) * 100, 1),
            "per_quality": round((math.exp(slope) - 1) * 100, 1),
            "support": len(points),
        }
    return dict(sorted(result.items(), key=lambda kv: -kv[1]["per_quality"]))


def good_roll_counts(listings) -> dict:
    """{k: {"median_uplift": %, "support": n}} for listings with k good rolls."""
    groups = _group_by_item(listings)
    ranges = roll_ranges([r for rows in groups.values() for r in rows])
    by_k = defaultdict(list)
    for listing, rel in _relative_prices(groups):
        good = 0
        for stat, value in listing.rolls:
            low, high, count = ranges[listing.item_id][stat]
            if count >= 3 and percentile(value, low, high) >= GOOD_ROLL_PERCENTILE:
                good += 1
        by_k[good].append(rel)
    return {k: {"median_uplift": round((math.exp(statistics.median(v)) - 1) * 100, 1), "support": len(v)}
            for k, v in sorted(by_k.items())}


def extra_good_roll_factor(counts: dict):
    """How much of the best good roll's uplift each further good roll adds (0..1), or None."""
    one, two = counts.get(1), counts.get(2)
    if not one or not two or min(one["support"], two["support"]) < MIN_STAT_SUPPORT:
        return None
    first = math.log1p(one["median_uplift"] / 100) - math.log1p(counts.get(0, {"median_uplift": 0})["median_uplift"] / 100)
    second = math.log1p(two["median_uplift"] / 100) - math.log1p(one["median_uplift"] / 100)
    if first <= 0:
        return None
    return round(max(0.0, min(1.0, second / first)), 2)


def pair_synergies(listings, top=10) -> list:
    """Stat pairs whose listings are worth more than their stats' separate uplifts suggest."""
    groups = _group_by_item(listings)
    rel_rows = _relative_prices(groups)
    single = defaultdict(list)
    for listing, rel in rel_rows:
        for stat, _ in listing.rolls:
            single[stat].append(rel)
    base = {s: _mean(v) for s, v in single.items()}
    pairs = defaultdict(list)
    for listing, rel in rel_rows:
        stats = sorted({s for s, _ in listing.rolls})
        for i, a in enumerate(stats):
            for b in stats[i + 1:]:
                pairs[(a, b)].append(rel)
    out = []
    for (a, b), rels in pairs.items():
        if len(rels) < MIN_STAT_SUPPORT:
            continue
        synergy = _mean(rels) - max(base[a], base[b])
        out.append({"pair": f"{a} + {b}", "synergy": round((math.exp(synergy) - 1) * 100, 1), "support": len(rels)})
    return sorted(out, key=lambda p: -p["synergy"])[:top]


def rarity_steps(listings) -> dict:
    """Median price ratio between consecutive rarities of the same item archetype."""
    medians = defaultdict(dict)
    by_key = defaultdict(list)
    for listing in listings:
        if listing.item_count == 1 and listing.rarity:
            archetype = listing.item_id.rsplit("_", 1)[0]
            by_key[(archetype, listing.rarity)].append(listing.price)
    for (archetype, rarity), prices in by_key.items():
        if len(prices) >= 3:
            medians[archetype][rarity] = statistics.median(prices)
    ratios = defaultdict(list)
    for per_rarity in medians.values():
        for rarity, price in per_rarity.items():
            lower = per_rarity.get(rarity - 1)
            if lower:
                ratios[(rarity - 1, rarity)].append(price / lower)
    return {f"{a}->{b}": {"median_ratio": round(statistics.median(v), 2), "archetypes": len(v)}
            for (a, b), v in sorted(ratios.items())}


def price_habits(listings) -> dict:
    prices = [listing.price for listing in listings if listing.item_count == 1]
    if not prices:
        return {}
    ends = defaultdict(int)
    for p in prices:
        if p % 100 == 0:
            ends["x00"] += 1
        elif p % 100 == 99:
            ends["x99"] += 1
        elif p % 50 == 0:
            ends["x50"] += 1
        elif p % 111 == 0 or p % 1111 == 0:
            ends["repeating digits"] += 1
    return {k: f"{v / len(prices) * 100:.0f}%" for k, v in sorted(ends.items(), key=lambda kv: -kv[1])}


def lowball_share(listings) -> str:
    groups = _group_by_item(listings)
    total = low = 0
    for rows in groups.values():
        median = statistics.median(r.price for r in rows)
        total += len(rows)
        low += sum(1 for r in rows if r.price < LOWBALL_RATIO * median)
    return f"{low / total * 100:.1f}%" if total else "n/a"


def below_vendor(listings, vendor_prices: dict, top=10) -> list:
    """Listings priced under what a merchant pays — buy-and-vendor opportunities."""
    deals = []
    for listing in listings:
        vendor = vendor_prices.get(listing.item_id, 0) * listing.item_count
        if vendor and listing.price < vendor:
            deals.append({"item": listing.item_id, "price": listing.price, "vendor": vendor,
                          "gain": vendor - listing.price})
    return sorted(deals, key=lambda d: -d["gain"])[:top]


def seller_concentration(listings, top=5) -> list:
    counts = defaultdict(int)
    for listing in listings:
        if listing.seller:
            counts[listing.seller] += 1
    total = sum(counts.values())
    return [{"seller_share": f"{c / total * 100:.1f}%", "listings": c}
            for _, c in sorted(counts.items(), key=lambda kv: -kv[1])[:top]] if total else []


def stat_premiums_by_type(listings, item_types: dict) -> dict:
    """stat_premiums split by item type (weapon / armor / accessory ...)."""
    by_type = defaultdict(list)
    for listing in listings:
        by_type[item_types.get(listing.item_id, "other")].append(listing)
    return {t: stat_premiums(rows) for t, rows in by_type.items() if len(rows) >= MIN_ITEM_LISTINGS * 2}


def analyze(listings, vendor_prices=None, item_types=None) -> dict:
    counts = good_roll_counts(listings)
    return {
        "stat_premiums_by_type": stat_premiums_by_type(listings, item_types or {}),
        "listings": len(listings),
        "items": len({l.item_id for l in listings}),
        "stat_premiums": stat_premiums(listings),
        "good_roll_counts": counts,
        "extra_good_roll_factor": extra_good_roll_factor(counts),
        "pair_synergies": pair_synergies(listings, top=MODEL_PAIRS),
        "rarity_steps": rarity_steps(listings),
        "price_habits": price_habits(listings),
        "lowball_share": lowball_share(listings),
        "below_vendor": below_vendor(listings, vendor_prices or {}),
        "seller_concentration": seller_concentration(listings),
        "roll_ranges": roll_ranges(listings),
    }
