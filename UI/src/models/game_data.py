import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from .icon_pak import canonical_icon_path

logger = logging.getLogger(__name__)

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

    def reload(self) -> None:
        """Force the item data cache to reload from disk."""
        with self._lock:
            self._data = None
            self._loaded = False
        try:
            self._ensure_loaded()
        except Exception as exc:  # pragma: no cover - defensive safeguard
            logger.warning("Failed to reload items.json: %s", exc, exc_info=True)

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
            canonical = canonical_icon_path(icon_path)
            if canonical:
                return Path(canonical)
        return None

    def get_item_id_from_design_str(self, item_id):
        design_str = "DesignDataItem:Id_Item_"
        return item_id.replace(design_str, "")

    @staticmethod
    def format_design_id_as_name(item_id):
        """Convert a stripped design ID into a human-readable item name.

        Example: 'Armor_Leather_ChestArmor_0001' → 'Armor Leather ChestArmor'
        """
        if not item_id:
            return ""
        import re
        # Strip trailing numeric-only segments (e.g. _0001, _6001)
        cleaned = re.sub(r'(?:_\d+)+$', '', item_id)
        # Replace underscores with spaces
        return cleaned.replace('_', ' ').strip()

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

    def get_item_slot_type(self, item_id):
        """Get equipment slot type for an item (e.g. Head, Chest, Hands)."""
        self._ensure_loaded()
        item = self._data.get(item_id, {})
        return item.get("slot_type", "")

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
    logger.info("Dimensions: %s x %s", width, height)

    rarity = manager.get_item_rarity_from_id("WizardShoes_6001")
    logger.info("Rarity: %s", rarity)

    icon_path = manager.get_item_image_path_from_id("WizardShoes_6001")
    logger.info("Icon path: %s", icon_path)


if __name__ == "__main__":
    main()
