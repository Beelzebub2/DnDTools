"""
Single-instance guard for DnDTools on Windows.

Uses a named mutex to detect an already-running instance and a named event
to signal it to restore its window from the system tray.

Usage (inside main()):
    guard = SingleInstanceGuard("DnDTools")
    if not guard.acquire():
        # Another instance is running — ask it to show itself, then exit.
        guard.signal_restore()
        sys.exit(0)

    # ... normal startup ...
    # After the API is ready, start listening for the restore signal:
    guard.start_listener(on_restore_callback)

    # On shutdown:
    guard.release()
"""

import ctypes
import ctypes.wintypes as wintypes
import logging
import sys
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Windows constants
_MUTEX_ALL_ACCESS = 0x001F0001
_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_INFINITE = 0xFFFFFFFF


class SingleInstanceGuard:
    """
    Windows named-mutex guard + named-event signalling.

    * ``acquire()`` creates a named mutex.  Returns *True* if this is the
      first instance, *False* if another instance already holds the mutex.
    * ``signal_restore()`` sets a named event so the first instance knows
      it should restore its window.
    * ``start_listener(callback)`` watches the named event in a background
      thread and calls *callback* whenever a second instance signals.
    * ``release()`` cleans up handles.
    """

    def __init__(self, app_id: str = "DnDTools"):
        # Names visible in the Windows object namespace
        self._mutex_name = f"Global\\{app_id}_SingleInstance"
        self._event_name = f"Global\\{app_id}_RestoreEvent"

        self._mutex_handle: Optional[int] = None
        self._event_handle: Optional[int] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── public API ──────────────────────────────────────────────────────

    def acquire(self) -> bool:
        """Try to become the single running instance.

        Returns True if the mutex was freshly created (we are first).
        Returns False if an existing instance already owns the mutex.
        """
        if sys.platform != "win32":
            return True  # No-op on non-Windows; always succeeds

        kernel32 = ctypes.windll.kernel32

        handle = kernel32.CreateMutexW(None, False, self._mutex_name)
        last_err = kernel32.GetLastError()

        if handle == 0 or handle is None:
            logger.warning("CreateMutexW failed (error %s)", last_err)
            return True  # Fail-open — let the app start

        self._mutex_handle = handle

        if last_err == _ERROR_ALREADY_EXISTS:
            logger.info("Another instance already running (mutex exists).")
            return False

        logger.info("Single-instance mutex acquired.")
        return True

    def signal_restore(self) -> None:
        """Signal the existing instance to restore its window."""
        if sys.platform != "win32":
            return

        kernel32 = ctypes.windll.kernel32

        evt = kernel32.OpenEventW(0x0002, False, self._event_name)  # EVENT_MODIFY_STATE
        if evt and evt != 0:
            kernel32.SetEvent(evt)
            kernel32.CloseHandle(evt)
            logger.info("Signalled existing instance to restore.")
        else:
            logger.warning("Could not open restore event — existing instance may not be listening.")

    def start_listener(self, callback: Callable[[], None]) -> None:
        """Start a background thread that waits for restore signals."""
        if sys.platform != "win32":
            return

        kernel32 = ctypes.windll.kernel32

        # Create a manual-reset event (initially non-signalled)
        evt = kernel32.CreateEventW(None, True, False, self._event_name)
        if not evt or evt == 0:
            logger.warning("CreateEventW failed — restore listener not started.")
            return

        self._event_handle = evt
        self._stop_event.clear()

        def _listen():
            try:
                while not self._stop_event.is_set():
                    result = kernel32.WaitForSingleObject(evt, 500)  # 500ms poll
                    if result == _WAIT_OBJECT_0:
                        logger.info("Restore signal received from another instance.")
                        kernel32.ResetEvent(evt)
                        try:
                            callback()
                        except Exception as exc:
                            logger.error("Restore callback failed: %s", exc, exc_info=True)
            except Exception as exc:
                logger.error("Restore listener crashed: %s", exc, exc_info=True)

        t = threading.Thread(target=_listen, daemon=True, name="SingleInstanceListener")
        t.start()
        self._listener_thread = t
        logger.info("Single-instance restore listener started.")

    def release(self) -> None:
        """Release the mutex and stop the listener."""
        self._stop_event.set()
        kernel32 = ctypes.windll.kernel32

        if self._event_handle:
            try:
                kernel32.CloseHandle(self._event_handle)
            except Exception:
                pass
            self._event_handle = None

        if self._mutex_handle:
            try:
                kernel32.ReleaseMutex(self._mutex_handle)
                kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            self._mutex_handle = None

        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=2.0)
            self._listener_thread = None

        logger.info("Single-instance guard released.")
