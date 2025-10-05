import heapq
import logging
from enum import Enum

from src.models import macros
from src.models.appdirs import resource_path
from src.models.game_data import item_data_manager
from src.models.item import Item
from src.models.point import Point

logger = logging.getLogger(__name__)

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
            self.height = 20
            self.base_screen_pos = macros.get_screen_positions()['stash']
        elif self.stash_type == StashType.BAG.value:
            self.base_screen_pos = macros.get_screen_positions()['inv']
            self.width = 10
            self.height = 5
        
        self.size = self.height * self.width
        self.grid = [[0 for _ in range(self.height)] for _ in range(self.width)]
        self.pq = []
        self._reserved_slots: set[tuple[int, int]] = set()
        self.load()
    
    def get_items(self):
        """
        Get items from the storage grid.
        Note: This method needs to be implemented to identify unique items in the grid
        and convert position to slotID and other necessary fields.
        For now, recapturing the stash packet is recommended.
        """
        # Implementation needed: identify unique items in grid then convert 
        # position to slotID and other necessary fields
        # Alternative: recapture the stash packet
        return []

    def move(self, item, end_pos, end_stash):
        logger.debug("Moving %s to %s", item, end_pos)

        if self is not end_stash:
            for dx in range(item.width):
                for dy in range(item.height):
                    self._reserved_slots.discard((item.position.x + dx, item.position.y + dy))
        if end_stash is not self:
            for dx in range(item.width):
                for dy in range(item.height):
                    end_stash._reserved_slots.discard((end_pos.x + dx, end_pos.y + dy))

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
        macros.move_from_to_reliable(self, item.position, end_stash, end_pos, item.width, item.height, item.width, item.height)
        item.position = end_pos
        
    def find_empty_slot(self, item):
        for y in range(self.height - item.height, -1, -1):  # bottom to top
            for x in range(self.width - item.width, -1, -1):  # right to left
                fits = True
                for dx in range(item.width):
                    for dy in range(item.height):
                        slot = (x + dx, y + dy)
                        if slot in self._reserved_slots or self.grid[slot[0]][slot[1]] != 0:
                            fits = False
                            break
                    if not fits:
                        break
                if fits:
                    return Point(x, y)
        return None  # no valid position found

    def load(self):
        for obj in self.data:
            # Set items with no slotId to 0
            slot_id = obj.get("slotId")
            if slot_id is None:
                obj["slotId"] = 0
            try:
                design_str = obj.get("itemId", "")
                item_id = item_data_manager.get_item_id_from_design_str(design_str)
                width, height = item_data_manager.get_item_dimensions_from_id(item_id)
                rarity = item_data_manager.get_item_rarity_from_id(item_id)
                name = item_data_manager.get_item_name_from_id(item_id)

                slot_id = obj.get("slotId")
                x = slot_id % self.width
                y = slot_id // self.width

                position = Point(x, y)

                rarity_id = item_data_manager.rarity_to_id(rarity)

                if (rarity_id == None):
                    logger.error("Invalid rarity data for item %s (rarity=%s, rarity_id=%s)", item_id, rarity, rarity_id)
                    exit()

                vendor_price = item_data_manager.get_item_vendor_price(item_id)
                quantity = obj.get("itemCount", 1)
                max_stack = item_data_manager.get_item_max_stack_size(item_id)

                item = Item(
                    item_id,
                    name,
                    rarity_id,
                    position,
                    width,
                    height,
                    self,
                    vendor_price=vendor_price,
                    quantity=quantity,
                    max_stack_size=max_stack,
                )

            except Exception as e:
                logger.error("Error creating item from data: %s", e)
                continue

            # Verify placement within bounds
            out_of_bounds = False
            for dx in range(item.width):
                for dy in range(item.height):
                    x = item.position.x + dx
                    y = item.position.y + dy
                    if not (0 <= x < self.width and 0 <= y < self.height):
                        logger.warning("Item %s position out of bounds at (%s, %s), skipping", item, x, y)
                        out_of_bounds = True
                        break
                if out_of_bounds:
                    break
            if out_of_bounds:
                continue
            # Place item and enqueue for sorting
            for dx in range(item.width):
                for dy in range(item.height):
                    self.grid[item.position.x + dx][item.position.y + dy] = item
            heapq.heappush(self.pq, item)
    
    def __repr__(self):
        # import os
        # import sys
        # import json
        # from pathlib import Path

        # # Determine the correct data directory (same logic as StashManager)
        # if globals().get('__compiled__', False):
        #     base_dir = os.getcwd()
        # else:
        #     base_dir = Path(__file__).parent.parent.parent
        # data_dir = os.path.join(base_dir, 'data')
        # characters = []

        # if os.path.exists(data_dir):
        #     for char_file in os.listdir(data_dir):
        #         if char_file.endswith('.json'):
        #             with open(os.path.join(data_dir, char_file), 'r', encoding='utf-8') as f:
        #                 char_data = json.load(f)
        #                 characters.append(char_data)

        # if not characters:
        #     return "No character data found. Please capture character data first."

        # Create the grid representation
        grid = [["." for _ in range(self.width)] for _ in range(self.height)]

        for x in range(self.width):
            for y in range(self.height):
                item = self.grid[x][y]
                if item != 0:
                    grid[y][x] = item.name[0].upper() if item.name else "#"

        # Create the display string
        lines = []
        # Add character information
        # for char in characters:
        #     lines.append(f"\nCharacter: {char.get('characterName', 'Unknown')}")
        #     lines.append(f"Class: {char.get('characterClass', 'Unknown')}")
        #     lines.append(f"Level: {char.get('level', 'Unknown')}")
        #     rank = char.get('rank', {}).get('name', 'Unknown') if isinstance(char.get('rank'), dict) else char.get('rank', 'Unknown')
        #     lines.append(f"Rank: {rank}")
        #     lines.append("-" * 40)

        # Add inventory grid
        lines.append("\nStorage:")
        for row in grid:
            lines.append(" ".join(row))

        return "\n".join(lines)
