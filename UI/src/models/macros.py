import time
from src.models.point import Point
import os
import json
import re

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

def get_game_resolution():
    """Get resolution from DungeonCrawler GameUserSettings.ini file"""
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

# Get user-selected or auto-detected resolution
# settings.json should have 'resolution': 'Auto' or '1680x1050' etc.
def get_current_resolution():
    settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'settings.json')
    user_res = 'Auto'
    try:
        with open(settings_path, 'r') as f:
            settings = json.load(f)
            user_res = settings.get('resolution', 'Auto')
    except Exception:
        pass
    if user_res == 'Auto':
        res = get_game_resolution()
        if res in RESOLUTION_POSITIONS:
            return res
    else:
        try:
            x, y = map(int, user_res.split('x'))
            if (x, y) in RESOLUTION_POSITIONS:
                return (x, y)
        except Exception:
            pass
    # fallback
    return (1920, 1080)

# Get correct positions for current resolution
def get_screen_positions():
    res = get_current_resolution()
    return RESOLUTION_POSITIONS.get(res, RESOLUTION_POSITIONS[(1920, 1080)])

# distance between stash cells
jump = 40

# Use these in your logic
stash_screen_pos = get_screen_positions()['stash']
inv_screen_pos = get_screen_positions()['inv']

def get_sort_delay():
    settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'settings.json')
    try:
        with open(settings_path, 'r') as f:
            settings = json.load(f)
            return float(settings.get('sortSpeed', 0.2))
    except Exception:
        return 0.2


def move_from_to_reliable(start_stash, start_pos, end_stash, end_pos, start_width=1, start_height=1, end_width=1, end_height=1):
    """
    Most reliable mouse move and click: uses both pyautogui and pywinauto, with delays and redundancy.
    """
    DELAY = get_sort_delay()
    # Calculate center of the item at start
    start_x = start_stash.base_screen_pos.x + (jump * start_pos.x) + (jump * start_width) / 2
    start_y = start_stash.base_screen_pos.y + (jump * start_pos.y) + (jump * start_height) / 2
    # Calculate center of the item at end
    end_x = end_stash.base_screen_pos.x + (jump * end_pos.x) + (jump * end_width) / 2
    end_y = end_stash.base_screen_pos.y + (jump * end_pos.y) + (jump * end_height) / 2

    # Move to start position
    import pyautogui
    from pywinauto.mouse import move, press, release
    sx, sy = int(start_x), int(start_y)
    ex, ey = int(end_x), int(end_y)
    pyautogui.moveTo(start_x, start_y, duration=DELAY)
    move((sx, sy))
    time.sleep(DELAY)

    # Mouse down (redundant)
    pyautogui.mouseDown(button='left')
    press(button='left', coords=(sx, sy))
    time.sleep(DELAY)

    # Move to end position
    pyautogui.moveTo(end_x, end_y, duration=DELAY)
    move((ex, ey))
    time.sleep(DELAY)

    # Mouse up (redundant)
    pyautogui.mouseUp(button='left')
    release(button='left', coords=(ex, ey))
    time.sleep(DELAY)

    # Optional: repeat click at end position for extra reliability
    pyautogui.click(x=end_x, y=end_y, button='left')
    time.sleep(DELAY/2)
    press(button='left', coords=(ex, ey))
    release(button='left', coords=(ex, ey))
    time.sleep(DELAY/2)