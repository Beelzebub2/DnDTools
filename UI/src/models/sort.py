from src.models.stash_preview import parse_stashes
import time
from src.models.storage import Storage, StashType
import heapq
import keyboard
import os
from src.models.point import Point
from src.models.item import Item
import pygetwindow as gw

def intersects(pos1, width1, height1, pos2, width2, height2):
    if pos1.x + width1 <= pos2.x or pos2.x + width2 <= pos1.x:
        return False
    if pos1.y + height1 <= pos2.y or pos2.y + height2 <= pos1.y:
        return False
    return True

class StashSorter:
    def __init__(self, stash: Storage, inv: Storage, pack_mode: bool = False):
        self.stash = stash
        self.inv = inv
        self.cur_x = 0
        self.cur_y = 0
        self.cur_height = 0
        self.cancel_event = None
        self.pack_mode = bool(pack_mode)
        self.pack_positions = {}
        if self.pack_mode:
            self.pack_positions = self._compute_pack_plan()
            if not self.pack_positions:
                print("Pack mode plan could not be generated; falling back to sequential layout.")
                self.pack_mode = False

    def sort(self, cancel_event=None):
        self.cancel_event = cancel_event

        while self.stash.pq:
            if self.cancel_event and self.cancel_event.is_set():
                print("Sort operation cancelled")
                return False

            item = heapq.heappop(self.stash.pq)
            print("Processing item: ", item)

            if self.pack_mode:
                target_point = self.pack_positions.get(id(item))
                if target_point is None:
                    target_point = self._compute_next_sequential_position(item)
            else:
                target_point = self._compute_next_sequential_position(item)

            if target_point is None:
                print("No valid target position found; aborting sort")
                return False

            print(f"Target position: {target_point}, Current position: {item.position}")
            if target_point == item.position:
                print("Item already in correct position")
                continue

            if not self._ensure_area_available(item, target_point):
                print("Failed to clear target area; aborting sort")
                return False

            if self.cancel_event and self.cancel_event.is_set():
                print("Sort operation cancelled before final placement")
                return False

            item.stash.move(item, target_point, self.stash)
            print(f"Current stash state:\n{self.stash}")

            if self.cancel_event and self.cancel_event.is_set():
                print("Sort operation cancelled after item placement")
                return False

        return True

    def _ensure_area_available(self, item: Item, target_point: Point) -> bool:
        moved_items = set()
        for dx in range(item.width):
            for dy in range(item.height):
                x = target_point.x + dx
                y = target_point.y + dy

                if x >= self.stash.width or y >= self.stash.height:
                    print("Target position out of bounds")
                    return False

                occupying_item = self.stash.grid[x][y]
                if occupying_item == 0 or occupying_item == item or occupying_item in moved_items:
                    continue

                if self.cancel_event and self.cancel_event.is_set():
                    print("Sort operation cancelled during area clearing")
                    return False

                new_pos = self.stash.find_empty_slot(occupying_item)
                if new_pos:
                    print(f"Moving {occupying_item} to empty slot in stash")
                    self.stash.move(occupying_item, new_pos, self.stash)
                else:
                    new_pos = self.inv.find_empty_slot(occupying_item)
                    if new_pos:
                        print(f"Moving {occupying_item} to inventory")
                        self.stash.move(occupying_item, new_pos, self.inv)
                    else:
                        print("No valid positions found")
                        return False

                moved_items.add(occupying_item)

        return True

    def _compute_next_sequential_position(self, item: Item):
        if self.cur_height == 0:
            self.cur_height = item.height

        if self.cur_x + item.width > self.stash.width:
            self.cur_y += self.cur_height
            self.cur_x = 0
            self.cur_height = item.height

        if self.cur_y + item.height > self.stash.height:
            print("Sequential layout ran out of space")
            return None

        target_point = Point(self.cur_x, self.cur_y)
        self.cur_x += item.width
        self.cur_height = max(self.cur_height, item.height)
        return target_point

    def _compute_pack_plan(self):
        plan = {}
        occupancy = [[False for _ in range(self.stash.width)] for _ in range(self.stash.height)]
        temp_heap = list(self.stash.pq)
        heapq.heapify(temp_heap)

        while temp_heap:
            item = heapq.heappop(temp_heap)
            placed = False
            for y in range(0, self.stash.height - item.height + 1):
                for x in range(0, self.stash.width - item.width + 1):
                    fits = True
                    for dy in range(item.height):
                        for dx in range(item.width):
                            if occupancy[y + dy][x + dx]:
                                fits = False
                                break
                        if not fits:
                            break
                    if fits:
                        plan[id(item)] = Point(x, y)
                        for dy in range(item.height):
                            for dx in range(item.width):
                                occupancy[y + dy][x + dx] = True
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                print(f"Unable to find pack position for item {item}; aborting pack plan")
                return {}
        return plan


def main():
    def force_exit():
        print("F7 pressed. Exiting...")
        os._exit(0)
    keyboard.add_hotkey('F7', force_exit)

    # Focus the 'Dark and Darker' window before sorting
    windows = [w for w in gw.getAllWindows() if w.title == "Dark and Darker"]
    if windows:
        try:
            windows[0].activate()
            print("Focused window: Dark and Darker")
        except Exception as e:
            print(f"Error focusing window: {e}")
    else:
        print("No window with exact title 'Dark and Darker' found.")

    time.sleep(2)

    # not working
    stashes = parse_stashes({})

    stash = Storage(StashType.STORAGE.value, stashes[StashType.STORAGE.value])
    bag = stashes.get(StashType.BAG.value, [])
    inv = Storage(StashType.BAG.value, bag)

    sorter = StashSorter(stash, inv)

    print(stash)
    print(inv)
    #exit()
    sorter.sort()

if __name__ == "__main__":
    main()
