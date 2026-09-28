"""Runs one market lister operation at a time on a background thread."""
import logging
import threading
import time
from dataclasses import asdict

logger = logging.getLogger(__name__)
LISTING_MODES = ("list", "dry_run")


class MonitoredRunner:
    """Runs each runner action (list, price, crawl, collect) under the sorter's safety monitor."""

    def __init__(self, runner, monitor):
        self._runner = runner
        self._monitor = monitor

    def __getattr__(self, name):
        action = getattr(self._runner, name)
        if not callable(action):
            return action

        def monitored(*args, **kwargs):
            self._monitor.start()
            try:
                return action(*args, **kwargs)
            finally:
                self._monitor.stop()
        return monitored


NO_MERCHANT_RUNNER = "Selling to merchants isn't available in this build."
SELL_BOX_HINT = " If items are in the Sell box, press Escape in the game to put them back."


class ListerJob:
    def __init__(self, runner_factory, hover_factory, merchant_factory=None):
        self._runner_factory = runner_factory
        self._hover_factory = hover_factory
        self._merchant_factory = merchant_factory  # event -> MerchantRunner
        self._lock = threading.Lock()
        self._thread = None
        self._event = None
        self._status = {"state": "idle", "mode": None, "results": [], "stopped_reason": None}
        self._last_finished_at = None
        self._last_list_finished_at = None
        self._sold_ids = frozenset()  # unique ids sold to merchants by this app session

    @property
    def last_finished_at(self):
        """time.time() when the last list / dry-run finished, or None."""
        with self._lock:
            return self._last_finished_at

    @property
    def last_list_finished_at(self):
        """time.time() when the last real (non-dry) list run finished, or None."""
        with self._lock:
            return self._last_list_finished_at

    def sold_ids(self) -> frozenset:
        """Items sold to a merchant; if the stash data still shows one, the data predates the sale."""
        with self._lock:
            return self._sold_ids

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            return {**self._status, "results": list(self._status["results"])}

    def cancel(self) -> bool:
        with self._lock:
            if self._event is None or self._thread is None or not self._thread.is_alive():
                return False
            self._event.set()
            return True

    def _launch(self, mode, target) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._event = threading.Event()
            self._status = {"state": "running", "mode": mode, "results": [], "stopped_reason": None}
            self._thread = threading.Thread(target=target, args=(self._event,), daemon=True, name="MarketLister")
            self._thread.start()
            return True

    def _finish(self, stopped_reason):
        with self._lock:
            self._status = {**self._status, "state": "done", "stopped_reason": stopped_reason}
            if self._status["mode"] in LISTING_MODES:
                self._last_finished_at = time.time()
                if self._status["mode"] == "list":
                    self._last_list_finished_at = self._last_finished_at

    def _record(self, result):
        with self._lock:
            self._status["results"].append(asdict(result))
            if self._status["mode"] == "merchant" and result.status == "sold":
                self._sold_ids = self._sold_ids | {result.unique_id}

    def start(self, entries, dry_run, reprice=None) -> bool:
        def target(event):
            try:
                report = self._runner_factory(event).run(entries, dry_run=dry_run, on_progress=self._record,
                                                         reprice=reprice)
                self._finish(report.stopped_reason)
            except Exception as exc:  # never leave the job stuck in "running"
                logger.exception("Market lister run failed")
                self._finish(f"Unexpected error: {exc}")
        return self._launch("dry_run" if dry_run else "list", target)

    def price(self, entries, price_results) -> bool:
        """Price entries from the in-game market; `price_results(entries, rows)` builds the plan."""
        def target(event):
            try:
                rows, report = self._runner_factory(event).price_all(entries, on_progress=self._record)
                plan = price_results(entries, rows).to_dict()
                with self._lock:
                    self._status = {**self._status, "plan": plan}
                self._finish(report.stopped_reason)
            except Exception as exc:
                logger.exception("Market lister pricing failed")
                self._finish(f"Unexpected error: {exc}")
        return self._launch("price", target)

    def _run_report(self, mode, action):
        def target(event):
            try:
                report = action(self._runner_factory(event))
                self._finish(report.stopped_reason)
            except Exception as exc:
                logger.exception("Market lister %s failed", mode)
                self._finish(f"Unexpected error: {exc}")
        return self._launch(mode, target)

    def crawl(self, pages, is_old_page=None) -> bool:
        return self._run_report("crawl", lambda runner: runner.crawl_market(
            pages, on_progress=self._record, is_old_page=is_old_page))

    def collect(self) -> bool:
        return self._run_report("collect", lambda runner: runner.collect_payouts(on_progress=self._record))

    def sell_to_merchant(self, entries, dry_run) -> bool:
        """Sell entries to a merchant (mode "merchant"); a dry run stages them and puts them back."""
        def target(event):
            try:
                if self._merchant_factory is None:
                    self._finish(NO_MERCHANT_RUNNER)
                    return
                report = self._merchant_factory(event).sell(entries, dry_run=dry_run, on_progress=self._record)
                self._finish(report.stopped_reason)
            except Exception as exc:
                logger.exception("Merchant sale failed")
                self._finish(f"Unexpected error: {exc}.{SELL_BOX_HINT}")
        return self._launch("merchant_dry_run" if dry_run else "merchant", target)

    def hover_test(self) -> bool:
        def target(event):
            try:
                self._hover_factory(event)()
                self._finish("Cancelled" if event.is_set() else None)
            except Exception as exc:
                logger.exception("Hover test failed")
                self._finish(f"Unexpected error: {exc}")
        return self._launch("hover", target)
