import pyautogui
import win32api
import time

class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"Point({self.x}, {self.y})"
    
    def __eq__(self, other):
        if not isinstance(other, Point):
            return NotImplemented
        return self.x == other.x and self.y == other.y

if __name__ == "__main__":
    from PIL import ImageGrab, ImageDraw

    # Screenshot the screen
    img = ImageGrab.grab()
    draw = ImageDraw.Draw(img)

    # Box parameters
    box_size = 80  # Size of the box (adjust as needed)
    boxes = [
        (1378, 199),  # stash_screen_pos
        (690, 626)    # inv_screen_pos
    ]

    for x, y in boxes:
        draw.rectangle([x, y, x + box_size, y + box_size], outline="red", width=4)

    img.save("screenshot_with_boxes.png")
    print("Screenshot saved as screenshot_with_boxes.png")

