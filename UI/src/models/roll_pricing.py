"""Roll-aware pricing from in-game Marketplace search results (pure, no I/O).

How a price is built (low, for a fast sale - never a point inside a price range):

1. Anchors. For each of our random rolls, the anchor is one real listing: the cheapest
   non-lowball copy with that stat at our level or the nearest similar weaker level (within
   CLOSE_ROLL_RATIO) - or anything stronger that happens to be cheaper. So we are never
   dearer than a copy at least as good, nor than a slightly weaker one. Rolls far weaker or
   far stronger than ours are no comparison: a small roll isn't priced like a god roll and
   one expensive listing can't drag the price up.
2. Best roll + extra good rolls. The best roll's anchor is the base. Every other roll that
   commands a premium over a plain copy adds a share of that premium (a learned pair bonus
   when the two stats sell together, else EXTRA_ROLL_SHARE).
3. Ceiling. A listing with the same rolls that is at least as good on every stat caps
   the price: nobody pays more for ours than for a better copy.
4. Items without rolls use the cheapest real (non-lowball) listing.

Lowballs (asks far below comparable copies) are ignored everywhere - they rarely last.

Base stats count toward "at least as good" with a small tolerance, since they vary a
little between otherwise identical copies. Every price carries a confidence level and a
plain explanation for the review table.
"""
import math
import statistics
from dataclasses import dataclass

from src.models.market_rules import listing_fee

PROPERTY_PREFIX = "Effect_"
NO_SELLERS_REASON = "nobody is selling this right now"
LOWBALL_RATIO = 0.5       # an ask under half the typical ask of comparable copies is a lowball
CLOSE_ROLL_RATIO = 1.5    # rolls within 1.5x of ours (either way) are similar enough to compare
BASE_TOLERANCE = 0.05     # base stats within 5% (at least 1 point) count as equal
EXTRA_ROLL_SHARE = 0.25   # fallback share of an extra roll's premium when no pair synergy is known
MAX_SYNERGY_PCT = 50.0    # cap on a learned pair bonus
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
FAR_APART_FLAG = "Only two listings and they're far apart — check this price."


@dataclass(frozen=True)
class MarketRow:
    item_id: str
    price: int
    base: tuple   # ((stat, value), ...) primary properties
    rolls: tuple  # ((stat, value), ...) random secondary properties
    listing_id: str = ""
    count: int = 1  # stack size of the listing; its price is for the whole stack


@dataclass(frozen=True)
class RollPrice:
    ok: bool
    price: int | None
    fee: int
    reason: str
    flag: str = ""
    compared: str = ""
    confidence: str = ""


@dataclass(frozen=True)
class _Estimate:
    value: float
    how: str
    confidence: str  # "high" | "medium" | "low"
    beats_all: bool = False


def stat_name(property_type_id: str) -> str:
    """'DesignDataItemPropertyType:Id_ItemPropertyType_Effect_Luck' -> 'Luck'."""
    return str(property_type_id).split(PROPERTY_PREFIX)[-1]


def _base_ok(row: MarketRow, base: tuple) -> bool:
    theirs = dict(row.base)
    return all(theirs.get(stat, float("-inf")) >= value - max(1.0, abs(value) * BASE_TOLERANCE)
               for stat, value in base)


def _dominates(row: MarketRow, base: tuple, rolls: tuple) -> bool:
    theirs = dict(row.rolls)
    return _base_ok(row, base) and all(theirs.get(stat, float("-inf")) >= value for stat, value in rolls)


def _sane_min(prices) -> int:
    """Cheapest price, ignoring lowball listings far below the typical (median) ask.

    The lower median is used so a single wild high ask can't drag the threshold up and
    throw away every normal listing.
    """
    typical = statistics.median_low(prices)
    return min([p for p in prices if p >= LOWBALL_RATIO * typical] or prices)


def _without_lowballs(points):
    """Drop lowball asks from (roll value, price) points.

    A listing is judged only against copies no better than it: a cheap weak roll is a real
    price level, while a strong roll dumped far below weaker copies is a lowball.
    """
    kept = [(v, p) for v, p in points
            if p >= LOWBALL_RATIO * statistics.median_low([q for w, q in points if w <= v])]
    return kept or points


def _unique(rows):
    """Both searches can return the same listing; keep each once."""
    return list({(r.listing_id or (r.item_id, r.price, r.base, r.rolls)): r for r in rows}.values())


