"""Builds the auto market lister's reviewable listing plan."""
from dataclasses import asdict, dataclass, replace

from src.models.market_rules import Skip, compute_price, listing_fee, rarity_id, select_candidates
from src.models.marketplace_layout import tab_icon_index
from src.models.roll_pricing import EXTRA_ROLL_SHARE, price_from_market

MAX_LISTING_PRICE = 1_000_000
STALE_DATA_SECONDS = 300
UNMAPPED_TAB_REASON = "stash tab not mapped in DnDTools settings"


class PlanError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


MAX_STATS = 16
CONFIDENCE_LEVELS = ("high", "medium", "low")
MAX_QUANTITY = 999


def stat_pairs(raw) -> tuple:
    """[[stat, value], ...] from JSON / enhanced items -> ((stat, int), ...); junk is dropped."""
    pairs = []
    for pair in raw if isinstance(raw, (list, tuple)) else ():
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(pair[1], (int, float))                 and not isinstance(pair[1], bool):
            pairs.append((str(pair[0])[:64], int(pair[1])))
    return tuple(pairs[:MAX_STATS])


def _positive_int(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is out of range")
    return value


@dataclass(frozen=True)
class PlanEntry:
    unique_id: str
    name: str
    rarity: int
    stash_id: str
    slot_id: int
    width: int
    height: int
    price: int
    fee: int
    vendor_price: int
    item_id: str = ""
    base_rolls: tuple = ()   # ((stat, value), ...) primary properties of our copy
    rolls: tuple = ()        # ((stat, value), ...) random secondary properties
    flag: str = ""           # pricing warning for the user to review
    compared: str = ""       # what the price was compared against
    confidence: str = ""     # "high" | "medium" | "low" — how well the market backs the price
    quantity: int = 1        # stack size to list (the price is for the whole stack)
    recommended: int = 0     # the price the lister computed (differs from `price` if the user edited it)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict, allow_unpriced: bool = False) -> "PlanEntry":
        if not isinstance(data, dict) or not str(data.get("unique_id") or "").strip():
            raise ValueError("entry is missing unique_id")
        price = _positive_int(data.get("price"), "price", 0 if allow_unpriced else 1, MAX_LISTING_PRICE)
        return cls(
            unique_id=str(data["unique_id"]),
            name=str(data.get("name") or "?")[:128],
            rarity=_positive_int(data.get("rarity", 0), "rarity", 0, 8),
            stash_id=str(data.get("stash_id") or ""),
            slot_id=_positive_int(data.get("slot_id"), "slot_id", 0, 239),
            width=_positive_int(data.get("width", 1), "width", 1, 4),
            height=_positive_int(data.get("height", 1), "height", 1, 4),
            price=price,
            fee=listing_fee(price) if price else 0,
            vendor_price=_positive_int(data.get("vendor_price", 0), "vendor_price", 0),
            item_id=str(data.get("item_id") or "")[:128],
            base_rolls=stat_pairs(data.get("base_rolls")),
            rolls=stat_pairs(data.get("rolls")),
            flag=str(data.get("flag") or "")[:300],
            compared=str(data.get("compared") or "")[:300],
            confidence=str(data.get("confidence") or "") if data.get("confidence") in CONFIDENCE_LEVELS else "",
            quantity=_positive_int(data.get("quantity", 1), "quantity", 1, MAX_QUANTITY),
            recommended=_positive_int(data.get("recommended", 0), "recommended", 0, MAX_LISTING_PRICE),
        )


@dataclass(frozen=True)
class Plan:
    entries: tuple
    skipped: tuple
    warnings: tuple

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "skipped": [asdict(s) for s in self.skipped],
            "warnings": list(self.warnings),
        }


def _entry(candidate, decision=None) -> PlanEntry:
    """A plan entry; without a decision it is unpriced (price 0) until the game prices it."""
    item = candidate.item
    return PlanEntry(
        unique_id=str(item.get("itemUniqueId")), name=item.get("name", "?"),
        rarity=rarity_id(item.get("rarity")), stash_id=candidate.stash_id,
        slot_id=int(item.get("slotId", 0)), width=int(item.get("width") or 1),
        height=int(item.get("height") or 1),
        price=decision.price if decision else 0, fee=decision.fee if decision else 0,
        recommended=decision.price if decision else 0,
        vendor_price=int(item.get("vendor_price") or 0), item_id=str(item.get("itemId") or ""),
        base_rolls=stat_pairs(item.get("pp")), rolls=stat_pairs(item.get("sp")),
        quantity=max(int(item.get("itemCount") or 1), 1),
    )


def _skip(candidate, reason) -> Skip:
    return Skip(candidate.item.get("name", "?"), candidate.stash_id, int(candidate.item.get("slotId", 0)), reason)


def _limit(rules, free_spots):
    return rules.max_items_per_run if free_spots is None else min(rules.max_items_per_run, max(free_spots, 0))


def _drop_listed(candidates, skipped, exclude_unique_ids):
    kept = []
    for candidate in candidates:
        if str(candidate.item.get("itemUniqueId")) in exclude_unique_ids:
            skipped.append(_skip(candidate, "already listed"))
        else:
            kept.append(candidate)
    return kept


