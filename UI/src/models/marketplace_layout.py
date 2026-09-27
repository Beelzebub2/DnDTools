"""Marketplace (My Listings) screen coordinates for any resolution."""
from dataclasses import dataclass

from src.models.market_rules import INVENTORY_STASH_ID
from src.models.screen_scaling import scale_for, scale_length, scale_point

SPOTS_PER_PAGE = 10
INVENTORY_COLUMNS, INVENTORY_ROWS = 10, 5
STASH_COLUMNS, STASH_ROWS = 12, 20

# Measured from 16:9 screenshots, expressed at 1920x1080.
BASE_POINTS = {
    "spot_row_origin": (298, 516),
    "next_page_arrow": (374, 1025),
    "tab_icon_origin": (1315, 196),
    "inv_grid_origin": (1443, 622),
    "stash_grid_origin": (1369, 184),
    "price_field": (960, 618),
    "create_listing_button": (960, 968),
    # In-game pricing flow (measured at 3840x2160, halved):
    "form_search_button": (960, 467),      # "Search" under the selected item in List an Item
    "market_attr_reset": (1677, 207),      # reset icon of View Market's Random Attribute filter
    "market_search_button": (1794, 277),   # View Market "Search" button
    "my_listings_tab": (1062, 123),        # "My Listings" tab header
    "market_next_page": (1032, 1015),      # View Market next-page arrow
    "confirm_listing_yes": (861, 620),     # "Would you like to list the item?" -> Yes
}
BASE_LENGTHS = {"spot_row_spacing": 50.0, "tab_icon_spacing": 46.5, "cell": 41.3}


def spot_location(order_index: int):
    return divmod(int(order_index), SPOTS_PER_PAGE)


FIRST_STASH_TAB_ID, GEAR_SET_FIRST_ID = 4, 100


def auto_tab_order(stash_ids):
    """Marketplace stash tab icons follow the account's stash ids in ascending order."""
    ids = []
    for raw in stash_ids:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if FIRST_STASH_TAB_ID <= value < GEAR_SET_FIRST_ID:
            ids.append(value)
    return sorted(set(ids))


def tab_icon_index(stash_id: str, tab_mapping):
    if str(stash_id) == INVENTORY_STASH_ID:
        return 0
    try:
        stash_type = int(stash_id)
    except (TypeError, ValueError):
        return None
    if stash_type == 0 or stash_type not in tab_mapping:
        return None
    return 1 + list(tab_mapping).index(stash_type)


@dataclass(frozen=True)
class MarketplaceLayout:
    points: dict
    lengths: dict

    def point(self, key: str):
        return self.points[key]

    def _offset(self, key: str, dx: float, dy: float):
        x, y = self.points[key]
        return int(round(x + dx)), int(round(y + dy))

    def spot_row(self, row: int):
        return self._offset("spot_row_origin", 0, self.lengths["spot_row_spacing"] * row)

    def tab_icon(self, icon_index: int):
        return self._offset("tab_icon_origin", 0, self.lengths["tab_icon_spacing"] * icon_index)

    def item_centre(self, stash_id: str, slot_id: int, width: int, height: int):
        is_inv = str(stash_id) == INVENTORY_STASH_ID
        columns = INVENTORY_COLUMNS if is_inv else STASH_COLUMNS
        origin = "inv_grid_origin" if is_inv else "stash_grid_origin"
        row, col = divmod(int(slot_id), columns)
        cell = self.lengths["cell"]
        return self._offset(origin, cell * (col + width / 2), cell * (row + height / 2))

    def hover_targets(self):
        return [
            ("spot row 1", self.spot_row(0)),
            ("spot row 10", self.spot_row(SPOTS_PER_PAGE - 1)),
            ("next page arrow", self.point("next_page_arrow")),
            ("inventory icon", self.tab_icon(0)),
            ("first stash tab icon", self.tab_icon(1)),
            ("inventory first cell", self.item_centre(INVENTORY_STASH_ID, 0, 1, 1)),
            ("inventory last cell", self.item_centre(INVENTORY_STASH_ID, INVENTORY_COLUMNS * INVENTORY_ROWS - 1, 1, 1)),
            ("stash first cell", self.item_centre("4", 0, 1, 1)),
            ("stash last cell", self.item_centre("4", STASH_COLUMNS * STASH_ROWS - 1, 1, 1)),
            ("price field", self.point("price_field")),
            ("create listing button", self.point("create_listing_button")),
        ]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _point_delta(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    dx, dy = _number(raw[0]), _number(raw[1])
    return None if dx is None or dy is None else (dx, dy)


def build_layout(resolution, window_origin=(0, 0), calibration=None) -> MarketplaceLayout:
    scale = scale_for(resolution)
    calibration = calibration if isinstance(calibration, dict) else {}
    point_deltas = calibration.get("points") if isinstance(calibration.get("points"), dict) else {}
    length_deltas = calibration.get("lengths") if isinstance(calibration.get("lengths"), dict) else {}
    ox, oy = window_origin
    points = {}
    for key, (bx, by) in BASE_POINTS.items():
        x, y = scale_point(bx, by, scale)
        dx, dy = _point_delta(point_deltas.get(key)) or (0.0, 0.0)
        points[key] = (int(round(x + ox + dx)), int(round(y + oy + dy)))
    lengths = {}
    for key, base in BASE_LENGTHS.items():
        delta = _number(length_deltas.get(key)) or 0.0
        lengths[key] = max(scale_length(base, scale) + delta, 1.0)
    return MarketplaceLayout(points, lengths)