def _undercut(reference, rules) -> int:
    return math.floor(reference * (1 - rules.undercut_pct / 100))


def _roll(row, stat):
    return dict(row.rolls).get(stat)


def _anchor_estimate(rows, base, stat, value) -> _Estimate | None:
    """What a buyer who wants our `value` of `stat` pays elsewhere: one real listing's price."""
    points = [(v, r.price) for r in rows if _base_ok(r, base) and (v := _roll(r, stat)) is not None]
    real = _without_lowballs(points)
    if not real:
        return None
    if all(v < value for v, _ in real):
        best_v = max(v for v, _ in real)
        best_p = min(p for v, p in real if v == best_v)  # equal rolls: the cheaper ask is the reference
        return _Estimate(best_p, f"{stat} {value} beats every listing (best {stat} {best_v} asks {best_p}g)",
                         "low", beats_all=True)
    low, high = (value / CLOSE_ROLL_RATIO, value * CLOSE_ROLL_RATIO) if value > 0 else (value, value)
    similar_better = [p for v, p in real if value <= v <= high]
    if similar_better:  # a weaker copy far below similar ones at least as good as ours is a dump
        floor = LOWBALL_RATIO * min(similar_better)
        real = [(v, p) for v, p in real if v >= value or p >= floor]
    level = max((v for v, _ in real if low <= v < value), default=value)  # nearest similar weaker level
    competing = [(v, p) for v, p in real if v >= level]
    if all(v > high for v, _ in competing):
        return None  # only much stronger rolls are listed: this roll doesn't set our price
    price = min(p for _, p in competing)
    confidence = "high" if similar_better and any(v < value for v, _ in real) else "medium"
    return _Estimate(price, f"{stat} {value}: cheapest listing with {stat} ≥ {level} asks {price}g", confidence)


def _extra_roll_bonus(best_stat, best_value, extras, extra_share, synergies):
    """(gold, explanation) added for rolls beyond the best one.

    Market data shows unrelated extra rolls add little, but rolls that suit the same build
    sell for more together: a learned pair synergy (percent of the best roll's price) wins,
    otherwise a small share of the extra roll's own premium is added.
    """
    bonus, parts = 0.0, []
    for stat, value, premium in extras:
        pct = (synergies or {}).get(frozenset({best_stat, stat}))
        if pct and pct > 0:
            gain = best_value * min(pct, MAX_SYNERGY_PCT) / 100
            parts.append(f"{stat} pairs with {best_stat} (+{pct:.0f}% in market data)")
        else:
            gain = max(premium, 0) * extra_share
            if gain:
                parts.append(f"{stat} {value}")
        bonus += gain
    how = f"; +{round(bonus)}g for extra rolls ({', '.join(parts)})" if bonus and parts else ""
    return bonus, how


def _combine(rows, base, rolls, baseline, extra_share, synergies=None):
    """Best roll's anchor price plus a bonus for the other rolls."""
    estimates = [(stat, value, est) for stat, value in rolls
                 if (est := _anchor_estimate(rows, base, stat, value)) is not None]
    if not estimates:
        return None
    estimates.sort(key=lambda e: (-e[2].value, CONFIDENCE_RANK[e[2].confidence]))  # ties: the surer one
    best_stat, _, best = estimates[0]
    extras = [(s, v, e.value - baseline) for s, v, e in estimates[1:]]
    bonus, extra_how = _extra_roll_bonus(best_stat, best.value, extras, extra_share, synergies)
    beats_any = any(e.beats_all for _, _, e in estimates)
    return best.value + bonus, best.how + extra_how, best.confidence, beats_any


def _like_for_like(rows, quantity):
    """Stacks sell for less per unit than single items: compare a stack with other stacks and a
    single item with other singles whenever the market has any."""
    bulk = [r for r in rows if r.count > 1]
    singles = [r for r in rows if r.count <= 1]
    if quantity > 1 and bulk:
        return bulk
    if quantity <= 1 and singles:
        return singles
    return rows


def _stack_reference(rows, quantity):
    """(reference, flag, explanation, confidence, floor) for items without rolls."""
    rows = _like_for_like(rows, quantity)
    units = [r.price / max(r.count, 1) for r in rows]
    unit = _sane_min(units)
    confidence, flag = ("high" if len(rows) >= 3 else "medium"), ""
    if len(units) == 2 and min(units) < LOWBALL_RATIO * max(units):  # can't tell which one is off
        confidence, flag = "low", FAR_APART_FLAG
    if quantity == 1 and all(r.count == 1 for r in rows):
        return round(unit), flag, f"cheapest of {len(rows)} listings: {round(unit)}g", confidence, round(unit)
    how = f"cheapest of {len(rows)} listings: {unit:.1f}g per unit x {quantity}"
    return unit * quantity, flag, how, confidence, unit * quantity


