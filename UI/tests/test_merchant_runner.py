from src.market_lister import PlanEntry
from src.models.marketplace_layout import build_layout, tab_icon_index
from src.models.merchant_runner import (
    MERCHANT_CARD_INDEX, NOT_AT_MERCHANT, NO_DEAL_REPLY, MerchantRunner,
)
from src.models.merchant_seller import SELL_BOX_COLUMNS, SELL_BOX_ROWS
from src.models.merchant_state import SELL_SUCCESS, MerchantState, SellBack
from src.models.marketplace_runner import CURSOR_DEVIATION_PX, MOUSE_MOVED

LAYOUT = build_layout((1920, 1080))
MAPPING = [4, 5, 20, 21, 30]
COLLECTOR = "TheCollector"


def _entry(uid, stash="4", slot=0, width=1, height=1, vendor=10, quantity=1):
    return PlanEntry(uid, f"Item {uid}", 3, stash, slot, width, height, 0, 0, vendor, quantity=quantity)


class FakeDriver:
    def __init__(self):
        self.actions = []
        self.pos = (0, 0)
        self.listener = None
        self.after_drag = None  # lets a test move the "real" cursor after a drag

    def _did(self, action):
        self.actions.append(action)
        if self.listener:
            self.listener(action)

    def click(self, x, y):
        self.pos = (x, y)
        self._did(("click", (x, y)))

    def drag(self, x1, y1, x2, y2):
        self.pos = (x2, y2)
        self._did(("drag", (x1, y1), (x2, y2)))
        if self.after_drag:
            self.after_drag(self)

    def press_escape(self):
        self._did(("escape",))

    def move_to(self, x, y):
        self.pos = (x, y)
        self._did(("move", (x, y)))

    def position(self):
        return self.pos


class FakeGame(MerchantState):
    """Opens the merchant whose card is clicked and sells what was dragged into the Sell box."""

    def __init__(self, entries, opens=COLLECTOR, answers=True, refuses=(), extra_sold=(), result=SELL_SUCCESS):
        super().__init__(clock=lambda: 50.0)
        self.by_point = {LAYOUT.item_centre(e.stash_id, e.slot_id, e.width, e.height): e.unique_id for e in entries}
        self.opens, self.answers, self.refuses = opens, answers, set(refuses)
        self.extra_sold, self.result = tuple(extra_sold), result
        self.opened, self.staged, self.reply, self.deals = None, [], None, []

    def on_action(self, action):
        if action[0] == "click" and action[1] == LAYOUT.merchant_card(MERCHANT_CARD_INDEX):
            self.opened = self.opens
        elif action[0] == "drag":
            uid = self.by_point.get(action[1])
            if uid is not None and uid not in self.refuses:
                self.staged.append(uid)
        elif action[0] == "click" and action[1] == LAYOUT.point("merchant_make_deal"):
            sold = tuple(self.staged) + self.extra_sold if self.result == SELL_SUCCESS else ()
            self.deals.append(sold)
            self.reply = SellBack(51.0, self.result, sold) if self.answers else None
            self.staged = []
        elif action[0] == "escape":
            self.staged = []

    def wait_for_merchant(self, key, since, timeout):
        return self.opened == key

    def wait_for_sell_back(self, since, timeout):
        reply, self.reply = self.reply, None
        return reply


class Safety:
    def __init__(self, fail_after=None):
        self.reason = None
        self.calls = 0
        self.fail_after = fail_after

    def checkpoint(self):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            self.reason = "game_window_unfocused"
            return False
        return True

    def snapshot_position(self):
        return None


def _run(entries, game=None, dry_run=False, cancelled=lambda: False, safety=None, driver=None):
    driver = driver or FakeDriver()
    game = game or FakeGame(entries)
    driver.listener = game.on_action
    runner = MerchantRunner(driver, LAYOUT, game, tab_mapping=MAPPING, is_cancelled=cancelled,
                            pause=lambda: None, safety=safety)
    progress = []
    report = runner.sell(entries, dry_run=dry_run, on_progress=progress.append)
    assert [r.unique_id for r in progress] == [r.unique_id for r in report.results]
    return report, driver, game


def _statuses(report):
    return [(r.unique_id, r.status) for r in report.results]


def test_sell_opens_the_collector_stages_each_tab_and_makes_one_deal():
    entries = [_entry("powder", "4", 34, vendor=250), _entry("goblet", "20", 154, 1, 2, vendor=100)]
    report, driver, game = _run(entries)
    tab = lambda stash: LAYOUT.tab_icon(tab_icon_index(stash, MAPPING))  # noqa: E731
    assert driver.actions == [
        ("click", LAYOUT.point("merchants_tab")),
        ("click", LAYOUT.merchant_card(MERCHANT_CARD_INDEX)),
        ("click", LAYOUT.point("merchant_sell_tab")),
        ("click", LAYOUT.point("merchant_sell_mode")),   # Sell, never Buyback, before Make Deal
        ("click", tab("4")),
        ("drag", LAYOUT.item_centre("4", 34, 1, 1), LAYOUT.sell_box_centre(0, 0, 1, 1)),
        ("click", tab("20")),
        ("drag", LAYOUT.item_centre("20", 154, 1, 2), LAYOUT.sell_box_centre(1, 0, 1, 2)),
        ("click", LAYOUT.point("merchant_make_deal")),
        ("escape",),
    ]
    assert report.stopped_reason is None
    assert _statuses(report) == [("powder", "sold"), ("goblet", "sold")]
    assert [r.message for r in report.results] == ["250g", "100g"]


