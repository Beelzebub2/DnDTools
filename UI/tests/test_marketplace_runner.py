import sys
import time

import networking.protos

# Generated *_pb2 modules import siblings by bare name (e.g. `import _Item_pb2`).
_PROTOS_PATH = str(next(iter(networking.protos.__path__)))
if _PROTOS_PATH not in sys.path:
    sys.path.insert(0, _PROTOS_PATH)

from networking.protos import MarketPlace_pb2

from src.market_lister import PlanEntry
from src.models.marketplace_layout import build_layout
from src.models.marketplace_runner import MarketplaceRunner
from src.models.marketplace_state import FIRST_PAGE, MAX_SNAPSHOT_AGE_S, MarketplaceState, RegisterOutcome

MAPPING = [4, 20, 5, 6, 7, 8, 9, 30]
LAYOUT = build_layout((1920, 1080))


class FakeDriver:
    def __init__(self):
        self.actions = []
        self.on_create = None

    def click(self, x, y):
        self.actions.append(("click", (x, y)))
        if (x, y) == LAYOUT.point("create_listing_button") and self.on_create:
            self.on_create()

    def move_to(self, x, y):
        self.actions.append(("move", (x, y)))

    def clear_and_type(self, text):
        self.actions.append(("type", text))


class ScriptedState(MarketplaceState):
    """Answers register / listing waits from a script instead of packets."""

    def __init__(self, used=2, outcomes=(), confirm=True, available=(), clock=None, page=FIRST_PAGE):
        super().__init__(clock or time.monotonic)
        msg = MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(
            totalItemCount=used, availableOrderIndexes=available, currentPage=page)
        self.handle_my_item_list(msg)
        self.outcomes = list(outcomes)
        self.confirm = confirm

    def wait_for_register(self, timeout):
        return self.outcomes.pop(0) if self.outcomes else RegisterOutcome("ok")

    def wait_for_listing(self, unique_id, since, timeout):
        return self.confirm


def _entry(uid, stash="2", slot=0, price=900):
    return PlanEntry(uid, f"Item {uid}", 5, stash, slot, 1, 1, price, 45, 10)


def _runner(driver, state, cancelled=lambda: False):
    return MarketplaceRunner(driver, LAYOUT, state, tab_mapping=MAPPING, is_cancelled=cancelled, pause=lambda: None)


def test_run_clicks_full_sequence_for_one_item():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=2)).run([_entry("a", stash="4", slot=13)])
    assert report.stopped_reason is None
    assert [r.status for r in report.results] == ["listed"]
    assert driver.actions == [
        ("click", LAYOUT.spot_row(2)),
        ("click", LAYOUT.tab_icon(1)),
        ("click", LAYOUT.item_centre("4", 13, 1, 1)),
        ("click", LAYOUT.point("price_field")),
        ("type", "900"),
        ("click", LAYOUT.point("create_listing_button")),
    ]


def test_run_turns_pages_when_spots_full():
    driver = FakeDriver()
    _runner(driver, ScriptedState(used=19)).run([_entry("a"), _entry("b", slot=1)])
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
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
    # Timeout results in "unconfirmed" status
    report = _runner(FakeDriver(), ScriptedState(outcomes=[RegisterOutcome("timeout")])).run([_entry("a"), _entry("b", slot=1)])
    assert len(report.results) == 1 and report.results[0].status == "unconfirmed"
    assert "not confirmed" in report.stopped_reason.lower()

    # General failure (657) results in "failed" status
    report = _runner(FakeDriver(), ScriptedState(outcomes=[RegisterOutcome("failed", 657)])).run([_entry("a"), _entry("b", slot=1)])
    assert len(report.results) == 1 and report.results[0].status == "failed"
    assert "gold" in report.stopped_reason.lower()


def test_run_continues_after_item_level_failure():
    state = ScriptedState(outcomes=[RegisterOutcome("failed", 666), RegisterOutcome("ok")])
    report = _runner(FakeDriver(), state).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["failed", "listed"]
    assert report.stopped_reason is None


def test_run_stops_when_listing_not_confirmed():
    report = _runner(FakeDriver(), ScriptedState(confirm=False)).run([_entry("a"), _entry("b", slot=1)])
    assert len(report.results) == 1
    assert report.results[0].status == "unconfirmed"
    assert "couldn't confirm" in report.stopped_reason


def test_run_stops_when_cancelled():
    flag = {"cancel": False}
    driver = FakeDriver()
    driver.on_create = lambda: flag.update(cancel=True)
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
        if call_count[0] > 6:  # First item completes (6 clicks), second item fails
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
    # Spot click happens before unmapped stash error
    assert len(driver.actions) == 1 and driver.actions[0][0] == "click"


def test_run_continues_on_fail_code_662():
    state = ScriptedState(outcomes=[RegisterOutcome("failed", 662), RegisterOutcome("ok")])
    report = _runner(FakeDriver(), state).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["failed", "listed"]
    assert report.stopped_reason is None


def test_run_accepts_available_guard_normal():
    # used=2, available=(2,3,4) — index 2 is available, should proceed normally
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=2, available=(2, 3, 4))).run([_entry("a")])
    assert report.results[0].status == "listed"
    assert report.stopped_reason is None


def test_run_stops_on_available_guard_blocked():
    # used=2, available=(5,6) — index 2 is not available, should stop without clicks
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=2, available=(5, 6))).run([_entry("a")])
    assert report.results == ()
    assert "not laid out as expected" in report.stopped_reason
    assert driver.actions == []


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


def test_run_refuses_on_stale_listings_snapshot():
    clock = FakeClock()
    state = ScriptedState(used=0, clock=clock)
    clock.t += MAX_SNAPSHOT_AGE_S + 1
    driver = FakeDriver()
    report = _runner(driver, state).run([_entry("a")])
    assert report.results == ()
    assert report.stopped_reason == "Open (or re-open) Trade → Marketplace → My Listings in the game first."
    assert driver.actions == []


def test_run_accepts_recent_listings_snapshot():
    clock = FakeClock()
    state = ScriptedState(used=0, clock=clock)
    clock.t += MAX_SNAPSHOT_AGE_S - 1
    report = _runner(FakeDriver(), state).run([_entry("a")])
    assert report.stopped_reason is None
    assert [r.status for r in report.results] == ["listed"]


def test_run_refuses_when_not_on_first_page():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=0, page=FIRST_PAGE + 1)).run([_entry("a")])
    assert report.results == ()
    assert report.stopped_reason == "Go to page 1 of My Listings, then start again."
    assert driver.actions == []


def test_run_accepts_first_page():
    report = _runner(FakeDriver(), ScriptedState(used=0, page=FIRST_PAGE)).run([_entry("a")])
    assert report.stopped_reason is None
