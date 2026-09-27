"""Drives the Marketplace 'List an Item' flow one plan entry at a time."""
from dataclasses import asdict, dataclass
from typing import Protocol

from src.models.marketplace_layout import spot_location, tab_icon_index
from src.models.marketplace_state import (
    FIRST_PAGE, ITEM_LEVEL_FAIL_CODES, MAX_SNAPSHOT_AGE_S, MY_ITEM_SOLD, REGISTER_SUCCESS, describe_fail_code,
)

MAX_PAGES = 4
CURSOR_DEVIATION_PX = 120
SEARCH_SETTLE_PAUSES = 4
MAX_PAYOUTS_PER_RUN = 40
CRAWL_RARITIES = (5, 6, 4, 7)  # Epic, Legendary, Rare, Unique
RARITY_NAMES = {1: "Poor", 2: "Common", 3: "Uncommon", 4: "Rare", 5: "Epic", 6: "Legendary",
                7: "Unique", 8: "Artifact"}
NEXT_PAGE_ATTEMPTS = 6
NEXT_PAGE_TIMEOUT_S = 2.0
MARKET_PAGES = 10       # all-roll result pages read per item (cheapest first)
SAME_SEARCH_PAGES = 5   # same-roll search result pages read per item
MARKET_PAGE_SIZE = 10   # listings per View Market page  # the View Market screen needs a moment after each switch
SAFETY_REASON_TEXT = {
    "game_window_unfocused": "the game lost focus",
    "mouse_interference": "the mouse was moved",
}


def _friendly_reason(reason):
    return SAFETY_REASON_TEXT.get(reason, reason)


class InputDriver(Protocol):
    def click(self, x: int, y: int) -> None: ...
    def move_to(self, x: int, y: int) -> None: ...
    def clear_and_type(self, text: str) -> None: ...
    def position(self) -> tuple[int, int]: ...


class Safety(Protocol):
    reason: str | None

    def checkpoint(self) -> bool: ...
    def snapshot_position(self) -> None: ...


class _NullSafety:
    reason = None

    def checkpoint(self) -> bool:
        return True

    def snapshot_position(self) -> None:
        return None


@dataclass(frozen=True)
class ItemResult:
    unique_id: str
    name: str
    status: str
    message: str = ""


@dataclass(frozen=True)
class RunReport:
    results: tuple[ItemResult, ...]
    stopped_reason: str | None

    def to_dict(self) -> dict:
        return {"results": [asdict(r) for r in self.results], "stopped_reason": self.stopped_reason}


class _Stop(Exception):
    pass


