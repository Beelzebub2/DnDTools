import json
import os
import re
import difflib
from typing import Tuple, Dict, List, Optional
from dataclasses import dataclass
from PIL import Image, ImageDraw, ImageFont
import logging
from datetime import datetime
from src.models.game_data import item_data_manager
import sys
from src.models.appdirs import resource_path, get_resource_dir

@dataclass
class ItemInfo:
    name: str
    slotId: int  # Changed from slot_id to match incoming JSON
    itemId: str  # Changed from item_id to match incoming JSON
    itemCount: int  # Changed from item_count to match incoming JSON
    data: Dict 

class StashPreviewGenerator:
    def __init__(self, cell_size: int = 45, resource_dir=None):
        self.CELL_SIZE = cell_size
        self.resource_dir = resource_dir or get_resource_dir()
        logging.basicConfig(level=logging.INFO)
        try:
            self.font = ImageFont.truetype("arial.ttf", 16)
        except:
            self.font = ImageFont.load_default()
            
        # Define rarity colors with alpha (RGBA)
        self.rarity_colors = {
            0: (128, 128, 128, 100),  # None - Gray
            1: (150, 150, 150, 100),  # Poor - Light Gray
            2: (255, 255, 255, 100),  # Common - White
            3: (0, 255, 0, 100),      # Uncommon - Green
            4: (0, 112, 221, 100),    # Rare - Blue
            5: (163, 53, 238, 100),   # Epic - Purple
            6: (255, 128, 0, 100),    # Legend - Orange
            7: (233, 237, 154, 150),   # Unique - Gold
            8: (255, 0, 0, 150),      # Artifact - Red
        }

    def _get_stash_dimensions(self, stash_id: str) -> Tuple[int, int]:
        """Return appropriate grid dimensions based on stash type"""
        from src.models.storage import StashType
        
        # Convert stash_id to int if it's a string of digits
        try:
            stash_id_int = int(stash_id)
            # Try to get StashType directly
            try:
                stash_type = StashType(stash_id_int)
            except ValueError:
                # Handle purchased storage special case
                if stash_id_int >= StashType.PURCHASED_STORAGE_0.value and stash_id_int <= StashType.PURCHASED_STORAGE_4.value:
                    # All purchased storage uses standard stash dimensions
                    return 12, 20
                # Default to standard stash size if we can't match the type
                return 12, 20
            
            # Return dimensions based on stash type
            if stash_type == StashType.BAG:
                return 10, 5
            elif stash_type == StashType.EQUIPMENT:
                # Equipment has a special layout that's not a simple grid
                # We use a larger canvas to accommodate the specific positions
                return 10, 10
            elif stash_type in (StashType.STORAGE, StashType.SHARED_STASH_0, StashType.SHARED_STASH_SEASONAL_0):
                return 12, 20
            else:
                return 12, 20  # Default to standard stash size
                
        except (ValueError, TypeError):
            # Default to standard stash size if we can't parse the ID
            return 12, 20

    def generate_preview(self, stash_id: str, items: List[ItemInfo]) -> Image.Image:
        # Get appropriate dimensions for this stash type
        grid_width, grid_height = self._get_stash_dimensions(stash_id)
        
        preview = Image.new("RGBA", 
                          (grid_width * self.CELL_SIZE, 
                           grid_height * self.CELL_SIZE))
                           
        from src.models.storage import StashType
        try:
            stash_id_int = int(stash_id)
            stash_type = StashType(stash_id_int)
        except ValueError:
            # Handle purchased storage or invalid types
            stash_type = None
        
        # Special handling for equipment screen
        if stash_type == StashType.EQUIPMENT:
            self._draw_equipment_layout(preview)
        else:
            self._draw_grid(preview, grid_width, grid_height)
        
        for item in items:
            # Special handling for equipment screen
            if stash_type == StashType.EQUIPMENT:
                self._place_equipment_item(preview, item)
            else:
                self._place_item(preview, item, grid_width, grid_height)
            
        return preview
        
    def _draw_equipment_layout(self, img: Image.Image) -> None:
        """Draw the special equipment layout with slots for armor, weapons, consumables"""
        draw = ImageDraw.Draw(img)
        
        # Fill background
        draw.rectangle([0, 0, img.width, img.height], fill=(24, 20, 16, 255))
        
        # Draw border
        draw.rectangle([0, 0, img.width - 1, img.height - 1],
                      outline=(212, 175, 55, 255), width=4)
        
        # Define slot positions based on the screenshot
        # Format: (x, y, width, height) in grid cells
        equipment_slots = {
            # Weapon slots (1x4)
            "weapon1": (0, 0, 2, 4),  # Left weapon slots
            "weapon2": (8, 0, 2, 4),  # Right weapon slots
            
            # Top row - Head and amulet
            "head": (3, 0, 2, 2),     # Helmet/head armor
            "amulet": (5, 0, 2, 2),   # Amulet/necklace
            
            # Middle - Body armor and rings
            "chest": (3, 2, 4, 3),    # Body armor (centered)
            "ring1": (2, 3, 1, 1),    # Left ring
            "ring2": (7, 3, 1, 1),    # Right ring
            
            # Gloves and accessories
            "gloves": (2, 5, 2, 2),   # Gloves (left of pants)
            "cape": (6, 5, 2, 2),     # Cape (right of pants)
            
            # Bottom row - Pants and boots
            "pants": (3, 5, 4, 3),    # Pants/leggings
            "boots": (4, 8, 2, 2),    # Boots
            
            # Consumable slots - 6 on each side in two columns
            # Left side consumables
            "consumable1": (0, 5, 1, 1),
            "consumable2": (1, 5, 1, 1),
            "consumable3": (0, 6, 1, 1),
            "consumable4": (1, 6, 1, 1),
            "consumable5": (0, 7, 1, 1),
            "consumable6": (1, 7, 1, 1),
            
            # Right side consumables
            "consumable7": (8, 5, 1, 1),
            "consumable8": (9, 5, 1, 1),
            "consumable9": (8, 6, 1, 1),
            "consumable10": (9, 6, 1, 1),
            "consumable11": (8, 7, 1, 1),
            "consumable12": (9, 7, 1, 1)
        }
        
        # Draw each equipment slot
        for slot_name, (x, y, w, h) in equipment_slots.items():
            # Draw slot border
            draw.rectangle(
                [x * self.CELL_SIZE, y * self.CELL_SIZE,
                (x + w) * self.CELL_SIZE - 1, (y + h) * self.CELL_SIZE - 1],
                outline=(100, 90, 70, 255), width=2
            )
            
    def _place_equipment_item(self, preview: Image.Image, item: ItemInfo) -> None:
        img_path = item_data_manager.get_item_image_path_from_id(item.itemId)
        if img_path:
            # Convert PathLib to string and use resource_path
            img_path = resource_path(str(img_path))
        w, h = item_data_manager.get_item_dimensions_from_id(item.itemId)
        name = item_data_manager.get_item_name_from_id(item.itemId)

        if not img_path or not os.path.exists(img_path):
            logging.warning(f"Item not found or missing image: {item.itemId}")
            return
        
        try:
            item_img = Image.open(img_path).convert("RGBA")
            
            # Map slot IDs to positions based on the equipment layout from the screenshot
            slot_positions = {
                # Weapons (left and right columns)
                0: (0, 0),  # Weapon slot 1 (left)
                1: (8, 0),  # Weapon slot 2 (right)
                
                # Helmet and accessory slots (top row)
                2: (3, 0),  # Helmet slot
                7: (5, 0),  # Necklace/amulet slot
                
                # Body armor (center)
                3: (3, 2),  # Body armor
                
                # Rings (middle row)
                8: (2, 3),  # Left ring
                9: (7, 3),  # Right ring
                
                # Accessories & armor (bottom half)
                6: (2, 5),  # Gloves (left side)
                4: (3, 5),  # Pants/leggings (center)
                10: (6, 5), # Cape/cloak (right side)
                5: (4, 8),  # Boots (center bottom)
                
                # Consumable slots - Left column
                20: (0, 5),  # Top left
                21: (1, 5),
                22: (0, 6),
                23: (1, 6),
                24: (0, 7),
                25: (1, 7),  # Bottom left
                
                # Consumable slots - Right column
                26: (8, 5),  # Top right
                27: (9, 5),
                28: (8, 6),
                29: (9, 6),
                30: (8, 7),
                31: (9, 7)   # Bottom right
            }
            
            # Get position for this slot
            if item.slotId in slot_positions:
                x, y = slot_positions[item.slotId]
                expected_size = ((w or 1) * self.CELL_SIZE, (h or 1) * self.CELL_SIZE)
                
                if item_img.size != expected_size:
                    item_img = item_img.resize(expected_size, Image.LANCZOS)
                
                # Paste the item
                preview.paste(item_img, (x * self.CELL_SIZE, y * self.CELL_SIZE), item_img)
                logging.info(f"Placed equipment '{name}' at slot {item.slotId} ({x},{y})")
                
                # Draw item count if greater than 1
                if item.itemCount > 1:
                    draw = ImageDraw.Draw(preview)
                    count_text = str(item.itemCount)
                    bbox = draw.textbbox((0, 0), count_text, font=self.font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                    # Calculate position based on item size (width and height)
                    text_x = (x + (w or 1)) * self.CELL_SIZE - text_width - 4
                    text_y = (y + (h or 1)) * self.CELL_SIZE - text_height - 2
                    # Draw text with shadow for better visibility
                    draw.text((text_x+1, text_y+1), count_text, fill='black', font=self.font)
                    draw.text((text_x, text_y), count_text, fill='white', font=self.font)
            else:
                logging.warning(f"Unknown equipment slot ID: {item.slotId} for item {item.itemId}")
                
        except Exception as e:
            logging.error(f"Failed to process equipment item {item.itemId}: {e}")

    def _draw_grid(self, img: Image.Image, grid_width: int, grid_height: int) -> None:
        draw = ImageDraw.Draw(img)
        # Fill background
        draw.rectangle([0, 0, img.width, img.height], fill=(24, 20, 16, 255))
        # Draw border
        draw.rectangle([0, 0, img.width - 1, img.height - 1],
                      outline=(212, 175, 55, 255), width=4)
        # Draw grid lines
        grid_color = (60, 50, 30, 180)
        for x in range(1, grid_width):
            x_pos = x * self.CELL_SIZE
            draw.line([(x_pos, 0), (x_pos, img.height)], fill=grid_color)
        for y in range(1, grid_height):
            y_pos = y * self.CELL_SIZE
            draw.line([(0, y_pos), (img.width, y_pos)], fill=grid_color)

    def _place_item(self, preview: Image.Image, item: ItemInfo, grid_width: int, grid_height: int) -> None:
        img_path = item_data_manager.get_item_image_path_from_id(item.itemId)
        if img_path:
            # Convert PathLib to string and use resource_path
            img_path = resource_path(str(img_path))
        w, h = item_data_manager.get_item_dimensions_from_id(item.itemId)
        name = item_data_manager.get_item_name_from_id(item.itemId)

        # Get rarity from the item data
        parts = item.itemId.split('_')
        rarity = None
        if len(parts) > 1 and parts[-1].isdigit():
            rarity = int(parts[-1][0])
        else:
            # Check if it's a unique item in the data
            item_data = item_data_manager.data.get(item.itemId, {})
            rarity_str = item_data.get("rarity", None)
            if rarity_str is not None:
                rarity_map = {
                    "None": 0,
                    "Poor": 1,
                    "Common": 2,
                    "Uncommon": 3,
                    "Rare": 4, 
                    "Epic": 5,
                    "Legendary": 6,
                    "Unique": 7,
                    "Artifact": 8
                }
                rarity = rarity_map.get(rarity_str, 0)
            else:
                # Try to load from items_stripped JSON file
                try:
                    items_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'items_stripped')
                    json_path = os.path.join(items_dir, f"{item.itemId}.json")
                    if os.path.exists(json_path):
                        with open(json_path, 'r', encoding='utf-8') as f:
                            json_data = json.load(f)
                        rarity_str = json_data.get("rarity", None)
                        if rarity_str is not None:
                            rarity_map = {
                                "None": 0,
                                "Poor": 1,
                                "Common": 2,
                                "Uncommon": 3,
                                "Rare": 4, 
                                "Epic": 5,
                                "Legendary": 6,
                                "Unique": 7,
                                "Artifact": 8
                            }
                            rarity = rarity_map.get(rarity_str, 0)
                except Exception as e:
                    logging.warning(f"Could not load rarity from items_stripped for {item.itemId}: {e}")
        if rarity is None:
            rarity = 0

        if not img_path or not os.path.exists(img_path):
            logging.warning(f"Item not found or missing image: {item.itemId}")
            return

        try:
            # Calculate item position
            x, y = item.slotId % grid_width, item.slotId // grid_width
            if x + (w or 1) > grid_width or y + (h or 1) > grid_height:
                logging.warning(f"Item {item.itemId} at position ({x},{y}) with size {w}x{h} doesn't fit in grid {grid_width}x{grid_height}")
                return
            
            # Create background color for rarity
            bg_color = self.rarity_colors.get(rarity, self.rarity_colors[0])
            bg_rect = Image.new('RGBA', ((w or 1) * self.CELL_SIZE, (h or 1) * self.CELL_SIZE), bg_color)
            preview.paste(bg_rect, (x * self.CELL_SIZE, y * self.CELL_SIZE), bg_rect)
            
            # Place the item image
            item_img = Image.open(img_path).convert("RGBA")
            expected_size = ((w or 1) * self.CELL_SIZE, (h or 1) * self.CELL_SIZE)
            if item_img.size != expected_size:
                item_img = item_img.resize(expected_size, Image.LANCZOS)
            
            preview.paste(item_img, (x * self.CELL_SIZE, y * self.CELL_SIZE), item_img)
            logging.info(f"Placed '{name}' (rarity {rarity}) at ({x},{y})")
            
            # Draw item count if greater than 1
            if item.itemCount > 1:
                draw = ImageDraw.Draw(preview)
                count_text = str(item.itemCount)
                bbox = draw.textbbox((0, 0), count_text, font=self.font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                text_x = (x + (w or 1)) * self.CELL_SIZE - text_width - 4
                text_y = (y + (h or 1)) * self.CELL_SIZE - text_height - 2
                draw.text((text_x+1, text_y+1), count_text, fill='black', font=self.font)
                draw.text((text_x, text_y), count_text, fill='white', font=self.font)
            
        except Exception as e:
            logging.error(f"Failed to process image {img_path}: {e}")


def parse_stashes(packet_data):
    stashes = {}
    # stashes
    storage_infos = packet_data.get("characterDataBase", {}).get("CharacterStorageInfos", [])
    for storage in storage_infos:
        inventory_id = storage.get("inventoryId")
        items = storage.get("CharacterStorageItemList", [])
        stash_items = []
        used_slots = set()
        # First process items with defined slots
        for item in items:
            if "slotId" in item:
                design_str = item.get("itemId", "")
                item_id = item_data_manager.get_item_id_from_design_str(design_str)
                name = item_data_manager.get_item_name_from_id(item_id)
                slot_id = item["slotId"]
                stash_items.append({
                    "name": name,
                    "slotId": slot_id,
                    "itemId": item_id,
                    "itemCount": item.get("itemCount", 1),
                    "data": item
                })
                used_slots.add(slot_id)
        # Then process items without slots, assign to next free slot
        for item in items:
            if "slotId" not in item:
                slot_id = 0
                used_slots.add(slot_id)
                design_str = item.get("itemId", "")
                item_id = item_data_manager.get_item_id_from_design_str(design_str)
                name = item_data_manager.get_item_name_from_id(item_id)
                stash_items.append({
                    "name": name,
                    "slotId": slot_id,
                    "itemId": item_id,
                    "itemCount": item.get("itemCount", 1),
                    "data": item
                })
        if stash_items:
            stashes[inventory_id] = stash_items
    # inventory
    item_list = packet_data.get("characterDataBase", {}).get("CharacterItemList", [])
    for item in item_list:
        inventory_id = item.get("inventoryId")
        if inventory_id not in stashes:
            stashes[inventory_id] = []
        # Assign slotId = 0 if missing, otherwise use the provided slotId
        slot_id = item.get("slotId", 0)
        design_str = item.get("itemId", "")
        item_id = item_data_manager.get_item_id_from_design_str(design_str)
        name = item_data_manager.get_item_name_from_id(item_id)
        stashes[inventory_id].append({
            "name": name,
            "slotId": slot_id,
            "itemId": item_id,
            "itemCount": item.get("itemCount", 1),
            "data": item
        })
    return stashes

def main():
    from pathlib import Path

    folder = Path(r"data")

    # TODO
    # for file in folder.iterdir():
    #     if file.is_file():
    #         base_dir = os.path.dirname(os.path.abspath(__file__))
    #         packet_data_path = os.path.join(base_dir, file)
    #         output_dir = os.path.join(base_dir, "output")
    #         os.makedirs(output_dir, exist_ok=True)

    #         try:
    #             if not os.path.exists(packet_data_path):
    #                 print(f"Error: {packet_data_path} not found. Please run packet capture first.")
    #                 return
                    
    #             packet_data = ItemDataManager.load_json(packet_data_path)
    #             matching_db = {}  # collect original → matched names

    #             stashes = parse_stashes(packet_data)
    #             if not stashes:
    #                 print("No stashes found in packet data.")
    #                 return

    #             generator = StashPreviewGenerator()

    #             for stash_id, items in stashes.items():
    #                 print(f"\nProcessing stash inventoryId={stash_id} with {len(items)} items...")
    #                 preview = generator.generate_preview(stash_id, [ItemInfo(**item) for item in items])
    #                 outname = os.path.join(output_dir, f"stash_preview_{stash_id}.png")
    #                 preview.save(outname)
    #                 print(f"Preview saved as {outname}")
                
    #         except Exception as e:
    #             print(f"Error generating previews: {e}")
    #             logging.error(f"Failed to generate previews: {e}", exc_info=True)

if __name__ == "__main__":
    main()
