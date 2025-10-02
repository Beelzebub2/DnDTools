from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

try:  # pragma: no cover - platform specific
    import win32api  # type: ignore
    import win32con  # type: ignore
    import win32gui  # type: ignore
except Exception:  # pragma: no cover - graceful degradation when pywin32 missing
    win32api = None  # type: ignore
    win32con = None  # type: ignore
    win32gui = None  # type: ignore

from src.models import macros

logger = logging.getLogger(__name__)


@dataclass
class OverlayState:
    visible: bool = False
    heading: str = ""
    subtitle: str = ""
    logs: List[str] = field(default_factory=list)
    status: str = "info"


class NullOverlaySession:
    """Fallback session used when overlay support is unavailable."""

    finished: bool = True

    def wait_for_countdown(self) -> bool:
        return True

    def update_status(self, _subtitle: str, _status: str = "info") -> None:
        return None

    def add_log(self, _message: str) -> None:
        return None

    def finish(self, _success: bool = True, _message: Optional[str] = None) -> None:
        return None

    def force_close(self) -> None:
        return None


class GameOverlayManager:
    """Creates an always-on-top overlay above the game window during sorting."""

    WM_OVERLAY_UPDATE = 0x8001
    WM_OVERLAY_CLOSE = 0x8002

    BACKGROUND_COLOR = (28, 24, 20)
    ACCENT_COLORS = {
        "info": (228, 200, 105),
        "warning": (255, 177, 66),
        "success": (130, 214, 130),
        "error": (255, 120, 120),
    }
    STATUS_BACKGROUND = {
        "info": (28, 24, 20),
        "warning": (38, 29, 12),
        "success": (23, 33, 27),
        "error": (40, 20, 20),
    }

    def __init__(self) -> None:
        self.enabled = bool(sys.platform.startswith("win")) and win32gui is not None
        self.max_logs = 6
        self._state = OverlayState()
        self._state_lock = threading.RLock()
        self._window_thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._hwnd = None
        self._heading_font = None
        self._body_font = None
        self._heading_font_is_stock = False
        self._body_font_is_stock = False
        self._reposition_needed = False
        self._hide_timer: Optional[threading.Timer] = None
        self._active_session: Optional[SortOverlaySession] = None
        self._state_owner: Optional[SortOverlaySession] = None
        self._log_handler: Optional[logging.Handler] = None
        self._log_handler_registered = False

    # ------------------------------------------------------------------ public
    def begin_sort_session(
        self,
        countdown_seconds: float = 1.0,
        context: Optional[dict] = None,
    ) -> "SortOverlaySession | NullOverlaySession":
        if not self.enabled:
            return NullOverlaySession()

        session = SortOverlaySession(self, countdown_seconds=countdown_seconds, context=context)
        self._ensure_thread()
        self._cancel_hide_timer()
        self.hide()
        with self._state_lock:
            self._active_session = session
            self._state_owner = session
        return session

    def end_session(self, session: "SortOverlaySession") -> None:
        with self._state_lock:
            if self._active_session is session:
                self._active_session = None

    def schedule_hide(self, session: "SortOverlaySession", delay: float) -> None:
        if not self.enabled:
            return

        def _hide() -> None:
            self.hide(session=session)

        self._cancel_hide_timer()
        timer = threading.Timer(delay, _hide)
        timer.daemon = True
        self._hide_timer = timer
        timer.start()

    def hide(self, session: Optional["SortOverlaySession"] = None) -> None:
        if not self.enabled:
            return
        with self._state_lock:
            if session is not None and session is not self._state_owner:
                return
            self._state = OverlayState()
            if session is None or self._state_owner is session:
                self._state_owner = None
        self._post_update()

    def show_message(
        self,
        session: "SortOverlaySession",
        *,
        heading: str,
        subtitle: str,
        status: str = "info",
        logs: Optional[List[str]] = None,
        visible: bool = True,
        reposition: bool = False,
    ) -> None:
        if not self.enabled:
            return
        with self._state_lock:
            if session is not self._state_owner:
                return
            logs_copy = list(logs or [])
            self._state = OverlayState(
                visible=visible,
                heading=heading,
                subtitle=subtitle,
                logs=logs_copy,
                status=status,
            )
            self._reposition_needed = self._reposition_needed or reposition
        self._post_update()

    def handle_log(self, level: int, message: str, logger_name: str) -> None:
        if not self.enabled:
            return
        session = self._active_session
        if not session or session.finished:
            return
        session.add_log(_format_log(level, message, logger_name))

    def register_logging(self) -> None:
        if not self.enabled or self._log_handler_registered:
            return
        handler = self._get_or_create_log_handler()
        root = logging.getLogger()
        root.addHandler(handler)
        self._log_handler_registered = True

    # ----------------------------------------------------------------- internal
    def _get_or_create_log_handler(self) -> logging.Handler:
        if self._log_handler is not None:
            return self._log_handler
        handler = OverlayLogHandler(self)
        handler.setLevel(logging.WARNING)
        handler.addFilter(ModulePrefixFilter((
            "src.models.sort",
            "src.models.storage",
            "src.models.stash_manager",
        )))
        self._log_handler = handler
        return handler

    def _ensure_thread(self) -> None:
        if not self.enabled:
            return
        if self._window_thread and self._window_thread.is_alive():
            return
        self._ready_event.clear()
        thread = threading.Thread(target=self._thread_main, name="GameOverlayThread", daemon=True)
        self._window_thread = thread
        thread.start()
        # Wait briefly for window creation to avoid race conditions
        self._ready_event.wait(timeout=1.0)

    def _thread_main(self) -> None:  # pragma: no cover - GUI thread
        if not self.enabled or win32gui is None or win32con is None or win32api is None:
            self._ready_event.set()
            return
        try:
            class_name = "DnDToolsGameOverlay"
            wnd_class = win32gui.WNDCLASS()
            wnd_class.hInstance = win32api.GetModuleHandle(None)
            wnd_class.lpszClassName = class_name
            wnd_class.lpfnWndProc = self._wnd_proc
            wnd_class.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            wnd_class.hbrBackground = win32gui.CreateSolidBrush(win32api.RGB(*self.BACKGROUND_COLOR))
            atom = win32gui.RegisterClass(wnd_class)
            ex_style = (
                win32con.WS_EX_TOPMOST
                | win32con.WS_EX_LAYERED
                | win32con.WS_EX_TOOLWINDOW
                | win32con.WS_EX_TRANSPARENT
            )
            style = win32con.WS_POPUP
            hwnd = win32gui.CreateWindowEx(
                ex_style,
                atom,
                "DnDToolsOverlay",
                style,
                0,
                0,
                0,
                0,
                0,
                0,
                wnd_class.hInstance,
                None,
            )
            self._hwnd = hwnd
            alpha = 235
            win32gui.SetLayeredWindowAttributes(hwnd, 0, alpha, win32con.LWA_ALPHA)

            # Create fonts once for reuse
            def _safe_create_font(height: int, weight: int, name: str):
                try:
                    handle = win32gui.CreateFont(
                        height,
                        0,
                        0,
                        0,
                        weight,
                        False,
                        False,
                        False,
                        win32con.DEFAULT_CHARSET,
                        win32con.OUT_DEFAULT_PRECIS,
                        win32con.CLIP_DEFAULT_PRECIS,
                        win32con.DEFAULT_QUALITY,
                        win32con.DEFAULT_PITCH | win32con.FF_DONTCARE,
                        name,
                    )
                    if handle:
                        return handle, False
                except Exception as font_exc:
                    logger.debug("CreateFont failed for %s: %s", name, font_exc)
                stock_font_id = getattr(win32con, "DEFAULT_GUI_FONT", None)
                if stock_font_id is None:
                    stock_font_id = getattr(win32con, "SYSTEM_FONT", 13)
                try:
                    stock_handle = win32gui.GetStockObject(stock_font_id)
                except Exception as stock_exc:
                    logger.debug("GetStockObject fallback failed: %s", stock_exc)
                    stock_handle = 0
                return stock_handle, True

            self._heading_font, self._heading_font_is_stock = _safe_create_font(-28, win32con.FW_BOLD, "Segoe UI")
            self._body_font, self._body_font_is_stock = _safe_create_font(-18, win32con.FW_NORMAL, "Segoe UI")

            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            self._ready_event.set()
            win32gui.PumpMessages()
        except Exception:
            logger.exception("Failed to start overlay window")
            self._ready_event.set()
        finally:
            if self._heading_font and win32gui and not self._heading_font_is_stock:
                win32gui.DeleteObject(self._heading_font)
            if self._body_font and win32gui and not self._body_font_is_stock:
                win32gui.DeleteObject(self._body_font)
            self._heading_font = None
            self._body_font = None
            self._heading_font_is_stock = False
            self._body_font_is_stock = False
            self._hwnd = None

    def _wnd_proc(self, hwnd, msg, w_param, l_param):  # pragma: no cover - GUI thread
        if msg == self.WM_OVERLAY_UPDATE:
            state = self._get_state_copy()
            if state.visible:
                if self._reposition_needed:
                    self._position_overlay(hwnd)
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            win32gui.InvalidateRect(hwnd, None, True)
            self._reposition_needed = False
            return 0
        if msg == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == win32con.WM_ERASEBKGND:
            return 1
        if msg == self.WM_OVERLAY_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, w_param, l_param)

    def _paint(self, hwnd) -> None:  # pragma: no cover - GUI thread
        state = self._get_state_copy()
        if not state.visible:
            return
        hdc, paint_struct = win32gui.BeginPaint(hwnd)
        try:
            rect = list(win32gui.GetClientRect(hwnd))
            bg_color = self.STATUS_BACKGROUND.get(state.status, self.BACKGROUND_COLOR)
            accent = self.ACCENT_COLORS.get(state.status, self.ACCENT_COLORS["info"])

            brush_bg = win32gui.CreateSolidBrush(win32api.RGB(*bg_color))
            win32gui.FillRect(hdc, tuple(rect), brush_bg)
            win32gui.DeleteObject(brush_bg)

            accent_rect = (rect[0], rect[1], rect[0] + 6, rect[3])
            brush_accent = win32gui.CreateSolidBrush(win32api.RGB(*accent))
            win32gui.FillRect(hdc, accent_rect, brush_accent)
            win32gui.DeleteObject(brush_accent)

            padding = 18
            left = rect[0] + padding + 6
            right = rect[2] - padding
            top = rect[1] + padding

            win32gui.SetBkMode(hdc, win32con.TRANSPARENT)

            if self._heading_font:
                prev_font = win32gui.SelectObject(hdc, self._heading_font)
                win32gui.SetTextColor(hdc, win32api.RGB(241, 221, 150))
                heading_rect = [left, top, right, top + 80]
                win32gui.DrawText(
                    hdc,
                    state.heading or "Sorting stash",
                    -1,
                    tuple(heading_rect),
                    win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_NOPREFIX,
                )
                win32gui.SelectObject(hdc, prev_font)
                top = heading_rect[1] + 32

            if self._body_font:
                prev_font = win32gui.SelectObject(hdc, self._body_font)
                win32gui.SetTextColor(hdc, win32api.RGB(220, 210, 200))
                if state.subtitle:
                    subtitle_rect = [left, top, right, top + 80]
                    win32gui.DrawText(
                        hdc,
                        state.subtitle,
                        -1,
                        tuple(subtitle_rect),
                        win32con.DT_LEFT
                        | win32con.DT_TOP
                        | win32con.DT_WORDBREAK
                        | win32con.DT_NOPREFIX,
                    )
                    top = subtitle_rect[1] + 36

                for log_line in state.logs:
                    if not log_line:
                        continue
                    log_rect = [left, top, right, top + 60]
                    win32gui.DrawText(
                        hdc,
                        f"• {log_line}",
                        -1,
                        tuple(log_rect),
                        win32con.DT_LEFT
                        | win32con.DT_TOP
                        | win32con.DT_WORDBREAK
                        | win32con.DT_NOPREFIX,
                    )
                    top = log_rect[1] + 28

                win32gui.SelectObject(hdc, prev_font)
        finally:
            win32gui.EndPaint(hwnd, paint_struct)

    def _position_overlay(self, hwnd) -> None:  # pragma: no cover - GUI thread
        area = macros.get_window_area_pos()
        if area:
            left, top, width, height = area
        else:
            width = win32api.GetSystemMetrics(0)
            height = win32api.GetSystemMetrics(1)
            left = 0
            top = 0
        overlay_width = max(420, min(int(width * 0.45), 720))
        overlay_height = max(240, min(int(height * 0.35), 520))
        x = left + (width - overlay_width) // 2
        y = top + int(height * 0.18)
        flags = (
            win32con.SWP_NOACTIVATE
            | win32con.SWP_NOOWNERZORDER
            | win32con.SWP_NOREDRAW
            | win32con.SWP_NOZORDER
        )
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, overlay_width, overlay_height, flags)

    def _post_update(self) -> None:
        if not self.enabled or not self._hwnd:
            return
        win32gui.PostMessage(self._hwnd, self.WM_OVERLAY_UPDATE, 0, 0)

    def _get_state_copy(self) -> OverlayState:
        with self._state_lock:
            return OverlayState(
                visible=self._state.visible,
                heading=self._state.heading,
                subtitle=self._state.subtitle,
                logs=list(self._state.logs),
                status=self._state.status,
            )

    def _cancel_hide_timer(self) -> None:
        timer = self._hide_timer
        if timer and timer.is_alive():
            timer.cancel()
        self._hide_timer = None


