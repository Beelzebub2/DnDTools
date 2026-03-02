"""Interactive calibration overlay that lets the user drag stash & inventory
grid boxes to match their actual game layout.

The overlay is a full-screen topmost Win32 window (like the sort
:class:`GridDebugOverlay`) but **interactive** -- it receives mouse and
keyboard input so the user can reposition the grids.

Usage:
    from src.models.calibration_overlay import run_calibration_overlay

    result = run_calibration_overlay()
    # result = {
    #     'saved': True,
    #     'stash': {'x': 1378, 'y': 199},
    #     'inv': {'x': 690, 'y': 626},
    #     'resolution': {'width': 1920, 'height': 1080},
    #     'jump': 40.0,
    #     'originalStash': {'x': 1378, 'y': 199},
    #     'originalInv': {'x': 690, 'y': 626},
    #     'tabOrigin': {'x': 1322, 'y': 208},
    #     'tabSpacing': 47.0,
    #     'originalTabOrigin': {'x': 1322, 'y': 208},
    #     'originalTabSpacing': 47.0,
    # }
    # -- or {'saved': False} when cancelled
"""

from __future__ import annotations

import logging
import sys
import threading
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

# Grid dimensions (must match the sort engine)
_STASH_COLS, _STASH_ROWS = 12, 20
_INV_COLS, _INV_ROWS = 10, 5


