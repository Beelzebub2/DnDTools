"""Roll-aware pricing from in-game Marketplace search results (pure, no I/O).

How a price is built:

1. Price ladders. For each of our random rolls, listings of the same item carrying that
   stat form a ladder: the cheapest copy with the stat at least as high as ours is the
   most a buyer who wants that stat would pay elsewhere. We place our value on that
   ladder between the nearest weaker roll and the nearest equal-or-better one, so a small
   roll is not priced as if it were a god roll.
2. Best roll + extra good rolls. The best roll's ladder price is the base. Every other
   roll that commands a premium over a plain copy adds a share of that premium
   (EXTRA_ROLL_SHARE by default, learned from market data when available).
3. Ceiling. A listing with the same rolls that is at least as good on every stat caps
   the price: nobody pays more for ours than for a better copy.
4. Items without rolls use the cheapest real (non-lowball) listing.

Base stats count toward "at least as good" with a small tolerance, since they vary a
little between otherwise identical copies. Every price carries a confidence level and a
plain explanation for the review table.
"""
import math
from dataclasses import dataclass

from src.models.market_rules import listing_fee

PROPERTY_PREFIX = "Effect_"
NO_SELLERS_REASON = "nobody is selling this right now"
LOWBALL_RATIO = 0.5       # a listing under half the average ask is treated as a lowball outlier
CLOSE_ROLL_RATIO = 1.5    # with no weaker listing, a roll up to 1.5x ours still counts as comparable
BASE_TOLERANCE = 0.05     # base stats within 5% (at least 1 point) count as equal
EXTRA_ROLL_SHARE = 0.5    # default share of each extra good roll's premium added to the price


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
    """Cheapest price, ignoring lone lowball listings far below the rest."""
    average = sum(prices) / len(prices)
    return min([p for p in prices if p >= LOWBALL_RATIO * average] or prices)


def _unique(rows):
    """Both searches can return the same listing; keep each once."""
    return list({(r.listing_id or (r.item_id, r.price, r.base, r.rolls)): r for r in rows}.values())


def _undercut(reference, rules) -> int:
    return math.floor(reference * (1 - rules.undercut_pct / 100))


def _roll(row, stat):
    return dict(row.rolls).get(stat)


def _ladder_estimate(rows, base, stat, value) -> _Estimate | None:
    """Where our `value` of `stat` sits on this item's price ladder for that stat."""
    points = [(v, r.price) for r in rows if _base_ok(r, base) and (v := _roll(r, stat)) is not None]
    if not points:
        return None
    above = [(v, p) for v, p in points if v >= value]
    below = [(v, p) for v, p in points if v < value]
    if not above:
        best_v, best_p = max(below)
        return _Estimate(best_p, f"{stat} {value} beats every listing (best {stat} {best_v} asks {best_p}g)",
                         "low", beats_all=True)
    ceiling = _sane_min([p for _, p in above])
    ceiling_v = min(v for v, p in above if p == ceiling)
    if ceiling_v == value:
        return _Estimate(ceiling, f"{stat} {value}: cheapest copy with it at least as good asks {ceiling}g",
                         "high" if below else "medium")
    if below:
        low_v = max(v for v, _ in below)
        low_p = _sane_min([p for v, p in points if v >= low_v])  # what the next-weaker roll level costs
        share = (value - low_v) / (ceiling_v - low_v)
        estimate = low_p + (ceiling - low_p) * share
        return _Estimate(estimate, f"{stat} {value} sits between {stat} {low_v} ({low_p}g) and "
                                   f"{stat} {ceiling_v} ({ceiling}g)", "high")
    if value > 0 and ceiling_v > value * CLOSE_ROLL_RATIO:
        estimate = ceiling * value / ceiling_v
        return _Estimate(estimate, f"{stat} {value}: only stronger rolls listed — scaled from "
                                   f"{stat} {ceiling_v} at {ceiling}g", "low")
    return _Estimate(ceiling, f"{stat} {value}: cheapest copy with it at least as good asks {ceiling}g", "medium")


