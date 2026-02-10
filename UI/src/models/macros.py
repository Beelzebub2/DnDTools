# Rebuilt macros module with cancellation support
import ctypes
import logging
import os
import random
import re
import time

import win32gui

from src.models.point import Point
from src.models.settings import settings_manager

logger = logging.getLogger(__name__)


class MacroCancelled(RuntimeError):
    """Raised when a macro operation is aborted due to a cancellation request."""


_active_cancel_event = None


def push_cancel_event(event):
    """Register a threading.Event that signals macro cancellation."""
    global _active_cancel_event
    if event is None:
        return
    _active_cancel_event = event


def pop_cancel_event(event=None):
    """Clear the registered cancellation event if it matches the provided one."""
    global _active_cancel_event
    if event is None or _active_cancel_event is event:
        _active_cancel_event = None


def is_cancelled():
    return _active_cancel_event is not None and _active_cancel_event.is_set()


def _ensure_not_cancelled():
    if is_cancelled():
        raise MacroCancelled()


def _sleep_with_cancel(duration: float) -> None:
    if duration <= 0:
        _ensure_not_cancelled()
        return

    end_time = time.perf_counter() + duration
    while True:
        _ensure_not_cancelled()
        remaining = end_time - time.perf_counter()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 0.01))


try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass


# Note: Jump values may need adjustment for different resolutions
# Currently optimized for common gaming resolutions

# Supported resolutions are generated from a single 1920x1080 calibration
BASE_RESOLUTION = (1920, 1080)
STANDARD_ASPECT = 16.0 / 9.0  # ~1.7778 – the aspect ratio the calibration targets

BASE_LAYOUT = {
    'stash': Point(1378, 199),
    'inv': Point(690, 626),
    'jump': 40,
}

# Resolutions that benefit from hand-tuned offsets can live here
MANUAL_OVERRIDES = {
    # Keeping the empirically tested fullscreen values for 720p
    (1280, 720): {'stash': Point(918, 132), 'inv': Point(457, 416), 'jump': 27},
}

COMMON_RESOLUTIONS = [
    BASE_RESOLUTION,
    (2560, 1440),
    (3840, 2160),
    (1680, 1050),
    (1440, 900),
    (1366, 768),
    (1360, 768),
    (1280, 800),
    (1280, 768),
    (1280, 720),
    # Ultrawide 21:9
    (2560, 1080),
    (3440, 1440),
    (5120, 2160),
    # Super-ultrawide 32:9
    (3840, 1080),
    (5120, 1440),
]


def _clone_layout(layout):
    cloned = {}
    for key, value in layout.items():
        if isinstance(value, Point):
            cloned[key] = Point(value.x, value.y)
        else:
            cloned[key] = value
    return cloned


def _is_ultrawide(resolution):
    """Return True if the resolution has a wider aspect ratio than 16:9."""
    w, h = resolution
    return (w / max(1, h)) > (STANDARD_ASPECT + 0.01)


def _scaled_layout(resolution):
    w, h = resolution

    if _is_ultrawide(resolution):
        # Ultrawide / super-ultrawide: the game UI is rendered within a
        # centred 16:9 viewport.  We scale uniformly by height and add a
        # horizontal pillarbox offset so coordinates land inside that
        # viewport instead of being stretched across the full width.
        scale = h / BASE_RESOLUTION[1]
        viewport_w = h * STANDARD_ASPECT
        pillarbox = (w - viewport_w) / 2.0

        return {
            'stash': Point(int(round(BASE_LAYOUT['stash'].x * scale + pillarbox)),
                           int(round(BASE_LAYOUT['stash'].y * scale))),
            'inv': Point(int(round(BASE_LAYOUT['inv'].x * scale + pillarbox)),
                         int(round(BASE_LAYOUT['inv'].y * scale))),
            'jump': max(BASE_LAYOUT['jump'] * scale, 1.0)
        }

    # Standard (≤16:9) aspect ratio – independent axis scaling
    scale_x = w / BASE_RESOLUTION[0]
    scale_y = h / BASE_RESOLUTION[1]

    return {
        'stash': Point(int(round(BASE_LAYOUT['stash'].x * scale_x)),
                       int(round(BASE_LAYOUT['stash'].y * scale_y))),
        'inv': Point(int(round(BASE_LAYOUT['inv'].x * scale_x)),
                     int(round(BASE_LAYOUT['inv'].y * scale_y))),
        'jump': max(BASE_LAYOUT['jump'] * scale_y, 1.0)
    }


