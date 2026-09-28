"""Item Worth: what an item is worth for its exact rolls, learned from the local market history.

An additive model on log price per unit, fitted with ridge regression on sparse one-hot features:

    log(price) = group(slot, rarity) + item
                 + Σ rolls [ e(stat, tier) + e(stat, rarity, tier) + e(stat, slot, rarity, tier) ]
                 + Σ pairs e(stat A + stat B)

A roll's tier is where it sits in that stat's range on the item (weak / mid / strong). Fitting the
broad and the specific terms together lets ridge shrink thin cells (a rare item, a stat seldom
seen on a slot) toward the broader ones. A robust refit drops listings far from the first fit
(lowballs, wild asks). Training needs numpy / scikit-learn; predicting works from the saved JSON.
"""
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from src.models.market_history import rarity_of

MODEL_VERSION = 1
TIER_EDGES = (1 / 3, 2 / 3)
MIN_PAIR_SUPPORT = 50        # listings carrying a stat pair before it gets its own term
MIN_RANGE_VALUES = 3         # values seen before a stat's range on an item is trusted
MIN_SPREAD_ROWS = 5
RIDGE_ALPHA = 1.0
OUTLIER_MADS = 3.0
MAD_TO_SIGMA = 1.4826
HIGH_CONFIDENCE_SUPPORT = 30
MEDIUM_CONFIDENCE_SUPPORT = 8
HIGH_CONFIDENCE_SPREAD = 0.35  # typical log error below this counts as a tight fit
UNKNOWN_SLOT = "?"
PAIR_REPORT_PCT = 1.0          # pair bonuses smaller than this count in the value but aren't listed
AGE_EDGES_DAYS = (1, 3, 5)     # how long a listing had been up when first seen: 0 = fresh .. 3 = old
FRESH = 0


def roll_quality(value, low, high) -> float:
    """Where a roll sits in its range, 0 (worst) .. 1 (best); 0.5 when the range is unknown."""
    return 0.5 if high <= low else max(0.0, min(1.0, (value - low) / (high - low)))


def _tier(quality):
    return 0 if quality < TIER_EDGES[0] else 1 if quality < TIER_EDGES[1] else 2


def _roll_names(stat, slot, rarity, tier):
    return (f"r|{stat}|{tier}", f"rr|{stat}|{rarity}|{tier}", f"rs|{stat}|{slot}|{rarity}|{tier}")


def _pair_key(a, b):
    return "+".join(sorted((a, b)))


@dataclass(frozen=True)
class RollWorth:
    stat: str
    value: float
    quality: float     # 0..1 within the stat's range on this item
    effect_pct: float  # price change vs an average roll


@dataclass(frozen=True)
class PairWorth:
    stats: tuple
    effect_pct: float


@dataclass(frozen=True)
class Estimate:
    value: float       # for the whole quantity
    low: float
    high: float
    confidence: str    # "high" | "medium" | "low", or "unknown": no market data for its item or group
    typical: float     # the same item with average rolls
    rolls: tuple       # RollWorth per roll
    pairs: tuple       # PairWorth per stat pair bonus
    listings: int      # listings of this item the model learned from


@dataclass(frozen=True)
class _Row:
    item_id: str
    rarity: int
    slot: str
    price: float       # per unit
    rolls: tuple
    age: int | None    # age bucket, None when unknown


def _age_bucket(days):
    if days is None:
        return None
    return sum(days >= edge for edge in AGE_EDGES_DAYS)


def _rows(listings, item_slots):
    out = []
    for listing in listings:
        count = max(int(listing.item_count or 1), 1)
        if listing.price and listing.price > 0:
            rarity = listing.rarity or rarity_of(listing.item_id)
            out.append(_Row(listing.item_id, int(rarity), item_slots.get(listing.item_id) or UNKNOWN_SLOT,
                            listing.price / count, tuple((str(s), float(v)) for s, v in listing.rolls),
                            _age_bucket(getattr(listing, "age_days", None))))
    return out


def _ranges(rows):
    per_item, per_group = defaultdict(lambda: defaultdict(list)), defaultdict(lambda: defaultdict(list))
    for row in rows:
        for stat, value in row.rolls:
            per_item[row.item_id][stat].append(value)
            per_group[f"{row.slot}|{row.rarity}"][stat].append(value)

    def span(table):
        return {key: {stat: [min(v), max(v)] for stat, v in stats.items() if len(v) >= MIN_RANGE_VALUES}
                for key, stats in table.items()}
    return span(per_item), span(per_group)