class CalibrationOverlay:
    """Full-screen interactive overlay for manual grid calibration.

    Modelled closely after :class:`GridDebugOverlay` -- same window creation,
    font handling, and GDI drawing patterns -- but interactive (no
    ``WS_EX_TRANSPARENT``) so that mouse / keyboard events are delivered.
    """

    # -- Colours --
    BG_COLOR: Tuple[int, int, int] = (10, 8, 6)
    STASH_COLOR: Tuple[int, int, int] = (0, 230, 120)
    STASH_FILL: Tuple[int, int, int] = (0, 50, 25)
    INV_COLOR: Tuple[int, int, int] = (255, 200, 50)
    INV_FILL: Tuple[int, int, int] = (60, 48, 10)
    TAB_COLOR: Tuple[int, int, int] = (100, 160, 255)
    TAB_FILL: Tuple[int, int, int] = (20, 30, 60)
    HIGHLIGHT: Tuple[int, int, int] = (255, 255, 255)
    BORDER_WIDTH = 3
    OVERLAY_ALPHA = 210

    def __init__(self) -> None:
        self._enabled = sys.platform.startswith("win") and win32gui is not None
        self._hwnd: Optional[int] = None
        self._done = threading.Event()
        self._ready = threading.Event()
        self._result: dict = {"saved": False}

        # Current positions (mutated during drag)
        self._stash_x = 0
        self._stash_y = 0
        self._inv_x = 0
        self._inv_y = 0
        self._jump = 40.0
        self._resolution: Tuple[int, int] = (1920, 1080)

        # Originals (for reset)
        self._orig_stash: Tuple[int, int] = (0, 0)
        self._orig_inv: Tuple[int, int] = (0, 0)

        # Stash tab selector positions (vertical column of boxes)
        self._tab_x = 0
        self._tab_y = 0
        self._tab_spacing = 47.0
        self._tab_count = 8
        self._tab_labels: list = []
        self._orig_tab: Tuple[int, int] = (0, 0)
        self._orig_tab_spacing = 47.0

        # Interaction state
        self._dragging: Optional[str] = None  # 'stash' | 'inv' | 'tabs' | None
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._selected: Optional[str] = None  # last-interacted box (arrow keys)

        # Resize state
        self._resizing: Optional[str] = None       # 'stash' | 'inv' | None
        self._resize_corner: Optional[str] = None  # 'tl' | 'tr' | 'bl' | 'br'
        self._resize_anchor: Tuple[int, int] = (0, 0)
        self._orig_jump: float = 40.0
        self.RESIZE_HANDLE = 18  # px from a corner that triggers resize

        # Fonts (created per session, cleaned up after)
        self._label_font: Optional[int] = None
        self._small_font: Optional[int] = None

        # Window class
        self._class_registered = False
        self._class_atom: Optional[int] = None

    # ---------------------------------------------------------------- public

    def open(self) -> dict:
        """Open the calibration overlay.  **Blocks** until Save or Cancel."""
        if not self._enabled:
            return {"saved": False, "error": "Win32 overlay not available on this platform"}

        # Snapshot current detected positions
        try:
            positions = macros.get_screen_positions()
            self._resolution = macros.get_current_resolution()
        except Exception:
            logger.debug("Cannot read screen positions for calibration", exc_info=True)
            return {"saved": False, "error": "Failed to read screen positions"}

        jump = float(positions.get("jump", 40))
        stash = positions["stash"]
        inv = positions["inv"]

        self._jump = jump
        self._stash_x = int(stash.x)
        self._stash_y = int(stash.y)
        self._inv_x = int(inv.x)
        self._inv_y = int(inv.y)

        # Stash tab selectors
        tab_origin = positions.get("stash_tab_origin")
        if tab_origin:
            self._tab_x = int(tab_origin.x)
            self._tab_y = int(tab_origin.y)
        self._tab_spacing = float(positions.get("stash_tab_spacing", 47))
        self._tab_count = macros.STASH_TAB_COUNT
        self._tab_labels = list(macros.STASH_TAB_LABELS)

        # Use uncalibrated base positions for the "R" reset so that
        # pressing R truly returns to the auto-detected defaults rather
        # than the previously saved calibration.
        try:
            base = macros.get_base_screen_positions()
            self._orig_stash = (int(base["stash"].x), int(base["stash"].y))
            self._orig_inv = (int(base["inv"].x), int(base["inv"].y))
            self._orig_jump = float(base.get("jump", jump))
            base_tab = base.get("stash_tab_origin")
            if base_tab:
                self._orig_tab = (int(base_tab.x), int(base_tab.y))
            else:
                self._orig_tab = (self._tab_x, self._tab_y)
            self._orig_tab_spacing = float(base.get("stash_tab_spacing", self._tab_spacing))
        except Exception:
            self._orig_stash = (self._stash_x, self._stash_y)
            self._orig_inv = (self._inv_x, self._inv_y)
            self._orig_jump = self._jump
            self._orig_tab = (self._tab_x, self._tab_y)
            self._orig_tab_spacing = self._tab_spacing
        self._dragging = None
        self._resizing = None
        self._resize_corner = None
        self._selected = None

        self._done.clear()
        self._ready.clear()
        self._result = {"saved": False}

        thread = threading.Thread(target=self._run, daemon=True, name="CalibrationOverlay")
        thread.start()
        self._ready.wait(timeout=3.0)
        self._done.wait(timeout=300.0)  # generous timeout (5 min)

        return self._result

    # ------------------------------------------------------------- internals

    def _run(self) -> None:  # pragma: no cover - GUI thread
        try:
            cls_name = "DnDToolsCalibration"

            # Always unregister the old class so we get a fresh registration.
            # This avoids the stale-class-atom problem when the module-level
            # singleton is reused across calls.
            if self._class_registered:
                try:
                    win32gui.UnregisterClass(cls_name, win32api.GetModuleHandle(None))
                except Exception:
                    pass
                self._class_registered = False
                self._class_atom = None

            wc = win32gui.WNDCLASS()
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = cls_name
            wc.lpfnWndProc = self._wnd_proc
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            wc.hbrBackground = win32gui.CreateSolidBrush(
                win32api.RGB(*self.BG_COLOR)
            )
            try:
                self._class_atom = win32gui.RegisterClass(wc)
            except Exception:
                self._class_atom = cls_name
            self._class_registered = True

            screen_w = win32api.GetSystemMetrics(0)
            screen_h = win32api.GetSystemMetrics(1)

            # Topmost + layered + tool-window (no taskbar entry)
            # NOTE: WS_EX_TRANSPARENT is deliberately omitted so mouse
            #       events are delivered to this window, unlike the
            #       click-through GridDebugOverlay.
            ex_style = (
                win32con.WS_EX_TOPMOST
                | win32con.WS_EX_LAYERED
                | win32con.WS_EX_TOOLWINDOW
            )

            hwnd = win32gui.CreateWindowEx(
                ex_style,
                self._class_atom or cls_name,
                "DnDCalibration",
                win32con.WS_POPUP,
                0, 0, screen_w, screen_h,
                0, 0,
                win32api.GetModuleHandle(None),
                None,
            )
            self._hwnd = hwnd

            # Semi-transparent overlay.  No colour-key so the entire
            # surface captures mouse input.
            win32gui.SetLayeredWindowAttributes(
                hwnd, 0, self.OVERLAY_ALPHA, win32con.LWA_ALPHA,
            )

            # Fonts -- same pattern as the working GridDebugOverlay
            self._label_font = self._create_font(-16, win32con.FW_BOLD)
            self._small_font = self._create_font(-12, win32con.FW_NORMAL)

            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            win32gui.InvalidateRect(hwnd, None, True)
            self._ready.set()

            # Block on the message pump until WM_QUIT
            win32gui.PumpMessages()
        except Exception:
            logger.debug("Calibration overlay failed", exc_info=True)
        finally:
            self._cleanup_fonts()
            self._hwnd = None
            self._done.set()

    # ------------------------------------------------------------- helpers

    def _create_font(self, height: int, weight: int) -> Optional[int]:
        """Create a GDI font handle -- same approach as GridDebugOverlay."""
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
        for attr in ("_label_font", "_small_font"):
            handle = getattr(self, attr, None)
            if handle and win32gui:
                try:
                    win32gui.DeleteObject(handle)
                except Exception:
                    pass
            setattr(self, attr, None)

    # -- geometry --

    def _stash_rect(self) -> Tuple[int, int, int, int]:
        w = int(_STASH_COLS * self._jump)
        h = int(_STASH_ROWS * self._jump)
        return (self._stash_x, self._stash_y, self._stash_x + w, self._stash_y + h)

    def _inv_rect(self) -> Tuple[int, int, int, int]:
        w = int(_INV_COLS * self._jump)
        h = int(_INV_ROWS * self._jump)
        return (self._inv_x, self._inv_y, self._inv_x + w, self._inv_y + h)

    def _tab_box_size(self) -> int:
        """Side length for each tab selector box."""
        return max(10, int(self._jump * 0.85))

    def _tab_rects(self) -> list:
        """Return a list of (x1, y1, x2, y2) rects for each stash tab box.

        Each rect is **centred** on the tab position point so the boxes align
        with the in-game tab button centres.
        """
        sz = self._tab_box_size()
        half = sz // 2
        rects = []
        for i in range(self._tab_count):
            cx = self._tab_x
            cy = int(round(self._tab_y + i * self._tab_spacing))
            rects.append((cx - half, cy - half, cx - half + sz, cy - half + sz))
        return rects

    def _tabs_bounding_rect(self) -> Tuple[int, int, int, int]:
        """Bounding rect enclosing all tab boxes (used for hit-testing the column)."""
        sz = self._tab_box_size()
        half = sz // 2
        last_y = int(round(self._tab_y + (self._tab_count - 1) * self._tab_spacing))
        return (self._tab_x - half, self._tab_y - half,
                self._tab_x - half + sz, last_y - half + sz)

    @staticmethod
    def _point_in_rect(px: int, py: int, rect: Tuple[int, int, int, int]) -> bool:
        return rect[0] <= px <= rect[2] and rect[1] <= py <= rect[3]

    def _corner_at_point(self, px: int, py: int, rect: Tuple[int, int, int, int]) -> Optional[str]:
        """Return 'tl','tr','bl','br' if *(px, py)* is near a corner of *rect*."""
        x1, y1, x2, y2 = rect
        h = self.RESIZE_HANDLE
        if abs(px - x1) <= h and abs(py - y1) <= h:
            return "tl"
        if abs(px - x2) <= h and abs(py - y1) <= h:
            return "tr"
        if abs(px - x1) <= h and abs(py - y2) <= h:
            return "bl"
        if abs(px - x2) <= h and abs(py - y2) <= h:
            return "br"
        return None

    @staticmethod
    def _unpack_lparam(lp: int) -> Tuple[int, int]:
        """Extract signed (x, y) from an LPARAM."""
        x = lp & 0xFFFF
        y = (lp >> 16) & 0xFFFF
        if x >= 0x8000:
            x -= 0x10000
        if y >= 0x8000:
            y -= 0x10000
        return x, y

    # -- result builder --

    def _save_result(self) -> None:
        self._result = {
            "saved": True,
            "stash": {"x": self._stash_x, "y": self._stash_y},
            "inv": {"x": self._inv_x, "y": self._inv_y},
            "resolution": {"width": self._resolution[0], "height": self._resolution[1]},
            "jump": self._jump,
            "originalStash": {"x": self._orig_stash[0], "y": self._orig_stash[1]},
            "originalInv": {"x": self._orig_inv[0], "y": self._orig_inv[1]},
            "originalJump": self._orig_jump,
            "tabOrigin": {"x": self._tab_x, "y": self._tab_y},
            "tabSpacing": self._tab_spacing,
            "originalTabOrigin": {"x": self._orig_tab[0], "y": self._orig_tab[1]},
            "originalTabSpacing": self._orig_tab_spacing,
        }

    # -- Win32 message handler --

    def _wnd_proc(self, hwnd, msg, wp, lp):  # pragma: no cover
        if msg == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == win32con.WM_ERASEBKGND:
            return 1
        if msg == win32con.WM_SETCURSOR:
            # Let the OS set the default cursor; we override in WM_MOUSEMOVE.
            return win32gui.DefWindowProc(hwnd, msg, wp, lp)
        if msg == win32con.WM_LBUTTONDOWN:
            self._on_mouse_down(hwnd, lp)
            return 0
        if msg == win32con.WM_MOUSEMOVE:
            self._on_mouse_move(hwnd, lp)
            return 0
        if msg == win32con.WM_LBUTTONUP:
            self._on_mouse_up(hwnd)
            return 0
        if msg == win32con.WM_KEYDOWN:
            self._on_key_down(hwnd, wp)
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wp, lp)

    # -- mouse --

    def _on_mouse_down(self, hwnd, lp) -> None:
        px, py = self._unpack_lparam(lp)
        stash_r = self._stash_rect()
        inv_r = self._inv_rect()

        # Check corners first -- resize takes priority over drag
        for which, rect in [("stash", stash_r), ("inv", inv_r)]:
            corner = self._corner_at_point(px, py, rect)
            if corner:
                self._resizing = which
                self._selected = which
                self._resize_corner = corner
                x1, y1, x2, y2 = rect
                anchors = {"tl": (x2, y2), "tr": (x1, y2), "bl": (x2, y1), "br": (x1, y1)}
                self._resize_anchor = anchors[corner]
                win32gui.SetCapture(hwnd)
                win32gui.InvalidateRect(hwnd, None, False)
                return

        # Full-box hit -> drag
        if self._point_in_rect(px, py, stash_r):
            self._dragging = "stash"
            self._selected = "stash"
            self._drag_offset_x = px - self._stash_x
            self._drag_offset_y = py - self._stash_y
            win32gui.SetCapture(hwnd)
        elif self._point_in_rect(px, py, inv_r):
            self._dragging = "inv"
            self._selected = "inv"
            self._drag_offset_x = px - self._inv_x
            self._drag_offset_y = py - self._inv_y
            win32gui.SetCapture(hwnd)
        elif self._point_in_rect(px, py, self._tabs_bounding_rect()):
            # Clicking any tab box drags the whole column
            self._dragging = "tabs"
            self._selected = "tabs"
            self._drag_offset_x = px - self._tab_x
            self._drag_offset_y = py - self._tab_y
            win32gui.SetCapture(hwnd)
        else:
            self._selected = None

        win32gui.InvalidateRect(hwnd, None, False)

    def _on_mouse_move(self, hwnd, lp) -> None:
        px, py = self._unpack_lparam(lp)

        # -- active resize --
        if self._resizing is not None:
            ax, ay = self._resize_anchor
            raw_w = abs(px - ax)
            raw_h = abs(py - ay)

            cols = _STASH_COLS if self._resizing == "stash" else _INV_COLS
            rows = _STASH_ROWS if self._resizing == "stash" else _INV_ROWS

            jump_w = raw_w / cols if cols else self._jump
            jump_h = raw_h / rows if rows else self._jump
            # Weight by cell count: the axis with more cells provides a
            # more precise estimate (mouse imprecision is smaller relative
            # to the longer span).  Round to nearest 0.5 to avoid tiny
            # drift that accumulates across 20 rows during sorting.
            weighted = (jump_w * cols + jump_h * rows) / (cols + rows)
            new_jump = max(15.0, min(120.0, round(weighted * 2.0) / 2.0))

            self._jump = new_jump
            actual_w = int(cols * new_jump)
            actual_h = int(rows * new_jump)

            if self._resize_corner == "tl":
                nx, ny = ax - actual_w, ay - actual_h
            elif self._resize_corner == "tr":
                nx, ny = ax, ay - actual_h
            elif self._resize_corner == "bl":
                nx, ny = ax - actual_w, ay
            else:  # br
                nx, ny = ax, ay

            if self._resizing == "stash":
                self._stash_x, self._stash_y = nx, ny
            else:
                self._inv_x, self._inv_y = nx, ny

            win32gui.InvalidateRect(hwnd, None, False)
            return

        # -- active drag --
        if self._dragging is not None:
            new_x = px - self._drag_offset_x
            new_y = py - self._drag_offset_y

            if self._dragging == "stash":
                self._stash_x = new_x
                self._stash_y = new_y
            elif self._dragging == "inv":
                self._inv_x = new_x
                self._inv_y = new_y
            elif self._dragging == "tabs":
                self._tab_x = new_x
                self._tab_y = new_y

            win32gui.InvalidateRect(hwnd, None, False)
            return

        # -- hover: update cursor based on what's under the pointer --
        stash_r = self._stash_rect()
        inv_r = self._inv_rect()
        tab_br = self._tabs_bounding_rect()
        cursor = win32con.IDC_ARROW
        for rect in (stash_r, inv_r):
            corner = self._corner_at_point(px, py, rect)
            if corner:
                cursor = (
                    win32con.IDC_SIZENWSE if corner in ("tl", "br")
                    else win32con.IDC_SIZENESW
                )
                break
            if self._point_in_rect(px, py, rect):
                cursor = win32con.IDC_SIZEALL
                break
        else:
            if self._point_in_rect(px, py, tab_br):
                cursor = win32con.IDC_SIZEALL
        try:
            win32gui.SetCursor(win32gui.LoadCursor(0, cursor))
        except Exception:
            pass

    def _on_mouse_up(self, hwnd) -> None:
        if self._resizing:
            self._resizing = None
            self._resize_corner = None
            win32gui.ReleaseCapture()
            win32gui.InvalidateRect(hwnd, None, False)
            return
        if self._dragging:
            self._dragging = None
            win32gui.ReleaseCapture()
            win32gui.InvalidateRect(hwnd, None, False)

    # -- keyboard --

    def _on_key_down(self, hwnd, vk) -> None:
        # Escape -> cancel
        if vk == win32con.VK_ESCAPE:
            self._result = {"saved": False}
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return

        # Enter -> save
        if vk == win32con.VK_RETURN:
            self._save_result()
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return

        # R -> reset to auto-detected positions AND cell size
        if vk == ord("R"):
            self._stash_x, self._stash_y = self._orig_stash
            self._inv_x, self._inv_y = self._orig_inv
            self._jump = self._orig_jump
            self._tab_x, self._tab_y = self._orig_tab
            self._tab_spacing = self._orig_tab_spacing
            win32gui.InvalidateRect(hwnd, None, False)
            return

        # +/= -> increase cell size (or tab spacing if tabs selected)
        # -/_ -> decrease cell size (or tab spacing if tabs selected)
        # VK_OEM_PLUS=0xBB  VK_OEM_MINUS=0xBD  (not in win32con)
        if vk in (0xBB, win32con.VK_ADD):
            shift_held = win32api.GetKeyState(win32con.VK_SHIFT) < 0
            if self._selected == "tabs":
                self._tab_spacing = min(200.0, self._tab_spacing + (5.0 if shift_held else 1.0))
            else:
                self._jump = min(120.0, self._jump + (5.0 if shift_held else 1.0))
            win32gui.InvalidateRect(hwnd, None, False)
            return
        if vk in (0xBD, win32con.VK_SUBTRACT):
            shift_held = win32api.GetKeyState(win32con.VK_SHIFT) < 0
            if self._selected == "tabs":
                self._tab_spacing = max(5.0, self._tab_spacing - (5.0 if shift_held else 1.0))
            else:
                self._jump = max(15.0, self._jump - (5.0 if shift_held else 1.0))
            win32gui.InvalidateRect(hwnd, None, False)
            return

        # Arrow keys -> nudge selected box (Shift = 10 px)
        if self._selected and vk in (
            win32con.VK_LEFT, win32con.VK_RIGHT,
            win32con.VK_UP, win32con.VK_DOWN,
        ):
            shift_held = win32api.GetKeyState(win32con.VK_SHIFT) < 0
            step = 10 if shift_held else 1
            dx, dy = 0, 0
            if vk == win32con.VK_LEFT:
                dx = -step
            elif vk == win32con.VK_RIGHT:
                dx = step
            elif vk == win32con.VK_UP:
                dy = -step
            elif vk == win32con.VK_DOWN:
                dy = step

            if self._selected == "stash":
                self._stash_x += dx
                self._stash_y += dy
            elif self._selected == "inv":
                self._inv_x += dx
                self._inv_y += dy
            elif self._selected == "tabs":
                self._tab_x += dx
                self._tab_y += dy

            win32gui.InvalidateRect(hwnd, None, False)

    # -- drawing --
    #
    #  Paint DIRECTLY to hdc (no double-buffer) -- exactly like the
    #  working GridDebugOverlay.  This ensures that if a later drawing
    #  step fails, earlier ones (background, grids) are still visible.
    #

    def _paint(self, hwnd) -> None:  # pragma: no cover
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            client = win32gui.GetClientRect(hwnd)
            screen_w = client[2]

            # Background
            bg_brush = win32gui.CreateSolidBrush(win32api.RGB(*self.BG_COLOR))
            win32gui.FillRect(hdc, client, bg_brush)
            win32gui.DeleteObject(bg_brush)

            # Grids
            self._draw_grid(hdc, "stash")
            self._draw_grid(hdc, "inv")

            # Stash tab selectors
            self._draw_tab_selectors(hdc)
        except Exception:
            logger.debug("Calibration paint error", exc_info=True)
        finally:
            win32gui.EndPaint(hwnd, ps)

    def _draw_grid(self, hdc, which: str) -> None:
        if which == "stash":
            x, y = self._stash_x, self._stash_y
            cols, rows = _STASH_COLS, _STASH_ROWS
            color = self.STASH_COLOR
            fill = self.STASH_FILL
            label = "Stash"
            is_sel = self._selected == "stash"
        else:
            x, y = self._inv_x, self._inv_y
            cols, rows = _INV_COLS, _INV_ROWS
            color = self.INV_COLOR
            fill = self.INV_FILL
            label = "Inventory"
            is_sel = self._selected == "inv"

        jump = self._jump
        w = int(cols * jump)
        h = int(rows * jump)

        # -- fill --
        fill_brush = win32gui.CreateSolidBrush(win32api.RGB(*fill))
        win32gui.FillRect(hdc, (x, y, x + w, y + h), fill_brush)
        win32gui.DeleteObject(fill_brush)

        # -- inner grid lines (dimmed) --
        dim = tuple(max(2, c // 2) for c in color)
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

        # -- outer border (highlight if selected) --
        border_color = self.HIGHLIGHT if is_sel else color
        border_w = self.BORDER_WIDTH + (1 if is_sel else 0)
        border_pen = win32gui.CreatePen(
            win32con.PS_SOLID, border_w, win32api.RGB(*border_color),
        )
        old_pen = win32gui.SelectObject(hdc, border_pen)
        old_brush = win32gui.SelectObject(
            hdc, win32gui.GetStockObject(win32con.NULL_BRUSH),
        )
        win32gui.Rectangle(hdc, x, y, x + w, y + h)
        win32gui.SelectObject(hdc, old_brush)
        win32gui.SelectObject(hdc, old_pen)
        win32gui.DeleteObject(border_pen)

        # -- corner resize handles --
        handle = max(14, int(jump * 0.5))
        corners = [
            (x, y),                              # tl
            (x + w - handle, y),                  # tr
            (x, y + h - handle),                  # bl
            (x + w - handle, y + h - handle),     # br
        ]
        handle_brush = win32gui.CreateSolidBrush(win32api.RGB(*border_color))
        for cx, cy in corners:
            win32gui.FillRect(hdc, (cx, cy, cx + handle, cy + handle), handle_brush)
        win32gui.DeleteObject(handle_brush)

        # inner notch (gives a "grip" look)
        notch = max(4, handle // 3)
        notch_brush = win32gui.CreateSolidBrush(win32api.RGB(*fill))
        for cx, cy in corners:
            nx = cx + (handle - notch) // 2
            ny = cy + (handle - notch) // 2
            win32gui.FillRect(hdc, (nx, ny, nx + notch, ny + notch), notch_brush)
        win32gui.DeleteObject(notch_brush)

        # -- label above the grid (DrawText, same as GridDebugOverlay) --
        win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        label_text = f"{label}  ({x}, {y})"

        if self._label_font:
            old_font = win32gui.SelectObject(hdc, self._label_font)
            # shadow
            win32gui.SetTextColor(hdc, win32api.RGB(0, 0, 0))
            win32gui.DrawText(
                hdc, label_text, -1,
                (x + 3, y - 28, x + w + 200, y - 2),
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )
            # main
            win32gui.SetTextColor(hdc, win32api.RGB(*color))
            win32gui.DrawText(
                hdc, label_text, -1,
                (x + 2, y - 29, x + w + 200, y - 3),
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )
            win32gui.SelectObject(hdc, old_font)

        # -- detail line below the grid (cell size) --
        detail = f"cell {self._jump:.1f}px   {self._resolution[0]}\u00d7{self._resolution[1]}"
        if self._small_font:
            old_font = win32gui.SelectObject(hdc, self._small_font)
            win32gui.SetTextColor(hdc, win32api.RGB(200, 200, 200))
            detail_rect = (x + 2, y + h + 4, x + w + 2, y + h + 24)
            win32gui.DrawText(
                hdc, detail, -1, detail_rect,
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )
            win32gui.SelectObject(hdc, old_font)

    def _draw_tab_selectors(self, hdc) -> None:
        """Draw stash tab selector boxes as a vertical column."""
        is_sel = self._selected == "tabs"
        color = self.TAB_COLOR
        fill = self.TAB_FILL
        border_color = self.HIGHLIGHT if is_sel else color
        sz = self._tab_box_size()
        rects = self._tab_rects()

        for i, (x1, y1, x2, y2) in enumerate(rects):
            # Fill
            fill_brush = win32gui.CreateSolidBrush(win32api.RGB(*fill))
            win32gui.FillRect(hdc, (x1, y1, x2, y2), fill_brush)
            win32gui.DeleteObject(fill_brush)

            # Border
            bw = 2 + (1 if is_sel else 0)
            pen = win32gui.CreatePen(win32con.PS_SOLID, bw, win32api.RGB(*border_color))
            old_pen = win32gui.SelectObject(hdc, pen)
            old_brush = win32gui.SelectObject(
                hdc, win32gui.GetStockObject(win32con.NULL_BRUSH),
            )
            win32gui.Rectangle(hdc, x1, y1, x2, y2)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.DeleteObject(pen)

            # Tab number inside box
            win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
            if self._small_font:
                old_font = win32gui.SelectObject(hdc, self._small_font)
                win32gui.SetTextColor(hdc, win32api.RGB(*color))
                label_text = str(i + 1)
                win32gui.DrawText(
                    hdc, label_text, -1,
                    (x1, y1, x2, y2),
                    win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
                )
                win32gui.SelectObject(hdc, old_font)

            # Tab name to the right of the box
            if self._small_font and i < len(self._tab_labels):
                old_font = win32gui.SelectObject(hdc, self._small_font)
                win32gui.SetTextColor(hdc, win32api.RGB(*color))
                win32gui.DrawText(
                    hdc, self._tab_labels[i], -1,
                    (x2 + 4, y1, x2 + 200, y2),
                    win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
                )
                win32gui.SelectObject(hdc, old_font)

        # Label above the column
        win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        label = f"Tabs  ({self._tab_x}, {self._tab_y})  spacing {self._tab_spacing:.0f}"
        if self._label_font:
            old_font = win32gui.SelectObject(hdc, self._label_font)
            # shadow
            win32gui.SetTextColor(hdc, win32api.RGB(0, 0, 0))
            win32gui.DrawText(
                hdc, label, -1,
                (self._tab_x + 3, self._tab_y - 28, self._tab_x + 400, self._tab_y - 2),
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )
            # main
            win32gui.SetTextColor(hdc, win32api.RGB(*color))
            win32gui.DrawText(
                hdc, label, -1,
                (self._tab_x + 2, self._tab_y - 29, self._tab_x + 400, self._tab_y - 3),
                win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )
            win32gui.SelectObject(hdc, old_font)


# ---------------------------------------------------------------- module API

_calibration_instance = CalibrationOverlay()


def run_calibration_overlay() -> dict:
    """Open the calibration overlay and block until the user saves or cancels.

    Returns a dict with ``'saved': True`` and position data on success,
    or ``'saved': False`` when cancelled or on error.
    """
    try:
        return _calibration_instance.open()
    except Exception:
        logger.debug("run_calibration_overlay failed", exc_info=True)
        return {"saved": False, "error": "Overlay failed to open"}