def get_positions_for_resolution(resolution):
    """Return stash/inventory/jump positions for the requested resolution."""
    if resolution in MANUAL_OVERRIDES:
        layout = _clone_layout(MANUAL_OVERRIDES[resolution])
    else:
        layout = _scaled_layout(resolution)

    if 'jump' in layout:
        layout['jump'] = float(layout['jump'])

    return layout


RESOLUTION_POSITIONS = {
    res: get_positions_for_resolution(res) for res in COMMON_RESOLUTIONS
}


def _ensure_positions(resolution):
    positions = RESOLUTION_POSITIONS.get(resolution)
    if positions is None:
        positions = get_positions_for_resolution(resolution)
        RESOLUTION_POSITIONS[resolution] = positions
    return positions

# Windows API constants
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# Virtual key codes
VK_MENU = 0x12     # Alt key
VK_CONTROL = 0x11  # Ctrl key

# Key event constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

WINDOW_MODE = 2


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
        ]

    _anonymous_ = ("union",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", _INPUT_UNION),
    ]


SendInput = ctypes.windll.user32.SendInput

INSTANT_MODE_MIN_PAUSE = 0.004  # tiny floor so instant mode still yields reliable inputs
DOUBLE_CLICK_PROTECT_WINDOW = 0.18

_last_drop_signature = None
_last_drop_time = 0.0
_last_pickup_signature = None
_last_pickup_time = 0.0


def move_mouse(x, y):
    screen_width = ctypes.windll.user32.GetSystemMetrics(0)
    screen_height = ctypes.windll.user32.GetSystemMetrics(1)

    abs_x = int(round(x * 65535 / max(1, (screen_width - 1))))
    abs_y = int(round(y * 65535 / max(1, (screen_height - 1))))

    mouse_input = INPUT(type=0)
    mouse_input.union.mi = MOUSEINPUT(
        dx=abs_x,
        dy=abs_y,
        mouseData=0,
        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
        time=0,
        dwExtraInfo=None
    )
    SendInput(1, ctypes.byref(mouse_input), ctypes.sizeof(mouse_input))


def mouse_down():
    mouse_input = INPUT(type=0)
    mouse_input.union.mi = MOUSEINPUT(
        dx=0,
        dy=0,
        mouseData=0,
        dwFlags=MOUSEEVENTF_LEFTDOWN,
        time=0,
        dwExtraInfo=None
    )
    SendInput(1, ctypes.byref(mouse_input), ctypes.sizeof(mouse_input))


def mouse_up():
    mouse_input = INPUT(type=0)
    mouse_input.union.mi = MOUSEINPUT(
        dx=0,
        dy=0,
        mouseData=0,
        dwFlags=MOUSEEVENTF_LEFTUP,
        time=0,
        dwExtraInfo=None
    )
    SendInput(1, ctypes.byref(mouse_input), ctypes.sizeof(mouse_input))


def get_game_resolution():
    config_path = os.path.expandvars(r'%LOCALAPPDATA%/DungeonCrawler/Saved/Config/Windows/GameUserSettings.ini')
    try:
        with open(config_path, 'r') as f:
            content = f.read()
            x_match = re.search(r'ResolutionSizeX=(\d+)', content)
            y_match = re.search(r'ResolutionSizeY=(\d+)', content)
            if x_match and y_match:
                return f"{x_match.group(1)}x{y_match.group(1)}"
    except Exception:
        pass
    return None


def get_game_window_mode():
    config_path = os.path.expandvars(r'%LOCALAPPDATA%/DungeonCrawler/Saved/Config/Windows/GameUserSettings.ini')
    try:
        with open(config_path, 'r') as f:
            content = f.read()
            match = re.search(r'FullscreenMode=(\d+)', content)
            if match:
                mode = int(match.group(1))
                return mode
    except Exception:
        pass
    return None


def get_current_resolution():
    user_res = settings_manager.get('resolution', 'Auto')
    resolution_str = None
    if user_res == 'Auto':
        resolution_str = get_game_resolution()
    else:
        resolution_str = user_res

    if resolution_str:
        try:
            x, y = map(int, resolution_str.split('x'))
            resolution = (x, y)
            _ensure_positions(resolution)
            return resolution
        except Exception:
            pass

    return BASE_RESOLUTION  # Fallback to calibrated default


