import sys
import time
from dataclasses import replace

import networking.protos

# Generated *_pb2 modules import siblings by bare name (e.g. `import _Item_pb2`).
_PROTOS_PATH = str(next(iter(networking.protos.__path__)))
if _PROTOS_PATH not in sys.path:
    sys.path.insert(0, _PROTOS_PATH)

from networking.protos import MarketPlace_pb2

from src.market_lister import PlanEntry
from src.models.marketplace_layout import build_layout, tab_icon_index
from src.models.roll_pricing import MarketRow
from src.models.marketplace_runner import CURSOR_DEVIATION_PX, NOT_ON_MY_LISTINGS, MarketplaceRunner, Recheck
from src.models.marketplace_state import FIRST_PAGE, MAX_SNAPSHOT_AGE_S, MarketplaceState, RegisterOutcome

MAPPING = [4, 20, 5, 6, 7, 8, 9, 30]
LAYOUT = build_layout((1920, 1080))
# Every flow starts by re-opening My Listings (via View Market) and waiting for the game to confirm it.
VERIFY_CLICKS = [LAYOUT.point("view_market_tab"), LAYOUT.point("my_listings_tab")]


class FakeDriver:
    def __init__(self):
        self.actions = []
        self.on_create = None
        self.on_type = None
        self.listener = None  # lets a fake game react to clicks (page turns, tab switches)
        self.pos = (0, 0)

    def click(self, x, y):
        self.actions.append(("click", (x, y)))
        self.pos = (x, y)
        if self.listener:
            self.listener((x, y))
        if (x, y) == LAYOUT.point("create_listing_button") and self.on_create:
            self.on_create()

    def move_to(self, x, y):
        self.actions.append(("move", (x, y)))
        self.pos = (x, y)

    def clear_and_type(self, text):
        self.actions.append(("type", text))
        if self.on_type:
            self.on_type()

    def position(self):
        return self.pos


class ScriptedState(MarketplaceState):
    """Answers register / listing waits from a script instead of packets."""

    def __init__(self, used=2, outcomes=(), confirm=True, available=None, clock=None, page=FIRST_PAGE,
                 answers_page=FIRST_PAGE, responsive=True, after_listing_page=None, turns_pages=True):
        super().__init__(clock or time.monotonic)
        # The game reports free spots in availableOrderIndexes; `used` is shorthand for
        # "spots 0..used-1 are taken" out of 40.
        free = tuple(range(used, 40)) if available is None else tuple(available)
        msg = MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(
            availableOrderIndexes=free, currentPage=page)
        self.handle_my_item_list(msg)
        self.outcomes = list(outcomes)
        self.confirm = confirm
        self.answers_page = answers_page              # page the game shows when My Listings opens
        self.responsive = responsive                  # False: the game never answers
        self.after_listing_page = after_listing_page  # page the game shows after a listing
        self.turns_pages = turns_pages                # False: the next-page arrow does nothing
        self.shown_page = answers_page

    def on_click(self, point):
        if point == LAYOUT.point("my_listings_tab"):
            self.shown_page = self.answers_page
        elif point == LAYOUT.point("next_page_arrow") and self.turns_pages:
            self.shown_page += 1
        elif point == LAYOUT.point("prev_page_arrow") and self.turns_pages:
            self.shown_page -= 1

    def wait_for_fresh_snapshot(self, since, timeout):
        """Stands in for the game re-sending My Listings when its tab is opened or a page turns."""
        if not self.responsive:
            return None
        self._snapshot = replace(self._snapshot, received_at=since + 0.001, current_page=self.shown_page)
        return self._snapshot

    def wait_for_register(self, timeout):
        return self.outcomes.pop(0) if self.outcomes else RegisterOutcome("ok")

    def wait_for_listing(self, unique_id, since, timeout):
        if self.confirm and self.after_listing_page is not None:
            self.shown_page = self.after_listing_page
            self._snapshot = replace(self._snapshot, received_at=since + 0.5, current_page=self.after_listing_page)
        return self.confirm


def _entry(uid, stash="2", slot=0, price=900):
    return PlanEntry(uid, f"Item {uid}", 5, stash, slot, 1, 1, price, 45, 10)


def _runner(driver, state, cancelled=lambda: False, safety=None):
    driver.listener = getattr(state, "on_click", None)
    return MarketplaceRunner(driver, LAYOUT, state, tab_mapping=MAPPING, is_cancelled=cancelled, pause=lambda: None,
                             safety=safety)


