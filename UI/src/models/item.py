class Item:
    # Class-level sort key order. Can be modified dynamically at runtime.
    sort_order = ["height", "width", "name", "rarity"]

    def __init__(self, name, rarity, position, width, height, stash, vendor_price=None):
        self.name = name
        self.rarity = rarity
        self.width = width
        self.height = height
        self.position = position
        self.stash = stash
        self.vendor_price = vendor_price

    def __lt__(self, other):
        for attr in Item.sort_order:
            self_val = getattr(self, attr)
            other_val = getattr(other, attr)
            if self_val is None:
                self_val = 0
            if other_val is None:
                other_val = 0
            if self_val != other_val:
                return self_val > other_val
        return False

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
            "vendor_price": self.vendor_price
        }