def test_stack_value_is_price_times_count():
    report, _, _ = _run([_entry("eyes", vendor=25, quantity=2)])
    assert report.results[0].message == "50g"


def test_wrong_or_missing_merchant_stops_before_any_item_is_touched():
    entries = [_entry("a")]
    for opens in (None, "Weaponsmith"):
        report, driver, game = _run(entries, game=FakeGame(entries, opens=opens))
        assert report.stopped_reason == NOT_AT_MERCHANT
        assert not [a for a in driver.actions if a[0] in ("drag", "escape")]
        assert LAYOUT.point("merchant_sell_tab") not in [a[1] for a in driver.actions if a[0] == "click"]
        assert game.deals == []


def test_items_the_merchant_refuses_are_reported_not_taken():
    entries = [_entry("a", slot=0), _entry("b", slot=1)]
    report, _, _ = _run(entries, game=FakeGame(entries, refuses={"b"}))
    assert report.stopped_reason is None
    assert _statuses(report) == [("a", "sold"), ("b", "not_taken")]
    assert "still in your stash" in report.results[1].message


def test_selling_an_item_that_was_not_picked_stops_and_says_buy_it_back():
    entries = [_entry("a", slot=0), _entry("b", slot=200)]
    game = FakeGame(entries, extra_sold=("999",))
    report, driver, _ = _run(entries, game=game)
    assert _statuses(report) == [("a", "sold"), ("b", "sold")]
    assert "999" in report.stopped_reason and "Buyback" in report.stopped_reason
    assert driver.actions[-1] != ("escape",)  # leave the merchant open so the item can be bought back


def test_no_reply_to_make_deal_stops():
    entries = [_entry("a")]
    report, _, game = _run(entries, game=FakeGame(entries, answers=False))
    assert report.stopped_reason == NO_DEAL_REPLY
    assert report.results == ()


def test_refused_deal_stops_and_sells_nothing():
    entries = [_entry("a")]
    report, _, _ = _run(entries, game=FakeGame(entries, result=7))
    assert "7" in report.stopped_reason
    assert report.results == ()


def test_dry_run_stages_the_first_batch_then_escapes_without_a_deal():
    entries = [_entry("a", vendor=5)]
    report, driver, game = _run(entries, dry_run=True)
    assert LAYOUT.point("merchant_make_deal") not in [a[1] for a in driver.actions if a[0] == "click"]
    assert driver.actions[-1] == ("escape",)
    assert game.deals == []
    assert _statuses(report) == [("a", "dry_run")]
    assert "5g" in report.results[0].message


def test_a_full_sell_box_is_sold_in_batches():
    count = SELL_BOX_COLUMNS * SELL_BOX_ROWS + 2
    entries = [_entry(str(i), slot=i) for i in range(count)]
    report, driver, game = _run(entries)
    assert [len(d) for d in game.deals] == [count - 2, 2]
    assert all(status == "sold" for _, status in _statuses(report))
    assert driver.actions.count(("click", LAYOUT.point("merchant_make_deal"))) == 2


def test_cancel_between_drags_stops_without_a_deal():
    entries = [_entry("a", slot=0), _entry("b", slot=1)]
    driver = FakeDriver()
    report, driver, game = _run(entries, driver=driver, cancelled=lambda: len(game_drags(driver)) >= 1)
    assert game.deals == []
    assert "Escape" in report.stopped_reason
    assert len(game_drags(driver)) == 1


def game_drags(driver):
    return [a for a in driver.actions if a[0] == "drag"]


def test_safety_stop_mid_batch_never_clicks_make_deal():
    entries = [_entry("a", slot=0), _entry("b", slot=1)]
    report, driver, game = _run(entries, safety=Safety(fail_after=2))
    assert game.deals == []
    assert "the game lost focus" in report.stopped_reason


def test_mouse_taken_during_staging_stops():
    entries = [_entry("a", slot=0), _entry("b", slot=1)]
    driver = FakeDriver()
    driver.after_drag = lambda d: setattr(d, "pos", (d.pos[0] + CURSOR_DEVIATION_PX + 50, d.pos[1]))
    report, _, game = _run(entries, driver=driver)
    assert game.deals == []
    assert report.stopped_reason.startswith(MOUSE_MOVED)


def test_items_in_unmapped_tabs_or_too_big_are_reported_and_the_rest_sold():
    entries = [_entry("eq", stash="3"), _entry("huge", height=SELL_BOX_ROWS + 1), _entry("a")]
    report, _, game = _run(entries)
    assert _statuses(report) == [("eq", "failed"), ("huge", "failed"), ("a", "sold")]
    assert game.deals == [("a",)]


def test_nothing_to_sell_does_not_touch_the_game():
    report, driver, _ = _run([])
    assert driver.actions == []
    assert report.stopped_reason == "Nothing to sell."
