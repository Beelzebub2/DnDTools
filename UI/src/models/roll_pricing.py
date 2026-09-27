"""Roll-aware pricing from in-game Marketplace search results (pure, no I/O).

An item is compared only with listings that have the same random attributes. The
cheapest listing whose base stats and rolls are all at least as good as ours sets the
price ceiling; we undercut it. When nothing comparable exists the price falls back to
all rolls and the entry is flagged for the user to check.
"""
import math
from dataclasses import dataclass

from src.models.market_rules import listing_fee

PROPERTY_PREFIX = "Effect_"
NO_SELLERS_REASON = "nobody is selling this right now"
LOWBALL_RATIO = 0.5  # a listing under half the average ask is treated as a lowball outlier
CLOSE_ROLL_RATIO = 1.5  # a roll up to 1.5x ours counts as comparable; beyond that we scale


@dataclass(frozen=True)
class MarketRow:
    item_id: str
    price: int
    base: tuple   # ((stat, value), ...) primary properties
    rolls: tuple  # ((stat, value), ...) random secondary properties
    listing_id: str = ""


@dataclass(frozen=True)
class RollPrice:
    ok: bool
    price: int | None
    fee: int
    reason: str
    flag: str = ""
    compared: str = ""


def stat_name(property_type_id: str) -> str:
    """'DesignDataItemPropertyType:Id_ItemPropertyType_Effect_Luck' -> 'Luck'."""
    return str(property_type_id).split(PROPERTY_PREFIX)[-1]


def _at_least_as_good(row: MarketRow, base: tuple, rolls: tuple) -> bool:
    theirs = dict(row.base) | dict(row.rolls)
    return all(theirs.get(stat, float("-inf")) >= value for stat, value in base + rolls)


def _sane_min(prices) -> int:
    """Cheapest price, ignoring lone lowball listings far below the rest."""
    average = sum(prices) / len(prices)
    return min([p for p in prices if p >= LOWBALL_RATIO * average] or prices)


def _unique(rows):
    """Both searches can return the same listing; keep each once."""
    return list({(r.listing_id or (r.item_id, r.price, r.base, r.rolls)): r for r in rows}.values())


def _roll_value(row, stat):
    return dict(row.rolls).get(stat, float("-inf"))


def _single_roll_reference(rows, base, stat, value):
    """(reference price, explanation) that one of our rolls supports, or None."""
    eligible = [r for r in rows if _at_least_as_good(r, base, ())]
    supporters = [r for r in eligible if _roll_value(r, stat) >= value]
    if not supporters:
        return None
    cheapest = _sane_min([r.price for r in supporters])
    match = min((r for r in supporters if r.price == cheapest), key=lambda r: _roll_value(r, stat))
    theirs = _roll_value(match, stat)
    if value <= 0 or theirs <= value * CLOSE_ROLL_RATIO:
        return cheapest, f"cheapest listing with it at least as good: {cheapest}g"
    # Only much stronger rolls are listed: scale by roll strength, floored by weaker listings.
    estimate = cheapest * value / theirs
    weaker = [r.price for r in eligible if value > _roll_value(r, stat) > float("-inf")]
    if weaker:
        estimate = max(estimate, max(weaker))
    return estimate, f"scaled from a {stat} {theirs} listing at {cheapest}g"


def _best_single_roll(rows, base, rolls):
    """((stat, value), reference, explanation) for our roll the market values most, or None.

    A buyer after one of our rolls pays at least what the cheapest comparable listing with
    that roll costs, so the item's value is set by whichever roll supports the highest price.
    """
    best = None
    for stat, value in rolls:
        found = _single_roll_reference(rows, base, stat, value)
        if found and (best is None or found[0] > best[1]):
            best = ((stat, value), found[0], found[1])
    return best


def _undercut(reference, rules) -> int:
    return math.floor(reference * (1 - rules.undercut_pct / 100))


def _reference(item_id, base, rolls, same_rows, all_rows, rules):
    """Return (price, flag, compared) or None when nobody sells this item."""
    rows = _unique(r for r in list(same_rows) + list(all_rows) if r.item_id == item_id)
    if not rows:
        return None
    roll_set = {stat for stat, _ in rolls}
    similar = [r for r in rows if {s for s, _ in r.rolls} == roll_set]
    better = [r for r in similar if _at_least_as_good(r, base, rolls)]
    if better:
        cheapest = _sane_min([r.price for r in better])
        compared = f"{len(similar)} listings with the same rolls; cheapest at least as good: {cheapest}g"
        return _undercut(cheapest, rules), "", compared
    if similar:
        top = max(r.price for r in similar)
        flag = f"Your rolls are better than every similar listing (best asks {top}g) — set this price yourself."
        return top, flag, f"{len(similar)} listings with the same rolls, all worse"
    best = _best_single_roll(rows, base, rolls)
    if best is not None:
        (stat, value), reference, how = best
        compared = f"matched on your best roll: {stat} {value} ({how})"
        flag = "Priced from your best single roll (no exact roll match) — check this price."
        return _undercut(reference, rules), flag, compared
    cheapest = _sane_min([r.price for r in rows])
    flag = "There are no listings with the same rolls — priced against all rolls; check this price."
    return _undercut(cheapest, rules), flag, f"{len(rows)} listings of any roll; cheapest {cheapest}g"


def price_from_market(item_id, base, rolls, vendor_price, same_rows, all_rows, rules) -> RollPrice:
    found = _reference(item_id, tuple(base), tuple(rolls), same_rows, all_rows, rules)
    if found is None:
        return RollPrice(False, None, 0, NO_SELLERS_REASON)
    price, flag, compared = found
    if price < max(rules.min_price, 1):
        return RollPrice(False, None, 0, "below min price", flag, compared)
    fee = listing_fee(price)
    net = price - fee
    if net <= int(vendor_price or 0):
        return RollPrice(False, None, 0, "vendor pays more", flag, compared)
    if net / price < rules.min_net_ratio:
        return RollPrice(False, None, 0, "fee too high", flag, compared)
    return RollPrice(True, price, fee, "ok", flag, compared)
