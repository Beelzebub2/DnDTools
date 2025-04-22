from stash_preview import ItemDataManager

class Item:
    def __init__(self, name, rarity, position, width, height):
        self.name = name
        self.rarity = rarity
        self.width = width
        self.height = height
        self.position = position

    def __lt__(self, other):
        if self.height != other.height:
            return self.height > other.height
        if self.width != other.width:
            return self.width > other.width
        if self.rarity != other.rarity:
            return self.rarity > other.rarity
        return self.name < other.name
    
    def __eq__(self, other):
        if self and other:
            return self.name == other.name and self.rarity == other.rarity and self.position == other.position
    
    def __hash__(self):
        return hash((self.name, self.rarity, self.position))

    def __repr__(self):
        return f"{self.rarity} {self.name} {self.position} {self.width}X{self.height}"
    
    def to_dict(self):
        return {
            "name": self.name,
            "rarity": self.rarity,
            "position": self.position,
            "width": self.width,
            "height": self.height,
        }

    @staticmethod
    def from_dict(data):
        # "itemId": "DesignDataItem:Id_Item_BloodsapBlade_5001",
        # "slotId": 211,
        item_id = data["itemId"]
        design_str = "DesignDataItem:Id_Item_"
        parts = item_id.replace(design_str, "").split("_")
        name = parts[0]

        # parts
        # ['BloodsapBlade', '5001']
        # ['GoldCoinPurse']
        if len(parts) == 2:
            rarity_id = int(parts[1][0])
        elif len(parts) == 1:
            # some items have no rarity apparently
            rarity_id = 0

        position = data.get("slotId", -1)
        # Retrieve dimensions using ItemDataManager (was this the intention?)
        manager = ItemDataManager()
        _, width, height, _ = manager.get_item_image_path(name) or (None, 1, 1, None)

        return Item(name, rarity_id, position, width, height)

