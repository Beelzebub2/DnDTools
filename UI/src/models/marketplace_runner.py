"""Drives the Marketplace 'List an Item' flow one plan entry at a time."""
from dataclasses import asdict, dataclass
from typing import Protocol

from src.models.marketplace_layout import spot_location, tab_icon_index
from src.models.marketplace_state import ITEM_LEVEL_FAIL_CODES, describe_fail_code

MAX_PAGES = 4


class InputDriver(Protocol):
    def click(self, x: int, y: int) -> None: ...
    def move_to(self, x: int, y: int) -> None: ...
    def clear_and_type(self, text: str) -> None: ...


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
                 safety=None, register_timeout=5.0, confirm_timeout=3.0):
        self._driver = driver
        self._layout = layout
        self._state = state
        self._tab_mapping = list(tab_mapping)
        self._is_cancelled = is_cancelled
        self._pause = pause
        self._safety = safety or _NullSafety()
        self._register_timeout = register_timeout
        self._confirm_timeout = confirm_timeout
        self._page = 0

    def run(self, entries, dry_run=False, on_progress=None) -> RunReport:
        snapshot = self._state.snapshot()
        if snapshot is None:
            return RunReport((), "Open Trade → Marketplace → My Listings in the game first.")
        results, used, self._page = [], snapshot.used, 0
        current = None
        try:
            for entry in entries:
                current = entry
                result = self._list_one(entry, used, snapshot.available, dry_run)
                results.append(result)
                if on_progress:
                    on_progress(result)
                if result.status in ("listed", "dry_run"):
                    used += 1
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

    def _check(self):
        if self._is_cancelled():
            reason = self._safety.reason
            raise _Stop(f"Stopped for safety: {reason}" if reason else "Cancelled")

    def _click(self, point):
        self._check()
        self._driver.click(*point)
        self._pause()

    def _go_to_spot(self, index, available):
        page, row = spot_location(index)
        if page >= MAX_PAGES:
            raise _Stop("No free listing spots left.")
        if available and index not in available:
            raise _Stop("Listing spots are not laid out as expected — check My Listings.")
        while self._page < page:
            self._click(self._layout.point("next_page_arrow"))
            self._page += 1
        self._click(self._layout.spot_row(row))

    def _fill_form(self, entry):
        icon = tab_icon_index(entry.stash_id, self._tab_mapping)
        if icon is None:
            raise _Stop(f"Stash tab for {entry.name} is not mapped in DnDTools settings.")
        self._click(self._layout.tab_icon(icon))
        self._click(self._layout.item_centre(entry.stash_id, entry.slot_id, entry.width, entry.height))
        self._click(self._layout.point("price_field"))
        self._check()
        self._driver.clear_and_type(str(entry.price))
        self._pause()

    def _stop_with(self, result, message):
        stop = _Stop(message)
        stop.results = [result]
        return stop

    def _submit(self, entry):
        self._state.begin_register()
        since = self._state.now()
        self._check()
        self._driver.click(*self._layout.point("create_listing_button"))
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

    def _list_one(self, entry, used, available, dry_run) -> ItemResult:
        if not self._safety.checkpoint():
            raise _Stop(f"Stopped for safety: {self._safety.reason or 'game lost focus'}")
        self._go_to_spot(used, available)
        self._fill_form(entry)
        self._safety.snapshot_position()
        if dry_run:
            return ItemResult(entry.unique_id, entry.name, "dry_run", f"would list at {entry.price}g")
        return self._submit(entry)
