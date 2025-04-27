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
    # Example calibration code (commented out)
    """
    from PIL import ImageGrab, ImageDraw, ImageTk, Image
    import tkinter as tk

    # Screenshot the screen
    img = ImageGrab.grab()
    img.save("screenshot_for_calibration.png")

    # Tkinter window for calibration
    root = tk.Tk()
    root.title("Click: 1) Stash top-left, 2) Inventory top-left")
    tk_img = ImageTk.PhotoImage(img)
    canvas = tk.Canvas(root, width=img.width, height=img.height)
    canvas.pack()
    canvas.create_image(0, 0, anchor=tk.NW, image=tk_img)

    clicks = []
    labels = ["Stash top-left", "Inventory top-left"]

    def on_click(event):
        if len(clicks) < 2:
            x, y = event.x, event.y
            clicks.append((x, y))
            canvas.create_rectangle(x, y, x+80, y+80, outline="red", width=4)
            canvas.create_text(x+40, y-10, text=labels[len(clicks)-1], fill="red", font=("Arial", 14, "bold"))
            if len(clicks) == 2:
                print(f"Stash: ({clicks[0][0]}, {clicks[0][1]})  Inventory: ({clicks[1][0]}, {clicks[1][1]})")
                with open("calibrated_points.txt", "w") as f:
                    f.write(f"stash: {clicks[0][0]}, {clicks[0][1]}\ninv: {clicks[1][0]}, {clicks[1][1]}\n")
                print("Saved to calibrated_points.txt. You can now update your RESOLUTION_POSITIONS.")

    canvas.bind("<Button-1>", on_click)
    root.mainloop()
    """

