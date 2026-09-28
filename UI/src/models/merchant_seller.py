"""Pure rules for selling the lister's leftovers to a merchant (no I/O)."""
from dataclasses import dataclass

from src.market_lister import plan_entry
from src.models.market_rules import CURRENCY_ITEM_PREFIXES, Candidate

SELL_BOX_COLUMNS, SELL_BOX_ROWS = 10, 6  # the merchant's Sell box, in cells
GOLD_REFUSAL = "gold is never sold"
MISSING_REFUSAL = "not found in the chosen stash tabs"
NOT_TRADABLE_REFUSAL = "not tradable — usually a quest item, so it's kept (sell it by hand if you're sure)"


def _is_tradable(item) -> bool:
    """Tradable items carry tradable=1; quest items (e.g. Huntress' emblems) have no flag."""
    return (item.get("originalData") or {}).get("tradable") == 1


def merchant_value(entry) -> int:
    """Gold a merchant pays for the whole stack."""
    return entry.vendor_price * entry.quantity


def resolve_sell_entries(stashes: dict, unique_ids, allowed_stash_ids):
    """(entries, refused) for the requested items as they sit in the stash right now.

    Positions always come from the stash data, never from the caller. refused holds
    (unique_id, reason) for gold, non-tradable items and items not in the allowed tabs.
    """
    found = {}
    for stash_id in allowed_stash_ids:
        for item in stashes.get(str(stash_id)) or []:
            found.setdefault(str(item.get("itemUniqueId")), (str(stash_id), item))
    entries, refused, seen = [], [], set()
    for raw in unique_ids:
        uid = str(raw)
        if uid in seen:
            continue
        seen.add(uid)
        if uid not in found:
            refused.append((uid, MISSING_REFUSAL))
            continue
        stash_id, item = found[uid]
        if str(item.get("itemId") or "").startswith(CURRENCY_ITEM_PREFIXES):
            refused.append((uid, GOLD_REFUSAL))
            continue
        if not _is_tradable(item):
            refused.append((uid, NOT_TRADABLE_REFUSAL))
            continue
        entries.append(plan_entry(Candidate(stash_id, item)))
    return entries, refused


@dataclass(frozen=True)
class Placement:
    """Where one item goes in the Sell box (its top-left cell)."""
    entry: object
    col: int
    row: int


def _cells(col, row, width, height):
    return frozenset((col + dx, row + dy) for dx in range(width) for dy in range(height))


def _first_fit(occupied, width, height, columns, rows):
    for row in range(rows - height + 1):
        for col in range(columns - width + 1):
            if not occupied & _cells(col, row, width, height):
                return col, row
    return None


def pack_sell_box(entries, columns=SELL_BOX_COLUMNS, rows=SELL_BOX_ROWS):
    """(batches, too_big): each batch fills one Sell box without overlaps, in the given order.

    Items that don't fit wait for the next batch; items larger than an empty box are too_big.
    """
    too_big = tuple(e for e in entries if e.width > columns or e.height > rows)
    pending = [e for e in entries if e not in too_big]
    batches = []
    while pending:
        occupied, batch, rest = frozenset(), [], []
        for entry in pending:
            spot = _first_fit(occupied, entry.width, entry.height, columns, rows)
            if spot is None:
                rest.append(entry)
                continue
            occupied = occupied | _cells(*spot, entry.width, entry.height)
            batch.append(Placement(entry, *spot))
        batches.append(tuple(batch))
        pending = rest
    return batches, too_big


@dataclass(frozen=True)
class SaleOutcome:
    sold: tuple        # entries the merchant took
    not_taken: tuple   # staged entries still in the stash
    unexpected: tuple  # unique ids sold that were never staged (a wrong item was dragged)


def sale_outcome(staged, deleted_ids) -> SaleOutcome:
    deleted = {str(i) for i in deleted_ids or ()}
    staged_ids = {e.unique_id for e in staged}
    return SaleOutcome(
        sold=tuple(e for e in staged if e.unique_id in deleted),
        not_taken=tuple(e for e in staged if e.unique_id not in deleted),
        unexpected=tuple(sorted(deleted - staged_ids)),
    )