def _frequent_pairs(rows, min_support):
    counts = Counter()
    for row in rows:
        stats = sorted({s for s, _ in row.rolls})
        for i, a in enumerate(stats):
            for b in stats[i + 1:]:
                counts[_pair_key(a, b)] += 1
    return sorted(p for p, n in counts.items() if n >= min_support)


class WorthModel:
    """A trained model; predict() needs only the plain data it was saved as."""

    def __init__(self, data: dict):
        self._d = data

    @classmethod
    def from_dict(cls, data: dict) -> "WorthModel":
        if not isinstance(data, dict) or data.get("version") != MODEL_VERSION:
            raise ValueError("not a saved worth model (or an older version)")
        return cls(data)

    def to_dict(self) -> dict:
        return self._d

    @property
    def listings(self) -> int:
        return int(self._d.get("listings", 0))

    def knows(self, item_id) -> bool:
        return item_id in self._d["support"]

    def _range(self, item_id, slot, rarity, stat):
        found = self._d["ranges"].get(item_id, {}).get(stat) or \
            self._d["group_ranges"].get(f"{slot}|{rarity}", {}).get(stat)
        return tuple(found) if found else None

    def quality(self, item_id, stat, value, slot=None, rarity=None) -> float:
        slot = self._d["slots"].get(item_id) or slot or UNKNOWN_SLOT
        rarity = rarity if rarity is not None else rarity_of(item_id)
        bounds = self._range(item_id, slot, rarity, stat)
        return roll_quality(value, *bounds) if bounds else 0.5

    def _roll_effect(self, stat, slot, rarity, quality):
        coef = self._d["coef"]
        tiers = sum(coef.get(name, 0.0) for name in _roll_names(stat, slot, rarity, _tier(quality)))
        return tiers + coef.get(f"q|{stat}|{rarity}", 0.0) * (quality - 0.5)

    def predict(self, item_id, rolls, quantity=1, slot=None, rarity=None, age_days=None) -> Estimate:
        """What this item sells for as a fresh listing (or at age_days, for evaluation)."""
        d, coef = self._d, self._d["coef"]
        rarity = rarity if rarity is not None else rarity_of(item_id)
        slot = d["slots"].get(item_id) or slot or UNKNOWN_SLOT
        rolls = tuple((str(s), float(v)) for s, v in rolls)
        log_base = d["intercept"] + coef.get(f"g|{slot}|{rarity}", 0.0) + coef.get(f"i|{item_id}", 0.0)             + coef.get(f"a|{FRESH if age_days is None else _age_bucket(age_days)}", 0.0)  # old = unsold, overpriced
        if not rolls and item_id in d.get("medians", {}):
            log_base = math.log(d["medians"][item_id])  # unrolled items: the median ask beats any fit
        average = d["avg_roll"].get(str(rarity), 0.0)
        roll_parts = []
        for stat, value in rolls:
            quality = self.quality(item_id, stat, value, slot, rarity)
            roll_parts.append((stat, value, quality, self._roll_effect(stat, slot, rarity, quality)))
        stats = {s for s, _ in rolls}
        pair_parts = [(tuple(p.split("+")), coef.get(f"p|{p}", 0.0)) for p in d["pairs"]
                      if set(p.split("+")) <= stats]
        log_value = log_base + sum(e for *_, e in roll_parts) + sum(e for _, e in pair_parts)
        quantity = max(int(quantity or 1), 1)
        value = math.exp(log_value) * quantity
        spread = d["spread"].get(item_id) or d["group_spread"].get(f"{slot}|{rarity}") or d["global_spread"]
        band = math.exp(MAD_TO_SIGMA * spread)
        support = int(d["support"].get(item_id, 0))
        if not support and f"g|{slot}|{rarity}" not in coef:
            confidence = "unknown"  # neither the item nor anything like it was ever listed: no real estimate
        elif support >= HIGH_CONFIDENCE_SUPPORT and spread <= HIGH_CONFIDENCE_SPREAD:
            confidence = "high"
        elif support >= MEDIUM_CONFIDENCE_SUPPORT:
            confidence = "medium"
        else:
            confidence = "low"
        return Estimate(
            value=value, low=value / band, high=value * band, confidence=confidence,
            typical=math.exp(log_base + average * len(rolls)) * quantity,
            rolls=tuple(RollWorth(s, v, round(q, 3), round((math.exp(e - average) - 1) * 100, 1))
                        for s, v, q, e in roll_parts),
            pairs=tuple(PairWorth(stats_, pct) for stats_, e in pair_parts
                        if abs(pct := round((math.exp(e) - 1) * 100, 1)) >= PAIR_REPORT_PCT),
            listings=support,
        )