class SortOverlaySession:
    def __init__(self, manager: GameOverlayManager, countdown_seconds: float, context: Optional[dict] = None) -> None:
        self.manager = manager
        self.countdown_seconds = max(0.0, float(countdown_seconds or 0.0))
        self.context = context or {}
        self.logs: List[str] = []
        self.max_logs = manager.max_logs
        self.status = "warning" if self.countdown_seconds > 0 else "info"
        self.subtitle = ""
        self.heading = self._build_heading()
        self._finished = False
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------ helpers
    def _build_heading(self) -> str:
        character_name = self.context.get("character") or self.context.get("character_id")
        character_class = self.context.get("character_class")
        stash_id = self.context.get("stash")

        if character_name and character_class:
            title = f"{character_name} ({character_class})"
        elif character_name:
            title = str(character_name)
        else:
            title = "Character stash"

        if stash_id is not None:
            return f"Sorting {title} – Stash {stash_id}"
        return f"Sorting {title}"

    def _refresh_overlay(self, *, reposition: bool = False) -> None:
        if self.manager.enabled:
            self.manager.show_message(
                self,
                heading=self.heading,
                subtitle=self.subtitle,
                status=self.status,
                logs=self.logs,
                visible=True,
                reposition=reposition,
            )

    # ------------------------------------------------------------------ API
    @property
    def finished(self) -> bool:
        return self._finished

    def wait_for_countdown(self) -> bool:
        if self.countdown_seconds <= 0:
            self.status = "info"
            self.subtitle = "Sorting in progress... Please keep the mouse still."
            self._refresh_overlay(reposition=True)
            return True

        start = time.time()
        remaining = self.countdown_seconds
        first = True
        while remaining > 0 and not self._cancel_event.is_set():
            self.status = "warning"
            self.subtitle = f"Sorting begins in {remaining:.1f}s — don't touch the mouse"
            self._refresh_overlay(reposition=first)
            first = False
            time.sleep(0.1)
            remaining = self.countdown_seconds - (time.time() - start)

        if self._cancel_event.is_set():
            return False

        self.status = "info"
        self.subtitle = "Sorting in progress... Please keep the mouse still."
        self._refresh_overlay()
        return True

    def update_status(self, subtitle: str, status: str = "info") -> None:
        if self._finished:
            return
        self.subtitle = subtitle
        self.status = status
        self._refresh_overlay()

    def add_log(self, message: str) -> None:
        if self._finished or not message:
            return
        clean = message.strip()
        if not clean:
            return
        if len(clean) > 160:
            clean = clean[:157] + "…"
        self.logs.append(clean)
        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs :]
        self._refresh_overlay()

    def finish(self, success: bool = True, message: Optional[str] = None) -> None:
        if self._finished:
            return
        self._finished = True
        self.status = "success" if success else "error"
        if message:
            self.subtitle = message
        else:
            self.subtitle = "Sorting complete!" if success else "Sorting failed."
        self._refresh_overlay()
        hide_delay = 3.0 if success else 5.0
        self.manager.schedule_hide(self, hide_delay)
        self.manager.end_session(self)

    def force_close(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.manager.hide(self)
        self.manager.end_session(self)


class OverlayLogHandler(logging.Handler):
    def __init__(self, manager: GameOverlayManager) -> None:
        super().__init__()
        self.manager = manager
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.manager.handle_log(record.levelno, message, record.name)


class ModulePrefixFilter(logging.Filter):
    def __init__(self, prefixes: Iterable[str]):
        super().__init__()
        self.prefixes = tuple(prefixes)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self.prefixes)


def _format_log(level: int, message: str, logger_name: str) -> str:
    prefix: Optional[str] = None
    if level >= logging.ERROR:
        prefix = "Error"
    elif level >= logging.WARNING:
        prefix = "Warning"

    if prefix:
        return f"{prefix}: {message}"
    return message


def register_overlay_logging() -> None:
    overlay_manager.register_logging()


overlay_manager = GameOverlayManager()

__all__ = [
    "overlay_manager",
    "register_overlay_logging",
    "NullOverlaySession",
    "SortOverlaySession",
]
