"""Builds the auto market lister's reviewable listing plan."""
from dataclasses import asdict, dataclass

from src.models.market_rules import Skip, compute_price, listing_fee, rarity_id, select_candidates
from src.models.marketplace_layout import tab_icon_index

MAX_LISTING_PRICE = 1_000_000
STALE_DATA_SECONDS = 300


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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PlanEntry":
        if not isinstance(data, dict) or not str(data.get("unique_id") or "").strip():
            raise ValueError("entry is missing unique_id")
        price = _positive_int(data.get("price"), "price", 1, MAX_LISTING_PRICE)
        return cls(
            unique_id=str(data["unique_id"]),
            name=str(data.get("name") or "?")[:128],
            rarity=_positive_int(data.get("rarity", 0), "rarity", 0, 8),
            stash_id=str(data.get("stash_id") or ""),
            slot_id=_positive_int(data.get("slot_id"), "slot_id", 0, 239),
            width=_positive_int(data.get("width", 1), "width", 1, 4),
            height=_positive_int(data.get("height", 1), "height", 1, 4),
            price=price,
            fee=listing_fee(price),
            vendor_price=_positive_int(data.get("vendor_price", 0), "vendor_price", 0),
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


def _entry(candidate, decision) -> PlanEntry:
    item = candidate.item
    return PlanEntry(
        unique_id=str(item.get("itemUniqueId")), name=item.get("name", "?"),
        rarity=rarity_id(item.get("rarity")), stash_id=candidate.stash_id,
        slot_id=int(item.get("slotId", 0)), width=int(item.get("width") or 1),
        height=int(item.get("height") or 1), price=decision.price, fee=decision.fee,
        vendor_price=int(item.get("vendor_price") or 0),
    )


def _skip(candidate, reason) -> Skip:
    return Skip(candidate.item.get("name", "?"), candidate.stash_id, int(candidate.item.get("slotId", 0)), reason)


def _limit(rules, free_spots):
    return rules.max_items_per_run if free_spots is None else min(rules.max_items_per_run, max(free_spots, 0))


def build_plan(stashes, rules, price_lookup, *, tab_mapping, free_spots, data_age_s, pause=lambda: None) -> Plan:
    candidates, skipped = select_candidates(stashes, rules)
    warnings, entries = [], []
    if data_age_s is not None and data_age_s > STALE_DATA_SECONDS:
        warnings.append(f"Stash data is {int(data_age_s // 60)} minutes old — reopen your character to refresh.")
    limit = _limit(rules, free_spots)
    for candidate in candidates:
        if len(entries) >= limit:
            break
        if tab_icon_index(candidate.stash_id, tab_mapping) is None:
            skipped.append(_skip(candidate, "stash tab not mapped in DnDTools settings"))
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
    return Plan(tuple(entries), tuple(skipped), tuple(warnings))