def _design(rows, ranges, group_ranges, pairs):
    """Sparse one-hot design matrix; returns (matrix, column names)."""
    from scipy.sparse import csr_matrix
    index, data_rows, data_cols, data_vals = {}, [], [], []
    probe = WorthModel({"ranges": ranges, "group_ranges": group_ranges, "slots": {}})
    pair_set = set(pairs)
    for r, row in enumerate(rows):
        names = [(f"g|{row.slot}|{row.rarity}", 1.0), (f"i|{row.item_id}", 1.0)]
        if row.age is not None:
            names.append((f"a|{row.age}", 1.0))
        for stat, value in row.rolls:
            bounds = probe._range(row.item_id, row.slot, row.rarity, stat)
            quality = roll_quality(value, *bounds) if bounds else 0.5
            names.extend((name, 1.0) for name in _roll_names(stat, row.slot, row.rarity, _tier(quality)))
            names.append((f"q|{stat}|{row.rarity}", quality - 0.5))
        stats = sorted({s for s, _ in row.rolls})
        names.extend((f"p|{_pair_key(a, b)}", 1.0) for i, a in enumerate(stats) for b in stats[i + 1:]
                     if _pair_key(a, b) in pair_set)
        for name, value in names:
            data_rows.append(r)
            data_cols.append(index.setdefault(name, len(index)))
            data_vals.append(value)
    matrix = csr_matrix((data_vals, (data_rows, data_cols)), shape=(len(rows), len(index)))
    return matrix, list(index)


def _fit(matrix, targets, alpha):
    from sklearn.linear_model import Ridge
    model = Ridge(alpha=alpha, solver="sparse_cg", max_iter=5000, tol=1e-6)
    model.fit(matrix, targets)
    return model


def _mad(values):
    centre = statistics.median(values)
    return statistics.median(abs(v - centre) for v in values)


