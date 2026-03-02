import copy

_DEFAULT_DIRECTION = "desc"
_DEFAULT_SORTABLE_FIELDS = ("height", "width", "name", "rarity")
_DEFAULT_SORT_ORDER = [{"field": key, "direction": _DEFAULT_DIRECTION} for key in _DEFAULT_SORTABLE_FIELDS]


class Item:
    """Represents an item that can be sorted inside a stash."""

    SORTABLE_FIELDS = _DEFAULT_SORTABLE_FIELDS
    DEFAULT_DIRECTION = _DEFAULT_DIRECTION
    sort_order = copy.deepcopy(_DEFAULT_SORT_ORDER)

    _RARITY_RANK = {
        "none": 0,
        "poor": 1,
        "common": 2,
        "uncommon": 3,
        "rare": 4,
        "epic": 5,
        "legend": 6,
        "legendary": 6,
        "unique": 7,
        "artifact": 8,
    }

    def __init__(
        self,
        item_id,
        name,
        rarity,
        position,
        width,
        height,
        stash,
        vendor_price=None,
        quantity=1,
        max_stack_size=1,
    ):
        self.item_id = item_id
        self.name = name
        self.rarity = rarity
        self.width = width
        self.height = height
        self.position = position
        self.stash = stash
        self.vendor_price = vendor_price
        self.quantity = int(quantity) if quantity is not None else 1
        if self.quantity < 1:
            self.quantity = 1
        self.max_stack_size = int(max_stack_size) if max_stack_size is not None else 1
        if self.max_stack_size < 1:
            self.max_stack_size = 1
        self.stacked = False

    def __lt__(self, other):
        if not isinstance(other, Item):
            return NotImplemented
        return Item.compare_items(self, other) < 0

    def __eq__(self, other):
        if not isinstance(other, Item):
            return False
        return self.name == other.name and self.rarity == other.rarity and self.position == other.position

    def __hash__(self):
        pos = (self.position.x, self.position.y) if self.position else (None, None)
        return hash((self.item_id, self.rarity, pos))

    def __repr__(self):
        return f"{self.rarity} {self.name} {self.position} {self.width}X{self.height}"

    def to_dict(self):
        return {
            "item_id": self.item_id,
            "name": self.name,
            "rarity": self.rarity,
            "position": self.position,
            "width": self.width,
            "height": self.height,
            "vendor_price": self.vendor_price,
            "itemCount": self.quantity,
            "maxStackSize": self.max_stack_size,
        }

    @classmethod
    def normalize_sort_order(cls, order):
        candidates = []
        if isinstance(order, (list, tuple)):
            candidates = list(order)
        elif isinstance(order, (str, dict)):
            candidates = [order]

        normalized = []
        seen = set()

        for value in candidates:
            directive = cls._parse_sort_directive(value)
            if not directive:
                continue
            field = directive["field"]
            if field in seen:
                continue
            normalized.append(directive)
            seen.add(field)

        for field in cls.SORTABLE_FIELDS:
            if field not in seen:
                normalized.append({"field": field, "direction": cls.DEFAULT_DIRECTION})
                seen.add(field)

        return normalized

    @classmethod
    def build_sort_comparator(cls, sort_order=None):
        if sort_order:
            directives = sort_order if isinstance(sort_order, list) else cls.normalize_sort_order(sort_order)
        else:
            directives = cls.sort_order

        def comparator(left, right):
            return cls.compare_items(left, right, directives)

        return comparator

    @classmethod
    def compare_items(cls, left, right, sort_order=None):
        if sort_order:
            directives = sort_order if isinstance(sort_order, list) else cls.normalize_sort_order(sort_order)
        else:
            directives = cls.sort_order

        for directive in directives:
            if not isinstance(directive, dict):
                continue
            field = directive.get("field")
            direction = cls._normalize_direction(directive.get("direction"))
            if not field or field not in cls.SORTABLE_FIELDS:
                continue

            if field in ("height", "width"):
                result = cls._compare_numeric(getattr(left, field, 0), getattr(right, field, 0), direction)
            elif field == "rarity":
                result = cls._compare_numeric(
                    cls._rarity_rank(getattr(left, "rarity", None)),
                    cls._rarity_rank(getattr(right, "rarity", None)),
                    direction,
                )
            elif field == "name":
                result = cls._compare_string(getattr(left, "name", ""), getattr(right, "name", ""), direction)
            else:
                result = cls._compare_numeric(getattr(left, field, 0), getattr(right, field, 0), direction)

            if result:
                return result

        fallback = cls._compare_numeric(cls._area(left), cls._area(right), cls.DEFAULT_DIRECTION)
        if fallback:
            return fallback

        fallback = cls._compare_numeric(cls._longest_side(left), cls._longest_side(right), cls.DEFAULT_DIRECTION)
        if fallback:
            return fallback

        fallback = cls._compare_numeric(
            cls._rarity_rank(getattr(left, "rarity", None)),
            cls._rarity_rank(getattr(right, "rarity", None)),
            cls.DEFAULT_DIRECTION,
        )
        if fallback:
            return fallback

        fallback = cls._compare_string(getattr(left, "name", ""), getattr(right, "name", ""), cls.DEFAULT_DIRECTION)
        if fallback:
            return fallback

        left_id = id(left)
        right_id = id(right)
        if left_id == right_id:
            return 0
        return -1 if left_id < right_id else 1

    @staticmethod
    def _compare_numeric(a, b, direction):
        a_val = Item._safe_numeric(a)
        b_val = Item._safe_numeric(b)
        if a_val == b_val:
            return 0
        if direction == "asc":
            return -1 if a_val < b_val else 1
        return -1 if a_val > b_val else 1

    @staticmethod
    def _compare_string(a, b, direction):
        a_val = (a or "").strip().lower()
        b_val = (b or "").strip().lower()
        if a_val == b_val:
            return 0
        if direction == "asc":
            return -1 if a_val < b_val else 1
        return -1 if a_val > b_val else 1

    @staticmethod
    def _safe_numeric(value):
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return value
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _rarity_rank(cls, rarity):
        if isinstance(rarity, (int, float)):
            return rarity
        if not rarity:
            return 0
        key = str(rarity).strip().lower()
        return cls._RARITY_RANK.get(key, 0)

    @staticmethod
    def _area(item):
        return Item._safe_numeric(getattr(item, "width", 0)) * Item._safe_numeric(getattr(item, "height", 0))

    @staticmethod
    def _longest_side(item):
        width = Item._safe_numeric(getattr(item, "width", 0))
        height = Item._safe_numeric(getattr(item, "height", 0))
        return max(width, height)

    @classmethod
    def _parse_sort_directive(cls, value):
        field = None
        direction = cls.DEFAULT_DIRECTION
        if isinstance(value, dict):
            field = value.get("field") or value.get("key") or value.get("sort")
            direction = value.get("direction") or value.get("dir")
        elif isinstance(value, str):
            text = value.strip()
            if ":" in text:
                field, direction = text.split(":", 1)
            elif " " in text:
                field, direction = text.split(None, 1)
            else:
                field = text
        elif isinstance(value, (tuple, list)) and value:
            field = value[0]
            if len(value) > 1:
                direction = value[1]

        if not field:
            return None

        field_key = str(field).strip().lower()
        if field_key not in cls.SORTABLE_FIELDS:
            return None

        return {"field": field_key, "direction": cls._normalize_direction(direction)}

    @classmethod
    def _normalize_direction(cls, direction):
        if isinstance(direction, str) and direction.strip().lower() == "asc":
            return "asc"
        return cls.DEFAULT_DIRECTION

    @classmethod
    def copy_sort_order(cls, order=None):
        return copy.deepcopy(order if order is not None else cls.sort_order)

