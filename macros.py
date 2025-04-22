import pyautogui

# all positions are for 1920x1080 resolution

# distance between stash cells
jump = 40

def move_from_to(start_pos, end_pos, stash):
    # positions are in slotID
    # a int starting from top left going right then down
    start_row = start_pos // stash.width
    start_col = start_pos % stash.width

    end_row = end_pos // stash.width
    end_col = end_pos % stash.width

    start_x = stash.base_screen_pos.x + (jump * start_col)
    start_y = stash.base_screen_pos.y + (jump * start_row) 
    end_x = stash.base_screen_pos.x + (jump * end_col)
    end_y = stash.base_screen_pos.y + (jump * end_row)

    pyautogui.moveTo(start_x, start_y, duration=0.1)
    pyautogui.mouseDown()
    pyautogui.moveTo(end_x, end_y, duration=0.2)
    pyautogui.mouseUp()