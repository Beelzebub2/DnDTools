"""Tracks merchant packets so the merchant seller can confirm where it is and what was sold."""
import threading
import time
from dataclasses import dataclass

SELL_SUCCESS = 1
QUEST_MARK = "Id_Quest_"
UINT64 = 1 << 64  # deleteUniqueIds is int64, itemUniqueId uint64: ids from 2**63 arrive negative


@dataclass(frozen=True)
class SellBack:
    """S2C_MERCHANT_STOCK_SELL_BACK_RES: the game's answer to Make Deal on the Sell tab."""
    received_at: float
    result: int
    deleted_ids: tuple  # itemUniqueIds that left the stash (were sold)


def _bare(design_id) -> str:
    return str(design_id).split(":")[-1]


def _names_merchant(quest_ids, key: str) -> bool:
    """True when a merchant's quest list belongs to `key` (e.g. Id_Quest_TheCollector_01).

    requiredQuestMerchantId is ignored: it names a prerequisite merchant, not the owner.
    """
    return any(quest_id.startswith(f"{QUEST_MARK}{key}_") for quest_id in quest_ids)


class MerchantState:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._cond = threading.Condition()
        self._quest_list = None  # (received_at, (quest_id, ...))
        self._sell_back = None

    def now(self) -> float:
        return self._clock()

    def handle_quest_list(self, message) -> None:
        """S2C_MERCHANT_QUEST_LIST_INFO_RES arrives whenever a merchant's window opens."""
        quests = tuple(_bare(q.questId) for q in message.quests)
        with self._cond:
            self._quest_list = (self._clock(), quests)
            self._cond.notify_all()

    def handle_sell_back(self, message) -> None:
        reply = SellBack(self._clock(), int(message.result),
                         tuple(str(int(i) % UINT64) for i in message.merchantResult.deleteUniqueIds))
        with self._cond:
            self._sell_back = reply
            self._cond.notify_all()

    def wait_for_merchant(self, key: str, since: float, timeout: float) -> bool:
        """True once a quest list received after `since` names merchant `key`."""
        with self._cond:
            self._cond.wait_for(lambda: self._quest_list is not None and self._quest_list[0] > since, timeout)
            fresh = self._quest_list is not None and self._quest_list[0] > since
            return fresh and _names_merchant(self._quest_list[1], key)

    def wait_for_sell_back(self, since: float, timeout: float):
        """The first sell reply received after `since`, or None if the game did not answer in time."""
        with self._cond:
            self._cond.wait_for(lambda: self._sell_back is not None and self._sell_back.received_at > since, timeout)
            reply = self._sell_back
        return reply if reply is not None and reply.received_at > since else None
