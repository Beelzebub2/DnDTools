from src.models.game_data import item_data_manager
from src.models.point import Point

class Item:
    def __init__(self, name, rarity, position, width, height, stash):
        self.name = name
        self.rarity = rarity
        self.width = width
        self.height = height
        self.position = position
        self.stash = stash

    def __lt__(self, other):
        if self.height != other.height:
            return self.height > other.height
        if self.width != other.width:
            return self.width > other.width
        if self.name != other.name:
            return self.name > other.name
        # Safely compare rarity, treating None as 0
        return (self.rarity or 0) > (other.rarity or 0)

    
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

