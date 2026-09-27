import pytest

from src.models.marketplace_layout import (
    SPOTS_PER_PAGE, auto_tab_order, build_layout, spot_location, tab_icon_index,
)
from src.models.screen_scaling import Scale, scale_for, scale_length, scale_point

MAPPING = [4, 20, 5, 6, 7, 8, 9, 30]


def test_scale_for_standard_and_ultrawide():
    assert scale_for((1920, 1080)) == Scale(1.0, 1.0, 0.0)
    assert scale_for((3840, 2160)) == Scale(2.0, 2.0, 0.0)
    uw = scale_for((3440, 1440))
    assert uw.sx == uw.sy == pytest.approx(1440 / 1080)
    assert uw.offset_x == pytest.approx((3440 - 1440 * 16 / 9) / 2)


def test_scale_point_matches_existing_sorter_math():
    # macros BASE_LAYOUT['stash'] = (1378, 199); 4K → (2756, 398)
    assert scale_point(1378, 199, scale_for((3840, 2160))) == (2756, 398)
    assert scale_length(40.5, scale_for((2560, 1440))) == pytest.approx(54.0)
    assert scale_length(0.1, scale_for((1280, 720))) == 1.0


def test_spot_location_pages_of_ten():
    assert SPOTS_PER_PAGE == 10
    assert spot_location(0) == (0, 0)
    assert spot_location(9) == (0, 9)
    assert spot_location(10) == (1, 0)
    assert spot_location(37) == (3, 7)


def test_tab_icon_index_inventory_and_stash():
    assert tab_icon_index("2", MAPPING) == 0
    assert tab_icon_index("4", MAPPING) == 1
    assert tab_icon_index("5", MAPPING) == 3


def test_tab_icon_index_unmapped_returns_none():
    assert tab_icon_index("9", [4, 20, 5, 6, 7, 8, 0, 30]) is None
    assert tab_icon_index("abc", MAPPING) is None


def test_auto_tab_order_is_stash_ids_ascending():
    # Verified in game: Marketplace tab icons follow the account's stash ids in ascending
    # order; inventory (2) and equipment (3) are not stash tabs.
    assert auto_tab_order(["2", "3", "4", "20", "5", "21", "30"]) == [4, 5, 20, 21, 30]
    assert auto_tab_order(["2", "3", "abc", "101"]) == []


def test_build_layout_1080p_base_points():
    layout = build_layout((1920, 1080))
    assert layout.point("price_field") == (960, 618)
    assert layout.point("create_listing_button") == (960, 968)
    assert layout.spot_row(0) == (298, 516)
    assert layout.spot_row(2) == (298, 616)
    assert layout.tab_icon(1) == (1315, 242)  # 196 + 46.5 = 242.5 → Python rounds half to even


def test_build_layout_4k_scales_everything():
    layout = build_layout((3840, 2160))
    assert layout.point("price_field") == (1920, 1236)
    assert layout.spot_row(1) == (596, 1132)


def test_item_centre_uses_grid_width_per_stash():
    layout = build_layout((1920, 1080))
    # inventory 10 columns: slot 12 → col 2, row 1; 1x1 item
    assert layout.item_centre("2", 12, 1, 1) == (round(1443 + 41.3 * 2.5), round(622 + 41.3 * 1.5))
    # stash 12 columns: slot 13 → col 1, row 1; 2x2 item
    assert layout.item_centre("4", 13, 2, 2) == (round(1369 + 41.3 * 2), round(184 + 41.3 * 2))


def test_window_origin_and_calibration_offsets():
    calibration = {"points": {"price_field": [5, -3]}, "lengths": {"cell": 1.5}}
    layout = build_layout((1920, 1080), window_origin=(100, 50), calibration=calibration)
    assert layout.point("price_field") == (1065, 665)
    assert layout.lengths["cell"] == pytest.approx(42.8)


def test_calibration_ignores_junk():
    layout = build_layout((1920, 1080), calibration={"points": {"price_field": "x", "nope": [1, 1]}, "lengths": {"cell": "big"}})
    assert layout.point("price_field") == (960, 618)
    assert "nope" not in layout.points


def test_hover_targets_cover_every_click_point():
    names = [name for name, _ in build_layout((1920, 1080)).hover_targets()]
    assert names == ["spot row 1", "spot row 10", "next page arrow", "inventory icon", "first stash tab icon",
                     "inventory first cell", "inventory last cell", "stash first cell", "stash last cell",
                     "price field", "create listing button"]
