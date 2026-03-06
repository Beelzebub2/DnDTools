"""
Game window watcher — runs in a child process so the main app never blocks.

Primary strategy: WinEvent hooks (low-latency, event-driven).
Fallback:          EnumWindows polling every ~1 s (works without hooks).

The watcher puts state dicts (or ``None`` for "game not found") into a
``multiprocessing.Queue`` that the main application drains.
"""

import ctypes
from ctypes import wintypes
import multiprocessing as mp
import time
import sys
import logging
import os

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

if sys.platform.startswith('win'):
    EVENT_SYSTEM_FOREGROUND  = 0x0003
    EVENT_OBJECT_CREATE      = 0x8000
    EVENT_OBJECT_DESTROY     = 0x8001
    EVENT_OBJECT_SHOW        = 0x8002
    EVENT_OBJECT_NAMECHANGE  = 0x800C
    OBJID_WINDOW             = 0x00000000
    WINEVENT_OUTOFCONTEXT    = 0x0000
    WINEVENT_SKIPOWNPROCESS  = 0x0002
    PM_REMOVE                = 0x0001
    WM_QUIT                  = 0x0012
    WM_TIMER                 = 0x0113

    # Callback type for SetWinEventHook
    WinEventProcType = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,   # hWinEventHook
        wintypes.DWORD,    # event
        wintypes.HWND,     # hwnd
        wintypes.LONG,     # idObject
        wintypes.LONG,     # idChild
        wintypes.DWORD,    # idEventThread
        wintypes.DWORD,    # dwmsEventTime
    )

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
    )
else:
    EVENT_SYSTEM_FOREGROUND = EVENT_OBJECT_CREATE = 0
    EVENT_OBJECT_DESTROY = EVENT_OBJECT_SHOW = EVENT_OBJECT_NAMECHANGE = 0
    OBJID_WINDOW = WINEVENT_OUTOFCONTEXT = WINEVENT_SKIPOWNPROCESS = 0
    PM_REMOVE = WM_QUIT = WM_TIMER = 0
    WinEventProcType = None
    EnumWindowsProc = None

PID_NAME_CACHE_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 1.0

