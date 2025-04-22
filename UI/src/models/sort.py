from src.models.stash_preview import parse_stashes, ItemDataManager, StashPreviewGenerator, ItemInfo
import time
from src.models.storage import Storage, StashType
import heapq
import keyboard
import os
from src.models.point import Point

def intersects(pos1, width1, height1, pos2, width2, height2):
    # Check if there's no overlap on the x-axis (horizontal)
    if pos1.x + width1 <= pos2.x or pos2.x + width2 <= pos1.x:
        return False
    
    # Check if there's no overlap on the y-axis (vertical)
    if pos1.y + height1 <= pos2.y or pos2.y + height2 <= pos1.y:
        return False
    
    # If there's overlap in both x and y axes, they intersect
    return True

def sort(stash, inv):
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
                    new_pos = stash.find_empty_slot(item)
                    if new_pos:
                        if not intersects(new_pos, occupying_item.width, occupying_item.height, item.position, item.width, item.height):
                            print("Moving Stash")
                            stash.move(occupying_item, new_pos, stash)
                            continue

                    print("Cannot find valid temp location checking inv")
                    new_pos = inv.find_empty_slot(occupying_item)
                    if new_pos:
                        print("Moving Inv")
                        stash.move(occupying_item, new_pos, inv)
                    else:
                        print("Cannot find valid temp location")
                        print("Out of space")
                        exit()

        item.stash.move(item, Point(cur_x, cur_y), stash)
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

    type = StashType.STORAGE.value
    data = stashes[type]
    stash = Storage(type, data)

    type = StashType.BAG.value
    data = stashes[type]
    inv = Storage(type, data)

    #stash.move(stash.grid[0][0], Point(8, 3), inv)

    sort(stash, inv)


if __name__ == "__main__":
    main()
