from src.models.stash_preview import parse_stashes, ItemDataManager, StashPreviewGenerator, ItemInfo
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

    def sort(self):
        while self.stash.pq:
            item = heapq.heappop(self.stash.pq)
            print("1. ", item)

            if self.cur_height == 0:
                self.cur_height = item.height

            if self.cur_x + item.width > self.stash.width:
                self.cur_y += self.cur_height
                self.cur_height = item.height
                self.cur_x = 0

            if self.cur_y + item.height > self.stash.height:
                print("Out of space")
                return False

            for x in range(item.width):
                for y in range(item.height):
                    occupying_item = self.stash.grid[self.cur_x + x][self.cur_y + y]
                    if occupying_item != 0 and occupying_item != item:
                        new_pos = self.stash.find_empty_slot(occupying_item)
                        if new_pos:
                            if not intersects(new_pos, occupying_item.width, occupying_item.height,
                                              Point(self.cur_x, self.cur_y), item.width, item.height):
                                print("Moving Stash")
                                self.stash.move(occupying_item, new_pos, self.stash)
                                continue
                            
                        print("Cannot find valid temp location checking inv")
                        new_pos = self.inv.find_empty_slot(occupying_item)
                        if new_pos:
                            print("Moving Inv")
                            self.stash.move(occupying_item, new_pos, self.inv)
                        else:
                            print("Cannot find valid temp location")
                            print("Out of space")
                            return False

            item.stash.move(item, Point(self.cur_x, self.cur_y), self.stash)
            self.cur_x += item.width
            print(item.stash)

        return True

def main():
    def force_exit():
        print("F7 pressed. Exiting...")
        os._exit(0)
    keyboard.add_hotkey('F7', force_exit)

    time.sleep(2)

    item_data = ItemDataManager().item_data
    packet_data = ItemDataManager.load_json("data/4696745.json")
    stashes = parse_stashes(packet_data, item_data)

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
