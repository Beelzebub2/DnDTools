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
