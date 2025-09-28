import json
from pathlib import Path
from typing import Dict, Optional
import threading

class ItemDataManager:
    def __init__(self):
        self._data: Optional[Dict] = None
        self._loaded = False
        self._lock = threading.Lock()
        self._file_path = Path(__file__).resolve().parent.parent.parent / "assets" / "items.json"

    def _ensure_loaded(self):
        """Lazy load the data only when first accessed"""
        if not self._loaded:
            with self._lock:
                if not self._loaded:  # Double-check pattern
                    with open(self._file_path, "r", encoding="utf-8") as file:
                        self._data = json.load(file)
                    self._loaded = True

    def get_item_dimensions_from_id(self, item_id):
        self._ensure_loaded()
        item = self._data.get(item_id, {})
        width = item.get("inventory_width", 1)
        height = item.get("inventory_height", 1)
        return width, height

    def get_item_rarity_from_id(self, item_id):
        self._ensure_loaded()
        item = self._data.get(item_id, {})
        return item.get("rarity", 0)

    def get_item_name_from_id(self, item_id):
        self._ensure_loaded()
        item = self._data.get(item_id, {})
        return item.get("name", "")

    def get_item_image_path_from_id(self, item_id):
        self._ensure_loaded()
        item = self._data.get(item_id, {})
        icon_path = item.get("iconPath", None)
        if icon_path:
            # Return just the icon path without 'assets/' prefix
            return Path(icon_path)
        return None

    def get_item_id_from_design_str(self, item_id):
        design_str = "DesignDataItem:Id_Item_"
        return item_id.replace(design_str, "")

    def get_item_vendor_price(self, item_id):
        """Get vendor price for an item"""
        self._ensure_loaded()
        item = self._data.get(item_id, {})
        return item.get("vendor_price", 0)

    def get_item_max_stack_size(self, item_id):
        """Get maximum stack size for an item"""
        self._ensure_loaded()
        item = self._data.get(item_id, {})
        return item.get("max_stack_size", 1)

    def get_item_data(self, item_id):
        """Get full item data for an item"""
        self._ensure_loaded()
        return self._data.get(item_id, {})

    def search_items_by_name(self, query: str, limit: int = 50):
        """Search items by name with lazy loading and pagination"""
        self._ensure_loaded()
        query_lower = query.lower()
        results = []

        for item_id, item_data in self._data.items():
            if len(results) >= limit:
                break
            name = item_data.get("name", "").lower()
            if query_lower in name:
                results.append({
                    'id': item_id,
                    'name': item_data.get("name", ""),
                    'rarity': item_data.get("rarity", 0),
                    'vendor_price': item_data.get("vendor_price", 0)
                })

        return results

    @staticmethod
    def rarity_to_id(rarity_name):
        mapping = {
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
        return mapping.get(rarity_name, None)

    @staticmethod
    def id_to_rarity(rarity_id):
        mapping = {
            0: "None",
            1: "Poor",
            2: "Common",
            3: "Uncommon",
            4: "Rare",
            5: "Epic",
            6: "Legendary",
            7: "Unique",
            8: "Artifact"
        }
        return mapping.get(rarity_id, None)

item_data_manager = ItemDataManager()

def main():
    manager = ItemDataManager()

    width, height = manager.get_item_dimensions_from_id("WizardShoes_6001")
    print("Dimensions:", width, height)

    rarity = manager.get_item_rarity_from_id("WizardShoes_6001")
    print("Rarity:", rarity)

    icon_path = manager.get_item_image_path_from_id("WizardShoes_6001")
    print(icon_path)


if __name__ == "__main__":
    main()