# ---------------------------------------------------------------------------
# Helper: resolve process name from PID without psutil (pure ctypes)
# ---------------------------------------------------------------------------

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _process_name_from_pid(pid: int) -> str:
    """Return the executable base name for *pid*, or '' on failure."""
    if not sys.platform.startswith('win'):
        return ''
    try:
        handle = ctypes.windll.kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
        )
        if not handle:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size),
            )
            if ok and buf.value:
                return os.path.basename(buf.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------------------
# Main watcher process
# ---------------------------------------------------------------------------

class GameWindowWatcherProcess(mp.Process):
    """
    Detect game window open/close via WinEvent hooks (preferred) or
    EnumWindows polling (fallback).  Results are placed into *result_queue*.
    """

    def __init__(
        self,
        result_queue: mp.Queue,
        target_process_names: list,
        target_window_titles: list,
        excluded_window_titles: list,
        log_level: int = logging.WARNING,
    ):
        super().__init__(name='GameWindowWatcherProcess', daemon=True)
        self._result_queue = result_queue
        self._target_names = {n.lower() for n in target_process_names}
        self._target_titles = list(target_window_titles)
        self._excluded_titles = set(excluded_window_titles)
        self._log_level = log_level

        # Populated inside run() (per-process)
        self._pid_cache: dict = {}
        self._last_state = None
        self._user32 = None
        self._kernel32 = None
        self.logger = None

    # ----- process entry-point -------------------------------------------

    def run(self):
        if not sys.platform.startswith('win'):
            return

        logging.basicConfig(
            level=self._log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        )
        self.logger = logging.getLogger('GameWindowWatcherProcess')

        try:
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
        except Exception as exc:
            self.logger.error("Failed to load user32/kernel32: %s", exc)
            return

        # Declare argument types for the Win32 functions we call frequently
        # so ctypes validates/converts parameters correctly.
        self._declare_argtypes()

        # Initial scan
        self._perform_full_scan()

        # Try to install hooks; fall back to polling on failure
        hooks = self._install_hooks()
        if hooks:
            self.logger.info(
                "Game window watcher started with %d hook(s)", len(hooks),
            )
            self._run_message_loop_with_poll(hooks)
            for h in hooks:
                try:
                    self._user32.UnhookWinEvent(h)
                except Exception:
                    pass
        else:
            self.logger.warning(
                "WinEvent hooks unavailable — using polling fallback",
            )
            self._poll_loop()

    # ----- ctypes setup --------------------------------------------------

    def _declare_argtypes(self):
        """Set argtypes / restype for frequently-used Win32 calls."""
        u = self._user32

        u.SetWinEventHook.argtypes = [
            wintypes.DWORD,   # eventMin
            wintypes.DWORD,   # eventMax
            wintypes.HMODULE, # hmodWinEventProc
            WinEventProcType, # pfnWinEventProc
            wintypes.DWORD,   # idProcess
            wintypes.DWORD,   # idThread
            wintypes.DWORD,   # dwFlags
        ]
        u.SetWinEventHook.restype = wintypes.HANDLE

        u.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        u.UnhookWinEvent.restype = wintypes.BOOL

        u.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        u.GetMessageW.restype = wintypes.BOOL

        u.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        u.PeekMessageW.restype = wintypes.BOOL

        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowW.restype = wintypes.HWND

        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.restype = ctypes.c_int

        u.GetWindowTextW.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int,
        ]
        u.GetWindowTextW.restype = ctypes.c_int

        u.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD

        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL

        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = wintypes.HWND

        u.GetWindowRect.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.RECT),
        ]
        u.GetWindowRect.restype = wintypes.BOOL

        u.SetTimer.argtypes = [
            wintypes.HWND,
            ctypes.c_size_t,   # UINT_PTR nIDEvent
            wintypes.UINT,     # uElapse
            ctypes.c_void_p,   # TIMERPROC
        ]
        u.SetTimer.restype = ctypes.c_size_t  # UINT_PTR

    # ----- hook installation ---------------------------------------------

    def _install_hooks(self):
        hooks = []
        # WINEVENT_OUTOFCONTEXT alone — we're a separate process so
        # SKIPOWNPROCESS/SKIPOWNTHREAD are not needed and can cause
        # the call to silently fail on some Windows builds.
        flags = WINEVENT_OUTOFCONTEXT

        for event_id in (
            EVENT_SYSTEM_FOREGROUND,
            EVENT_OBJECT_SHOW,
            EVENT_OBJECT_CREATE,
            EVENT_OBJECT_DESTROY,
            EVENT_OBJECT_NAMECHANGE,
        ):
            try:
                hook = self._user32.SetWinEventHook(
                    event_id, event_id,
                    None,           # hmodWinEventProc — NULL for out-of-context
                    self._callback_ref,
                    0, 0,           # all processes, all threads
                    flags,
                )
                if hook:
                    hooks.append(hook)
                else:
                    self.logger.debug(
                        "SetWinEventHook returned NULL for event 0x%X",
                        event_id,
                    )
            except Exception as exc:
                self.logger.debug(
                    "SetWinEventHook raised for 0x%X: %s", event_id, exc,
                )
        return hooks

    # ----- message loop + periodic poll ----------------------------------

    @property
    def _callback_ref(self):
        """Lazily create and cache the C callback so it stays alive."""
        cb = getattr(self, '_cb', None)
        if cb is None:
            cb = WinEventProcType(self._handle_event)
            self._cb = cb
        return cb

    def _run_message_loop_with_poll(self, hooks):
        """Process Windows messages and also run a periodic full scan.

        Hooks cover *most* window events but can miss some edge-cases
        (e.g. a game that creates its window before we install hooks).
        A lightweight timer-based poll every ~2 s ensures we stay in sync.
        """
        # Install a WM_TIMER so we get periodic poll ticks inside the
        # message loop without needing a second thread.
        POLL_TIMER_ID = 1
        POLL_MS = 2000
        self._user32.SetTimer(None, POLL_TIMER_ID, POLL_MS, None)

        msg = wintypes.MSG()
        while True:
            ret = self._user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == WM_TIMER:
                self._perform_full_scan()
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

    # ----- pure polling loop (fallback) ----------------------------------

    def _poll_loop(self):
        """Simple EnumWindows polling — used when hooks are unavailable."""
        while True:
            self._perform_full_scan()
            time.sleep(POLL_INTERVAL_SECONDS)

    # ----- scanning / checking -------------------------------------------

    def _perform_full_scan(self):
        """Enumerate all top-level windows looking for the game."""
        found = False

        # Fast path: FindWindowW with known titles
        for title in self._target_titles:
            hwnd = self._user32.FindWindowW(None, title)
            if hwnd and self._check_and_report(hwnd):
                found = True
                break

        if found:
            return

        # Slow path: EnumWindows
        if EnumWindowsProc is not None:
            container = {'found': False}

            @EnumWindowsProc
            def _cb(hwnd, _):
                try:
                    if self._check_and_report(hwnd):
                        container['found'] = True
                        return False  # stop enumeration
                except Exception:
                    pass
                return True

            try:
                self._user32.EnumWindows(_cb, 0)
            except Exception:
                pass

            if container['found']:
                return

        # Game not found
        self._report_state(None)

    def _handle_event(self, _hook, event, hwnd, id_object, _id_child, *_):
        if id_object != OBJID_WINDOW or not hwnd:
            return

        try:
            if event == EVENT_OBJECT_DESTROY:
                if self._last_state and self._last_state.get('hwnd') == hwnd:
                    self._report_state(None)
                return

            self._check_and_report(hwnd)
        except Exception:
            pass

    def _check_and_report(self, hwnd) -> bool:
        """Return True if *hwnd* is the game window and report its state."""
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_value = int(pid.value)
        if not pid_value:
            return False

        name = self._resolve_process_name(pid_value)
        if not name or name.lower() not in self._target_names:
            return False

        title = self._read_window_text(hwnd)
        if not title:
            return False
        if title in self._excluded_titles:
            return False

        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

        visible = bool(self._user32.IsWindowVisible(hwnd))
        focused = hwnd == self._user32.GetForegroundWindow()

        state = {
            'hwnd': int(hwnd),
            'pid': pid_value,
            'title': title,
            'rect': (rect.left, rect.top, rect.right, rect.bottom),
            'visible': visible,
            'focused': focused,
            'timestamp': time.time(),
        }
        self._report_state(state)
        return True

    # ----- state reporting -----------------------------------------------

    def _report_state(self, state):
        if self._states_equal(state, self._last_state):
            return
        self._last_state = state
        try:
            self._result_queue.put(state)
        except Exception:
            pass

    @staticmethod
    def _states_equal(s1, s2):
        if s1 is None and s2 is None:
            return True
        if s1 is None or s2 is None:
            return False
        return (
            s1.get('hwnd') == s2.get('hwnd')
            and s1.get('visible') == s2.get('visible')
            and s1.get('focused') == s2.get('focused')
            and s1.get('title') == s2.get('title')
        )

    # ----- helpers -------------------------------------------------------

    def _resolve_process_name(self, pid: int) -> str:
        now = time.time()
        cached = self._pid_cache.get(pid)
        if cached and now - cached[1] < PID_NAME_CACHE_SECONDS:
            return cached[0]

        name = _process_name_from_pid(pid)
        if not name:
            # Fallback: try psutil if available
            try:
                import psutil
                name = psutil.Process(pid).name()
            except Exception:
                name = ''

        if name:
            self._pid_cache[pid] = (name, now)
        else:
            self._pid_cache.pop(pid, None)
        return name

    def _read_window_text(self, hwnd) -> str:
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ''
        buf = ctypes.create_unicode_buffer(length + 1)
        if self._user32.GetWindowTextW(hwnd, buf, length + 1):
            return buf.value
        return ''
