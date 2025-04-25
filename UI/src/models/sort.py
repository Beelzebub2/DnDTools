from src.models.stash_preview import parse_stashes
from src.models.game_data import ItemDataManager
import time
from src.models.storage import Storage, StashType
import heapq
import keyboard
import os
from src.models.point import Point

def intersects(pos1, width1, height1, pos2, width2, height2):
    if pos1.x + width1 <= pos2.x or pos2.x + width2 <= pos1.x:
        return False
    if pos1.y + height1 <= pos2.y or pos2.y + height2 <= pos1.y:
        return False
    return True

class StashSorter:
    def __init__(self, stash: Storage, inv: Storage):
        self.stash = stash
        self.inv = inv
        self.cur_x = 0
        self.cur_y = 0
        self.cur_height = 0
        self.cancel_event = None

    def sort(self, cancel_event=None):
        self.cancel_event = cancel_event
        
        while self.stash.pq:
            # Check for cancellation at the start of each item
            if self.cancel_event and self.cancel_event.is_set():
                print("Sort operation cancelled")
                return False
                
            item = heapq.heappop(self.stash.pq)
            print("Processing item: ", item)

            if self.cur_height == 0:
                self.cur_height = item.height

            if self.cur_x + item.width > self.stash.width:
                self.cur_y += self.cur_height
                self.cur_height = item.height
                self.cur_x = 0

            if self.cur_y + item.height > self.stash.height:
                print("Out of space")
                return False
            
            print(f"Target position: {Point(self.cur_x, self.cur_y)}, Current position: {item.position}")
            if Point(self.cur_x, self.cur_y) != item.position:
                for x in range(item.width):
                    for y in range(item.height):
                        # Check for cancellation during item placement
                        if self.cancel_event and self.cancel_event.is_set():
                            print("Sort operation cancelled during item placement")
                            return False
                            
                        occupying_item = self.stash.grid[self.cur_x + x][self.cur_y + y]
                        if occupying_item != 0 and occupying_item != item:
                            new_pos = self.stash.find_empty_slot(occupying_item)
                            if new_pos:
                                if not intersects(new_pos, occupying_item.width, occupying_item.height,
                                              item.position, item.width, item.height):
                                    print(f"Moving {occupying_item} to empty slot in stash")
                                    self.stash.move(occupying_item, new_pos, self.stash)
                                    continue

                            print(f"Checking inventory for {occupying_item}")
                            new_pos = self.inv.find_empty_slot(occupying_item)
                            if new_pos:
                                print(f"Moving {occupying_item} to inventory")
                                self.stash.move(occupying_item, new_pos, self.inv)
                            else:
                                print("No valid positions found")
                                return False

                # Check for cancellation before final item placement
                if self.cancel_event and self.cancel_event.is_set():
                    print("Sort operation cancelled before final placement")
                    return False
                    
                item.stash.move(item, Point(self.cur_x, self.cur_y), self.stash)
            else:
                print("Item already in correct position")

            self.cur_x += item.width
            print(f"Current stash state:\n{self.stash}")
            
            # Check for cancellation after item placement
            if self.cancel_event and self.cancel_event.is_set():
                print("Sort operation cancelled after item placement")
                return False

        return True
    
    def pack(self):
        # TODO
        pass


def main():
    def force_exit():
        print("F7 pressed. Exiting...")
        os._exit(0)
    keyboard.add_hotkey('F7', force_exit)

    time.sleep(2)

    stashes = parse_stashes(packet_data)

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
