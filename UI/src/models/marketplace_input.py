"""Real Windows input + live layout for the market lister (not unit-tested)."""
import ctypes
import random
import time
from ctypes import wintypes

from src.models import macros
from src.models.marketplace_layout import build_layout

CLICK_HOLD_SECONDS = 0.12
APPROACH_OFFSET_PX = 5
APPROACH_SECONDS = 0.12
HOVER_SETTLE_SECONDS = 0.15
SAME_SPOT_PX = 2
REPEAT_CLICK_SETTLE_SECONDS = 0.05
KEY_GAP_SECONDS = 0.03
MIN_STEP_DELAY = 0.15
STEP_JITTER = 0.07
VK_BACK = 0x08
VK_A = 0x41
VK_DIGIT_0 = 0x30


class MacrosInputDriver:
    def position(self):
        pt = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            raise RuntimeError("couldn't read the mouse position")
        return (pt.x, pt.y)

    def move_to(self, x, y):
        macros.move_mouse(x, y)

    def click(self, x, y):
        # The game ignores instant clicks: arrive with a small approach move, let the
        # hover register, then hold the button briefly (verified in game).
        cx, cy = self.position()
        if abs(cx - x) <= SAME_SPOT_PX and abs(cy - y) <= SAME_SPOT_PX:
            time.sleep(REPEAT_CLICK_SETTLE_SECONDS)  # already hovering (e.g. paging): no approach needed
        else:
            macros.move_mouse(x - APPROACH_OFFSET_PX, y - APPROACH_OFFSET_PX)
            time.sleep(APPROACH_SECONDS)
            macros.move_mouse(x, y)
            time.sleep(HOVER_SETTLE_SECONDS)
        macros.mouse_down()
        try:
            time.sleep(CLICK_HOLD_SECONDS)
        finally:
            macros.mouse_up()

    def _tap(self, vk):
        macros.send_key(vk)
        time.sleep(KEY_GAP_SECONDS)
        macros.send_key(vk, key_up=True)
        time.sleep(KEY_GAP_SECONDS)

    def clear_and_type(self, text):
        if not text.isdigit():
            raise ValueError("price must be digits only")
        try:
            macros.send_key(macros.VK_CONTROL)
            self._tap(VK_A)
            macros.send_key(macros.VK_CONTROL, key_up=True)
            self._tap(VK_BACK)
            for ch in text:
                self._tap(VK_DIGIT_0 + int(ch))
        finally:
            macros.release_modifiers()


def resolution_key():
    w, h = macros.get_current_resolution()
    return f"{w}x{h}"


def _window_origin(resolution):
    if macros.get_game_window_mode() != macros.WINDOW_MODE:
        return (0, 0)
    area = macros.get_window_area_pos()
    if area and (area[2], area[3]) == tuple(resolution):
        return (area[0], area[1])
    return (0, 0)


def current_layout():
    resolution = macros.get_current_resolution()
    overrides = macros.settings_manager.get('marketplaceCalibrationOverride') or {}
    calibration = overrides.get(f"{resolution[0]}x{resolution[1]}") if isinstance(overrides, dict) else None
    return build_layout(resolution, _window_origin(resolution), calibration)


def current_tab_mapping():
    mapping = macros.settings_manager.get('stashTabMapping')
    if isinstance(mapping, list) and len(mapping) == len(macros.DEFAULT_STASH_TAB_MAPPING):
        return mapping
    return list(macros.DEFAULT_STASH_TAB_MAPPING)


def make_pause(is_cancelled):
    def pause():
        delay = max(MIN_STEP_DELAY, macros.settings_manager.get_sort_speed()) + random.uniform(0, STEP_JITTER)
        end = time.perf_counter() + delay
        while time.perf_counter() < end:
            if is_cancelled():
                return
            time.sleep(0.01)
    return pause
