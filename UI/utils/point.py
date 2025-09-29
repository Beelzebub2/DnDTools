import logging
import time

import pyautogui
import win32api


logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Mouse position capture utility")
    res = input("Enter your screen resolution (e.g., 1920×1080): ")
    logger.info("Resolution entered: %s", res)
    logger.info("Press F7 to get mouse position (will capture 2 positions)")

    count = 0
    a = -1
    
    while count < 2:
        x, y = pyautogui.position()
        a = win32api.GetKeyState(0x76)  # F7 key
        
        if a < 0:
            output = f"Position {count + 1}: ({x}, {y})"
            logger.info(output)
            count += 1
            
        time.sleep(0.1)