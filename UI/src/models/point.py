import pyautogui
import win32api
import time

class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"Point({self.x}, {self.y})"

if __name__ == "__main__":
    a = -1
    print("Press F7 to get mouse position (x, y)")

    while True:
        x = 0
        y = 0
        x, y = pyautogui.position()
        a = win32api.GetKeyState(0x76)
        if a < 0:
            print(Point(x, y))
        time.sleep(0.1)