def _reference(item_id, base, rolls, same_rows, all_rows, rules, extra_share, quantity=1, synergies=None):
    """(reference price, flag, explanation, confidence, floor) or None when nobody sells this item.

    floor is the cheapest real listing of the item, whatever its rolls.
    """
    rows = _unique(r for r in list(same_rows) + list(all_rows) if r.item_id == item_id)
    if not rows:
        return None
    if not rolls:
        return _stack_reference(rows, quantity)
    baseline = _sane_min([r.price for r in rows])
    combined = _combine(rows, base, rolls, baseline, extra_share, synergies)
    roll_set = {stat for stat, _ in rolls}
    dominating = [r.price for r in rows if {s for s, _ in r.rolls} == roll_set and _dominates(r, base, rolls)]
    if combined is None:
        flag = "There are no listings with rolls like yours — priced against copies of any roll; check this price."
        return baseline, flag, f"{len(rows)} listings of any roll; cheapest {baseline}g", "low", baseline
    value, how, confidence, beats_all = combined
    dearest = max(r.price for r in rows)
    if value > dearest:  # never above the most expensive listing of this item
        value, how = dearest, how + f"; capped at the dearest listing ({dearest}g)"
    real_caps = [p for p in dominating if p >= LOWBALL_RATIO * value]  # a dumped better copy is no ceiling
    if real_caps:
        ceiling = min(real_caps)
        if value > ceiling:
            value, how = ceiling, how + f"; capped at {ceiling}g (a copy at least as good on every stat)"
    flag = ""
    if beats_all and not dominating:
        flag = "Your rolls beat everything listed — consider pricing higher yourself."
    elif confidence == "low":
        flag = "Few comparable listings — check this price."
    return value, flag, how, confidence, baseline


def price_from_market(item_id, base, rolls, vendor_price, same_rows, all_rows, rules,
                      extra_share=EXTRA_ROLL_SHARE, quantity=1, synergies=None, model_value=None,
                      model_floor=None) -> RollPrice:
    """Price for our copy (or our stack of `quantity`); vendor_price is per unit.

    synergies: {frozenset({stat_a, stat_b}): percent} learned from market data.
    model_value: the Item Worth model's value for these exact rolls (whole quantity). The reference
    never exceeds it — a price inherited from a listing's *other* stats can't stick to junk rolls —
    and never drops below the cheapest real listing of the item.
    model_floor: the model's lowest reasonable price for these rolls (whole quantity). For a fast
    sale we list at the lower of it and the usual undercut, but never below half of it (dumps).
    """
    found = _reference(item_id, tuple(base), tuple(rolls), same_rows, all_rows, rules, extra_share, quantity,
                       synergies)
    if found is None:
        return RollPrice(False, None, 0, NO_SELLERS_REASON)
    reference, flag, compared, confidence, floor = found
    if model_value and model_value < reference:
        capped = max(model_value, floor)
        if capped < reference:
            reference = capped
            compared += f"; capped at the value model's {round(model_value)}g for these exact rolls"
            if flag.startswith("Your rolls beat"):
                flag = ""  # beating every listing on a stat buyers don't value is no reason to price higher
    price = _undercut(reference, rules)
    if model_floor:
        fast = max(min(price, math.floor(model_floor)), math.floor(LOWBALL_RATIO * model_floor))
        if fast != price:
            compared += (f"; fast sale at the lowest reasonable price for these rolls ({math.floor(model_floor)}g)"
                         if fast == math.floor(model_floor) else
                         f"; not below half the lowest reasonable price ({math.floor(model_floor)}g)")
        price = fast
    if price < max(rules.min_price, 1):
        return RollPrice(False, None, 0, "below min price", flag, compared, confidence)
    fee = listing_fee(price)
    net = price - fee
    if net <= int(vendor_price or 0) * quantity:
        return RollPrice(False, None, 0, "vendor pays more", flag, compared, confidence)
    if net / price < rules.min_net_ratio:
        return RollPrice(False, None, 0, "fee too high", flag, compared, confidence)
    return RollPrice(True, price, fee, "ok", flag, compared, confidence)