def test_run_clicks_full_sequence_for_one_item():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=2)).run([_entry("a", stash="4", slot=13)])
    assert report.stopped_reason is None
    assert [r.status for r in report.results] == ["listed"]
    assert driver.actions == [("click", p) for p in VERIFY_CLICKS] + [
        ("click", LAYOUT.spot_row(2)),
        ("click", LAYOUT.tab_icon(1)),
        ("click", LAYOUT.item_centre("4", 13, 1, 1)),
        ("click", LAYOUT.point("price_field")),
        ("type", "900"),
        ("click", LAYOUT.point("create_listing_button")),
        ("click", LAYOUT.point("confirm_listing_yes")),   # "Would you like to list the item?" -> Yes
    ]


def test_run_turns_pages_when_spots_full():
    driver = FakeDriver()
    _runner(driver, ScriptedState(used=19)).run([_entry("a"), _entry("b", slot=1)])
    clicks = [a[1] for a in driver.actions if a[0] == "click"][len(VERIFY_CLICKS):]
    arrow = LAYOUT.point("next_page_arrow")
    assert clicks[0] == arrow and clicks[1] == LAYOUT.spot_row(9)   # index 19 → page 1, row 9
    assert clicks.count(arrow) == 2                                  # index 20 → page 2
    assert clicks[clicks.index(arrow, 1) + 1] == LAYOUT.spot_row(0)


def test_dry_run_never_clicks_create_listing():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=0)).run([_entry("a"), _entry("b", slot=1)], dry_run=True)
    assert [r.status for r in report.results] == ["dry_run", "dry_run"]
    assert ("click", LAYOUT.point("create_listing_button")) not in driver.actions
    assert ("click", LAYOUT.spot_row(1)) in driver.actions  # second item uses next spot


def test_run_refuses_without_listings_snapshot():
    driver = FakeDriver()
    report = _runner(driver, MarketplaceState()).run([_entry("a")])
    assert report.results == ()
    assert "My Listings" in report.stopped_reason
    assert driver.actions == []


def test_run_stops_on_timeout_and_general_failure():
    # Timeout results in "unconfirmed" status, and No is clicked in case the dialog is still open
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(outcomes=[RegisterOutcome("timeout")])).run([_entry("a"), _entry("b", slot=1)])
    assert len(report.results) == 1 and report.results[0].status == "unconfirmed"
    assert "not confirmed" in report.stopped_reason.lower()
    assert driver.actions[-1] == ("click", LAYOUT.point("confirm_listing_no"))

    # General failure (657) results in "failed" status
    report = _runner(FakeDriver(), ScriptedState(outcomes=[RegisterOutcome("failed", 657)])).run([_entry("a"), _entry("b", slot=1)])
    assert len(report.results) == 1 and report.results[0].status == "failed"
    assert "gold" in report.stopped_reason.lower()


def test_run_continues_after_item_level_failure():
    driver = FakeDriver()
    state = ScriptedState(outcomes=[RegisterOutcome("failed", 666), RegisterOutcome("ok")])
    report = _runner(driver, state).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["failed", "listed"]
    assert report.stopped_reason is None
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    after_failure = clicks[clicks.index(LAYOUT.point("confirm_listing_yes")) + 1:]
    assert after_failure[:2] == VERIFY_CLICKS   # back on a confirmed My Listings before the next item


def test_item_level_failure_stops_when_my_listings_cannot_be_confirmed():
    state = ScriptedState(outcomes=[RegisterOutcome("failed", 666)])
    driver = FakeDriver()
    runner = _runner(driver, state)
    driver.on_create = lambda: setattr(state, "responsive", False)   # e.g. an error popup covers the tabs
    report = runner.run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["failed"]
    assert report.stopped_reason == NOT_ON_MY_LISTINGS


def test_page_turn_the_game_does_not_confirm_stops_before_the_spot_click():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=19, turns_pages=False)).run([_entry("a")])
    assert report.results == () and "Couldn't turn My Listings to page 2" in report.stopped_reason
    assert ("click", LAYOUT.spot_row(9)) not in driver.actions


def test_cancel_while_the_game_is_unfocused_does_not_click_no_blindly():
    flag = {"cancel": False}
    driver = FakeDriver()
    driver.on_create = lambda: flag.update(cancel=True)
    safety = RecordingSafety(driver, reason="game_window_unfocused")
    report = _safe_runner(driver, ScriptedState(used=0), safety, cancelled=lambda: flag["cancel"]).run([_entry("a")])
    assert ("click", LAYOUT.point("confirm_listing_no")) not in driver.actions
    assert "may still be open" in report.stopped_reason


def test_run_stops_when_listing_not_confirmed():
    report = _runner(FakeDriver(), ScriptedState(confirm=False)).run([_entry("a"), _entry("b", slot=1)])
    assert len(report.results) == 1
    assert report.results[0].status == "unconfirmed"
    assert "couldn't confirm" in report.stopped_reason


