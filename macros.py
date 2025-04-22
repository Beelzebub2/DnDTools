import pyautogui
from point import Point

# all positions are for 1920x1080 resolution
stash_screen_pos = Point(1394, 218)
inv_screen_pos = Point(705, 644)

# distance between stash cells
jump = 40

def move_from_to(start_stash, start_pos, end_stash, end_pos):
    start_x = start_stash.base_screen_pos.x + (jump * start_pos.x)
    start_y = start_stash.base_screen_pos.y + (jump * start_pos.y) 
    end_x = end_stash.base_screen_pos.x + (jump * end_pos.x)
    end_y = end_stash.base_screen_pos.y + (jump * end_pos.y)

    pyautogui.moveTo(start_x, start_y, duration=0.1)
    pyautogui.mouseDown()
    pyautogui.moveTo(end_x, end_y, duration=0.2)
    pyautogui.mouseUp()