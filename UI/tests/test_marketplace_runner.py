import sys
import time

import networking.protos

# Generated *_pb2 modules import siblings by bare name (e.g. `import _Item_pb2`).
_PROTOS_PATH = str(next(iter(networking.protos.__path__)))
if _PROTOS_PATH not in sys.path:
    sys.path.insert(0, _PROTOS_PATH)

from networking.protos import MarketPlace_pb2

from src.market_lister import PlanEntry
from src.models.marketplace_layout import build_layout, tab_icon_index
from src.models.roll_pricing import MarketRow
from src.models.marketplace_runner import CURSOR_DEVIATION_PX, MarketplaceRunner
from src.models.marketplace_state import FIRST_PAGE, MAX_SNAPSHOT_AGE_S, MarketplaceState, RegisterOutcome

MAPPING = [4, 20, 5, 6, 7, 8, 9, 30]
LAYOUT = build_layout((1920, 1080))


class FakeDriver:
    def __init__(self):
        self.actions = []
        self.on_create = None
        self.on_type = None
        self.pos = (0, 0)

    def click(self, x, y):
        self.actions.append(("click", (x, y)))
        self.pos = (x, y)
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

    def __init__(self, used=2, outcomes=(), confirm=True, available=None, clock=None, page=FIRST_PAGE):
        super().__init__(clock or time.monotonic)
        # The game reports free spots in availableOrderIndexes; `used` is shorthand for
        # "spots 0..used-1 are taken" out of 40.
        free = tuple(range(used, 40)) if available is None else tuple(available)
        msg = MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(
            availableOrderIndexes=free, currentPage=page)
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
        ("click", LAYOUT.point("confirm_listing_yes")),   # "Would you like to list the item?" -> Yes
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
    # Cancel lands after Create Listing but before the confirmation: Yes is never clicked,
    # so nothing is listed and no fee is charged.
    flag = {"cancel": False}
    driver = FakeDriver()
    driver.on_create = lambda: flag.update(cancel=True)
    report = _runner(driver, ScriptedState(), cancelled=lambda: flag["cancel"]).run([_entry("a"), _entry("b", slot=1)])
    assert report.results == ()
    assert report.stopped_reason == "Cancelled"
    assert ("click", LAYOUT.point("confirm_listing_yes")) not in driver.actions


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
    return MarketplaceRunner(driver, LAYOUT, state, tab_mapping=MAPPING, is_cancelled=cancelled,
                             pause=lambda: None, safety=safety)


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
    def __init__(self, rows_per_search, **kw):
        super().__init__(**kw)
        self.rows_per_search = list(rows_per_search)

    def wait_for_item_list(self, since, timeout):
        return self.rows_per_search.pop(0) if self.rows_per_search else None


def test_price_all_runs_the_search_flow_per_item():
    driver = FakeDriver()
    row = MarketRow("HeaterShield_5001", 300, (), ())
    state = PricingState([[], [row], None, None], available=(2, 3))  # same-roll then all-roll search per item
    entries = [_entry("a", stash="20", slot=13), _entry("b", slot=1)]
    progress = []
    rows, report = _runner(driver, state).price_all(entries, on_progress=progress.append)
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    assert clicks[:7] == [
        LAYOUT.spot_row(2), LAYOUT.tab_icon(tab_icon_index("20", MAPPING)),
        LAYOUT.item_centre("20", 13, 1, 1), LAYOUT.point("form_search_button"),
        LAYOUT.point("market_attr_reset"), LAYOUT.point("market_search_button"),
        LAYOUT.point("my_listings_tab"),
    ]
    assert LAYOUT.point("create_listing_button") not in clicks
    assert rows == {"a": {"same": [], "all": [row]}, "b": {"same": [], "all": []}}
    assert [r.status for r in report.results] == ["priced", "no_results"]
    assert len(progress) == 2 and report.stopped_reason is None


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

    def wait_for_snapshot(self, since, timeout):
        self._snap = self.snapshots.pop(0) if self.snapshots else self._snap
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
    assert clicks == [LAYOUT.point("my_listings_tab"), LAYOUT.spot_row(1), LAYOUT.point("transfer_all_button"),
                      LAYOUT.point("my_listings_tab"), LAYOUT.spot_row(2), LAYOUT.point("transfer_all_button"),
                      LAYOUT.point("my_listings_tab")]
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


def test_crawl_market_reads_each_rarity_newest_first():
    driver = FakeDriver()
    full = [MarketRow("X_5001", 100 + i, (), ()) for i in range(10)]
    state = PricingState([full, full, full[:4]], available=(2,))
    report = _runner(driver, state).crawl_market(pages=5, rarities=(5,))
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
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