def test_run_stops_when_cancelled():
    # Cancel lands after Create Listing but before the confirmation: Yes is never clicked,
    # so nothing is listed and no fee is charged.
    flag = {"cancel": False}
    driver = FakeDriver()
    driver.on_create = lambda: flag.update(cancel=True)
    report = _runner(driver, ScriptedState(), cancelled=lambda: flag["cancel"]).run([_entry("a"), _entry("b", slot=1)])
    assert report.results == ()
    assert report.stopped_reason == "Cancelled"
    assert ("click", LAYOUT.point("confirm_listing_yes")) not in driver.actions
    assert driver.actions[-1] == ("click", LAYOUT.point("confirm_listing_no"))  # dialog dismissed


def test_run_cancel_between_items_keeps_the_listed_one():
    flag = {"cancel": False, "yes": 0}
    driver = FakeDriver()
    original = driver.click

    def click(x, y):
        original(x, y)
        if (x, y) == LAYOUT.point("confirm_listing_yes"):
            flag["cancel"] = True
    driver.click = click
    report = _runner(driver, ScriptedState(), cancelled=lambda: flag["cancel"]).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["listed"]
    assert report.stopped_reason == "Cancelled"


def test_run_stops_when_no_free_spots():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=40)).run([_entry("a")])
    assert "No free listing spots" in report.stopped_reason
    assert report.results == ()
    assert driver.actions == []


def test_run_stops_when_driver_click_raises():
    driver = FakeDriver()
    original_click = driver.click
    call_count = [0]
    progress_results = []

    def raising_click(x, y):
        call_count[0] += 1
        if call_count[0] > 8:  # My Listings check (2 clicks) + first item (6 clicks), then it fails
            raise ValueError("Driver crashed")
        original_click(x, y)

    driver.click = raising_click
    def track_progress(result):
        progress_results.append(result)

    report = _runner(driver, ScriptedState(outcomes=[RegisterOutcome("ok"), RegisterOutcome("ok")])).run([_entry("a"), _entry("b", slot=1)], on_progress=track_progress)
    assert len(report.results) == 2
    assert report.results[0].status == "listed"
    assert report.results[1].status == "failed"
    assert report.results[1].unique_id == "b"
    assert report.results[1].name == "Item b"
    assert report.results[1].message == "Driver crashed"
    assert "Stopped:" in report.stopped_reason
    # Verify on_progress received both items
    assert len(progress_results) == 2
    assert progress_results[0].unique_id == "a"
    assert progress_results[1].unique_id == "b"


def test_run_stops_for_safety_checkpoint_false():
    class FailSafety:
        reason = "game not focused"

        def checkpoint(self):
            return False

        def snapshot_position(self):
            pass

    driver = FakeDriver()
    runner = MarketplaceRunner(driver, LAYOUT, ScriptedState(used=0), tab_mapping=MAPPING,
                               is_cancelled=lambda: False, pause=lambda: None, safety=FailSafety())
    report = runner.run([_entry("a")])
    assert report.results == ()
    assert "Stopped for safety: game not focused" in report.stopped_reason
    assert driver.actions == []


def test_run_stops_on_unmapped_stash_tab():
    driver = FakeDriver()
    # Use stash "99" which is not in MAPPING
    report = _runner(driver, ScriptedState(used=0)).run([_entry("a", stash="99")])
    assert report.results == ()
    assert "not mapped" in report.stopped_reason
    assert driver.actions == []


def test_run_validates_every_tab_mapping_before_any_click():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=0)).run([_entry("a"), _entry("b", stash="99", slot=1)])
    assert report.results == ()
    assert report.stopped_reason == "Stash tab for Item b is not mapped in DnDTools settings."
    assert driver.actions == []


def test_run_continues_on_fail_code_662():
    state = ScriptedState(outcomes=[RegisterOutcome("failed", 662), RegisterOutcome("ok")])
    report = _runner(FakeDriver(), state).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["failed", "listed"]
    assert report.stopped_reason is None


def test_run_uses_free_spots_from_the_game_in_order():
    # Spots 0-4 taken except a gap at 2 (e.g. something sold): fill 2, then 5.
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(available=(2, 5, 6))).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["listed", "listed"]
    spot_clicks = [a[1] for a in driver.actions if a[1] in (LAYOUT.spot_row(2), LAYOUT.spot_row(5))]
    assert spot_clicks == [LAYOUT.spot_row(2), LAYOUT.spot_row(5)]


def test_run_stops_when_free_spots_run_out():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(available=(38,))).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["listed"]
    assert report.stopped_reason == "No free listing spots left."


