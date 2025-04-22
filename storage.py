import heapq
import macros
from item import Item
from point import Point
from enum import Enum

class StashType(Enum):
    NONE = 0
    CHEST = 1
    BAG = 2
    EQUIPMENT = 3
    STORAGE = 4
    PURCHASED_STORAGE_0 = 5
    PURCHASED_STORAGE_1 = 6
    PURCHASED_STORAGE_2 = 7
    PURCHASED_STORAGE_3 = 8
    PURCHASED_STORAGE_4 = 9
    SHARED_STASH_0 = 20
    SHARED_STASH_SEASONAL_0 = 30
    GEAR_SET_0 = 100
    GEAR_SET_1 = 101
    GEAR_SET_2 = 102
    MAX = 300

class RarityType(Enum):
    NONE_RARITY_TYPE = 0
    POOR = 1
    COMMON = 2
    UNCOMMON = 3
    RARE = 4
    EPIC = 5
    LEGEND = 6
    UNIQUE = 7
    ARTIFACT = 8

class Storage:
    def __init__(self, stash_type, data):
        self.stash_type = stash_type
        self.data = data
        # stardard or shared stash size 12x20
        if self.stash_type >= 4 and self.stash_type <= 30:
            self.width = 12
            self.height = 10
            self.base_screen_pos = macros.stash_screen_pos
        elif self.stash_type == StashType.BAG.value:
            self.base_screen_pos = macros.inv_screen_pos
            self.width = 10
            self.height = 5
        
        self.size = self.height * self.width
        self.grid = [[0 for _ in range(self.height)] for _ in range(self.width)]
        self.pq = []
        self.load()

    def move(self, item, end_pos, end_stash):
        print(f"Moving: {item} to {end_pos}")

        # Clear old location
        for dx in range(item.width):
            for dy in range(item.height):
                self.grid[item.position.x + dx][item.position.y + dy] = 0

        # Place item in new location
        for dx in range(item.width):
            for dy in range(item.height):
                end_stash.grid[end_pos.x + dx][end_pos.y + dy] = item
        
        # Update stash
        item.stash = end_stash
        
        macros.move_from_to(self, item.position, end_stash, end_pos)
        item.position = end_pos
    
    def find_empty_slot(self, item):
        for y in range(self.height - item.height, -1, -1):
            for x in range(self.width - item.width, -1, -1):
                go_next = False
                for dx in range(item.width):
                    for dy in range(item.height):
                        if self.grid[x + dx][y + dy] != 0:
                            go_next = True
                            break
                    if go_next:
                        break
                if not go_next:
                    return Point(x, y)

    def load(self):
        for obj in self.data:
            item = Item.from_dict(obj, self)
            for dx in range(item.width):
                for dy in range(item.height):
                    self.grid[item.position.x + dx][item.position.y + dy] = item

            heapq.heappush(self.pq, item)

    def __repr__(self):
        grid = [["." for _ in range(self.width)] for _ in range(self.height)]

        for x in range(self.width):
            for y in range(self.height):
                item = self.grid[x][y]
                if item != 0:
                    # Just display the first letter of the item name or a hash of it
                    grid[y][x] = item.name[0].upper() if item.name else "#"

        # Create a string representation row by row
        lines = []
        for row in grid:
            lines.append(" ".join(row))
        return "\n".join(lines)
