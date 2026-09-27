"""Runs one market lister operation at a time on a background thread."""
import logging
import threading
import time
from dataclasses import asdict

logger = logging.getLogger(__name__)
LISTING_MODES = ("list", "dry_run")


class ListerJob:
    def __init__(self, runner_factory, hover_factory):
        self._runner_factory = runner_factory
        self._hover_factory = hover_factory
        self._lock = threading.Lock()
        self._thread = None
        self._event = None
        self._status = {"state": "idle", "mode": None, "results": [], "stopped_reason": None}
        self._last_finished_at = None
        self._last_list_finished_at = None

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

    def start(self, entries, dry_run) -> bool:
        def target(event):
            try:
                report = self._runner_factory(event).run(entries, dry_run=dry_run, on_progress=self._record)
                self._finish(report.stopped_reason)
            except Exception as exc:  # never leave the job stuck in "running"
                logger.exception("Market lister run failed")
                self._finish(f"Unexpected error: {exc}")
        return self._launch("dry_run" if dry_run else "list", target)

    def hover_test(self) -> bool:
        def target(event):
            try:
                self._hover_factory(event)()
                self._finish("Cancelled" if event.is_set() else None)
            except Exception as exc:
                logger.exception("Hover test failed")
                self._finish(f"Unexpected error: {exc}")
        return self._launch("hover", target)
