import sys
import threading

import networking.protos

# Generated *_pb2 modules import siblings by bare name (e.g. `import _Item_pb2`).
_PROTOS_PATH = str(next(iter(networking.protos.__path__)))
if _PROTOS_PATH not in sys.path:
    sys.path.insert(0, _PROTOS_PATH)

from networking.protos import Merchant_pb2

from src.models.merchant_state import SELL_SUCCESS, MerchantState


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _quests(*quest_ids, required=""):
    msg = Merchant_pb2.SS2C_MERCHANT_QUEST_LIST_INFO_RES()
    for qid in quest_ids:
        msg.quests.add(questId=qid, requiredQuestMerchantId=required)
    return msg


def _sold(*unique_ids, result=SELL_SUCCESS):
    msg = Merchant_pb2.SS2C_MERCHANT_STOCK_SELL_BACK_RES(result=result)
    msg.merchantResult.deleteUniqueIds.extend(unique_ids)
    return msg


def test_quest_list_names_the_opened_merchant():
    clock = Clock()
    state = MerchantState(clock)
    clock.t = 101.0
    state.handle_quest_list(_quests("QuestData:Id_Quest_TheCollector_01", "QuestData:Id_Quest_TheCollector_02"))
    assert state.wait_for_merchant("TheCollector", since=100.5, timeout=0.01)
    assert not state.wait_for_merchant("Weaponsmith", since=100.5, timeout=0.01)


def test_a_quest_that_only_requires_the_merchant_does_not_name_it():
    # requiredQuestMerchantId is a prerequisite (Alchemist_01 requires TavernMaster_01), not the owner.
    state = MerchantState(Clock())
    state.handle_quest_list(_quests("QuestData:Id_Quest_Alchemist_01",
                                    required="DesignDataMerchant:Id_Merchant_TheCollector"))
    assert not state.wait_for_merchant("TheCollector", since=99.0, timeout=0.01)


def test_merchant_key_must_match_whole_name():
    state = MerchantState(Clock())
    state.handle_quest_list(_quests("QuestData:Id_Quest_TheCollectorX_01"))
    assert not state.wait_for_merchant("TheCollector", since=99.0, timeout=0.01)


def test_old_quest_lists_do_not_count():
    clock = Clock()
    state = MerchantState(clock)
    state.handle_quest_list(_quests("QuestData:Id_Quest_TheCollector_01"))
    assert not state.wait_for_merchant("TheCollector", since=100.0, timeout=0.01)


def test_wait_for_merchant_wakes_when_the_list_arrives():
    clock = Clock()
    state = MerchantState(clock)

    def later():
        clock.t = 102.0
        state.handle_quest_list(_quests("QuestData:Id_Quest_TheCollector_01"))
    timer = threading.Timer(0.05, later)
    timer.start()
    try:
        assert state.wait_for_merchant("TheCollector", since=101.0, timeout=2.0)
    finally:
        timer.cancel()


def test_sell_reply_reports_result_and_sold_ids():
    clock = Clock()
    state = MerchantState(clock)
    clock.t = 105.0
    state.handle_sell_back(_sold(722759077045754889, 5))
    reply = state.wait_for_sell_back(since=104.0, timeout=0.01)
    assert reply.result == SELL_SUCCESS
    assert reply.deleted_ids == ("722759077045754889", "5")


def test_sell_reply_before_since_is_ignored():
    state = MerchantState(Clock())
    state.handle_sell_back(_sold(1))
    assert state.wait_for_sell_back(since=100.0, timeout=0.01) is None


def test_failed_sell_reply_is_reported():
    clock = Clock()
    state = MerchantState(clock)
    clock.t = 101.0
    state.handle_sell_back(_sold(result=7))
    reply = state.wait_for_sell_back(since=100.0, timeout=0.01)
    assert reply.result == 7
    assert reply.deleted_ids == ()


def test_signed_ids_are_read_as_the_unsigned_item_ids():
    # deleteUniqueIds is int64 while itemUniqueId is uint64: ids from 2**63 arrive negative.
    clock = Clock()
    state = MerchantState(clock)
    clock.t = 101.0
    state.handle_sell_back(_sold(-1))
    assert state.wait_for_sell_back(since=100.0, timeout=0.01).deleted_ids == (str(2 ** 64 - 1),)
