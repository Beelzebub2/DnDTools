import heapq
import macros
from item import Item
from point import Point

class Stash:
    def __init__(self, stash_type, data):
        self.stash_type = stash_type
        self.data = data
        # stardard or shared stash size 12x20
        if self.stash_type >= 4 and self.stash_type <= 30:
            self.width = 12
            self.height = 20
            self.base_screen_pos = Point(1394, 218)
        else:
            # TODO implment for inv
            self.base_screen_pos = Point(705, 644)
            self.width = 10
            self.height = 5

        #   stash_type 
        #   enum InventoryId {
        #     NONE = 0;
        #     CHEST = 1;
        #     BAG = 2;
        #     EQUIPMENT = 3;
        #     STORAGE = 4;
        #     PURCHASED_STORAGE_0 = 5;
        #     PURCHASED_STORAGE_1 = 6;
        #     PURCHASED_STORAGE_2 = 7;
        #     PURCHASED_STORAGE_3 = 8;
        #     PURCHASED_STORAGE_4 = 9;
        #     SHARED_STASH_0 = 20;
        #     SHARED_STASH_SEASONAL_0 = 30;
        #     GEAR_SET_0 = 100;
        #     GEAR_SET_1 = 101;
        #     GEAR_SET_2 = 102;
        #     MAX = 300;
        # }
        
        self.size = self.height * self.width
        self.grid = [0 for _ in range(self.size)]
        self.pq = []
        self.load()

    def move(self, item, end_pos):
        print(f"Moving: {item} to {end_pos}")

        # clear old space 
        for dx in range(item.width):
             for dy in range(item.height):
                self.grid[item.position + (dy * self.width) + dx] = 0

        # update new space
        for dx in range(item.width):
            for dy in range(item.height):
                self.grid[end_pos + (dy * self.width) + dx] = item
        
        macros.move_from_to(item.position, end_pos, self)
        item.position = end_pos
    
    # TODO make it find a slot in the player inventory
    def find_empty_slot(self, item):
        for y in reversed(range(self.height - item.height + 1)):
            for x in reversed(range(self.width - item.width + 1)):
                go_next = False
                for dx in range(item.width):
                    for dy in range(item.height):
                        index = (y + dy) * self.width + (x + dx)
                        if self.grid[index] != 0:
                            go_next = True
                            break
                    if go_next:
                        break
                if not go_next:
                    return y * self.width + x
        return None

    def load(self):
        for obj in self.data:
            item = Item.from_dict(obj)
            self.grid[item.position] = item
            for dx in range(item.width):
                for dy in range(item.height):
                    self.grid[item.position + (dy * self.width) + dx] = item

            heapq.heappush(self.pq, item)

    def __repr__(self):
        return str(self.grid)