def test_run_calls_on_progress_for_failed_items():
    state = ScriptedState(outcomes=[RegisterOutcome("failed", 657)])
    progress_results = []

    def track_progress(result):
        progress_results.append(result)

    report = _runner(FakeDriver(), state).run([_entry("a"), _entry("b", slot=1)], on_progress=track_progress)
    assert len(progress_results) == 1
    assert progress_results[0].status == "failed"
    assert progress_results[0].unique_id == "a"


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_run_refuses_when_my_listings_was_seen_long_ago():
    # Seen too long ago: the player may be anywhere, so nothing is clicked.
    clock = FakeClock()
    state = ScriptedState(used=0, clock=clock)
    clock.t += MAX_SNAPSHOT_AGE_S + 1
    driver = FakeDriver()
    report = _runner(driver, state).run([_entry("a")])
    assert report.results == ()
    assert report.stopped_reason == "Open (or re-open) Trade → Marketplace → My Listings in the game first."
    assert driver.actions == []


def test_run_stops_when_the_game_does_not_confirm_my_listings():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=0, responsive=False)).run([_entry("a")])
    assert (report.results, report.stopped_reason) == ((), NOT_ON_MY_LISTINGS)
    assert [a[1] for a in driver.actions] == VERIFY_CLICKS   # nothing after the unanswered check


def test_my_listings_on_another_page_is_tracked_and_walked_back():
    # The game reopens My Listings on the page last used (here page 2); the free spot is on page 1.
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(available=(3,), answers_page=FIRST_PAGE + 1)).run([_entry("a")])
    assert [r.status for r in report.results] == ["listed"]
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    assert clicks[len(VERIFY_CLICKS):len(VERIFY_CLICKS) + 2] == [LAYOUT.point("prev_page_arrow"), LAYOUT.spot_row(3)]


def test_going_back_a_page_uses_the_previous_page_arrow():
    # After the first listing the game shows page 3; the next free spot is on page 2.
    driver = FakeDriver()
    state = ScriptedState(available=(3, 12), after_listing_page=FIRST_PAGE + 2)
    report = _runner(driver, state).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["listed", "listed"]
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    after_first = clicks[clicks.index(LAYOUT.point("confirm_listing_yes")) + 1:]
    assert after_first[:2] == [LAYOUT.point("prev_page_arrow"), LAYOUT.spot_row(2)]


def test_a_search_that_returns_to_a_later_page_keeps_going():
    # Returning from a market search, the game shows the page of the spot the form was opened on.
    driver = FakeDriver()
    state = PricingState([[], [], [], []], available=(12, 13), answers_page=FIRST_PAGE + 1)
    report = _runner(driver, state).run([_entry("a"), _entry("b", slot=1)], reprice=lambda e, m: Recheck(e.price))
    assert [r.status for r in report.results] == ["listed", "listed"]


def test_crawl_gear_only_ticks_every_class_and_stops_at_known_listings():
    driver = FakeDriver()
    full = [MarketRow("X_5001", 100 + i, (), (), str(i)) for i in range(10)]
    state = PricingState([full, full, full], available=(2,))
    report = _runner(driver, state).crawl_market(pages=10, rarities=(5,), gear_only=True,
                                                 is_old_page=lambda rows: rows is full)
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    expected = []
    for i in range(10):   # the dropdown closes after each tick, so it is reopened every time
        expected += [LAYOUT.point("class_dropdown"), LAYOUT.class_option(i)]
    assert clicks[len(VERIFY_CLICKS) + 4:len(VERIFY_CLICKS) + 24] == expected
    assert report.results[0].message == "1 pages, 10 listings"   # first page already known -> stop


def test_run_accepts_recent_listings_snapshot():
    clock = FakeClock()
    state = ScriptedState(used=0, clock=clock)
    clock.t += MAX_SNAPSHOT_AGE_S - 1
    report = _runner(FakeDriver(), state).run([_entry("a")])
    assert report.stopped_reason is None
    assert [r.status for r in report.results] == ["listed"]


def test_run_reopens_my_listings_on_page_one_when_another_page_was_open():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=0, page=FIRST_PAGE + 1)).run([_entry("a")])
    assert report.stopped_reason is None and [r.status for r in report.results] == ["listed"]
    assert [a[1] for a in driver.actions[:2]] == VERIFY_CLICKS


def test_run_accepts_first_page():
    report = _runner(FakeDriver(), ScriptedState(used=0, page=FIRST_PAGE)).run([_entry("a")])
    assert report.stopped_reason is None


class RecordingSafety:
    def __init__(self, driver, reason=None, ok=True):
        self.driver = driver
        self.reason = reason
        self.ok = ok

    def checkpoint(self):
        return self.ok

    def snapshot_position(self):
        self.driver.actions.append(("snapshot",))


def _safe_runner(driver, state, safety, cancelled=lambda: False):
    return _runner(driver, state, cancelled=cancelled, safety=safety)


