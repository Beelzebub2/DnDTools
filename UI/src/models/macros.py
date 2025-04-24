import pyautogui
import time
from src.models.point import Point

# all positions are for 1920x1080 resolution
stash_screen_pos = Point(1390, 215)
inv_screen_pos = Point(705, 641)

# distance between stash cells
jump = 40

DELAY = 0.2

def move_from_to(start_stash, start_pos, end_stash, end_pos):
    start_x = start_stash.base_screen_pos.x + (jump * start_pos.x)
    start_y = start_stash.base_screen_pos.y + (jump * start_pos.y) 
    end_x = end_stash.base_screen_pos.x + (jump * end_pos.x)
    end_y = end_stash.base_screen_pos.y + (jump * end_pos.y)

    pyautogui.moveTo(start_x, start_y, duration=DELAY)
    pyautogui.mouseDown()
    time.sleep(DELAY)
    pyautogui.moveTo(end_x, end_y, duration=DELAY)
    pyautogui.mouseUp()
    time.sleep(DELAY)