def get_window_area_pos(window_title="Dark and Darker  "):
    hwnd = win32gui.FindWindow(None, window_title)
    if hwnd == 0:
        logger.warning("No window found with title: '%s'", window_title)
        return None

    # Get client area (content) coordinates relative to the screen
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))

    # Get client area dimensions
    rect = win32gui.GetClientRect(hwnd)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]

    return (left, top, width, height)


def get_screen_positions():
    res = get_current_resolution()

    if get_game_window_mode() == WINDOW_MODE:
        window_area = get_window_area_pos()
        if window_area:
            window_left, window_top, width, height = window_area
            if width == res[0] and height == res[1]:
                base_pos = _ensure_positions(res)
                stash = Point(base_pos["stash"].x + window_left, base_pos["stash"].y + window_top)
                inv = Point(base_pos["inv"].x + window_left, base_pos["inv"].y + window_top)
                return {'stash': stash, 'inv': inv, 'jump': float(base_pos["jump"]) }

    return _clone_layout(_ensure_positions(res))


_initial_positions = get_screen_positions()
stash_screen_pos = _initial_positions['stash']
inv_screen_pos = _initial_positions['inv']
jump = float(_initial_positions['jump'])


def get_sort_delay():
    """Get sort delay from settings"""
    return settings_manager.get_sort_speed()


def move_mouse_smooth(x1, y1, x2, y2, steps=25, min_delay=0.008, max_delay=0.01, no_delay=False):
    """
    Move the mouse smoothly from (x1, y1) to (x2, y2).

    When ``no_delay`` is True we still interpolate the path but skip the per-step sleep
    to keep movement nearly instantaneous.
    """
    _ensure_not_cancelled()
    for i in range(1, steps + 1):
        _ensure_not_cancelled()
        t = i / steps
        # Linear interpolation
        x = x1 + (x2 - x1) * t
        y = y1 + (y2 - y1) * t
        move_mouse(round(x), round(y))
        if not no_delay:
            sleep_floor = max(0.003, min_delay)
            sleep_ceiling = max(sleep_floor, max_delay)
            _sleep_with_cancel(random.uniform(sleep_floor, sleep_ceiling))