def test_snapshot_position_taken_after_create_click():
    driver = FakeDriver()
    _safe_runner(driver, ScriptedState(used=0), RecordingSafety(driver)).run([_entry("a")])
    create = ("click", LAYOUT.point("create_listing_button"))
    assert driver.actions.index(("snapshot",)) > driver.actions.index(create)


def test_snapshot_position_taken_after_dry_run_form_fill():
    driver = FakeDriver()
    _safe_runner(driver, ScriptedState(used=0), RecordingSafety(driver)).run([_entry("a")], dry_run=True)
    assert driver.actions[-1] == ("snapshot",)
    assert driver.actions.index(("type", "900")) < driver.actions.index(("snapshot",))


def test_mouse_moved_before_create_stops_without_clicking_create():
    driver = FakeDriver()
    fx, fy = LAYOUT.point("price_field")
    driver.on_type = lambda: setattr(driver, "pos", (fx + CURSOR_DEVIATION_PX + 1, fy))
    progress = []
    report = _runner(driver, ScriptedState(used=0)).run([_entry("a"), _entry("b", slot=1)], on_progress=progress.append)
    assert ("click", LAYOUT.point("create_listing_button")) not in driver.actions
    assert report.stopped_reason == "Stopped for safety: mouse moved during listing"
    assert [(r.unique_id, r.status, r.message) for r in report.results] == [
        ("a", "failed", "stopped before Create Listing — no fee charged")]
    assert [r.unique_id for r in progress] == ["a"]


def test_small_cursor_drift_does_not_stop():
    driver = FakeDriver()
    fx, fy = LAYOUT.point("price_field")
    driver.on_type = lambda: setattr(driver, "pos", (fx + CURSOR_DEVIATION_PX, fy - CURSOR_DEVIATION_PX))
    report = _runner(driver, ScriptedState(used=0)).run([_entry("a")])
    assert report.stopped_reason is None
    assert [r.status for r in report.results] == ["listed"]


def test_safety_stop_uses_friendly_reason_text():
    driver = FakeDriver()
    safety = RecordingSafety(driver, reason="mouse_interference", ok=False)
    report = _safe_runner(driver, ScriptedState(used=0), safety).run([_entry("a")])
    assert report.stopped_reason == "Stopped for safety: the mouse was moved"

    driver = FakeDriver()
    safety = RecordingSafety(driver, reason="game_window_unfocused")
    report = _safe_runner(driver, ScriptedState(used=0), safety, cancelled=lambda: True).run([_entry("a")])
    assert report.stopped_reason == "Stopped for safety: the game lost focus"


class PricingState(ScriptedState):
    """Serves scripted result pages; page_numbers gives each page's (currentPage, maxPage)."""

    def __init__(self, rows_per_search, page_numbers=(), **kw):
        super().__init__(**kw)
        self.rows_per_search = list(rows_per_search)
        self.page_numbers = list(page_numbers)
        self.shown_numbers = None

    def wait_for_item_list(self, since, timeout):
        if not self.rows_per_search:
            return None
        rows = self.rows_per_search.pop(0)
        if rows and self.page_numbers:
            self.shown_numbers = self.page_numbers.pop(0)
        return rows

    def last_item_page(self):
        return self.shown_numbers


def test_price_all_runs_the_search_flow_per_item():
    driver = FakeDriver()
    row = MarketRow("HeaterShield_5001", 300, (), ())
    state = PricingState([[], [row], None, None], available=(2, 3))  # same-roll then all-roll search per item
    entries = [_entry("a", stash="20", slot=13), _entry("b", slot=1)]
    progress = []
    rows, report = _runner(driver, state).price_all(entries, on_progress=progress.append)
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    assert clicks[:9] == VERIFY_CLICKS + [
        LAYOUT.spot_row(2), LAYOUT.tab_icon(tab_icon_index("20", MAPPING)),
        LAYOUT.item_centre("20", 13, 1, 1), LAYOUT.point("form_search_button"),
        LAYOUT.point("market_attr_reset"), LAYOUT.point("market_search_button"),
        LAYOUT.point("my_listings_tab"),   # back, and confirmed, before the next item
    ]
    assert LAYOUT.point("create_listing_button") not in clicks
    # Item b's searches never answered: its (empty) view is marked incomplete.
    assert rows == {"a": {"same": [], "all": [row], "degraded": False},
                    "b": {"same": [], "all": [], "degraded": True}}
    assert [r.status for r in report.results] == ["priced", "no_results"]
    assert "search incomplete" in report.results[1].message
    assert len(progress) == 2 and report.stopped_reason is None