def build_plan(stashes, rules, price_lookup, *, tab_mapping, free_spots, data_age_s, pause=lambda: None,
               exclude_unique_ids: frozenset = frozenset()) -> Plan:
    candidates, skipped = select_candidates(stashes, rules)
    candidates = _drop_listed(candidates, skipped, exclude_unique_ids)
    warnings, entries = [], []
    if data_age_s is not None and data_age_s > STALE_DATA_SECONDS:
        warnings.append(f"Stash data is {int(data_age_s // 60)} minutes old — reopen your character to refresh.")
    limit = _limit(rules, free_spots)
    for candidate in candidates:
        if len(entries) >= limit:
            break
        if tab_icon_index(candidate.stash_id, tab_mapping) is None:
            skipped.append(_skip(candidate, UNMAPPED_TAB_REASON))
            continue
        if price_lookup is None:
            entries.append(_entry(candidate))
            continue
        if int(candidate.item.get("itemCount") or 1) > 1:  # DarkerDB quotes one unit, not the stack
            skipped.append(_skip(candidate, STACK_NEEDS_GAME_PRICING))
            continue
        check = price_lookup(candidate.item)
        pause()
        error = (check or {}).get("error_code")
        if error == "missing_api_key":
            raise PlanError(error, "Add your DarkerDB API key (DARKERDB_API_KEY) to price items.")
        if error == "rate_limited":
            warnings.append("DarkerDB rate limit hit — plan is partial. Try again in a minute.")
            break
        decision = compute_price(check, int(candidate.item.get("vendor_price") or 0), rules)
        if decision.ok:
            entries.append(_entry(candidate, decision))
        else:
            skipped.append(_skip(candidate, decision.reason))
    if free_spots is not None and len(entries) >= free_spots and len(candidates) > len(entries):
        warnings.append(f"Only {free_spots} free listing spots — some items were left out.")
    if price_lookup is None and entries:
        warnings.append("Prices will come from the in-game market — click \"Price from game\".")
    warnings.extend(_explain(entries, skipped))
    return Plan(tuple(entries), tuple(skipped), tuple(warnings))


NOT_PRICED_REASON = "not priced — the pricing run stopped first"
STACK_NEEDS_GAME_PRICING = "stacks are priced per unit from the game market — use Price from game"
ABOVE_MAX_REASON = "price would be above the game's maximum listing price"


def apply_game_prices(entries, market_by_unique_id, rules, extra_rows=None,
                      extra_share=EXTRA_ROLL_SHARE, exclude_listing_ids=frozenset(), synergies=None,
                      worth=None) -> Plan:
    """Price unpriced entries from in-game search results, comparing like rolls with like.

    market_by_unique_id: {unique_id: {"same": [MarketRow], "all": [MarketRow]}}.
    extra_rows(item_id): recent listings from the local market history to widen the view.
    exclude_listing_ids: our own listings, which must not set our prices.
    worth(entry): the Item Worth estimate for the entry's exact rolls (value and floor), a plain value, or
    None; the value caps the price and the floor sets the fast-sale price.
    """
    priced, skipped = [], []
    for entry in entries:
        market = market_by_unique_id.get(entry.unique_id)
        if market is None:
            skipped.append(Skip(entry.name, entry.stash_id, entry.slot_id, NOT_PRICED_REASON))
            continue
        history = list(extra_rows(entry.item_id)) if extra_rows else []

        def others(rows):
            return [r for r in rows if r.listing_id not in exclude_listing_ids]
        result = price_from_market(entry.item_id, entry.base_rolls, entry.rolls, entry.vendor_price,
                                   others(market.get("same") or []), others((market.get("all") or []) + history),
                                   rules, extra_share=extra_share, quantity=entry.quantity, synergies=synergies,
                                   **_model_prices(worth(entry) if worth else None))
        if result.ok and result.price > MAX_LISTING_PRICE:
            skipped.append(Skip(entry.name, entry.stash_id, entry.slot_id, ABOVE_MAX_REASON,
                                result.flag, result.confidence))
        elif result.ok:
            priced.append(replace(entry, price=result.price, fee=result.fee, flag=result.flag,
                                  compared=result.compared, confidence=result.confidence,
                                  recommended=result.price))
        else:
            skipped.append(Skip(entry.name, entry.stash_id, entry.slot_id, result.reason,
                                result.flag, result.confidence))
    warnings = list(_explain(priced, skipped))
    flagged = sum(1 for e in priced if e.flag)
    if flagged:
        warnings.insert(0, f"{flagged} price(s) marked ⚠️ need your check before listing.")
    return Plan(tuple(priced), tuple(skipped), tuple(warnings))


def _model_prices(estimate):
    """price_from_market keywords from an Item Worth estimate (or a bare value)."""
    if estimate is None:
        return {}
    if isinstance(estimate, (int, float)):
        return {"model_value": estimate}
    return {"model_value": estimate.value, "model_floor": getattr(estimate, "floor", None)}


def _explain(entries, skipped):
    """User-facing hints so an empty or thin plan never looks like nothing happened."""
    hints = []
    if any(s.reason == UNMAPPED_TAB_REASON for s in skipped):
        hints.append("Some stash tabs aren't mapped — set them in Settings → Stash Tab Mapping "
                     "so the lister knows which icon opens which stash.")
    if not entries:
        hints.append(f"No items to list — {len(skipped)} skipped. Open \"Skipped\" below to see why.")
    return hints
