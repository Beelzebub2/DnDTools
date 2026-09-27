"""Builds the auto market lister's reviewable listing plan."""
from dataclasses import asdict, dataclass, replace

from src.models.market_rules import Skip, compute_price, listing_fee, rarity_id, select_candidates
from src.models.marketplace_layout import tab_icon_index

MAX_LISTING_PRICE = 1_000_000
STALE_DATA_SECONDS = 300
UNMAPPED_TAB_REASON = "stash tab not mapped in DnDTools settings"


class PlanError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
        vendor_price=int(item.get("vendor_price") or 0), item_id=str(item.get("itemId") or ""),
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


NO_SELLERS_REASON = "nobody is selling this right now"


def _game_price_check(entry, rows):
    """Turn one page of View Market results (cheapest first) into a price-check summary."""
    prices = [price for item_id, price in rows if not entry.item_id or item_id == entry.item_id]
    if not prices:
        return None
    return {"success": True, "has_data": True, "lowest_ask": min(prices),
            "avg_price": sum(prices) / len(prices), "num_listings": len(prices)}


def apply_game_prices(entries, rows_by_unique_id, rules) -> Plan:
    """Price unpriced entries from in-game search results keyed by unique_id."""
    priced, skipped = [], []
    for entry in entries:
        check = _game_price_check(entry, rows_by_unique_id.get(entry.unique_id) or [])
        decision = compute_price(check, entry.vendor_price, rules) if check else None
        if decision is None:
            skipped.append(Skip(entry.name, entry.stash_id, entry.slot_id, NO_SELLERS_REASON))
        elif not decision.ok:
            skipped.append(Skip(entry.name, entry.stash_id, entry.slot_id, decision.reason))
        else:
            priced.append(replace(entry, price=decision.price, fee=decision.fee))
    warnings = tuple(_explain(priced, skipped))
    return Plan(tuple(priced), tuple(skipped), warnings)


def _explain(entries, skipped):
    """User-facing hints so an empty or thin plan never looks like nothing happened."""
    hints = []
    if any(s.reason == UNMAPPED_TAB_REASON for s in skipped):
        hints.append("Some stash tabs aren't mapped — set them in Settings → Stash Tab Mapping "
                     "so the lister knows which icon opens which stash.")
    if not entries:
        hints.append(f"No items to list — {len(skipped)} skipped. Open \"Skipped\" below to see why.")
    return hints