def test_search_showing_another_item_stops_before_listing():
    driver = FakeDriver()
    entry = replace(_entry("a"), item_id="HeaterShield_5001")
    state = PricingState([[MarketRow("GreatHelm_5001", 300, (), ())], []], available=(2,))
    report = _runner(driver, state).run([entry], reprice=lambda e, market: Recheck(e.price))
    assert "instead of Item a" in report.stopped_reason
    assert ("click", LAYOUT.point("create_listing_button")) not in driver.actions


def test_price_all_refuses_without_listings_snapshot():
    rows, report = _runner(FakeDriver(), MarketplaceState()).price_all([_entry("a")])
    assert rows == {} and "My Listings" in report.stopped_reason


def test_price_all_reads_up_to_three_pages_of_all_roll_results():
    driver = FakeDriver()
    page = [MarketRow("HeaterShield_5001", 300 + i, (), ()) for i in range(10)]
    page2 = [MarketRow("HeaterShield_5001", 400 + i, (), ()) for i in range(10)]
    page3 = [MarketRow("HeaterShield_5001", 500 + i, (), ()) for i in range(3)]
    state = PricingState([[], page, page2, page3], available=(2,))
    rows, _ = _runner(driver, state).price_all([_entry("a")])
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    assert clicks.count(LAYOUT.point("market_next_page")) == 2   # stops once a page is short
    assert len(rows["a"]["all"]) == 23


class PayoutState(MarketplaceState):
    """Serves a scripted sequence of My Listings snapshots and transfer results."""

    def __init__(self, snapshots, transfer_results):
        super().__init__()
        self.snapshots = list(snapshots)
        self.transfer_results = list(transfer_results)
        self._snap = self.snapshots.pop(0)  # what the game showed before the run

    def snapshot(self):
        return self._snap

    def wait_for_fresh_snapshot(self, since, timeout):
        if not self.snapshots:
            return None
        self._snap = replace(self.snapshots.pop(0), received_at=since + 0.001)
        return self._snap

    def wait_for_transfer(self, timeout):
        return self.transfer_results.pop(0) if self.transfer_results else None


def _snap(payouts, now):
    from src.models.marketplace_state import ListingsSnapshot
    return ListingsSnapshot(received_at=now, available=(5,), payouts=tuple(payouts))


def test_collect_payouts_transfers_each_sold_listing():
    import time as _t
    now = _t.monotonic()
    helm, robe = (1, 3, "GreatHelm_3001", 200), (3, 2, "OracleRobe_4001", 690)
    state = PayoutState([_snap([helm, robe], now), _snap([helm, robe], now), _snap([(2, 2, "OracleRobe_4001", 690)], now),
                         _snap([], now)], [1, 1])
    driver = FakeDriver()
    report = _runner(driver, state).collect_payouts()
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    # My Listings is re-opened and confirmed before every transfer: rows shift after each one.
    assert clicks == (VERIFY_CLICKS + [LAYOUT.spot_row(1), LAYOUT.point("transfer_all_button")]
                      + VERIFY_CLICKS + [LAYOUT.spot_row(2), LAYOUT.point("transfer_all_button")]
                      + VERIFY_CLICKS)
    assert [(r.name, r.status, r.message) for r in report.results] == [
        ("GreatHelm_3001", "collected", "200g collected"), ("OracleRobe_4001", "collected", "expired item returned")]
    assert report.stopped_reason is None


def test_collect_payouts_stops_on_transfer_failure():
    import time as _t
    now = _t.monotonic()
    helm = (1, 3, "GreatHelm_3001", 200)
    state = PayoutState([_snap([helm], now), _snap([helm], now)], [658])
    report = _runner(FakeDriver(), state).collect_payouts()
    assert report.results[0].status == "failed"
    assert "Marketplace error 658" in report.stopped_reason or "space" in report.stopped_reason.lower()


class CountingSafety:
    """Passes the first `ok_calls` checkpoints, then reports the mouse moved."""
    reason = "mouse_interference"

    def __init__(self, ok_calls):
        self.ok_calls = ok_calls

    def checkpoint(self):
        self.ok_calls -= 1
        return self.ok_calls >= 0

    def snapshot_position(self):
        pass


def test_collect_stops_when_the_mouse_moves_between_transfers():
    import time as _t
    now = _t.monotonic()
    helm, robe = (1, 3, "GreatHelm_3001", 200), (3, 2, "OracleRobe_4001", 690)
    state = PayoutState([_snap([helm, robe], now), _snap([helm, robe], now), _snap([robe], now)], [1, 1])
    runner = MarketplaceRunner(FakeDriver(), LAYOUT, state, tab_mapping=MAPPING, is_cancelled=lambda: False,
                               pause=lambda: None, safety=CountingSafety(ok_calls=1))
    report = runner.collect_payouts()
    assert [r.status for r in report.results] == ["collected"]
    assert report.stopped_reason == "Stopped for safety: the mouse was moved"