def _combine(rows, base, rolls, baseline, extra_share):
    """Best roll's ladder price plus a share of every other roll's premium over a plain copy."""
    estimates = [(stat, value, est) for stat, value in rolls
                 if (est := _ladder_estimate(rows, base, stat, value)) is not None]
    if not estimates:
        return None
    estimates.sort(key=lambda e: -e[2].value)
    best_stat, best_value, best = estimates[0]
    extras = [(s, v, e.value - baseline) for s, v, e in estimates[1:] if e.value - baseline > 0]
    bonus = sum(premium for _, _, premium in extras) * extra_share
    how = best.how
    if extras:
        names = ", ".join(f"{s} {v}" for s, v, _ in extras)
        how += f"; +{round(bonus)}g for extra good rolls ({names})"
    return best.value + bonus, how, best.confidence, best.beats_all


def _stack_reference(rows, quantity):
    units = [r.price / max(r.count, 1) for r in rows]
    unit = _sane_min(units)
    confidence = "high" if len(rows) >= 3 else "medium"
    if quantity == 1 and all(r.count == 1 for r in rows):
        return round(unit), "", f"cheapest of {len(rows)} listings: {round(unit)}g", confidence
    return unit * quantity, "", f"cheapest of {len(rows)} listings: {unit:.1f}g per unit x {quantity}", confidence


def _reference(item_id, base, rolls, same_rows, all_rows, rules, extra_share, quantity=1):
    """(reference price, flag, explanation, confidence) or None when nobody sells this item."""
    rows = _unique(r for r in list(same_rows) + list(all_rows) if r.item_id == item_id)
    if not rows:
        return None
    if not rolls:
        return _stack_reference(rows, quantity)
    baseline = _sane_min([r.price for r in rows])
    combined = _combine(rows, base, rolls, baseline, extra_share)
    roll_set = {stat for stat, _ in rolls}
    dominating = [r.price for r in rows if {s for s, _ in r.rolls} == roll_set and _dominates(r, base, rolls)]
    if combined is None:
        flag = "There are no listings with any of your rolls — priced against all rolls; check this price."
        return baseline, flag, f"{len(rows)} listings of any roll; cheapest {baseline}g", "low"
    value, how, confidence, beats_all = combined
    if dominating:
        ceiling = _sane_min(dominating)
        if value > ceiling:
            value, how = ceiling, how + f"; capped at {ceiling}g (a copy at least as good on every stat)"
    flag = ""
    if beats_all and not dominating:
        flag = "Your rolls beat everything listed — consider pricing higher yourself."
    elif confidence == "low":
        flag = "Few comparable listings — check this price."
    return value, flag, how, confidence


def price_from_market(item_id, base, rolls, vendor_price, same_rows, all_rows, rules,
                      extra_share=EXTRA_ROLL_SHARE, quantity=1) -> RollPrice:
    """Price for our copy (or our stack of `quantity`); vendor_price is per unit."""
    found = _reference(item_id, tuple(base), tuple(rolls), same_rows, all_rows, rules, extra_share, quantity)
    if found is None:
        return RollPrice(False, None, 0, NO_SELLERS_REASON)
    reference, flag, compared, confidence = found
    price = _undercut(reference, rules)
    if price < max(rules.min_price, 1):
        return RollPrice(False, None, 0, "below min price", flag, compared, confidence)
    fee = listing_fee(price)
    net = price - fee
    if net <= int(vendor_price or 0) * quantity:
        return RollPrice(False, None, 0, "vendor pays more", flag, compared, confidence)
    if net / price < rules.min_net_ratio:
        return RollPrice(False, None, 0, "fee too high", flag, compared, confidence)
    return RollPrice(True, price, fee, "ok", flag, compared, confidence)
