"""
Sort Safety Monitor — detects game window focus loss and user mouse
interference during automated sorting to prevent the user from getting
stuck in an uncontrollable state.

Two detection mechanisms run in parallel:

**Window focus** (background thread)
    Polls ``GetForegroundWindow`` every ~200 ms.  If the game window is
    *not* in the foreground for longer than ``FOCUS_LOSS_GRACE_SECONDS``
    the ``cancel_event`` is set.  A brief alt-tab that returns within the
    grace period does **not** cancel the sort.

**Mouse interference** (inline checkpoints)
    Between each sort operation the caller invokes ``checkpoint()``.  The
    checkpoint records the current cursor position and compares it with the
    position from the *previous* checkpoint (or from the last explicit
    ``snapshot_position()`` call).  A single deviation above the pixel
    threshold only increments a strike counter; cancellation is triggered
    only after ``MOUSE_DEVIATION_STRIKES`` **consecutive** deviations.
    This deliberate tolerance avoids aborting the sort from a momentary
    bump of the desk or a very small accidental nudge.
"""

import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SortSafetyMonitor:
    """Monitor game window focus and mouse interference during sorting.

    Usage::

        monitor = SortSafetyMonitor(cancel_event)
        monitor.start()
        try:
            for item in plan:
                if not monitor.checkpoint():
                    break
                do_move(item)
                monitor.snapshot_position()
        finally:
            monitor.stop()
    """

    # ── Tuning knobs ────────────────────────────────────────────────
    FOCUS_POLL_INTERVAL: float = 0.20
    """Seconds between background focus checks."""

    FOCUS_LOSS_GRACE_SECONDS: float = 0.60
    """How long the game may lose focus before the sort is cancelled.
    Short enough to react quickly, long enough to survive a brief
    taskbar flash or overlay tooltip."""

    MOUSE_DEVIATION_THRESHOLD_PX: int = 120
    """Pixels the cursor must move (in either axis) from the expected
    position to count as a deviation strike.  At 1080p the default
    stash cell jump is ~40 px, so 120 px ≈ 3 cells — well above
    normal macro jitter but easy to hit with a deliberate hand move."""

    MOUSE_DEVIATION_STRIKES: int = 3
    """Consecutive deviation checkpoints required before cancelling.
    Because checkpoints happen between item moves, this means the user
    must visibly fight the cursor for 3 successive items before the
    sort aborts — virtually impossible to trigger accidentally."""

    # ── Construction ────────────────────────────────────────────────

    def __init__(
        self,
        cancel_event: threading.Event,
        game_window_title: str = "Dark and Darker  ",
    ):
        self._cancel_event = cancel_event
        self._game_title = game_window_title
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Mouse tracking (updated from the sort thread via checkpoint / snapshot)
        self._expected_pos: Optional[Tuple[int, int]] = None
        self._deviation_strikes: int = 0
        self._lock = threading.Lock()

        # Focus tracking (background thread)
        self._focus_lost_since: Optional[float] = None

        # Cancellation reason (set once)
        self._reason: Optional[str] = None

    # ── Lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background focus-monitoring thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._reason = None
        self._focus_lost_since = None
        self._deviation_strikes = 0
        self._expected_pos = None
        self._thread = threading.Thread(
            target=self._focus_loop,
            daemon=True,
            name="SortSafetyMonitor",
        )
        self._thread.start()
        logger.debug("Sort safety monitor started")

    def stop(self) -> None:
        """Signal the background thread to exit and wait for it."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.debug("Sort safety monitor stopped (reason=%s)", self._reason)

    @property
    def reason(self) -> Optional[str]:
        """Machine-readable reason if the monitor triggered cancellation."""
        return self._reason

    # ── Inline mouse checkpoints (called from the sort thread) ─────

    def checkpoint(self) -> bool:
        """Record cursor position and check for user mouse interference.

        Returns ``True`` if sorting should continue, ``False`` if the
        monitor has detected interference and set the cancel event.
        """
        if self._cancel_event.is_set():
            return False

        try:
            pt = _POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            current = (pt.x, pt.y)
        except Exception:
            return True  # can't read cursor — don't cancel on transient error

        with self._lock:
            expected = self._expected_pos
            # Always update so the next checkpoint (or snapshot) has a
            # fresh baseline even when no macro move occurs in between.
            self._expected_pos = current

        if expected is None:
            # First checkpoint — nothing to compare against yet.
            return True

        dx = abs(current[0] - expected[0])
        dy = abs(current[1] - expected[1])
        deviation = max(dx, dy)

        if deviation > self.MOUSE_DEVIATION_THRESHOLD_PX:
            self._deviation_strikes += 1
            logger.debug(
                "Mouse deviation detected: %d px (strike %d/%d)",
                deviation,
                self._deviation_strikes,
                self.MOUSE_DEVIATION_STRIKES,
            )
            if self._deviation_strikes >= self.MOUSE_DEVIATION_STRIKES:
                self._reason = "mouse_interference"
                self._cancel_event.set()
                logger.info(
                    "Sort cancelled: user mouse interference detected "
                    "(%d consecutive deviations > %d px)",
                    self._deviation_strikes,
                    self.MOUSE_DEVIATION_THRESHOLD_PX,
                )
                return False
        else:
            # Cursor is close to where we left it — reset strike counter.
            self._deviation_strikes = 0

        return True

    def snapshot_position(self) -> None:
        """Record the current cursor position as the expected baseline.

        Call this immediately after a macro move completes so the next
        ``checkpoint()`` compares against the correct resting position.
        """
        try:
            pt = _POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            with self._lock:
                self._expected_pos = (pt.x, pt.y)
                self._deviation_strikes = 0
        except Exception:
            pass

    # ── Background focus loop ──────────────────────────────────────

    def _focus_loop(self) -> None:
        """Poll foreground window; cancel the sort if the game loses focus."""
        user32 = ctypes.windll.user32

        while not self._stop.is_set() and not self._cancel_event.is_set():
            self._stop.wait(self.FOCUS_POLL_INTERVAL)
            if self._stop.is_set() or self._cancel_event.is_set():
                break

            try:
                fg_hwnd = user32.GetForegroundWindow()
                buf_len = user32.GetWindowTextLengthW(fg_hwnd)
                if buf_len > 0:
                    buf = ctypes.create_unicode_buffer(buf_len + 1)
                    user32.GetWindowTextW(fg_hwnd, buf, buf_len + 1)
                    fg_title = buf.value
                else:
                    fg_title = ""

                game_focused = fg_title.startswith("Dark and Darker")

                if not game_focused:
                    now = time.perf_counter()
                    if self._focus_lost_since is None:
                        self._focus_lost_since = now
                        logger.debug("Game window focus lost — grace period started")
                    elif (now - self._focus_lost_since) >= self.FOCUS_LOSS_GRACE_SECONDS:
                        self._reason = "game_window_unfocused"
                        self._cancel_event.set()
                        logger.info(
                            "Sort cancelled: game window lost focus for %.1f s",
                            now - self._focus_lost_since,
                        )
                        break
                else:
                    if self._focus_lost_since is not None:
                        logger.debug("Game window focus restored within grace period")
                    self._focus_lost_since = None
            except Exception:
                pass  # never crash the monitor from a transient Win32 error