class MouseTakenState(PricingState):
    """The player grabs the mouse right after the first result page arrives."""

    def __init__(self, driver, rows_per_search, **kw):
        super().__init__(rows_per_search, **kw)
        self.driver = driver

    def wait_for_item_list(self, since, timeout):
        rows = super().wait_for_item_list(since, timeout)
        self.driver.pos = (5, 5)
        return rows


def test_crawl_stops_when_the_mouse_is_taken():
    driver = FakeDriver()
    full = [MarketRow("X_5001", 100 + i, (), ()) for i in range(10)]
    report = _runner(driver, MouseTakenState(driver, [full, full, full], available=(2,))).crawl_market(
        pages=5, rarities=(5,))
    assert report.stopped_reason == "Stopped for safety: the mouse was moved"
    assert LAYOUT.point("market_next_page") not in [a[1] for a in driver.actions]


def test_collect_stops_when_the_mouse_is_taken_during_a_transfer():
    import time as _t
    now = _t.monotonic()
    helm, robe = (1, 3, "GreatHelm_3001", 200), (3, 2, "OracleRobe_4001", 690)
    driver = FakeDriver()
    state = PayoutState([_snap([helm, robe], now), _snap([helm, robe], now), _snap([robe], now)], [1, 1])
    original = state.wait_for_transfer

    def taken(timeout):
        driver.pos = (5, 5)
        return original(timeout)
    state.wait_for_transfer = taken
    report = _runner(driver, state).collect_payouts()
    assert [r.status for r in report.results] == ["collected"]
    assert report.stopped_reason == "Stopped for safety: the mouse was moved"


