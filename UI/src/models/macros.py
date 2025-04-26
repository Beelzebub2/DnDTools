import pyautogui
import time
from src.models.point import Point

# all positions are for 1920x1080 resolution
stash_screen_pos = Point(1378, 199)
inv_screen_pos = Point(690, 626)

# distance between stash cells
jump = 40

DELAY = 0.2

def move_from_to(start_stash, start_pos, end_stash, end_pos, start_width=1, start_height=1, end_width=1, end_height=1):
    # Calculate center of the item at start (use float division for accuracy)
    start_x = start_stash.base_screen_pos.x + (jump * start_pos.x) + (jump * start_width) / 2
    start_y = start_stash.base_screen_pos.y + (jump * start_pos.y) + (jump * start_height) / 2
    # Calculate center of the item at end
    end_x = end_stash.base_screen_pos.x + (jump * end_pos.x) + (jump * end_width) / 2
    end_y = end_stash.base_screen_pos.y + (jump * end_pos.y) + (jump * end_height) / 2

    pyautogui.moveTo(start_x, start_y, duration=DELAY)
    pyautogui.mouseDown()
    time.sleep(DELAY)
    pyautogui.moveTo(end_x, end_y, duration=DELAY)
    pyautogui.mouseUp()
    time.sleep(DELAY)