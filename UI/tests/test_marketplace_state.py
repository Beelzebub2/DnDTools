import sys
import threading

import networking.protos

_PROTOS_PATH = str(next(iter(networking.protos.__path__)))
if _PROTOS_PATH not in sys.path:
    sys.path.insert(0, _PROTOS_PATH)

from networking.protos import MarketPlace_pb2

from src.models.marketplace_state import (
    MarketplaceState, RegisterOutcome, describe_fail_code,
)


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _my_list(total, available=(), unique_ids=()):
    msg = MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(totalItemCount=total)
    msg.availableOrderIndexes.extend(available)
    for uid in unique_ids:
        info = msg.myItemInfos.add()
        info.itemInfo.item.itemUniqueId = uid
        info.itemInfo.price = 500
    return msg


def test_snapshot_none_until_listing_packet():
    assert MarketplaceState().snapshot() is None


def test_my_item_list_records_free_spots_not_total_item_count():
    # Real packet: totalItemCount=35 while only spots 0-1 were used; free spots are the truth.
    clock = FakeClock()
    state = MarketplaceState(clock=clock)
    state.handle_my_item_list(_my_list(35, available=[2, 3, 4]))
    snap = state.snapshot()
    assert (snap.free, snap.available, snap.received_at) == (3, (2, 3, 4), 100.0)


def test_register_success_and_failure():
    state = MarketplaceState()
    state.begin_register()
    state.handle_register_res(MarketPlace_pb2.SS2C_MARKETPLACE_ITEM_REGISTER_RES(result=1))
    assert state.wait_for_register(0.1) == RegisterOutcome("ok")
    state.begin_register()
    state.handle_register_res(MarketPlace_pb2.SS2C_MARKETPLACE_ITEM_REGISTER_RES(result=657))
    assert state.wait_for_register(0.1) == RegisterOutcome("failed", 657)


def test_begin_register_discards_stale_result():
    state = MarketplaceState()
    state.handle_register_res(MarketPlace_pb2.SS2C_MARKETPLACE_ITEM_REGISTER_RES(result=1))
    state.begin_register()
    assert state.wait_for_register(0.05) == RegisterOutcome("timeout")


def test_wait_for_register_wakes_on_packet_from_other_thread():
    state = MarketplaceState()
    state.begin_register()
    timer = threading.Timer(0.05, state.handle_register_res,
                            [MarketPlace_pb2.SS2C_MARKETPLACE_ITEM_REGISTER_RES(result=1)])
    timer.start()
    assert state.wait_for_register(2.0).status == "ok"


def test_wait_for_listing_requires_newer_snapshot_with_item():
    clock = FakeClock()
    state = MarketplaceState(clock=clock)
    state.handle_my_item_list(_my_list(1, unique_ids=[555]))
    assert state.wait_for_listing("555", since=100.0, timeout=0.05) is False  # not newer
    clock.t = 101.0
    state.handle_my_item_list(_my_list(2, unique_ids=[555, 777]))
    assert state.wait_for_listing("777", since=100.5, timeout=0.05) is True
    assert state.wait_for_listing("999", since=100.5, timeout=0.05) is False


def _item_list(prices, item="HeaterShield_5001"):
    msg = MarketPlace_pb2.SS2C_MARKETPLACE_ITEM_LIST_RES(currentPage=1, maxPage=13)
    for price in prices:
        info = msg.itemInfos.add()
        info.item.itemId = f"DesignDataItem:Id_Item_{item}"
        info.price = price
        roll = info.item.secondaryPropertyArray.add()
        roll.propertyTypeId = "DesignDataItemPropertyType:Id_ItemPropertyType_Effect_Luck"
        roll.propertyValue = 17
    return msg


def test_wait_for_item_list_returns_prices_newer_than_since():
    clock = FakeClock()
    state = MarketplaceState(clock=clock)
    state.handle_item_list(_item_list([300, 310]))
    assert state.wait_for_item_list(since=100.0, timeout=0.05) is None  # not newer
    clock.t = 101.0
    state.handle_item_list(_item_list([300, 333], item="GemRing_6001"))
    rows = state.wait_for_item_list(since=100.5, timeout=0.05)
    assert [(r.item_id, r.price, r.rolls) for r in rows] == [
        ("GemRing_6001", 300, (("Luck", 17),)), ("GemRing_6001", 333, (("Luck", 17),))]


def test_describe_fail_code():
    assert "gold" in describe_fail_code(657).lower()
    assert "655" in describe_fail_code(655) or "maximum" in describe_fail_code(655).lower()
    assert describe_fail_code(12345) == "Marketplace error 12345"


def test_snapshot_records_current_page():
    state = MarketplaceState()
    msg = _my_list(3)
    msg.currentPage = 2
    state.handle_my_item_list(msg)
    assert state.snapshot().current_page == 2


def test_listed_ids_returns_seen_unique_ids():
    state = MarketplaceState()
    assert state.listed_ids() == frozenset()
    state.handle_my_item_list(_my_list(2, unique_ids=(11, 12)))
    assert state.listed_ids() == frozenset({"11", "12"})


def test_snapshot_lists_sold_and_expired_payouts():
    msg = _my_list(35, available=[2, 3])
    sold = msg.myItemInfos.add()
    sold.orderIndex = 1
    sold.myItemState = 3
    sold.itemInfo.price = 200
    sold.itemInfo.item.itemId = "DesignDataItem:Id_Item_GreatHelm_3001"
    active = msg.myItemInfos.add()
    active.orderIndex = 0
    active.myItemState = 1
    state = MarketplaceState()
    state.handle_my_item_list(msg)
    assert state.snapshot().payouts == ((1, 3, "GreatHelm_3001", 200),)


def test_transfer_result_wait():
    state = MarketplaceState()
    state.begin_transfer()
    assert state.wait_for_transfer(0.05) is None
    state.handle_transfer_res(MarketPlace_pb2.SS2C_MARKETPLACE_TRANSFER_ITEMS_RES(result=1))
    assert state.wait_for_transfer(0.05) == 1


def test_listed_ids_only_counts_items_still_for_sale():
    msg = _my_list(3, unique_ids=[555, 777])
    msg.myItemInfos[0].myItemState = 1   # listing
    msg.myItemInfos[1].myItemState = 2   # expired -> back to the stash, may be relisted
    state = MarketplaceState()
    state.handle_my_item_list(msg)
    assert state.listed_ids() == frozenset({"555"})


def test_own_listing_ids_and_fresh_snapshot():
    clock = FakeClock()
    state = MarketplaceState(clock=clock)
    msg = _my_list(3, unique_ids=[555])
    msg.myItemInfos[0].itemInfo.listingId = 4242
    msg.myItemInfos[0].myItemState = 1
    state.handle_my_item_list(msg)
    assert state.own_listing_ids() == frozenset({"4242"})
    assert state.wait_for_fresh_snapshot(since=100.0, timeout=0.05) is None   # not newer than `since`
    clock.t = 101.0
    state.handle_my_item_list(msg)
    assert state.wait_for_fresh_snapshot(since=100.0, timeout=0.05).received_at == 101.0
