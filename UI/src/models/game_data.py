import json
from pathlib import Path

class ItemDataManager:
    def __init__(self):
        file_path = Path(__file__).resolve().parent.parent.parent / "assets" / "items.json"
        with open(file_path, "r", encoding="utf-8") as file:
            self.data = json.load(file)

    def get_item_dimensions_from_id(self, item_id):
        item = self.data.get(item_id, {})
        width = item.get("inventory_width", 1)
        height = item.get("inventory_height", 1)
        return width, height

    def get_item_rarity_from_id(self, item_id):
        item = self.data.get(item_id, {})
        return item.get("rarity", 0)

    def get_item_name_from_id(self, item_id):
        item = self.data.get(item_id, {})
        return item.get("name", "")

    def get_item_image_path_from_id(self, item_id):
        item = self.data.get(item_id, {})
        icon_path = item.get("iconPath", None)
        if icon_path:
            # Return just the icon path without 'assets/' prefix
            return Path(icon_path)
        return None

    def get_item_id_from_design_str(self, item_id):
        design_str = "DesignDataItem:Id_Item_"
        return item_id.replace(design_str, "")
    
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
