import sys

import networking.protos

# Generated *_pb2 modules import siblings by bare name (e.g. `import _Item_pb2`).
_PROTOS_PATH = str(next(iter(networking.protos.__path__)))
if _PROTOS_PATH not in sys.path:
    sys.path.insert(0, _PROTOS_PATH)

from networking.protos import MarketPlace_pb2  # noqa: E402

from src.models.market_history import MarketHistory, rarity_of, rows_from_item_list  # noqa: E402

DAY_MS = 86_400_000


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def _page(*listings):
    """listings: (listing_id, item, price, remain_ms, luck)"""
    msg = MarketPlace_pb2.SS2C_MARKETPLACE_ITEM_LIST_RES(currentPage=1, maxPage=1)
    for listing_id, item, price, remain_ms, luck in listings:
        info = msg.itemInfos.add()
        info.listingId = listing_id
        info.price = price
        info.remainExpirationTime = remain_ms
        info.item.itemId = f"DesignDataItem:Id_Item_{item}"
        info.item.itemCount = 1
        info.nickname.originalNickName = "seller1"
        roll = info.item.secondaryPropertyArray.add()
        roll.propertyTypeId = "DesignDataItemPropertyType:Id_ItemPropertyType_Effect_Luck"
        roll.propertyValue = luck
    return msg


def _history(clock):
    return MarketHistory(":memory:", clock=clock)


def test_rarity_from_item_id_suffix():
    assert rarity_of("HeaterShield_5001") == 5
    assert rarity_of("GemRing_6001") == 6
    assert rarity_of("BonePowder") == 0


def test_rows_parse_listing_fields():
    row = rows_from_item_list(_page((11, "HeaterShield_5001", 300, 5 * DAY_MS, 17)))[0]
    assert (row.listing_id, row.item_id, row.price, row.rolls, row.seller) == (
        "11", "HeaterShield_5001", 300, (("Luck", 17),), "seller1")


def test_active_rows_returns_recent_unexpired_listings():
    clock = Clock()
    history = _history(clock)
    history.record_item_list(_page((1, "HeaterShield_5001", 300, 5 * DAY_MS, 17),
                                   (2, "HeaterShield_5001", 350, 1000, 12)))  # expires in 1 s
    clock.t += 60
    rows = history.active_rows("HeaterShield_5001", max_age_s=3600)
    assert [(r.listing_id, r.price, r.rolls) for r in rows] == [("1", 300, (("Luck", 17),))]


def test_scan_marks_listings_that_vanished_before_expiry():
    clock = Clock()
    history = _history(clock)
    history.record_item_list(_page((1, "HeaterShield_5001", 300, 5 * DAY_MS, 17),
                                   (2, "HeaterShield_5001", 900, 5 * DAY_MS, 20)))
    clock.t += 3600
    started = clock.t
    history.record_item_list(_page((1, "HeaterShield_5001", 300, 5 * DAY_MS, 17)))  # #2 is gone
    vanished = history.note_scan("HeaterShield_5001", started, max_price=500, complete=False)
    assert vanished == 0  # 900g is beyond what this scan covered, so we can't tell
    vanished = history.note_scan("HeaterShield_5001", started, max_price=500, complete=True)
    assert vanished == 1
    assert [r.listing_id for r in history.vanished_rows("HeaterShield_5001")] == ["2"]
    assert history.summary()["vanished"] == 1


def test_seen_again_listing_is_not_vanished():
    clock = Clock()
    history = _history(clock)
    history.record_item_list(_page((1, "HeaterShield_5001", 300, 5 * DAY_MS, 17)))
    clock.t += 60
    started = clock.t
    history.record_item_list(_page((1, "HeaterShield_5001", 290, 5 * DAY_MS, 17)))  # price cut, still listed
    assert history.note_scan("HeaterShield_5001", started, max_price=1000, complete=True) == 0
    assert history.active_rows("HeaterShield_5001", 3600)[0].price == 290


def test_my_listings_record_when_ours_sold():
    clock = Clock()
    history = _history(clock)
    msg = MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES()
    info = msg.myItemInfos.add()
    info.itemInfo.listingId = 41875349
    info.itemInfo.price = 200
    info.itemInfo.item.itemId = "DesignDataItem:Id_Item_GreatHelm_3001"
    info.myItemState = 1
    history.record_my_listings(msg)
    info.myItemState = 3
    clock.t += 50
    history.record_my_listings(msg)
    assert history.summary()["my_sold"] == 1
    sold_at = history.connection().execute("SELECT sold_at FROM my_listings").fetchone()[0]
    assert sold_at == clock.t


def test_count_seen_before_supports_incremental_crawls():
    clock = Clock()
    history = _history(clock)
    history.record_item_list(_page((1, "A_5001", 100, DAY_MS, 1), (2, "A_5001", 100, DAY_MS, 1)))
    clock.t += 10
    started = clock.t
    history.record_item_list(_page((3, "A_5001", 100, DAY_MS, 1)))
    assert history.count_seen_before(["1", "2", "3", "4"], started) == 2
    assert history.count_seen_before([], started) == 0