def move_from_to_reliable(start_stash, start_pos, end_stash, end_pos, start_width=1, start_height=1, end_width=1, end_height=1):
    """
    Most reliable mouse move and click: now uses raw Windows API SendInput.
    Adds random jitter and delay to mimic human input.
    Now uses smooth mouse movement with cancellation awareness.
    """
    global _last_drop_signature, _last_drop_time, _last_pickup_signature, _last_pickup_time

    _ensure_not_cancelled()
    DELAY = max(0.0, get_sort_delay() or 0.0)
    no_delay_mode = DELAY <= 0
    primary_button_held = False
    reliability_button_held = False

    def maybe_sleep(base_delay: float, jitter: float = 0.0, enforce_floor: bool = False) -> None:
        _ensure_not_cancelled()

        jitter_component = random.uniform(0, jitter) if jitter > 0 else 0.0

        if no_delay_mode:
            floor = INSTANT_MODE_MIN_PAUSE if enforce_floor else (INSTANT_MODE_MIN_PAUSE * 0.5)
            total = floor
        else:
            total = max(0.0, base_delay + jitter_component)

        if total > 0:
            _sleep_with_cancel(total)
        else:
            _ensure_not_cancelled()

    try:
        # Calculate center of the item at start
        start_x = start_stash.base_screen_pos.x + (jump * start_pos.x) + (jump * start_width) / 2
        start_y = start_stash.base_screen_pos.y + (jump * start_pos.y) + (jump * start_height) / 2
        # Calculate center of the item at end
        end_x = end_stash.base_screen_pos.x + (jump * end_pos.x) + (jump * end_width) / 2
        end_y = end_stash.base_screen_pos.y + (jump * end_pos.y) + (jump * end_height) / 2

        travel_x = abs(end_x - start_x)
        travel_y = abs(end_y - start_y)
        max_travel = max(travel_x, travel_y)
        jitter_range = 3.0
        if max_travel <= max(jump * 0.6, 12):
            jitter_range = 1.25

        sx = start_x + random.uniform(-jitter_range, jitter_range)
        sy = start_y + random.uniform(-jitter_range, jitter_range)
        ex = end_x + random.uniform(-jitter_range, jitter_range)
        ey = end_y + random.uniform(-jitter_range, jitter_range)

        # Move to start position smoothly from current mouse position
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        start_steps = 20
        if max_travel <= max(jump * 0.6, 12):
            start_steps = 24
        move_mouse_smooth(
            float(pt.x),
            float(pt.y),
            sx,
            sy,
            steps=start_steps,
            min_delay=0.001,
            max_delay=0.003,
            no_delay=no_delay_mode,
        )
        maybe_sleep(DELAY, 0.07, enforce_floor=True)

        # Protect against rapid consecutive pickups from the same slot or
        # picking up from where we just dropped — the game may interpret
        # this as a double-click / move action.
        pickup_signature = (id(start_stash), int(getattr(start_pos, "x", 0)), int(getattr(start_pos, "y", 0)))
        if no_delay_mode:
            now_pickup = time.perf_counter()
            same_pickup = (
                _last_pickup_signature == pickup_signature
                and (now_pickup - _last_pickup_time) < DOUBLE_CLICK_PROTECT_WINDOW
            )
            same_as_drop = (
                _last_drop_signature == pickup_signature
                and (now_pickup - _last_drop_time) < DOUBLE_CLICK_PROTECT_WINDOW
            )
            if same_pickup or same_as_drop:
                _sleep_with_cancel(DOUBLE_CLICK_PROTECT_WINDOW)

        _ensure_not_cancelled()
        mouse_down()
        primary_button_held = True
        maybe_sleep(DELAY, 0.07, enforce_floor=True)

        # Move to end position smoothly
        travel_steps = 25
        if max_travel <= max(jump * 0.6, 12):
            travel_steps = 28
        move_mouse_smooth(
            sx,
            sy,
            ex,
            ey,
            steps=travel_steps,
            min_delay=0.001,
            max_delay=0.003,
            no_delay=no_delay_mode,
        )
        maybe_sleep(DELAY, 0.07, enforce_floor=True)

        _ensure_not_cancelled()
        mouse_up()
        primary_button_held = False
        maybe_sleep(DELAY, 0.07, enforce_floor=True)

        drop_signature = (id(end_stash), int(getattr(end_pos, "x", 0)), int(getattr(end_pos, "y", 0)))
        now = time.perf_counter()
        should_skip_reliability_click = (
            _last_drop_signature == drop_signature and
            (now - _last_drop_time) < DOUBLE_CLICK_PROTECT_WINDOW
        )

        if not should_skip_reliability_click:
            base_reliability_delay = max((DELAY / 2) if DELAY > 0 else 0.0, 0.0)
            move_mouse_smooth(
                ex,
                ey,
                ex,
                ey,
                steps=5,
                min_delay=0.0005,
                max_delay=0.0015,
                no_delay=no_delay_mode,
            )
            maybe_sleep(base_reliability_delay, 0.02, enforce_floor=True)
            _ensure_not_cancelled()
            mouse_down()
            reliability_button_held = True
            maybe_sleep(base_reliability_delay / 2, 0.015, enforce_floor=True)
            _ensure_not_cancelled()
            mouse_up()
            reliability_button_held = False
            maybe_sleep(base_reliability_delay, 0.02, enforce_floor=True)
        else:
            logger.debug("Skipping reliability click to avoid double-click on %s", drop_signature)
            maybe_sleep(INSTANT_MODE_MIN_PAUSE, 0.005, enforce_floor=True)

        _last_pickup_signature = pickup_signature
        _last_pickup_time = now
        _last_drop_signature = drop_signature
        _last_drop_time = now
    finally:
        if reliability_button_held:
            try:
                mouse_up()
            except Exception:
                pass
        if primary_button_held:
            try:
                mouse_up()
            except Exception:
                pass


def send_key(vk_code, key_up=False):
    flags = KEYEVENTF_KEYUP if key_up else 0
    key_input = INPUT(type=INPUT_KEYBOARD)
    key_input.ki = KEYBDINPUT(
        wVk=vk_code,
        wScan=0,
        dwFlags=flags,
        time=0,
        dwExtraInfo=None
    )
    SendInput(1, ctypes.byref(key_input), ctypes.sizeof(key_input))


def send_alt_up():
    send_key(VK_MENU, True)  # Alt up


def send_ctrl_up():
    send_key(VK_CONTROL, True)  # Ctrl up


def tap_alt(delay: float = 0.035) -> None:
    """Press and release the Alt key to clear lingering modifier state."""
    send_key(VK_MENU, False)
    if delay > 0:
        time.sleep(delay)
    send_key(VK_MENU, True)


def release_modifiers():
    """Release common modifier keys that may be stuck down."""
    send_alt_up()
    send_ctrl_up()