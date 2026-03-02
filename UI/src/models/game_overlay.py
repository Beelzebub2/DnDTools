from __future__ import annotations

import logging
import math
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from pathlib import Path

try:
    import ctypes
    from ctypes import wintypes
except Exception:
    ctypes = None  # type: ignore
    wintypes = None  # type: ignore

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
class OverlayChip:
    label: str
    value: str
    detail: str = ""
    status: str = "info"


@dataclass
class OverlayState:
    visible: bool = False
    heading: str = ""
    subtitle: str = ""
    logs: List[str] = field(default_factory=list)
    status: str = "info"
    chips: List[OverlayChip] = field(default_factory=list)
    progress_current: int = 0
    progress_total: int = 0


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

    def set_chip(
        self,
        _key: str,
        *,
        label: str,
        value: str,
        detail: str = "",
        status: str = "info",
        refresh: bool = True,
    ) -> None:
        return None

    def update_sort_overview(self, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        return None

    def update_progress(self, _processed: int, _total: int) -> None:
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
        self._title_font = None
        self._heading_font_is_stock = False
        self._body_font_is_stock = False
        self._title_font_is_stock = False
        self._reposition_needed = False
        self._hide_timer: Optional[threading.Timer] = None
        self._active_session: Optional[SortOverlaySession] = None
        self._state_owner: Optional[SortOverlaySession] = None
        self._log_handler: Optional[logging.Handler] = None
        self._log_handler_registered = False
        self._gdip_token: Optional[int] = None
        self._gdiplus = None
        self._logo_hbitmap: Optional[int] = None
        self._logo_width = 0
        self._logo_height = 0
        self._pending_update = False

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
        try:
            session.begin()
        except Exception:
            logger.debug("Failed to prime overlay session", exc_info=True)
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

    def temporarily_hide_for_capture(self) -> None:
        """Hide the overlay window briefly for screen capture.

        Unlike :meth:`hide`, this does NOT alter the internal overlay state,
        so :meth:`restore_after_capture` can bring it back unchanged.
        """
        hwnd = self._hwnd
        if hwnd:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            except Exception:
                pass

    def restore_after_capture(self) -> None:
        """Restore the overlay after a :meth:`temporarily_hide_for_capture` call.

        Only shows the window if the internal state says it should be visible.
        """
        hwnd = self._hwnd
        if hwnd:
            with self._state_lock:
                should_show = self._state.visible
            if should_show:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                except Exception:
                    pass

    def show_message(
        self,
        session: "SortOverlaySession",
        *,
        heading: str,
        subtitle: str,
        status: str = "info",
        logs: Optional[List[str]] = None,
        chips: Optional[List[OverlayChip]] = None,
        visible: bool = True,
        reposition: bool = False,
        progress_current: int = 0,
        progress_total: int = 0,
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
                chips=list(chips or []),
                progress_current=max(0, int(progress_current)),
                progress_total=max(0, int(progress_total)),
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
            self._title_font, self._title_font_is_stock = _safe_create_font(-34, win32con.FW_BOLD, "Segoe UI Semibold")

            self._initialize_branding_assets()

            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            self._ready_event.set()
            if self._pending_update:
                try:
                    win32gui.PostMessage(hwnd, self.WM_OVERLAY_UPDATE, 0, 0)
                except Exception:
                    logger.debug("Failed to flush pending overlay update", exc_info=True)
            win32gui.PumpMessages()
        except Exception:
            logger.exception("Failed to start overlay window")
            self._ready_event.set()
        finally:
            if self._heading_font and win32gui and not self._heading_font_is_stock:
                win32gui.DeleteObject(self._heading_font)
            if self._body_font and win32gui and not self._body_font_is_stock:
                win32gui.DeleteObject(self._body_font)
            if self._title_font and win32gui and not self._title_font_is_stock:
                win32gui.DeleteObject(self._title_font)
            self._heading_font = None
            self._body_font = None
            self._title_font = None
            self._heading_font_is_stock = False
            self._body_font_is_stock = False
            self._title_font_is_stock = False
            self._hwnd = None
            self._dispose_branding_assets()

    def _initialize_branding_assets(self) -> None:
        if not self.enabled or ctypes is None:
            return
        if not self._start_gdiplus():
            return
        self._load_logo_bitmap()

    def _start_gdiplus(self) -> bool:
        if self._gdip_token is not None:
            return True
        if ctypes is None:
            return False
        try:
            gdiplus = ctypes.windll.gdiplus
        except AttributeError:
            return False

        class GdiplusStartupInput(ctypes.Structure):  # pragma: no cover - struct layout
            _fields_ = [
                ("GdiplusVersion", ctypes.c_uint32),
                ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", ctypes.c_uint32),
                ("SuppressExternalCodecs", ctypes.c_uint32),
            ]

        startup_input = GdiplusStartupInput(1, None, 0, 0)
        token = ctypes.c_ulong()
        status = gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(startup_input), None)
        if status != 0:
            return False
        self._gdiplus = gdiplus
        self._gdip_token = int(token.value)
        return True

    def _load_logo_bitmap(self) -> None:
        if self._gdiplus is None or self._logo_hbitmap or ctypes is None:
            return
        try:
            logo_path = Path(__file__).resolve().parents[2] / "assets" / "logo.png"
            if not logo_path.exists():
                return

            image_ptr = ctypes.c_void_p()
            status = self._gdiplus.GdipCreateBitmapFromFile(ctypes.c_wchar_p(str(logo_path)), ctypes.byref(image_ptr))
            if status != 0 or not image_ptr.value:
                return

            width = ctypes.c_uint32()
            height = ctypes.c_uint32()
            self._gdiplus.GdipGetImageWidth(image_ptr, ctypes.byref(width))
            self._gdiplus.GdipGetImageHeight(image_ptr, ctypes.byref(height))

            hbitmap = ctypes.c_void_p()
            status = self._gdiplus.GdipCreateHBITMAPFromBitmap(image_ptr, ctypes.byref(hbitmap), ctypes.c_uint32(0))
            self._gdiplus.GdipDisposeImage(image_ptr)
            if status == 0 and hbitmap.value:
                self._logo_hbitmap = int(hbitmap.value)
                self._logo_width = int(width.value)
                self._logo_height = int(height.value)
        except Exception:
            logger.debug("Failed to load overlay logo", exc_info=True)

    def _dispose_branding_assets(self) -> None:
        if self._logo_hbitmap and win32gui:
            try:
                win32gui.DeleteObject(self._logo_hbitmap)
            except Exception:
                logger.debug("Failed to delete overlay logo bitmap", exc_info=True)
        self._logo_hbitmap = None
        self._logo_width = 0
        self._logo_height = 0

        if self._gdip_token is not None and self._gdiplus is not None and ctypes is not None:
            try:
                self._gdiplus.GdiplusShutdown(ctypes.c_ulong(self._gdip_token))
            except Exception:
                logger.debug("Failed to shutdown GDI+", exc_info=True)
        self._gdip_token = None
        self._gdiplus = None

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
            header_color = tuple(min(255, c + 18) for c in bg_color)
            footer_color = tuple(max(0, c - 12) for c in bg_color)

            brush_bg = win32gui.CreateSolidBrush(win32api.RGB(*footer_color))
            win32gui.FillRect(hdc, tuple(rect), brush_bg)
            win32gui.DeleteObject(brush_bg)

            header_rect = (rect[0], rect[1], rect[2], rect[1] + 120)
            brush_header = win32gui.CreateSolidBrush(win32api.RGB(*header_color))
            win32gui.FillRect(hdc, header_rect, brush_header)
            win32gui.DeleteObject(brush_header)

            accent_rect = (rect[0], rect[1], rect[0] + 8, rect[3])
            brush_accent = win32gui.CreateSolidBrush(win32api.RGB(*accent))
            win32gui.FillRect(hdc, accent_rect, brush_accent)
            win32gui.DeleteObject(brush_accent)

            border_pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(90, 72, 48))
            old_pen = win32gui.SelectObject(hdc, border_pen)
            old_brush = win32gui.SelectObject(hdc, win32gui.GetStockObject(win32con.NULL_BRUSH))
            win32gui.RoundRect(hdc, rect[0], rect[1], rect[2], rect[3], 24, 24)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.DeleteObject(border_pen)

            win32gui.SetBkMode(hdc, win32con.TRANSPARENT)

            padding = 24
            left = rect[0] + padding + 8
            right = rect[2] - padding
            top = rect[1] + padding
            text_left = left
            text_top = top
            logo_bottom = top

            if self._logo_hbitmap and self._logo_width and self._logo_height:
                mem_dc = None
                old_bitmap = None
                try:
                    mem_dc = win32gui.CreateCompatibleDC(hdc)
                    if mem_dc:
                        old_bitmap = win32gui.SelectObject(mem_dc, self._logo_hbitmap)
                        target_height = 64
                        scale = min(target_height / float(self._logo_height), 1.0)
                        dest_height = max(32, int(self._logo_height * scale))
                        dest_width = max(32, int(self._logo_width * scale))
                        dest_width = min(dest_width, 96)
                        win32gui.SetStretchBltMode(hdc, win32con.HALFTONE)
                        win32gui.StretchBlt(
                            hdc,
                            left,
                            top,
                            dest_width,
                            dest_height,
                            mem_dc,
                            0,
                            0,
                            self._logo_width,
                            self._logo_height,
                            win32con.SRCCOPY,
                        )
                        text_left = left + dest_width + 18
                        logo_bottom = top + dest_height
                        text_top = top
                except Exception:
                    logger.debug("Failed to draw overlay logo", exc_info=True)
                finally:
                    if mem_dc:
                        if old_bitmap:
                            win32gui.SelectObject(mem_dc, old_bitmap)
                        win32gui.DeleteDC(mem_dc)

            title_text = "DnDTools"
            heading_text = state.heading or "Sorting stash"

            if self._title_font:
                prev_font = win32gui.SelectObject(hdc, self._title_font)
                win32gui.SetTextColor(hdc, win32api.RGB(241, 221, 150))
                title_rect = [text_left, text_top, right, text_top + 64]
                win32gui.DrawText(
                    hdc,
                    title_text,
                    -1,
                    tuple(title_rect),
                    win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_NOPREFIX | win32con.DT_SINGLELINE,
                )
                win32gui.SelectObject(hdc, prev_font)
                text_top = max(title_rect[3] + 6, logo_bottom + 8)

            if self._heading_font:
                prev_font = win32gui.SelectObject(hdc, self._heading_font)
                win32gui.SetTextColor(hdc, win32api.RGB(min(255, accent[0] + 5), min(255, accent[1] + 5), min(255, accent[2] + 5)))
                heading_rect = [text_left, text_top, right, text_top + 64]
                win32gui.DrawText(
                    hdc,
                    heading_text,
                    -1,
                    tuple(heading_rect),
                    win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_WORDBREAK | win32con.DT_NOPREFIX,
                )
                win32gui.SelectObject(hdc, prev_font)
                text_top = heading_rect[3] + 10

            divider_pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(80, 65, 40))
            old_pen = win32gui.SelectObject(hdc, divider_pen)
            win32gui.MoveToEx(hdc, text_left, text_top + 2)
            win32gui.LineTo(hdc, right, text_top + 2)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.DeleteObject(divider_pen)
            text_top += 12

            # ── Subtitle ──────────────────────────────────────────────
            subtitle_text = (state.subtitle or "").strip()
            if subtitle_text and self._body_font:
                prev_font = win32gui.SelectObject(hdc, self._body_font)
                win32gui.SetTextColor(hdc, win32api.RGB(220, 210, 195))
                subtitle_rect = [text_left, text_top, right, text_top + 48]
                win32gui.DrawText(
                    hdc,
                    subtitle_text,
                    -1,
                    tuple(subtitle_rect),
                    win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_WORDBREAK | win32con.DT_NOPREFIX,
                )
                win32gui.SelectObject(hdc, prev_font)
                text_top = subtitle_rect[3] + 8

            # ── Progress bar ──────────────────────────────────────────
            if state.progress_total > 0:
                bar_height = 8
                bar_left = text_left
                bar_right = right
                bar_width = max(1, bar_right - bar_left)
                fill_ratio = min(1.0, max(0.0, state.progress_current / float(state.progress_total)))
                filled_width = max(0, int(bar_width * fill_ratio))

                # Track background
                track_color = tuple(max(0, c - 8) for c in bg_color)
                track_brush = win32gui.CreateSolidBrush(win32api.RGB(*track_color))
                win32gui.FillRect(hdc, (bar_left, text_top, bar_right, text_top + bar_height), track_brush)
                win32gui.DeleteObject(track_brush)

                # Filled portion
                if filled_width > 0:
                    bar_accent = accent if fill_ratio < 1.0 else self.ACCENT_COLORS.get("success", accent)
                    fill_brush = win32gui.CreateSolidBrush(win32api.RGB(*bar_accent))
                    win32gui.FillRect(hdc, (bar_left, text_top, bar_left + filled_width, text_top + bar_height), fill_brush)
                    win32gui.DeleteObject(fill_brush)

                text_top += bar_height + 10

            # ── Chips ─────────────────────────────────────────────────
            if self._body_font:
                prev_font = win32gui.SelectObject(hdc, self._body_font)
                text_top = self._render_chips(
                    hdc,
                    state.chips,
                    text_left,
                    right,
                    text_top,
                    bg_color,
                )
                win32gui.SelectObject(hdc, prev_font)

            # ── Log entries ───────────────────────────────────────────
            visible_logs = (state.logs or [])[-4:]
            if visible_logs and self._body_font:
                # Separator line before logs
                log_sep_pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(60, 50, 35))
                old_pen = win32gui.SelectObject(hdc, log_sep_pen)
                win32gui.MoveToEx(hdc, text_left, text_top + 2)
                win32gui.LineTo(hdc, right, text_top + 2)
                win32gui.SelectObject(hdc, old_pen)
                win32gui.DeleteObject(log_sep_pen)
                text_top += 10

                prev_font = win32gui.SelectObject(hdc, self._body_font)
                win32gui.SetTextColor(hdc, win32api.RGB(180, 170, 155))
                line_height = 22
                for log_line in visible_logs:
                    log_rect = (text_left, text_top, right, text_top + line_height)
                    win32gui.DrawText(
                        hdc,
                        log_line,
                        -1,
                        log_rect,
                        win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE
                        | win32con.DT_NOPREFIX | win32con.DT_END_ELLIPSIS,
                    )
                    text_top += line_height
                win32gui.SelectObject(hdc, prev_font)
        finally:
            win32gui.EndPaint(hwnd, paint_struct)

    def _render_chips(
        self,
        hdc,
        chips: List[OverlayChip],
        left: int,
        right: int,
        top: int,
        base_bg: tuple[int, int, int],
    ) -> int:
        if not chips or not self._body_font:
            return top

        available_width = max(1, right - left)
        chip_gap = 12
        per_row = max(1, min(3, available_width // 160))
        per_row = min(per_row, len(chips)) or 1
        chip_width = max(140, (available_width - chip_gap * (per_row - 1)) // per_row)
        chip_height = 86
        text_padding = 14

        rows = math.ceil(len(chips) / per_row)

        for index, chip in enumerate(chips):
            row = index // per_row
            col = index % per_row
            chip_left = left + col * (chip_width + chip_gap)
            chip_top = top + row * (chip_height + chip_gap)
            chip_right = chip_left + chip_width
            chip_bottom = chip_top + chip_height
            accent = self.ACCENT_COLORS.get(chip.status, self.ACCENT_COLORS["info"])
            fill = tuple(min(255, int(base_bg[i] * 0.55 + accent[i] * 0.45 + 12)) for i in range(3))
            outline = tuple(min(255, accent[i] + 30) for i in range(3))

            brush = win32gui.CreateSolidBrush(win32api.RGB(*fill))
            pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(*outline))
            old_pen = win32gui.SelectObject(hdc, pen)
            old_brush = win32gui.SelectObject(hdc, brush)
            win32gui.RoundRect(hdc, chip_left, chip_top, chip_right, chip_bottom, 18, 18)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.DeleteObject(brush)
            win32gui.DeleteObject(pen)

            label_color = win32api.RGB(210, 198, 180)
            value_color = win32api.RGB(250, 236, 210)
            detail_color = win32api.RGB(195, 186, 174)

            label_rect = (chip_left + text_padding, chip_top + 8, chip_right - text_padding, chip_top + 28)
            win32gui.SetTextColor(hdc, label_color)
            win32gui.DrawText(
                hdc,
                chip.label.upper(),
                -1,
                label_rect,
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_NOPREFIX | win32con.DT_SINGLELINE,
            )

            prev_font = None
            if self._heading_font:
                prev_font = win32gui.SelectObject(hdc, self._heading_font)
            win32gui.SetTextColor(hdc, value_color)
            value_rect = (chip_left + text_padding, label_rect[3], chip_right - text_padding, label_rect[3] + 26)
            win32gui.DrawText(
                hdc,
                chip.value,
                -1,
                value_rect,
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_NOPREFIX | win32con.DT_SINGLELINE,
            )
            if prev_font:
                win32gui.SelectObject(hdc, self._body_font)

            detail_text = (chip.detail or "").strip()
            if detail_text:
                win32gui.SetTextColor(hdc, detail_color)
                detail_rect = (
                    chip_left + text_padding,
                    value_rect[3] + 6,
                    chip_right - text_padding,
                    chip_bottom - 10,
                )
                win32gui.DrawText(
                    hdc,
                    detail_text,
                    -1,
                    detail_rect,
                    win32con.DT_LEFT
                    | win32con.DT_TOP
                    | win32con.DT_WORDBREAK
                    | win32con.DT_NOPREFIX,
                )

        total_height = (chip_height * rows) + (chip_gap * (rows - 1 if rows > 1 else 0))
        return top + total_height + (12 if chips else 0)

    def _position_overlay(self, hwnd) -> None:  # pragma: no cover - GUI thread
        area = macros.get_window_area_pos()
        if area:
            left, top, width, height = area
        else:
            width = win32api.GetSystemMetrics(0)
            height = win32api.GetSystemMetrics(1)
            left = 0
            top = 0
        overlay_width = max(480, min(int(width * 0.45), 760))
        overlay_height = max(300, min(int(height * 0.50), 720))
        x = left + 20
        y = top + 20
        flags = (
            win32con.SWP_NOACTIVATE
            | win32con.SWP_NOOWNERZORDER
            | win32con.SWP_NOREDRAW
            | win32con.SWP_SHOWWINDOW
        )
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, overlay_width, overlay_height, flags)

    def _post_update(self) -> None:
        if not self.enabled:
            return
        hwnd = self._hwnd
        if not hwnd:
            self._pending_update = True
            self._ensure_thread()
            return
        try:
            win32gui.PostMessage(hwnd, self.WM_OVERLAY_UPDATE, 0, 0)
            self._pending_update = False
        except Exception:
            logger.debug("Failed to post overlay update", exc_info=True)

    def _get_state_copy(self) -> OverlayState:
        with self._state_lock:
            return OverlayState(
                visible=self._state.visible,
                heading=self._state.heading,
                subtitle=self._state.subtitle,
                logs=list(self._state.logs),
                status=self._state.status,
                chips=[OverlayChip(chip.label, chip.value, chip.detail, chip.status) for chip in self._state.chips],
                progress_current=self._state.progress_current,
                progress_total=self._state.progress_total,
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
        self.chips: List[OverlayChip] = []
        self._chip_store: Dict[str, OverlayChip] = {}
        self._chip_order: List[str] = []
        self.max_logs = manager.max_logs
        self.status = "warning" if self.countdown_seconds > 0 else "info"
        self.subtitle = "Preparing sort overlay..."
        self.heading = self._build_heading()
        self._finished = False
        self._cancel_event = threading.Event()
        self._last_log_message = None  # type: Optional[str]
        self._last_log_count = 0
        self._progress_current = 0
        self._progress_total = 0

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
                chips=self.chips,
                visible=True,
                reposition=reposition,
                progress_current=self._progress_current,
                progress_total=self._progress_total,
            )

    def begin(self) -> None:
        if self._finished:
            return
        self._refresh_overlay(reposition=True)

    def set_chip(
        self,
        key: str,
        *,
        label: str,
        value: str,
        detail: str = "",
        status: str = "info",
        refresh: bool = True,
    ) -> None:
        if self._finished:
            return
        label = (label or "").strip() or "Info"
        value = (value or "").strip()
        detail = (detail or "").strip()
        if len(value) > 32:
            value = value[:29] + "…"
        if len(detail) > 140:
            detail = detail[:137] + "…"
        normalized = (key or label).strip().lower()
        if not normalized:
            normalized = label.lower()
        self._chip_store[normalized] = OverlayChip(label=label, value=value, detail=detail, status=status)
        if normalized not in self._chip_order:
            self._chip_order.append(normalized)
        self._sync_chips()
        if refresh:
            self._refresh_overlay()

    def _sync_chips(self) -> None:
        max_visible = 5
        active = [self._chip_store[key] for key in self._chip_order if key in self._chip_store]
        if len(active) > max_visible:
            overflow = len(active) - (max_visible - 1)
            summary = OverlayChip(
                label="More",
                value=f"{overflow} extra",
                detail="Check logs for additional details.",
                status="info",
            )
            self.chips = active[: max_visible - 1] + [summary]
        else:
            self.chips = active

    def update_sort_overview(
        self,
        *,
        total_items: int,
        plan_moves: int,
        move_budget: Optional[int] = None,
        pack_mode: bool,
        stack_mode: bool,
        workspace_free: Optional[int] = None,
        workspace_target: Optional[int] = None,
        buffered_items: int = 0,
        difficulty_label: str,
        difficulty_score: float,
        difficulty_reason: str,
        difficulty_status: str = "info",
        ml_placement_active: bool = False,
        ml_risk_score: Optional[float] = None,
    ) -> None:
        if self._finished:
            return

        planned_moves = max(0, plan_moves)
        has_budget = move_budget is not None
        actual_moves = max(0, move_budget if has_budget else planned_moves)
        if has_budget and actual_moves != planned_moves:
            move_detail = f"{actual_moves} moves ({planned_moves} planned)"
        else:
            move_detail = f"{actual_moves} moves"
        mode_label = "Dense pack" if pack_mode else "Scanline"
        stack_detail = f"Stacking {'on' if stack_mode else 'off'}"
        workspace_detail_parts: List[str] = []
        workspace_status = "info"
        if workspace_target is not None:
            workspace_detail_parts.append(f"target {workspace_target}")
        if buffered_items:
            workspace_detail_parts.append(f"{buffered_items} buffered")
        elif workspace_target is not None:
            workspace_detail_parts.append("No buffer needed")
        detail_str = " · ".join(workspace_detail_parts)
        if workspace_free is not None and workspace_target is not None:
            if workspace_free < workspace_target:
                workspace_status = "warning"
            else:
                workspace_status = "success"
            workspace_detail = f"{workspace_free} free"
        elif workspace_free is not None:
            workspace_detail = f"{workspace_free} free"
        else:
            workspace_detail = "Workspace n/a"
        if detail_str:
            workspace_detail = f"{workspace_detail} · {detail_str}"

        difficulty_score = max(0.0, min(1.0, difficulty_score))
        difficulty_value = f"{difficulty_label} ({difficulty_score * 100:.0f}%)"
        difficulty_reason = difficulty_reason or "Balanced layout"

        self.set_chip(
            "items",
            label="Items",
            value=str(max(0, total_items)),
            detail=move_detail,
            status="info",
            refresh=False,
        )
        self.set_chip(
            "mode",
            label="Mode",
            value=mode_label,
            detail=stack_detail,
            status="info",
            refresh=False,
        )
        self.set_chip(
            "workspace",
            label="Workspace",
            value=workspace_detail,
            detail="",
            status=workspace_status,
            refresh=False,
        )
        self.set_chip(
            "difficulty",
            label="Difficulty",
            value=difficulty_value,
            detail=difficulty_reason,
            status=difficulty_status,
            refresh=False,
        )
        ml_value = "Active" if ml_placement_active else "Heuristic"
        ml_detail = ""
        if ml_risk_score is not None:
            ml_detail = f"Risk {ml_risk_score * 100:.0f}%"
        ml_status = "success" if ml_placement_active else "info"
        if ml_risk_score is not None and ml_risk_score > 0.5:
            ml_status = "warning"
        self.set_chip(
            "ml",
            label="ML",
            value=ml_value,
            detail=ml_detail,
            status=ml_status,
            refresh=False,
        )
        self._refresh_overlay()

    def update_progress(self, processed: int, total: int) -> None:
        if self._finished:
            return
        total = max(1, int(total))
        processed = max(0, min(int(processed), total))
        self._progress_current = processed
        self._progress_total = total
        percent = (processed / total) * 100.0
        status = "success" if processed >= total else "info"
        detail = f"{processed}/{total} moves"
        value = f"{percent:.0f}%"
        self.set_chip(
            "progress",
            label="Progress",
            value=value,
            detail=detail,
            status=status,
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
        if clean == self._last_log_message and self.logs:
            self._last_log_count += 1
            display = f"{clean} (x{self._last_log_count})"
            self.logs[-1] = display
        else:
            self._last_log_message = clean
            self._last_log_count = 1
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
