"""Pure rules and pricing for the auto market lister (no I/O)."""
import math
from dataclasses import dataclass, field

INVENTORY_STASH_ID = "2"
LISTING_FEE_RATE = 0.05
LISTING_FEE_MIN = 15
MAX_UNDERCUT_PCT = 90.0
LOWEST_ASK_MIN_RATIO = 0.5
# Gold coins and their containers (bags, purses, pouches, chests) plus silver are currency.
CURRENCY_ITEM_PREFIXES = ("GoldCoin", "SilverCoin")

_RARITY_IDS = {
    "poor": 1, "common": 2, "uncommon": 3, "rare": 4, "epic": 5,
    "legend": 6, "legendary": 6, "unique": 7, "artifact": 8,
}


def listing_fee(price: int) -> int:
    return max(LISTING_FEE_MIN, math.ceil(price * LISTING_FEE_RATE))


def rarity_id(value) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    return _RARITY_IDS.get(str(value).strip().lower(), 0)


def _clamp(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


PRICE_SOURCES = ("live", "database", "model")  # search in game / saved market data / value formula


@dataclass(frozen=True)
class ListerRules:
    source_stash_ids: tuple = (INVENTORY_STASH_ID,)
    min_rarity: int = 4
    min_price: int = 100
    undercut_pct: float = 10.0
    min_listings: int = 3
    max_items_per_run: int = 20
    exclude_item_ids: frozenset = field(default_factory=frozenset)
    min_net_ratio: float = 0.5
    allow_stacks: bool = False   # list stackable items (priced per unit x stack size)
    price_source: str = "live"   # where prices come from, see PRICE_SOURCES

    @classmethod
    def from_dict(cls, data: dict) -> "ListerRules":
        data = data if isinstance(data, dict) else {}
        d = cls()
        sources = data.get("source_stash_ids") or d.source_stash_ids
        return cls(
            source_stash_ids=tuple(str(s) for s in sources),
            min_rarity=rarity_id(data.get("min_rarity", d.min_rarity)) or d.min_rarity,
            min_price=int(_clamp(data.get("min_price"), 0, 10_000_000, d.min_price)),
            undercut_pct=_clamp(data.get("undercut_pct"), 0, MAX_UNDERCUT_PCT, d.undercut_pct),
            min_listings=int(_clamp(data.get("min_listings"), 0, 1000, d.min_listings)),
            max_items_per_run=int(_clamp(data.get("max_items_per_run"), 1, 40, d.max_items_per_run)),
            exclude_item_ids=frozenset(str(i) for i in data.get("exclude_item_ids") or ()),
            min_net_ratio=_clamp(data.get("min_net_ratio"), 0, 1, d.min_net_ratio),
            allow_stacks=data.get("allow_stacks") is True,
            price_source=data.get("price_source") if data.get("price_source") in PRICE_SOURCES else d.price_source,
        )

    def to_dict(self) -> dict:
        return {
            "source_stash_ids": list(self.source_stash_ids),
            "min_rarity": self.min_rarity,
            "min_price": self.min_price,
            "undercut_pct": self.undercut_pct,
            "min_listings": self.min_listings,
            "max_items_per_run": self.max_items_per_run,
            "exclude_item_ids": sorted(self.exclude_item_ids),
            "min_net_ratio": self.min_net_ratio,
            "allow_stacks": self.allow_stacks,
            "price_source": self.price_source,
        }


# Skip reasons that mean a merchant is the better buyer (the page offers these to "Sell to merchant").
MERCHANT_REASONS = frozenset({"vendor pays more", "below min price", "below minimum rarity"})
MERCHANT_REASON_PREFIX = "a merchant sells it for"


def is_merchant_reason(reason) -> bool:
    reason = str(reason or "")
    return reason in MERCHANT_REASONS or reason.startswith(MERCHANT_REASON_PREFIX)


@dataclass(frozen=True)
class Skip:
    name: str
    stash_id: str
    slot_id: int
    reason: str
    flag: str = ""        # a doubt about the price that led to the skip
    confidence: str = ""
    unique_id: str = ""   # the item's itemUniqueId, so a skipped item can still be sold to a merchant


@dataclass(frozen=True)
class Candidate:
    stash_id: str
    item: dict


def _is_currency(item_id: str) -> bool:
    return item_id.startswith(CURRENCY_ITEM_PREFIXES)


def _skip_reason(item: dict, rules: ListerRules):
    if _is_currency(str(item.get("itemId", ""))):
        return "gold is never listed"
    if int(item.get("max_stack_size") or 1) > 1 and not rules.allow_stacks:
        return "stackable items not supported yet"
    if str(item.get("itemId", "")) in rules.exclude_item_ids:
        return "on your never-sell list"
    if rarity_id(item.get("rarity")) < rules.min_rarity:
        return "below minimum rarity"
    return None


def select_candidates(stashes: dict, rules: ListerRules):
    candidates, skipped = [], []
    for stash_id in rules.source_stash_ids:
        items = sorted(stashes.get(stash_id) or [], key=lambda i: int(i.get("slotId", 0)))
        for item in items:
            reason = _skip_reason(item, rules)
            if reason:
                skipped.append(Skip(item.get("name", "?"), stash_id, int(item.get("slotId", 0)), reason,
                                    unique_id=str(item.get("itemUniqueId") or "")))
            else:
                candidates.append(Candidate(stash_id, item))
    return candidates, skipped


@dataclass(frozen=True)
class PriceDecision:
    ok: bool
    price: int | None
    fee: int
    reason: str


def _no(reason: str) -> PriceDecision:
    return PriceDecision(False, None, 0, reason)


def _positive_number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _reference_price(price_check: dict):
    lowest = _positive_number(price_check.get("lowest_ask"))
    avg = _positive_number(price_check.get("avg_price"))
    if lowest is not None and avg is not None and lowest < LOWEST_ASK_MIN_RATIO * avg:
        lowest = None  # a lone lowball listing shouldn't drag our price down
    refs = [p for p in (lowest, avg) if p is not None]
    return min(refs) if refs else None


def compute_price(price_check, vendor_price: int, rules: ListerRules) -> PriceDecision:
    if not price_check or not price_check.get("success") or not price_check.get("has_data"):
        return _no("no market data")
    if int(price_check.get("num_listings") or 0) < rules.min_listings:
        return _no("not enough market data")
    reference = _reference_price(price_check)
    if reference is None:
        return _no("no market data")
    price = math.floor(reference * (1 - rules.undercut_pct / 100))
    if price < max(rules.min_price, 1):
        return _no("below min price")
    fee = listing_fee(price)
    net = price - fee
    if net <= int(vendor_price or 0):
        return _no("vendor pays more")
    if net / price < rules.min_net_ratio:
        return _no("fee too high")
    return PriceDecision(True, price, fee, "ok")