def train(listings, item_slots, *, min_pair_support=MIN_PAIR_SUPPORT, alpha=RIDGE_ALPHA) -> WorthModel:
    """Fit the model on market listings; item_slots maps item id -> slot name (items.json slot_type)."""
    import numpy as np
    rows = _rows(listings, item_slots)
    if not rows:
        raise ValueError("no market listings to learn from yet")
    ranges, group_ranges = _ranges(rows)
    pairs = _frequent_pairs(rows, min_pair_support)
    matrix, names = _design(rows, ranges, group_ranges, pairs)
    targets = np.log([row.price for row in rows])
    fitted = _fit(matrix, targets, alpha)
    residuals = targets - fitted.predict(matrix)
    centre, mad = float(np.median(residuals)), _mad(residuals.tolist())
    if mad > 0:  # refit without the listings far from the first fit (lowballs, wild asks)
        keep = np.abs(residuals - centre) <= OUTLIER_MADS * MAD_TO_SIGMA * mad
        if keep.sum() >= max(len(rows) // 2, 1):
            rows = [row for row, k in zip(rows, keep) if k]
            matrix, names = _design(rows, ranges, group_ranges, pairs)
            targets = np.log([row.price for row in rows])
            fitted = _fit(matrix, targets, alpha)
            residuals = targets - fitted.predict(matrix)
    coef = {name: float(c) for name, c in zip(names, fitted.coef_) if abs(c) > 1e-9}
    residuals = _calibrate_items(coef, rows, residuals.tolist())
    data = {
        "version": MODEL_VERSION, "intercept": float(fitted.intercept_), "coef": coef,
        "ranges": ranges, "group_ranges": group_ranges, "pairs": pairs,
        "slots": {row.item_id: row.slot for row in rows if row.slot != UNKNOWN_SLOT},
        "support": dict(Counter(row.item_id for row in rows)), "listings": len(rows),
        "medians": _unrolled_medians(rows),
    }
    model = WorthModel({**data, "avg_roll": {}, "spread": {}, "group_spread": {}, "global_spread": 0.3})
    _finish(model, rows, residuals)
    return model


def _calibrate_items(coef, rows, residuals):
    """Shift each well-seen item to its median residual: asks are skewed, the median is the norm."""
    by_item = defaultdict(list)
    for row, res in zip(rows, residuals):
        by_item[row.item_id].append(res)
    shift = {item: statistics.median(v) for item, v in by_item.items() if len(v) >= MIN_SPREAD_ROWS}
    for item, delta in shift.items():
        coef[f"i|{item}"] = coef.get(f"i|{item}", 0.0) + delta
    return [res - shift.get(row.item_id, 0.0) for row, res in zip(rows, residuals)]


def _unrolled_medians(rows):
    prices = defaultdict(list)
    for row in rows:
        if not row.rolls:
            prices[row.item_id].append(row.price)
    return {item: statistics.median(v) for item, v in prices.items() if len(v) >= MIN_RANGE_VALUES}


def _finish(model, rows, residuals):
    """Average roll effect per rarity (for explanations) and residual spreads (for bands)."""
    d = model.to_dict()
    effects = defaultdict(list)
    for row in rows:
        for stat, value in row.rolls:
            effects[str(row.rarity)].append(
                model._roll_effect(stat, row.slot, row.rarity, model.quality(row.item_id, stat, value, row.slot, row.rarity)))
    d["avg_roll"] = {rarity: statistics.fmean(v) for rarity, v in effects.items()}
    by_item, by_group = defaultdict(list), defaultdict(list)
    for row, res in zip(rows, residuals):
        by_item[row.item_id].append(res)
        by_group[f"{row.slot}|{row.rarity}"].append(res)
    d["spread"] = {k: _mad(v) for k, v in by_item.items() if len(v) >= MIN_SPREAD_ROWS}
    d["group_spread"] = {k: _mad(v) for k, v in by_group.items() if len(v) >= MIN_SPREAD_ROWS}
    d["global_spread"] = _mad(residuals) if residuals else 0.3


def similar(model, item_id, rolls, candidates, limit=5):
    """Listings of the same item closest to these rolls: shared stats with close quality first."""
    ours = {str(s): model.quality(item_id, s, v) for s, v in rolls}

    def distance(listing):
        theirs = {str(s): model.quality(item_id, s, v) for s, v in listing.rolls}
        missing = sum(1.0 if s not in theirs else abs(q - theirs[s]) for s, q in ours.items())
        return missing + 0.5 * sum(1 for s in theirs if s not in ours), listing.price
    same = [c for c in candidates if c.item_id == item_id]
    return sorted(same, key=distance)[:limit]


def evaluate(listings, item_slots, *, holdout=0.2, seed=7, **train_kw) -> dict:
    """Hold out a share of listings, train on the rest, and compare errors with an item-median guess."""
    rows = list(listings)
    random.Random(seed).shuffle(rows)
    cut = max(int(len(rows) * holdout), 1)
    tested, trained = rows[:cut], rows[cut:]
    model = train(trained, item_slots, **train_kw)
    unit = defaultdict(list)
    for listing in trained:
        if listing.price > 0:
            unit[listing.item_id].append(listing.price / max(listing.item_count, 1))
    everything = [p for prices in unit.values() for p in prices]
    fallback = statistics.median(everything)
    model_err, base_err = [], []
    for listing in tested:
        if listing.price <= 0:
            continue
        actual = listing.price / max(listing.item_count, 1)
        guess = model.predict(listing.item_id, listing.rolls, slot=item_slots.get(listing.item_id),
                              age_days=getattr(listing, "age_days", None)).value
        base = statistics.median(unit[listing.item_id]) if unit.get(listing.item_id) else fallback
        model_err.append(abs(guess - actual) / actual)
        base_err.append(abs(base - actual) / actual)

    def summary(errors):
        return round(statistics.median(errors) * 100, 1), round(sum(e <= 0.25 for e in errors) / len(errors) * 100, 1)
    model_mdape, model_within = summary(model_err)
    base_mdape, base_within = summary(base_err)
    return {"tested": len(model_err), "trained": len(trained), "model_mdape": model_mdape,
            "model_within_25": model_within, "baseline_mdape": base_mdape, "baseline_within_25": base_within}
