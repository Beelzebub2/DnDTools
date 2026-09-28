"""Sells items to a merchant: stage them in the Sell box, Make Deal, confirm from the game's reply."""
from typing import Protocol

from src.market_lister import UNMAPPED_TAB_REASON
from src.models.marketplace_layout import tab_icon_index
from src.models.marketplace_runner import (
    CURSOR_DEVIATION_PX, MOUSE_MOVED, ItemResult, NullSafety, RunReport, friendly_reason,
)
from src.models.merchant_seller import merchant_value, pack_sell_box, sale_outcome
from src.models.merchant_state import SELL_SUCCESS

MERCHANT_KEY = "TheCollector"   # Id_Merchant_TheCollector; he took every kind of loot tried in game
MERCHANT_NAME = "The Collector"
MERCHANT_CARD_INDEX = 2         # Merchants & Travelers: Alchemist, Tavern Master, The Collector, ...
MERCHANT_OPEN_TIMEOUT_S = 5.0
SELL_REPLY_TIMEOUT_S = 6.0
NOTHING_TO_SELL = "Nothing to sell."
TOO_BIG = "too big for the sell box"
NOT_AT_MERCHANT = (f"Couldn't open {MERCHANT_NAME} — show the lobby in the game (close any merchant, "
                   "Marketplace or menu window) and try again.")
NO_DEAL_REPLY = f"The game didn't confirm the sale — check {MERCHANT_NAME}'s Buyback tab before trying again."
ITEMS_LEFT_STAGED = " Nothing in the sell box was sold — press Escape in the game to put the items back."


class MerchantInput(Protocol):
    def click(self, x: int, y: int) -> None: ...
    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None: ...
    def press_escape(self) -> None: ...
    def position(self) -> tuple[int, int]: ...


class _Stop(Exception):
    pass


def _by_tab(batch):
    """Placements grouped by stash tab, tabs in order of first use."""
    groups = {}
    for placement in batch:
        groups.setdefault(placement.entry.stash_id, []).append(placement)
    return list(groups.items())


