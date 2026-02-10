"""Lightweight overlay that draws coloured outlines around detected stash / inventory
grid areas so the user can visually verify whether the macro target regions line up
with the game UI.

Usage (from the sort flow):
    from src.models.grid_debug_overlay import show_grid_overlay
    show_grid_overlay(duration=3.0)   # non-blocking, auto-closes after *duration* seconds
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Optional, Tuple

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
except Exception:  # pragma: no cover
    win32api = None  # type: ignore
    win32con = None  # type: ignore
    win32gui = None  # type: ignore

from src.models import macros

logger = logging.getLogger(__name__)

# Colour used as the transparency key – anything painted in this exact colour
# becomes fully see-through.  Pick a near-black that will never match real content.
_TRANSPARENT_COLOR = (1, 1, 1)

# Default grid dimensions (stash 12×20, inventory 10×5)
_STASH_COLS, _STASH_ROWS = 12, 20
_INV_COLS, _INV_ROWS = 10, 5


class GridDebugOverlay:
    """Full-screen click-through overlay that highlights stash & inventory grids."""

    STASH_COLOR: Tuple[int, int, int] = (0, 230, 120)       # bright green
    STASH_FILL: Tuple[int, int, int] = (0, 80, 40)          # dark green fill
    INVENTORY_COLOR: Tuple[int, int, int] = (255, 200, 50)   # gold
    INVENTORY_FILL: Tuple[int, int, int] = (80, 65, 15)      # dark gold fill
    LABEL_SHADOW: Tuple[int, int, int] = (0, 0, 0)
    BORDER_WIDTH = 3

    def __init__(self) -> None:
        self._enabled = sys.platform.startswith("win") and win32gui is not None
        self._hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._close_event = threading.Event()
        self._font: Optional[int] = None
        self._small_font: Optional[int] = None
        self._class_registered = False
        self._class_atom: Optional[int] = None
        # Updated each time the overlay is shown so _paint reads fresh positions
        self._positions: Optional[dict] = None
        self._resolution: Optional[tuple] = None

    # -------------------------------------------------------------- public API

    def show(self, duration: float = 3.0) -> None:
        """Show the grid overlay for *duration* seconds (non-blocking)."""
        if not self._enabled:
            return

        # Snapshot current positions so the paint handler uses consistent data
        try:
            self._positions = macros.get_screen_positions()
            self._resolution = macros.get_current_resolution()
        except Exception:
            logger.debug("Failed to read screen positions for grid overlay", exc_info=True)
            return

        self._close_event.clear()
        self._ready.clear()

        thread = threading.Thread(
            target=self._run,
            args=(duration,),
            daemon=True,
            name="GridDebugOverlay",
        )
        self._thread = thread
        thread.start()
        # Wait briefly so the window is created before we return
        self._ready.wait(timeout=2.0)

    def close(self) -> None:
        """Programmatically close the overlay early."""
        hwnd = self._hwnd
        if hwnd and win32gui:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass

    # --------------------------------------------------------------- internals

    def _run(self, duration: float) -> None:  # pragma: no cover - GUI thread
        try:
            cls_name = "DnDToolsGridDebug"

            # Register window class once per process
            if not self._class_registered:
                wc = win32gui.WNDCLASS()
                wc.hInstance = win32api.GetModuleHandle(None)
                wc.lpszClassName = cls_name
                wc.lpfnWndProc = self._wnd_proc
                wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
                wc.hbrBackground = win32gui.CreateSolidBrush(
                    win32api.RGB(*_TRANSPARENT_COLOR)
                )
                try:
                    self._class_atom = win32gui.RegisterClass(wc)
                except Exception:
                    # Class may already exist from a previous invocation
                    self._class_atom = cls_name
                self._class_registered = True

            screen_w = win32api.GetSystemMetrics(0)
            screen_h = win32api.GetSystemMetrics(1)

            ex_style = (
                win32con.WS_EX_TOPMOST
                | win32con.WS_EX_LAYERED
                | win32con.WS_EX_TRANSPARENT
                | win32con.WS_EX_TOOLWINDOW
            )
            hwnd = win32gui.CreateWindowEx(
                ex_style,
                self._class_atom or cls_name,
                "DnDGridDebug",
                win32con.WS_POPUP,
                0, 0, screen_w, screen_h,
                0, 0,
                win32api.GetModuleHandle(None),
                None,
            )
            self._hwnd = hwnd

            # Color-key: _TRANSPARENT_COLOR becomes fully see-through.
            # Alpha 220 makes drawn elements slightly translucent.
            win32gui.SetLayeredWindowAttributes(
                hwnd,
                win32api.RGB(*_TRANSPARENT_COLOR),
                220,
                win32con.LWA_COLORKEY | win32con.LWA_ALPHA,
            )

            self._font = self._create_font(-22, win32con.FW_BOLD)
            self._small_font = self._create_font(-16, win32con.FW_NORMAL)

            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            win32gui.InvalidateRect(hwnd, None, True)
            self._ready.set()

            # Schedule auto-close after *duration* seconds
            def _auto_close() -> None:
                time.sleep(max(0.1, duration))
                if self._hwnd:
                    try:
                        win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
                    except Exception:
                        pass

            threading.Thread(target=_auto_close, daemon=True).start()
            win32gui.PumpMessages()
        except Exception:
            logger.debug("Grid debug overlay failed", exc_info=True)
        finally:
            self._cleanup_fonts()
            self._hwnd = None
            self._close_event.set()

    def _create_font(self, height: int, weight: int) -> Optional[int]:
        if not win32gui:
            return None
        try:
            return win32gui.CreateFont(
                height, 0, 0, 0, weight,
                False, False, False,
                win32con.DEFAULT_CHARSET,
                win32con.OUT_DEFAULT_PRECIS,
                win32con.CLIP_DEFAULT_PRECIS,
                win32con.CLEARTYPE_QUALITY,
                win32con.DEFAULT_PITCH | win32con.FF_DONTCARE,
                "Segoe UI",
            )
        except Exception:
            return None

    def _cleanup_fonts(self) -> None:
        for attr in ("_font", "_small_font"):
            handle = getattr(self, attr, None)
            if handle and win32gui:
                try:
                    win32gui.DeleteObject(handle)
                except Exception:
                    pass
            setattr(self, attr, None)

    # ---------------------------------------------------------- window message

    def _wnd_proc(self, hwnd, msg, wp, lp):  # pragma: no cover
        if msg == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == win32con.WM_ERASEBKGND:
            return 1
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wp, lp)

    # ----------------------------------------------------------------- drawing

    def _paint(self, hwnd) -> None:  # pragma: no cover
        positions = self._positions
        if not positions:
            return

        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            client = win32gui.GetClientRect(hwnd)

            # Clear background with the transparency key colour
            bg_brush = win32gui.CreateSolidBrush(win32api.RGB(*_TRANSPARENT_COLOR))
            win32gui.FillRect(hdc, client, bg_brush)
            win32gui.DeleteObject(bg_brush)

            jump = float(positions["jump"])
            stash_pos = positions["stash"]
            inv_pos = positions["inv"]
            res = self._resolution or macros.get_current_resolution()
            res_label = f"{res[0]}×{res[1]}"
            is_uw = macros._is_ultrawide(res) if hasattr(macros, "_is_ultrawide") else False
            res_suffix = " (ultrawide)" if is_uw else ""

            # ── Stash grid ──────────────────────────────────────────
            self._draw_grid(
                hdc, stash_pos, _STASH_COLS, _STASH_ROWS, jump,
                self.STASH_COLOR, self.STASH_FILL,
                f"Stash  {_STASH_COLS}×{_STASH_ROWS}",
                f"jump={jump:.1f}px   res={res_label}{res_suffix}",
            )

            # ── Inventory grid ──────────────────────────────────────
            self._draw_grid(
                hdc, inv_pos, _INV_COLS, _INV_ROWS, jump,
                self.INVENTORY_COLOR, self.INVENTORY_FILL,
                f"Inventory  {_INV_COLS}×{_INV_ROWS}",
                f"top-left=({int(inv_pos.x)}, {int(inv_pos.y)})",
            )
        except Exception:
            logger.debug("Grid overlay paint error", exc_info=True)
        finally:
            win32gui.EndPaint(hwnd, ps)

    def _draw_grid(
        self,
        hdc,
        pos,
        cols: int,
        rows: int,
        jump: float,
        color: Tuple[int, int, int],
        fill_color: Tuple[int, int, int],
        label: str,
        detail: str,
    ) -> None:
        x = int(pos.x)
        y = int(pos.y)
        w = int(cols * jump)
        h = int(rows * jump)

        # ── Semi-transparent fill ────────────────────────────────────
        fill_brush = win32gui.CreateSolidBrush(win32api.RGB(*fill_color))
        win32gui.FillRect(hdc, (x, y, x + w, y + h), fill_brush)
        win32gui.DeleteObject(fill_brush)

        # ── Inner grid lines (thin, dimmed) ──────────────────────────
        dim = tuple(max(2, c // 2) for c in color)  # avoid (0,0,0) and (1,1,1)
        grid_pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(*dim))
        old_pen = win32gui.SelectObject(hdc, grid_pen)

        for col in range(1, cols):
            cx = x + int(col * jump)
            win32gui.MoveToEx(hdc, cx, y)
            win32gui.LineTo(hdc, cx, y + h)
        for row in range(1, rows):
            cy = y + int(row * jump)
            win32gui.MoveToEx(hdc, x, cy)
            win32gui.LineTo(hdc, x + w, cy)

        win32gui.SelectObject(hdc, old_pen)
        win32gui.DeleteObject(grid_pen)

        # ── Outer border (thicker, bright) ───────────────────────────
        border_pen = win32gui.CreatePen(
            win32con.PS_SOLID, self.BORDER_WIDTH, win32api.RGB(*color),
        )
        old_pen = win32gui.SelectObject(hdc, border_pen)
        old_brush = win32gui.SelectObject(hdc, win32gui.GetStockObject(win32con.NULL_BRUSH))
        win32gui.Rectangle(hdc, x, y, x + w, y + h)
        win32gui.SelectObject(hdc, old_brush)
        win32gui.SelectObject(hdc, old_pen)
        win32gui.DeleteObject(border_pen)

        # ── Corner markers (small squares at each corner) ────────────
        corner_size = max(6, int(jump * 0.35))
        corners = [
            (x, y),
            (x + w - corner_size, y),
            (x, y + h - corner_size),
            (x + w - corner_size, y + h - corner_size),
        ]
        corner_brush = win32gui.CreateSolidBrush(win32api.RGB(*color))
        for cx, cy in corners:
            win32gui.FillRect(hdc, (cx, cy, cx + corner_size, cy + corner_size), corner_brush)
        win32gui.DeleteObject(corner_brush)

        # ── Labels above the grid ────────────────────────────────────
        win32gui.SetBkMode(hdc, win32con.TRANSPARENT)

        if self._font:
            old_font = win32gui.SelectObject(hdc, self._font)

            # Shadow
            win32gui.SetTextColor(hdc, win32api.RGB(0, 0, 0))
            shadow = (x + 3, y - 30, x + w + 3, y - 2)
            win32gui.DrawText(
                hdc, label, -1, shadow,
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )

            # Main label
            win32gui.SetTextColor(hdc, win32api.RGB(*color))
            label_rect = (x + 2, y - 31, x + w + 2, y - 3)
            win32gui.DrawText(
                hdc, label, -1, label_rect,
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )
            win32gui.SelectObject(hdc, old_font)

        # Detail line (smaller, below the main label)
        if detail and self._small_font:
            old_font = win32gui.SelectObject(hdc, self._small_font)
            win32gui.SetTextColor(hdc, win32api.RGB(200, 200, 200))
            detail_rect = (x + 2, y + h + 4, x + w + 2, y + h + 24)
            win32gui.DrawText(
                hdc, detail, -1, detail_rect,
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )
            win32gui.SelectObject(hdc, old_font)


# ---------------------------------------------------------------- module-level

_overlay_instance = GridDebugOverlay()


def show_grid_overlay(duration: float = 3.0) -> None:
    """Display the grid debug overlay for *duration* seconds.

    Non-blocking – the overlay auto-closes when the timer expires.
    Call :func:`close_grid_overlay` to dismiss it early.
    """
    try:
        _overlay_instance.show(duration=duration)
    except Exception:
        logger.debug("show_grid_overlay failed", exc_info=True)


def close_grid_overlay() -> None:
    """Dismiss the grid overlay immediately (if visible)."""
    try:
        _overlay_instance.close()
    except Exception:
        pass
