import ctypes
from ctypes import wintypes
import multiprocessing as mp
import threading
import time
import sys
import logging
import psutil

# Constants
if sys.platform.startswith('win'):
    EVENT_SYSTEM_FOREGROUND = 0x0003
    EVENT_OBJECT_CREATE = 0x8000
    EVENT_OBJECT_DESTROY = 0x8001
    EVENT_OBJECT_SHOW = 0x8002
    EVENT_OBJECT_NAMECHANGE = 0x800C
    OBJID_WINDOW = 0x00000000
    WINEVENT_OUTOFCONTEXT = 0x0000
    WINEVENT_SKIPOWNPROCESS = 0x0002
    WINEVENT_SKIPOWNTHREAD = 0x0004
    PM_REMOVE = 0x0001
    WM_QUIT = 0x0012
else:
    EVENT_SYSTEM_FOREGROUND = EVENT_OBJECT_CREATE = EVENT_OBJECT_DESTROY = EVENT_OBJECT_SHOW = EVENT_OBJECT_NAMECHANGE = OBJID_WINDOW = 0
    WINEVENT_OUTOFCONTEXT = WINEVENT_SKIPOWNPROCESS = WINEVENT_SKIPOWNTHREAD = 0
    PM_REMOVE = 0
    WM_QUIT = 0

PID_NAME_CACHE_SECONDS = 10.0

class GameWindowWatcherProcess(mp.Process):
    """
    Runs a Windows event loop in a separate process to detect game window events
    without blocking the main application or suffering from GIL contention.
    """
    def __init__(
        self,
        result_queue: mp.Queue,
        target_process_names: list[str],
        target_window_titles: list[str],
        excluded_window_titles: list[str],
        log_level: int = logging.WARNING
    ):
        super().__init__(name='GameWindowWatcherProcess', daemon=True)
        self._result_queue = result_queue
        self._target_names = {name.lower() for name in target_process_names}
        self._target_titles = target_window_titles
        self._excluded_titles = set(excluded_window_titles)
        self._log_level = log_level
        self._pid_cache = {}
        
        # Internal state to avoid sending duplicate updates
        self._last_state = None

    def run(self):
        if not sys.platform.startswith('win'):
            return

        # Setup logging in the new process
        logging.basicConfig(level=self._log_level, format='[%(levelname)s] %(message)s')
        self.logger = logging.getLogger('GameWindowWatcherProcess')
        
        try:
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
        except Exception as exc:
            self.logger.error("Failed to load user32/kernel32: %s", exc)
            return

        # Initial scan to find the window if it's already open
        self._perform_initial_scan()

        # Setup hooks
        WinEventProcType = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._callback = WinEventProcType(self._handle_event)
        
        hooks = []
        flags = WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS | WINEVENT_SKIPOWNTHREAD
        events = [
            EVENT_SYSTEM_FOREGROUND,
            EVENT_OBJECT_SHOW,
            EVENT_OBJECT_CREATE,
            EVENT_OBJECT_DESTROY,
            EVENT_OBJECT_NAMECHANGE
        ]
        
        for event_id in events:
            try:
                hook = self._user32.SetWinEventHook(event_id, event_id, 0, self._callback, 0, 0, flags)
                if hook:
                    hooks.append(hook)
            except Exception as exc:
                self.logger.debug("SetWinEventHook failed for %s: %s", event_id, exc)

        if not hooks:
            self.logger.error("Failed to install any window hooks")
            return

        self.logger.info("Game window watcher started with %d hooks", len(hooks))

        # Message loop
        msg = wintypes.MSG()
        while self._user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

        # Cleanup
        for hook in hooks:
            self._user32.UnhookWinEvent(hook)

    def _perform_initial_scan(self):
        """Scan for the window once at startup."""
        # We can use FindWindow for a quick check
        found = False
        for title in self._target_titles:
            hwnd = self._user32.FindWindowW(None, title)
            if hwnd:
                self._check_and_report(hwnd)
                found = True
                # Don't break, check all titles? No, usually just one game instance.
                if found: break
        
        if not found:
            # Report 'not found' state
            self._report_state(None)

    def _handle_event(self, _hook, event, hwnd, id_object, _id_child, *_):
        if id_object != OBJID_WINDOW or not hwnd:
            return
        
        try:
            if event == EVENT_OBJECT_DESTROY:
                # If the destroyed window was our tracked window, report it gone
                if self._last_state and self._last_state.get('hwnd') == hwnd:
                    self._report_state(None)
                return

            self._check_and_report(hwnd)
        except Exception:
            pass

    def _check_and_report(self, hwnd):
        # Check if this window belongs to our target process
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_value = int(pid.value)
        if not pid_value:
            return

        name = self._resolve_process_name(pid_value)
        if not name or name.lower() not in self._target_names:
            return

        # It matches the process. Check title.
        title = self._read_window_text(hwnd)
        if not title:
            return
            
        if title in self._excluded_titles:
            return

        # It's a valid game window. Get details.
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return

        visible = bool(self._user32.IsWindowVisible(hwnd))
        focused = hwnd == self._user32.GetForegroundWindow()

        state = {
            'hwnd': int(hwnd),
            'pid': pid_value,
            'title': title,
            'rect': (rect.left, rect.top, rect.right, rect.bottom),
            'visible': visible,
            'focused': focused,
            'timestamp': time.time()
        }
        
        self._report_state(state)

    def _report_state(self, state):
        # Only send if state changed (ignoring timestamp)
        if self._states_equal(state, self._last_state):
            return
            
        self._last_state = state
        try:
            # Clear queue to ensure we only have the latest state? 
            # No, queue might have other messages. But we want the latest.
            # Since we are the only producer, we can just put.
            # The consumer should drain the queue to get the latest.
            self._result_queue.put(state)
        except Exception:
            pass

    def _states_equal(self, s1, s2):
        if s1 is None and s2 is None: return True
        if s1 is None or s2 is None: return False
        return (
            s1['hwnd'] == s2['hwnd'] and
            s1['rect'] == s2['rect'] and
            s1['visible'] == s2['visible'] and
            s1['focused'] == s2['focused'] and
            s1['title'] == s2['title']
        )

    def _resolve_process_name(self, pid: int) -> str:
        now = time.time()
        cached = self._pid_cache.get(pid)
        if cached and now - cached[1] < PID_NAME_CACHE_SECONDS:
            return cached[0]
        try:
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = None
        if name:
            self._pid_cache[pid] = (name, now)
        else:
            self._pid_cache.pop(pid, None)
        return name

    def _read_window_text(self, hwnd: int) -> str:
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        if self._user32.GetWindowTextW(hwnd, buffer, length + 1):
            return buffer.value
        return ""