class MerchantRunner:
    def __init__(self, driver, layout, state, *, tab_mapping, is_cancelled, pause, safety=None,
                 open_timeout=MERCHANT_OPEN_TIMEOUT_S, reply_timeout=SELL_REPLY_TIMEOUT_S):
        self._driver = driver
        self._layout = layout
        self._state = state
        self._tab_mapping = list(tab_mapping)
        self._is_cancelled = is_cancelled
        self._pause = pause
        self._safety = safety or NullSafety()
        self._open_timeout = open_timeout
        self._reply_timeout = reply_timeout
        self._last_point = None
        self._open_tab = None

    def sell(self, entries, dry_run=False, on_progress=None) -> RunReport:
        """Sell `entries` (PlanEntry) to the merchant; a dry run stages the first batch, then puts it back."""
        results = []

        def record(result):
            results.append(result)
            if on_progress:
                on_progress(result)
        if not entries:
            return RunReport((), NOTHING_TO_SELL)
        batches = self._batches(entries, record)
        try:
            if batches:
                self._safety_checkpoint()
                self._open_merchant()
                self._click(self._layout.point("merchant_sell_tab"))
                self._click(self._layout.point("merchant_sell_mode"))  # Make Deal sells, never buys back
            for batch in batches:
                self._stage(batch)
                if dry_run:
                    for placement in batch:
                        entry = placement.entry
                        record(ItemResult(entry.unique_id, entry.name, "dry_run",
                                          f"staged — {MERCHANT_NAME} would pay {merchant_value(entry)}g"))
                    self._leave()
                    return RunReport(tuple(results), None)
                self._deal(batch, record)
            if batches:
                self._leave()
        except _Stop as stop:
            return RunReport(tuple(results), str(stop))
        return RunReport(tuple(results), None)

    def _batches(self, entries, record):
        """Sell-box batches for the entries that can be sold; the rest are reported as failed."""
        sellable = []
        for entry in entries:
            if tab_icon_index(entry.stash_id, self._tab_mapping) is None:
                record(ItemResult(entry.unique_id, entry.name, "failed", UNMAPPED_TAB_REASON))
            else:
                sellable.append(entry)
        batches, too_big = pack_sell_box(sellable)
        for entry in too_big:
            record(ItemResult(entry.unique_id, entry.name, "failed", TOO_BIG))
        return batches

    def _open_merchant(self):
        since = self._state.now()
        self._click(self._layout.point("merchants_tab"))
        self._click(self._layout.merchant_card(MERCHANT_CARD_INDEX))
        if not self._state.wait_for_merchant(MERCHANT_KEY, since, self._open_timeout):
            raise _Stop(NOT_AT_MERCHANT)
        self._open_tab = None  # the window opens on whichever tab the game remembers

    def _stage(self, batch):
        dragged = 0
        try:
            for stash_id, placements in _by_tab(batch):
                icon = tab_icon_index(stash_id, self._tab_mapping)
                if icon != self._open_tab:
                    self._click(self._layout.tab_icon(icon))
                    self._open_tab = icon
                for placement in placements:
                    entry = placement.entry
                    self._drag(self._layout.item_centre(entry.stash_id, entry.slot_id, entry.width, entry.height),
                               self._layout.sell_box_centre(placement.col, placement.row, entry.width, entry.height))
                    dragged += 1
        except _Stop as stop:
            raise _Stop(f"{stop}{ITEMS_LEFT_STAGED}" if dragged else str(stop)) from None

    def _deal(self, batch, record):
        entries = [placement.entry for placement in batch]
        try:
            self._check()
            self._safety_checkpoint()
            self._check_mouse_still()
        except _Stop as stop:
            raise _Stop(f"{stop}{ITEMS_LEFT_STAGED}") from None
        since = self._state.now()
        self._click(self._layout.point("merchant_make_deal"))
        reply = self._state.wait_for_sell_back(since, self._reply_timeout)
        if reply is None:
            raise _Stop(NO_DEAL_REPLY)
        if reply.result != SELL_SUCCESS:
            raise _Stop(f"{MERCHANT_NAME} refused the deal (game code {reply.result}).{ITEMS_LEFT_STAGED}")
        outcome = sale_outcome(entries, reply.deleted_ids)
        for entry in outcome.sold:
            record(ItemResult(entry.unique_id, entry.name, "sold", f"{merchant_value(entry)}g"))
        for entry in outcome.not_taken:
            record(ItemResult(entry.unique_id, entry.name, "not_taken",
                              f"{MERCHANT_NAME} didn't take it — it's still in your stash"))
        if outcome.unexpected:
            raise _Stop(f"Sold an item that wasn't picked (id {', '.join(outcome.unexpected)}) — "
                        f"buy it back from {MERCHANT_NAME}'s Buyback tab now.")

    def _leave(self):
        self._driver.press_escape()  # back to the merchant grid; staged items go back to the stash
        self._pause()

    def _check(self):
        if self._is_cancelled():
            reason = self._safety.reason
            raise _Stop(f"Stopped for safety: {friendly_reason(reason)}" if reason else "Cancelled")

    def _safety_checkpoint(self):
        if not self._safety.checkpoint():
            raise _Stop(f"Stopped for safety: {friendly_reason(self._safety.reason) or 'the game lost focus'}")

    def _check_mouse_still(self):
        """Stop when the cursor left the spot we last used: someone took the mouse."""
        if self._last_point is None:
            return
        (px, py), (cx, cy) = self._last_point, self._driver.position()
        if abs(cx - px) > CURSOR_DEVIATION_PX or abs(cy - py) > CURSOR_DEVIATION_PX:
            raise _Stop(MOUSE_MOVED)

    def _click(self, point):
        self._check()
        self._driver.click(*point)
        self._last_point = point
        self._safety.snapshot_position()
        self._pause()

    def _drag(self, source, target):
        self._check()
        self._safety_checkpoint()
        self._check_mouse_still()
        self._driver.drag(*source, *target)
        self._last_point = target
        self._safety.snapshot_position()
        self._pause()