class MarketplaceRunner:
    def __init__(self, driver, layout, state, *, tab_mapping, is_cancelled, pause,
                 safety=None, register_timeout=5.0, confirm_timeout=3.0, scan_observer=None):
        self._driver = driver
        self._layout = layout
        self._state = state
        self._tab_mapping = list(tab_mapping)
        self._is_cancelled = is_cancelled
        self._pause = pause
        self._safety = safety or _NullSafety()
        self._register_timeout = register_timeout
        self._confirm_timeout = confirm_timeout
        self._scan_observer = scan_observer  # (item_id, started_at, rows, complete) after each item scan
        self._page = 0
        self._arrow_attempt = 0

    def _refusal(self, snapshot):
        if snapshot is None:
            return "Open Trade → Marketplace → My Listings in the game first."
        if self._state.now() - snapshot.received_at > MAX_SNAPSHOT_AGE_S:
            return "Open (or re-open) Trade → Marketplace → My Listings in the game first."
        if snapshot.current_page != FIRST_PAGE:
            return "Go to page 1 of My Listings, then start again."
        return None

    def _unmapped_message(self, entries):
        for entry in entries:
            if tab_icon_index(entry.stash_id, self._tab_mapping) is None:
                return f"Stash tab for {entry.name} is not mapped in DnDTools settings."
        return None

    def run(self, entries, dry_run=False, on_progress=None) -> RunReport:
        snapshot = self._state.snapshot()
        entries = list(entries)
        refusal = self._refusal(snapshot) or self._unmapped_message(entries)
        if refusal:
            return RunReport((), refusal)
        # availableOrderIndexes lists the free spots (verified in game); use them in order.
        free_spots, consumed, self._page = sorted(snapshot.available), 0, 0
        results = []
        current = None
        try:
            for entry in entries:
                current = entry
                if consumed >= len(free_spots):
                    raise _Stop("No free listing spots left.")
                result = self._list_one(entry, free_spots[consumed], dry_run)
                results.append(result)
                if on_progress:
                    on_progress(result)
                if result.status in ("listed", "dry_run"):
                    consumed += 1
        except _Stop as stop:
            stop_results = list(results) + getattr(stop, "results", [])
            for r in getattr(stop, "results", []):
                if on_progress:
                    on_progress(r)
            return RunReport(tuple(stop_results), str(stop))
        except Exception as exc:
            # Catch unexpected exceptions to preserve already-listed results
            failed_uid = current.unique_id if current else ""
            failed_name = current.name if current else ""
            failed = ItemResult(failed_uid, failed_name, "failed", str(exc))
            results.append(failed)
            if on_progress:
                on_progress(failed)
            return RunReport(tuple(results), f"Stopped: {exc}")
        return RunReport(tuple(results), None)

    def price_all(self, entries, on_progress=None):
        """Look up each entry's current market listings via the in-game Search flow.

        Never clicks Create Listing. Returns ({unique_id: {"same": [MarketRow], "all": [MarketRow]}}, RunReport).
        """
        snapshot = self._state.snapshot()
        entries = list(entries)
        refusal = self._refusal(snapshot) or self._unmapped_message(entries)
        if refusal:
            return {}, RunReport((), refusal)
        if not snapshot.available:
            return {}, RunReport((), "No free listing spots left.")
        spot, rows_by_uid, results = min(snapshot.available), {}, []
        try:
            for entry in entries:
                if not self._safety.checkpoint():
                    raise _Stop(f"Stopped for safety: {_friendly_reason(self._safety.reason) or 'the game lost focus'}")
                found = self._search_market(entry, spot)
                rows_by_uid[entry.unique_id] = found
                total = len(found["same"]) + len(found["all"])
                result = ItemResult(entry.unique_id, entry.name, "priced" if total else "no_results",
                                    f"{len(found['same'])} with the same rolls, {len(found['all'])} of any roll")
                results.append(result)
                if on_progress:
                    on_progress(result)
                self._safety.snapshot_position()
        except _Stop as stop:
            return rows_by_uid, RunReport(tuple(results), str(stop))
        except Exception as exc:
            return rows_by_uid, RunReport(tuple(results), f"Stopped: {exc}")
        return rows_by_uid, RunReport(tuple(results), None)

    def collect_payouts(self, on_progress=None) -> RunReport:
        """Collect gold from sold listings and take back expired items ("Transfer All Items").

        The game destroys uncollected payouts after 7 days. Listings shift up after each
        transfer, so the My Listings tab is re-opened to get fresh positions every time.
        """
        refusal = self._refusal(self._state.snapshot())
        if refusal:
            return RunReport((), refusal)
        results = []
        try:
            for _ in range(MAX_PAYOUTS_PER_RUN):
                snapshot = self._reopen_my_listings()
                if not snapshot.payouts:
                    break
                order_index, state, item_id, price = min(snapshot.payouts)
                self._go_to_spot(order_index)
                self._state.begin_transfer()
                self._click(self._layout.point("transfer_all_button"))
                code = self._state.wait_for_transfer(self._register_timeout)
                if code != REGISTER_SUCCESS:
                    message = "no response from the game" if code is None else describe_fail_code(code)
                    fail = ItemResult(str(order_index), item_id, "failed", message)
                    raise self._stop_with(fail, f"Couldn't collect {item_id}: {message}")
                note = f"{price}g collected" if state == MY_ITEM_SOLD else "expired item returned"
                result = ItemResult(str(order_index), item_id, "collected", note)
                results.append(result)
                if on_progress:
                    on_progress(result)
        except _Stop as stop:
            stop_results = getattr(stop, "results", [])
            for r in stop_results:
                if on_progress:
                    on_progress(r)
            return RunReport(tuple(results + stop_results), str(stop))
        return RunReport(tuple(results), None)

    def crawl_market(self, pages: int, on_progress=None, rarities=CRAWL_RARITIES) -> RunReport:
        """Read the newest listings of every item, one rarity at a time (View Market filter).

        Nothing is bought or listed (the Buy buttons are never clicked); the captured pages
        feed the local market history.
        """
        refusal = self._refusal(self._state.snapshot())
        if refusal:
            return RunReport((), refusal)
        results = []
        try:
            for rarity in rarities:
                read, total = self._crawl_rarity(rarity, pages)
                result = ItemResult(f"rarity-{rarity}", RARITY_NAMES.get(rarity, str(rarity)), "crawled",
                                    f"{read} pages, {total} listings")
                results.append(result)
                if on_progress:
                    on_progress(result)
            self._click(self._layout.point("my_listings_tab"))
        except _Stop as stop:
            return RunReport(tuple(results), str(stop))
        return RunReport(tuple(results), None)

    def _crawl_rarity(self, rarity, pages):
        self._click(self._layout.point("view_market_tab"))
        self._settle()
        self._click(self._layout.point("market_reset_filters"))
        self._settle()
        self._click(self._layout.point("rarity_dropdown"))
        self._settle()
        self._click(self._layout.rarity_option(rarity))
        since = self._state.now()
        self._click(self._layout.point("market_search_button"))
        rows = self._state.wait_for_item_list(since, self._register_timeout) or []
        read, total = 0, 0
        while rows:
            read, total = read + 1, total + len(rows)
            if read >= pages or len(rows) < MARKET_PAGE_SIZE:
                break
            rows = self._next_page() or []
        return read, total

    def _next_page(self):
        """Click the next-page arrow (its position depends on the page counter width)."""
        attempts = [self._arrow_attempt] + [a for a in range(NEXT_PAGE_ATTEMPTS) if a != self._arrow_attempt]
        for attempt in attempts:
            since = self._state.now()
            self._click(self._layout.next_page_candidate(attempt))
            rows = self._state.wait_for_item_list(since, NEXT_PAGE_TIMEOUT_S)
            if rows is not None:
                self._arrow_attempt = attempt
                return rows
        return None

    def _reopen_my_listings(self):
        since = self._state.now()
        self._click(self._layout.point("my_listings_tab"))
        self._page = 0
        return self._state.wait_for_snapshot(since, self._register_timeout)

    def _settle(self):
        for _ in range(SEARCH_SETTLE_PAUSES):
            self._pause()

    def _search_market(self, entry, spot):
        self._page = 0  # returning to My Listings shows page 1 again
        self._go_to_spot(spot)
        self._select_item(entry)
        since = self._state.now()
        # The game pre-fills the search with our item's random attributes: same-roll listings.
        self._click(self._layout.point("form_search_button"))
        same, _ = self._read_pages(self._state.wait_for_item_list(since, self._register_timeout) or [],
                                   SAME_SEARCH_PAGES)
        self._settle()
        self._click(self._layout.point("market_attr_reset"))  # then every roll of this item
        self._settle()
        started = self._state.now()
        self._click(self._layout.point("market_search_button"))
        every, complete = self._read_pages(self._state.wait_for_item_list(started, self._register_timeout) or [],
                                           MARKET_PAGES)
        if self._scan_observer:
            self._scan_observer(entry.item_id, started, every, complete)
        self._click(self._layout.point("my_listings_tab"))
        self._settle()
        return {"same": same, "all": every}

    def _read_pages(self, first_page, max_pages):
        """(rows, complete) — keep paging (cheapest first) until results end or max_pages.

        complete is True only when the results ran out, i.e. every listing was seen.
        """
        rows, size, pages = list(first_page), len(first_page), 1
        while size >= MARKET_PAGE_SIZE and pages < max_pages:
            page = self._next_page()
            if page is None:  # couldn't turn the page
                return rows, False
            rows.extend(page)
            size, pages = len(page), pages + 1
        return rows, size < MARKET_PAGE_SIZE

    def _check(self):
        if self._is_cancelled():
            reason = self._safety.reason
            raise _Stop(f"Stopped for safety: {_friendly_reason(reason)}" if reason else "Cancelled")

    def _click(self, point):
        self._check()
        self._driver.click(*point)
        self._pause()

    def _go_to_spot(self, index):
        page, row = spot_location(index)
        if page >= MAX_PAGES:
            raise _Stop("No free listing spots left.")
        if page < self._page:
            raise _Stop("Listing spots are not laid out as expected — check My Listings.")
        while self._page < page:
            self._click(self._layout.point("next_page_arrow"))
            self._page += 1
        self._click(self._layout.spot_row(row))

    def _select_item(self, entry):
        icon = tab_icon_index(entry.stash_id, self._tab_mapping)
        if icon is None:
            raise _Stop(f"Stash tab for {entry.name} is not mapped in DnDTools settings.")
        self._click(self._layout.tab_icon(icon))
        self._click(self._layout.item_centre(entry.stash_id, entry.slot_id, entry.width, entry.height))

    def _fill_form(self, entry):
        self._select_item(entry)
        self._click(self._layout.point("price_field"))
        self._check()
        self._driver.clear_and_type(str(entry.price))
        self._pause()

    def _stop_with(self, result, message):
        stop = _Stop(message)
        stop.results = [result]
        return stop

    def _check_cursor(self, entry):
        px, py = self._layout.point("price_field")
        cx, cy = self._driver.position()
        if abs(cx - px) > CURSOR_DEVIATION_PX or abs(cy - py) > CURSOR_DEVIATION_PX:
            fail = ItemResult(entry.unique_id, entry.name, "failed", "stopped before Create Listing — no fee charged")
            raise self._stop_with(fail, "Stopped for safety: mouse moved during listing")

    def _submit(self, entry):
        self._check_cursor(entry)
        self._state.begin_register()
        since = self._state.now()
        self._check()
        self._driver.click(*self._layout.point("create_listing_button"))
        self._pause()
        # The game asks "Would you like to list the item?"; the fee is only charged on Yes.
        self._check()
        self._driver.click(*self._layout.point("confirm_listing_yes"))
        outcome = self._state.wait_for_register(self._register_timeout)
        if outcome.status == "timeout":
            fail = ItemResult(entry.unique_id, entry.name, "unconfirmed", "no response — may be listed, fee may have been charged")
            raise self._stop_with(fail, "Listing not confirmed by the game — check the Marketplace.")
        if outcome.status == "failed":
            message = describe_fail_code(outcome.fail_code)
            fail = ItemResult(entry.unique_id, entry.name, "failed", message)
            if outcome.fail_code in ITEM_LEVEL_FAIL_CODES:
                return fail
            raise self._stop_with(fail, message)
        if not self._state.wait_for_listing(entry.unique_id, since, self._confirm_timeout):
            fail = ItemResult(entry.unique_id, entry.name, "unconfirmed", "not seen in My Listings — fee may have been charged")
            raise self._stop_with(
                fail, f"Listed something but couldn't confirm it was {entry.name} — check My Listings and recalibrate.")
        self._pause()
        return ItemResult(entry.unique_id, entry.name, "listed", f"{entry.price}g")

    def _list_one(self, entry, spot_index, dry_run) -> ItemResult:
        if not self._safety.checkpoint():
            raise _Stop(f"Stopped for safety: {_friendly_reason(self._safety.reason) or 'the game lost focus'}")
        self._go_to_spot(spot_index)
        self._fill_form(entry)
        if dry_run:
            result = ItemResult(entry.unique_id, entry.name, "dry_run", f"would list at {entry.price}g")
        else:
            result = self._submit(entry)
        # Snapshot after the last click so the next checkpoint only sees user movement.
        self._safety.snapshot_position()
        return result
