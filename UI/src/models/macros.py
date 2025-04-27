import time
import os
import json
import re
import ctypes
import random
from src.models.point import Point

# Supported resolutions and their corresponding positions
RESOLUTION_POSITIONS = {
    (1920, 1080): {'stash': Point(1378, 199), 'inv': Point(690, 626)},
    (1680, 1050): {'stash': Point(1207, 193), 'inv': Point(605, 608)},
    (1440, 900):  {'stash': Point(1035, 165), 'inv': Point(518, 520)},
    (1366, 768):  {'stash': Point(982, 120),  'inv': Point(492, 445)},
    (1360, 768):  {'stash': Point(978, 120),  'inv': Point(490, 445)},
    (1280, 800):  {'stash': Point(917, 140),  'inv': Point(458, 462)},
    (1280, 768):  {'stash': Point(917, 120),  'inv': Point(458, 445)},
    (1280, 720):  {'stash': Point(917, 110),  'inv': Point(458, 420)},
}

# Windows API constants
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _fields_ = [("type", ctypes.c_ulong),
                ("union", _INPUT_UNION)]

SendInput = ctypes.windll.user32.SendInput

def move_mouse(x, y):
    screen_width = ctypes.windll.user32.GetSystemMetrics(0)
    screen_height = ctypes.windll.user32.GetSystemMetrics(1)

    abs_x = int(x * 65535 / (screen_width - 1))
    abs_y = int(y * 65535 / (screen_height - 1))

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
        dx=0, dy=0,
        mouseData=0,
        dwFlags=MOUSEEVENTF_LEFTDOWN,
        time=0,
        dwExtraInfo=None
    )
    SendInput(1, ctypes.byref(mouse_input), ctypes.sizeof(mouse_input))

def mouse_up():
    mouse_input = INPUT(type=0)
    mouse_input.union.mi = MOUSEINPUT(
        dx=0, dy=0,
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

def get_current_resolution():
    from src.models.appdirs import get_settings_file
    settings_file = get_settings_file()
    user_res = 'Auto'
    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)
            user_res = settings.get('resolution', 'Auto')
    except Exception:
        pass
    if user_res == 'Auto':
        res = get_game_resolution()
        if res:
            try:
                x, y = map(int, res.split('x'))
                if (x, y) in RESOLUTION_POSITIONS:
                    return (x, y)
            except Exception:
                pass
    else:
        try:
            x, y = map(int, user_res.split('x'))
            if (x, y) in RESOLUTION_POSITIONS:
                return (x, y)
        except Exception:
            pass
    return (1920, 1080)  # Default resolution

def get_screen_positions():
    res = get_current_resolution()
    return RESOLUTION_POSITIONS.get(res, RESOLUTION_POSITIONS[(1920, 1080)])

jump = 40
stash_screen_pos = get_screen_positions()['stash']
inv_screen_pos = get_screen_positions()['inv']

def get_sort_delay():
    """Get sort delay from settings"""
    from src.models.appdirs import get_settings_file
    settings_file = get_settings_file()
    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)
            return float(settings.get('sortSpeed', 0.2))
    except Exception:
        return 0.2

def move_mouse_smooth(x1, y1, x2, y2, steps=25, min_delay=0.003, max_delay=0.008):
    """
    Move the mouse smoothly from (x1, y1) to (x2, y2) in a number of small steps.
    """
    for i in range(1, steps + 1):
        t = i / steps
        # Linear interpolation
        x = int(x1 + (x2 - x1) * t)
        y = int(y1 + (y2 - y1) * t)
        move_mouse(x, y)
        time.sleep(random.uniform(min_delay, max_delay))

def move_from_to_reliable(start_stash, start_pos, end_stash, end_pos, start_width=1, start_height=1, end_width=1, end_height=1):
    """
    Most reliable mouse move and click: now uses raw Windows API SendInput.
    Adds random jitter and delay to mimic human input.
    Now uses smooth mouse movement.
    """
    DELAY = get_sort_delay()
    # Calculate center of the item at start
    start_x = start_stash.base_screen_pos.x + (jump * start_pos.x) + (jump * start_width) / 2
    start_y = start_stash.base_screen_pos.y + (jump * start_pos.y) + (jump * start_height) / 2
    # Calculate center of the item at end
    end_x = end_stash.base_screen_pos.x + (jump * end_pos.x) + (jump * end_width) / 2
    end_y = end_stash.base_screen_pos.y + (jump * end_pos.y) + (jump * end_height) / 2

    # Add random jitter (±3 pixels)
    sx = int(start_x + random.uniform(-3, 3))
    sy = int(start_y + random.uniform(-3, 3))
    ex = int(end_x + random.uniform(-3, 3))
    ey = int(end_y + random.uniform(-3, 3))

    # Move to start position smoothly from current mouse position
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    move_mouse_smooth(pt.x, pt.y, sx, sy, steps=20)
    time.sleep(DELAY + random.uniform(0, 0.07))

    # Mouse down (hold item)
    mouse_down()
    time.sleep(DELAY + random.uniform(0, 0.07))

    # Move to end position smoothly
    move_mouse_smooth(sx, sy, ex, ey, steps=25)
    time.sleep(DELAY + random.uniform(0, 0.07))

    # Mouse up (drop item)
    mouse_up()
    time.sleep(DELAY + random.uniform(0, 0.07))

    # Extra click at end for reliability
    move_mouse_smooth(ex, ey, ex, ey, steps=5)
    time.sleep((DELAY / 2) + random.uniform(0, 0.04))
    mouse_down()
    time.sleep((DELAY / 4) + random.uniform(0, 0.03))
    mouse_up()
    time.sleep((DELAY / 2) + random.uniform(0, 0.04))