def test_crawl_stops_for_safety_between_pages():
    full = [MarketRow("X_5001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([full, full, full], available=(2,))
    runner = MarketplaceRunner(FakeDriver(), LAYOUT, state, tab_mapping=MAPPING, is_cancelled=lambda: False,
                               pause=lambda: None, safety=CountingSafety(ok_calls=1))
    report = runner.crawl_market(pages=5, rarities=(5,))
    assert report.stopped_reason == "Stopped for safety: the mouse was moved"


def test_crawl_market_reads_each_rarity_newest_first():
    driver = FakeDriver()
    full = [MarketRow("X_5001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([full, full, full[:4]], available=(2,))
    report = _runner(driver, state).crawl_market(pages=5, rarities=(5,), gear_only=False)
    clicks = [a[1] for a in driver.actions if a[0] == "click"][len(VERIFY_CLICKS):]
    assert clicks[:5] == [LAYOUT.point("view_market_tab"), LAYOUT.point("market_reset_filters"),
                          LAYOUT.point("rarity_dropdown"), LAYOUT.rarity_option(5),
                          LAYOUT.point("market_search_button")]
    assert clicks.count(LAYOUT.point("market_next_page")) == 2  # stops after the short third page
    assert clicks[-1] == LAYOUT.point("my_listings_tab")
    assert (report.results[0].name, report.results[0].message) == ("Epic", "3 pages, 24 listings")


def test_next_page_tries_arrow_positions_until_one_works():
    driver = FakeDriver()
    full = [MarketRow("X_5001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([full, None, None, full, full[:2]], available=(2,))
    _runner(driver, state).crawl_market(pages=5, rarities=(5,))
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    tried = [c for c in clicks if c[1] == LAYOUT.point("market_next_page")[1]]
    assert tried == [LAYOUT.next_page_candidate(0), LAYOUT.next_page_candidate(1),
                     LAYOUT.next_page_candidate(2), LAYOUT.next_page_candidate(2)]  # remembers the one that worked


def test_price_all_reports_each_scan_to_the_observer():
    scans = []
    driver = FakeDriver()
    page = [MarketRow("HeaterShield_5001", 300 + i, (), ()) for i in range(4)]
    state = PricingState([[], page], available=(2,))
    runner = MarketplaceRunner(driver, LAYOUT, state, tab_mapping=MAPPING, is_cancelled=lambda: False,
                               pause=lambda: None, scan_observer=lambda *a: scans.append(a))
    runner.price_all([_entry("a", stash="20")])
    assert len(scans) == 1
    item_id, started, rows, complete = scans[0]
    assert (item_id, len(rows), complete) == ("", 4, True)   # _entry has no item_id; a short page means we saw all


def test_recheck_lowers_price_when_the_market_dropped():
    driver = FakeDriver()
    rows = [MarketRow("", 700, (), ())]
    state = PricingState([[], rows], available=(2, 3))
    report = _runner(driver, state).run([_entry("a", price=900)], reprice=lambda entry, market: Recheck(630))
    assert ("type", "630") in driver.actions
    assert report.results[0].message == "630g (market moved: planned 900g)"


def test_recheck_keeps_the_approved_price_when_market_rose():
    driver = FakeDriver()
    state = PricingState([[], []], available=(2, 3))
    _runner(driver, state).run([_entry("a", price=900)], reprice=lambda entry, market: Recheck(1200))
    assert ("type", "900") in driver.actions


def test_recheck_skips_items_no_longer_worth_listing():
    driver = FakeDriver()
    state = PricingState([[], [], [], []], available=(2, 3))
    report = _runner(driver, state).run([_entry("a"), _entry("b", slot=1)],
                                        reprice=lambda entry, market: Recheck(None if entry.unique_id == "a" else 900))
    assert [(r.unique_id, r.status) for r in report.results] == [("a", "skipped"), ("b", "listed")]
    assert report.results[0].message == "no longer worth listing at today's prices"
    assert ("type", "900") in driver.actions and driver.actions.count(("click", LAYOUT.point("create_listing_button"))) == 1


def test_recheck_skip_reason_is_shown():
    state = PricingState([[], []], available=(2,))
    report = _runner(FakeDriver(), state).run(
        [_entry("a")], reprice=lambda entry, market: Recheck(None, "market dropped to 500g"))
    assert (report.results[0].status, report.results[0].message) == ("skipped", "market dropped to 500g")


def test_stacks_fill_the_quantity_box_before_the_price():
    from dataclasses import replace as _replace
    driver = FakeDriver()
    entry = _replace(_entry("a", price=270), quantity=3)
    _runner(driver, ScriptedState(used=2)).run([entry])
    typed = [a for a in driver.actions if a[0] == "type"]
    assert typed == [("type", "3"), ("type", "270")]
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    assert clicks.index(LAYOUT.point("quantity_field")) < clicks.index(LAYOUT.point("price_field"))


def _crawl_runner(driver, state, passes):
    driver.listener = getattr(state, "on_click", None)
    return MarketplaceRunner(driver, LAYOUT, state, tab_mapping=MAPPING, is_cancelled=lambda: False,
                             pause=lambda: None, pass_observer=lambda rarity, started: passes.append(rarity) or 7)


def test_crawl_read_to_its_last_page_reports_a_complete_pass():
    passes, full = [], [MarketRow("X_5001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([full, full, full[:4]], available=(2,))
    report = _crawl_runner(FakeDriver(), state, passes).crawl_market(pages=50, rarities=(5,))
    assert passes == [5]
    assert report.results[0].message == "3 pages, 24 listings; 7 gone since the last full crawl (likely sold)"


def test_limited_or_incremental_crawls_are_not_complete_passes():
    passes, full = [], [MarketRow("X_5001", 100 + i, (), ()) for i in range(10)]
    _crawl_runner(FakeDriver(), PricingState([full, full, full[:4]], available=(2,)), passes).crawl_market(
        pages=2, rarities=(5,))
    _crawl_runner(FakeDriver(), PricingState([full, full[:4]], available=(2,)), passes).crawl_market(
        pages=50, rarities=(5,), is_old_page=lambda rows: False)
    assert passes == []


def test_search_stops_at_the_last_page_the_game_reports():
    driver = FakeDriver()
    page = [MarketRow("GoldBand_3001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([[], page, page], page_numbers=[(1, 2), (2, 2)], available=(2,))  # 1-based: 2 of 2
    rows, _ = _runner(driver, state).price_all([_entry("a")])
    assert len(rows["a"]["all"]) == 20 and rows["a"]["degraded"] is False
    assert [a[1] for a in driver.actions].count(LAYOUT.point("market_next_page")) == 1   # no click past the end


def test_a_full_last_page_is_the_end_not_a_failed_search():
    driver = FakeDriver()
    page = [MarketRow("GoldBand_3001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([[], page, page], page_numbers=[(0, 2), (1, 2)], available=(2,))  # 0-based: last is 1
    rows, report = _runner(driver, state).price_all([_entry("a")])
    assert len(rows["a"]["all"]) == 20 and rows["a"]["degraded"] is False
    assert "incomplete" not in report.results[0].message


def test_a_failed_page_turn_mid_results_is_still_incomplete():
    page = [MarketRow("GoldBand_3001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([[], page], page_numbers=[(0, 5)], available=(2,))
    rows, _ = _runner(FakeDriver(), state).price_all([_entry("a")])
    assert rows["a"]["degraded"] is True


def test_crawl_ending_on_a_full_last_page_is_a_complete_pass():
    passes, full = [], [MarketRow("X_5001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([full, full], page_numbers=[(1, 2), (2, 2)], available=(2,))
    _crawl_runner(FakeDriver(), state, passes).crawl_market(pages=50, rarities=(5,))
    assert passes == [5]
