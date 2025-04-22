from stash_preview import parse_stashes, ItemDataManager, StashPreviewGenerator, ItemInfo
import time
from stash import Stash
import heapq
import keyboard
import os
from point import Point

def sort(stash):
    cur_x = 0
    cur_y = 0
    cur_height = 0
    while stash.pq:
        item = heapq.heappop(stash.pq)
        print("1. ", item)

        # for first row
        if cur_height == 0:
            cur_height = item.height

        # check bounds and start a new row
        if cur_x + item.width > stash.width:
            cur_y += cur_height
            cur_height = item.height
            cur_x = 0
        
        if cur_y + item.height > stash.height:
            print("Out of space")
            return

        # clear space
        for x in range(item.width):
            for y in range(item.height):
                occupying_item = stash.grid[cur_x + x][cur_y + y]
                if occupying_item != 0 and occupying_item != item:
                    new_pos = stash.find_empty_slot(occupying_item)
                    if new_pos:
                        stash.move(occupying_item, new_pos, stash)
                    else:
                        print("Cannot find valid temp location")

        stash.move(item, Point(cur_x, cur_y), stash)
        cur_x += item.width

def main():
    # not ideal way to exit but it works
    def force_exit():
        print("F7 pressed. Exiting...")
        os._exit(0)
    keyboard.add_hotkey('F7', force_exit)

    time.sleep(2)

    item_data = ItemDataManager().item_data

    packet_data = ItemDataManager.load_json("packet_data.json")
    stashes = parse_stashes(packet_data, item_data)

    for stash_type, data in stashes.items():
        stash = Stash(stash_type, data)
        print(stash)
        sort(stash)
        exit()

if __name__ == "__main__":
    main()
