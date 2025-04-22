import json
import os
import re
import difflib
from typing import Tuple, Dict, List, Optional
from dataclasses import dataclass
from PIL import Image, ImageDraw
import logging

@dataclass
class ItemInfo:
    name: str
    slotId: int  # Changed from slot_id to match incoming JSON
    itemId: str  # Changed from item_id to match incoming JSON
    itemCount: int  # Changed from item_count to match incoming JSON

class ItemDataManager:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        assets_dir = os.path.join(base_dir, "assets")
        self.ITEM_DATA_FILE = os.path.join(assets_dir, "item-data.json")
        self.MATCHING_DB_FILE = os.path.join(assets_dir, "matchingdb.json")
        os.makedirs(assets_dir, exist_ok=True)
        
        self.item_data = self.load_json(self.ITEM_DATA_FILE)
        self.matching_db = self.load_matching_db()
        self.image_cache = {}

    @staticmethod
    def load_json(filename: str) -> dict:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_matching_db(self) -> dict:
        try:
            return self.load_json(self.MATCHING_DB_FILE)
        except Exception:
            return {}

    def save_matching_db(self) -> None:
        with open(self.MATCHING_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(self.matching_db, f, indent=2)

    @staticmethod
    def normalize_name(name: str) -> str:
        if not name:
            return ""
        name = name.lower()
        name = re.sub(r"designdataitem:|id_item_", "", name)
        name = re.sub(r"[^a-z0-9]", "", name)
        return name

    def get_item_image_path(self, item_name: str) -> Tuple[Optional[str], Optional[int], Optional[int], Optional[str]]:
        if item_name in self.matching_db:
            item_name = self.matching_db[item_name]

        norm_name = self.normalize_name(item_name)
        
        # Try exact match first
        for data in self.item_data.values():
            if self.normalize_name(data.get("name", "")) == norm_name:
                return (data["path"].replace("\\", os.sep),
                        data["inventory_width"],
                        data["inventory_height"],
                        data["name"])

        # Try fuzzy match as fallback
        name_map = {self.normalize_name(data.get("name", "")): data 
                   for data in self.item_data.values()}
        close = difflib.get_close_matches(norm_name, list(name_map.keys()), n=1, cutoff=0.7)
        
        if close:
            data = name_map[close[0]]
            logging.info(f"Fuzzy matched '{item_name}' → '{data['name']}'")
            return (data["path"].replace("\\", os.sep),
                    data["inventory_width"],
                    data["inventory_height"],
                    data["name"])
                    
        return None, None, None, None

class StashPreviewGenerator:
    def __init__(self, grid_width: int = 12, grid_height: int = 20, cell_size: int = 45):
        self.GRID_WIDTH = grid_width
        self.GRID_HEIGHT = grid_height
        self.CELL_SIZE = cell_size
        self.item_manager = ItemDataManager()
        logging.basicConfig(level=logging.INFO)

    def generate_preview(self, stash_id: str, items: List[ItemInfo]) -> Image.Image:
        preview = Image.new("RGBA", 
                          (self.GRID_WIDTH * self.CELL_SIZE, 
                           self.GRID_HEIGHT * self.CELL_SIZE))
        self._draw_grid(preview)
        
        for item in items:
            self._place_item(preview, item)
            
        return preview

    def _draw_grid(self, img: Image.Image) -> None:
        draw = ImageDraw.Draw(img)
        # Fill background
        draw.rectangle([0, 0, img.width, img.height], fill=(24, 20, 16, 255))
        # Draw border
        draw.rectangle([0, 0, img.width - 1, img.height - 1],
                      outline=(212, 175, 55, 255), width=4)
        # Draw grid lines
        grid_color = (60, 50, 30, 180)
        for x in range(1, self.GRID_WIDTH):
            x_pos = x * self.CELL_SIZE
            draw.line([(x_pos, 0), (x_pos, img.height)], fill=grid_color)
        for y in range(1, self.GRID_HEIGHT):
            y_pos = y * self.CELL_SIZE
            draw.line([(0, y_pos), (img.width, y_pos)], fill=grid_color)

    def _place_item(self, preview: Image.Image, item: ItemInfo) -> None:
        img_path, w, h, matched_name = self.item_manager.get_item_image_path(item.name)
        if not img_path or not os.path.exists(img_path):
            logging.warning(f"Item not found or missing image: {item.name}")
            return

        try:
            item_img = Image.open(img_path).convert("RGBA")
            expected_size = ((w or 1) * self.CELL_SIZE, (h or 1) * self.CELL_SIZE)
            if item_img.size != expected_size:
                item_img = item_img.resize(expected_size, Image.LANCZOS)
            
            x, y = item.slotId % self.GRID_WIDTH, item.slotId // self.GRID_WIDTH  # Updated to use slotId
            preview.paste(item_img, (x * self.CELL_SIZE, y * self.CELL_SIZE), item_img)
            logging.info(f"Placed '{matched_name}' at ({x},{y})")
            
            # Record successful match
            self.item_manager.matching_db[item.name] = matched_name
            
        except Exception as e:
            logging.error(f"Failed to process image {img_path}: {e}")

def get_item_name_from_id(item_id, item_data):
    # Extract base name from item_id
    if not item_id.startswith("DesignDataItem:Id_Item_"):
        return None
    base = item_id[len("DesignDataItem:Id_Item_"):]
    # Remove trailing _xxxx numbers
    base = re.sub(r'_\d+$', '', base)
    # Replace underscores with spaces
    base = base.replace("_", " ")
    return base

def parse_stashes(packet_data, item_data):
    stashes = {}
    storage_infos = packet_data.get("characterDataBase", {}).get("CharacterStorageInfos", [])
    for storage in storage_infos:
        inventory_id = storage.get("inventoryId")
        items = storage.get("CharacterStorageItemList", [])
        stash_items = []
        for item in items:
            item_id = item.get("itemId", "")
            slot_id = item.get("slotId")
            if slot_id is None:
                continue
            name = get_item_name_from_id(item_id, item_data)
            stash_items.append({
                "name": name,
                "slotId": slot_id,  # Already matches ItemInfo field name
                "itemId": item_id,   # Already matches ItemInfo field name
                "itemCount": item.get("itemCount", 1)  # Already matches ItemInfo field name
            })
        if stash_items:
            stashes[inventory_id] = stash_items
    return stashes

def slotid_to_xy(slot_id):
    return slot_id % 12, slot_id // 12

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    packet_data_path = os.path.join(base_dir, "packet_data.json")
    output_dir = os.path.join(base_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        item_data = ItemDataManager().item_data
        if not os.path.exists(packet_data_path):
            print(f"Error: {packet_data_path} not found. Please run packet capture first.")
            return
            
        packet_data = ItemDataManager.load_json(packet_data_path)
        matching_db = {}  # collect original → matched names

        stashes = parse_stashes(packet_data, item_data)
        if not stashes:
            print("No stashes found in packet data.")
            return

        generator = StashPreviewGenerator()

        for stash_id, items in stashes.items():
            print(f"\nProcessing stash inventoryId={stash_id} with {len(items)} items...")
            preview = generator.generate_preview(stash_id, [ItemInfo(**item) for item in items])
            outname = os.path.join(output_dir, f"stash_preview_{stash_id}.png")
            preview.save(outname)
            print(f"Preview saved as {outname}")

        # save the matching database
        generator.item_manager.save_matching_db()
        print("Matching DB saved as matchingdb.json")
        
    except Exception as e:
        print(f"Error generating previews: {e}")
        logging.error(f"Failed to generate previews: {e}", exc_info=True)

if __name__ == "__main__":
    main()
