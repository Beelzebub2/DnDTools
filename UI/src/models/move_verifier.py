"""Screen-based move verification using Win32 BitBlt region capture.

Captures small pixel regions at source and destination slots before and after
a drag-and-drop move.  If the pixels changed at either location the move is
considered verified.  Adds ~10-25 ms overhead per move (negligible compared
to the 100-300 ms move delay).
"""

import ctypes
import ctypes.wintypes
import logging
import struct
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

import numpy as np

from src.models import macros

logger = logging.getLogger(__name__)

SRCCOPY = 0x00CC0020
_RENDER_DELAY = 0.08  # 80 ms — wait for game to render after macro
_OVERLAY_SETTLE = 0.03  # 30 ms — wait for DWM to composite after overlay hide


@dataclass
class MoveResult:
    """Outcome of a single move verification check."""
    verified: bool
    source_changed: bool
    dest_changed: bool
    source_confidence: float
    dest_confidence: float


class MoveVerifier:
    """Lightweight screen-capture verifier for item drag-and-drop moves.

    Parameters
    ----------
    enabled : bool
        Master switch.  When False no captures are taken.
    capture_size : int
        Side length (px) of the square region captured at each slot.
    change_threshold : float
        Mean absolute pixel-difference above which a region is considered
        to have changed.
    overlay_hide : callable or None
        Called (no args) before every batch of captures to hide any overlay
        window that might cover the stash grid.
    overlay_show : callable or None
        Called (no args) after captures to restore the overlay.
    """

    def __init__(
        self,
        enabled: bool = True,
        capture_size: int = 20,
        change_threshold: float = 8.0,
        overlay_hide: Optional[Callable] = None,
        overlay_show: Optional[Callable] = None,
    ):
        self.enabled = enabled
        self._capture_size = capture_size
        self._change_threshold = change_threshold
        self._overlay_hide = overlay_hide
        self._overlay_show = overlay_show

    # ── Overlay helpers ───────────────────────────────────────────────

    def _hide_overlay(self) -> None:
        if self._overlay_hide is not None:
            try:
                self._overlay_hide()
            except Exception:
                pass

    def _show_overlay(self) -> None:
        if self._overlay_show is not None:
            try:
                self._overlay_show()
            except Exception:
                pass

    # ── Screen capture ────────────────────────────────────────────────

    def capture_region(self, center_x: int, center_y: int) -> np.ndarray:
        """Capture a small square region around (*center_x*, *center_y*).

        Uses Win32 BitBlt from the screen DC — typically 2-5 ms for a
        20x20 px region.  Returns an (H, W, 3) uint8 numpy array (BGR).
        """
        half = self._capture_size // 2
        left = int(center_x) - half
        top = int(center_y) - half
        w = self._capture_size
        h = self._capture_size

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        old_bmp = gdi32.SelectObject(hdc_mem, hbmp)

        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, left, top, SRCCOPY)

        # Read pixel data via GetDIBits with a BITMAPINFOHEADER
        bmi = struct.pack(
            "IiiHHIIiiII",
            40,  # biSize
            w,   # biWidth
            -h,  # biHeight (negative = top-down)
            1,   # biPlanes
            32,  # biBitCount (BGRA)
            0,   # biCompression (BI_RGB)
            0,   # biSizeImage
            0,   # biXPelsPerMeter
            0,   # biYPelsPerMeter
            0,   # biClrUsed
            0,   # biClrImportant
        )
        buffer = (ctypes.c_char * (w * h * 4))()
        gdi32.GetDIBits(
            hdc_mem, hbmp, 0, h, buffer,
            ctypes.c_char_p(bmi), 0,
        )

        # Cleanup GDI objects
        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

        pixels = np.frombuffer(buffer, dtype=np.uint8).reshape(h, w, 4)
        return pixels[:, :, :3].copy()  # drop alpha, return BGR

    # ── Comparison ────────────────────────────────────────────────────

    def regions_differ(
        self, before: np.ndarray, after: np.ndarray,
    ) -> Tuple[bool, float]:
        """Return (changed, confidence) based on mean absolute pixel diff."""
        diff = float(
            np.mean(np.abs(before.astype(np.int16) - after.astype(np.int16)))
        )
        return (diff > self._change_threshold, diff)

    # ── Grid → screen coordinate helper ───────────────────────────────

    @staticmethod
    def _slot_screen_center(
        stash, grid_x: int, grid_y: int, item_w: int, item_h: int,
    ) -> Tuple[int, int]:
        """Convert grid coordinates to screen pixel centre.

        Mirrors the calculation in ``macros.move_from_to_reliable``.
        """
        jump = macros.jump
        base = stash.base_screen_pos
        # base may be a Point or a plain tuple
        bx = getattr(base, "x", None)
        if bx is None:
            bx, by = base[0], base[1]
        else:
            by = base.y
        cx = int(bx + jump * grid_x + (jump * item_w) / 2)
        cy = int(by + jump * grid_y + (jump * item_h) / 2)
        return cx, cy

    # ── Pre / post workflow ───────────────────────────────────────────

    def pre_capture(
        self,
        src_stash,
        src_pos,
        dst_stash,
        dst_pos,
        item_w: int,
        item_h: int,
    ) -> Optional[Dict]:
        """Capture source and destination regions *before* the move.

        Returns a dict with the captured arrays, or ``None`` when disabled.
        """
        if not self.enabled:
            return None

        src_cx, src_cy = self._slot_screen_center(
            src_stash, src_pos.x, src_pos.y, item_w, item_h,
        )
        dst_cx, dst_cy = self._slot_screen_center(
            dst_stash, dst_pos.x, dst_pos.y, item_w, item_h,
        )

        self._hide_overlay()
        time.sleep(_OVERLAY_SETTLE)
        try:
            src_before = self.capture_region(src_cx, src_cy)
            dst_before = self.capture_region(dst_cx, dst_cy)
        finally:
            self._show_overlay()

        return {
            "src_before": src_before,
            "dst_before": dst_before,
            "src_center": (src_cx, src_cy),
            "dst_center": (dst_cx, dst_cy),
        }

    def post_verify(
        self,
        src_stash,
        src_pos,
        dst_stash,
        dst_pos,
        item_w: int,
        item_h: int,
        pre_data: Dict,
    ) -> MoveResult:
        """Capture source and destination *after* the move and compare.

        Waits briefly for the game to render, nudges the cursor away from
        the destination, hides the overlay, then captures both regions.
        """
        src_cx, src_cy = pre_data["src_center"]
        dst_cx, dst_cy = pre_data["dst_center"]

        # Wait for game to render the move
        time.sleep(_RENDER_DELAY)

        # Nudge cursor away from the destination centre to avoid
        # cursor pixels producing a false positive
        macros.nudge_cursor(dx=15, dy=0)

        self._hide_overlay()
        time.sleep(_OVERLAY_SETTLE)
        try:
            src_after = self.capture_region(src_cx, src_cy)
            dst_after = self.capture_region(dst_cx, dst_cy)
        finally:
            self._show_overlay()

        src_changed, src_conf = self.regions_differ(
            pre_data["src_before"], src_after,
        )
        dst_changed, dst_conf = self.regions_differ(
            pre_data["dst_before"], dst_after,
        )

        # Verified if EITHER source cleared OR destination filled.
        # Using OR because the overlay may partially cover one slot
        # but not the other, and a single confirmed change is strong
        # enough evidence that something happened.
        verified = src_changed or dst_changed

        return MoveResult(
            verified=verified,
            source_changed=src_changed,
            dest_changed=dst_changed,
            source_confidence=src_conf,
            dest_confidence=dst_conf,
        )
