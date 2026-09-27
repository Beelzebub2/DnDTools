"""Tracks Marketplace packets so the lister can confirm each listing."""
import threading
import time
from dataclasses import dataclass

REGISTER_SUCCESS = 1
MAX_SNAPSHOT_AGE_S = 120
# currentPage base (0 or 1) unverified — 0 never mistakes page 2 for page 1;
# flip to 1 if the in-game dry run refuses on page 1.
FIRST_PAGE = 0
ITEM_LEVEL_FAIL_CODES = frozenset({662, 666})
ITEM_ID_PREFIX = "Id_Item_"
FAIL_CODE_MESSAGES = {
    650: "Marketplace general error",
    655: "Maximum number of listings reached",
    656: "Price was not set (typing may have failed)",
    657: "Not enough gold for the listing fee",
    660: "Price is above the maximum allowed",
    662: "Item was looted in a raid and can't be traded",
    663: "Squires can't list items",
    664: "Not enough play time to list items",
    665: "Can't list while matchmaking",
    666: "Item is not tradable",
}


def describe_fail_code(code: int) -> str:
    return FAIL_CODE_MESSAGES.get(code, f"Marketplace error {code}")


@dataclass(frozen=True)
class ListingsSnapshot:
    received_at: float
    available: tuple  # free spot order indexes (availableOrderIndexes)
    current_page: int = FIRST_PAGE

    @property
    def free(self) -> int:
        return len(self.available)


@dataclass(frozen=True)
class RegisterOutcome:
    status: str
    fail_code: int | None = None


class MarketplaceState:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._cond = threading.Condition()
        self._snapshot = None
        self._listed_at = {}  # itemUniqueId(str) -> last received_at seen
        self._register_result = None
        self._item_list = None  # (received_at, [(item_id, price)])

    def now(self) -> float:
        return self._clock()

    def handle_my_item_list(self, message) -> None:
        received = self._clock()
        with self._cond:
            self._snapshot = ListingsSnapshot(
                received_at=received,
                available=tuple(int(i) for i in message.availableOrderIndexes),
                current_page=int(message.currentPage),
            )
            for info in message.myItemInfos:
                self._listed_at[str(info.itemInfo.item.itemUniqueId)] = received
            self._cond.notify_all()

    def handle_item_list(self, message) -> None:
        """S2C_MARKETPLACE_ITEM_LIST_RES: one page of View Market search results."""
        received = self._clock()
        rows = [(str(info.item.itemId).split(ITEM_ID_PREFIX)[-1], int(info.price)) for info in message.itemInfos]
        with self._cond:
            self._item_list = (received, rows)
            self._cond.notify_all()

    def wait_for_item_list(self, since: float, timeout: float):
        """Rows [(item_id, price)] from the first search result page received after `since`."""
        with self._cond:
            self._cond.wait_for(lambda: self._item_list is not None and self._item_list[0] > since, timeout)
            if self._item_list is None or self._item_list[0] <= since:
                return None
            return list(self._item_list[1])

    def handle_register_res(self, message) -> None:
        with self._cond:
            self._register_result = int(message.result)
            self._cond.notify_all()

    def snapshot(self):
        with self._cond:
            return self._snapshot

    def listed_ids(self) -> frozenset:
        with self._cond:
            return frozenset(self._listed_at)

    def begin_register(self) -> None:
        with self._cond:
            self._register_result = None

    def wait_for_register(self, timeout: float) -> RegisterOutcome:
        with self._cond:
            self._cond.wait_for(lambda: self._register_result is not None, timeout)
            result = self._register_result
        if result is None:
            return RegisterOutcome("timeout")
        if result == REGISTER_SUCCESS:
            return RegisterOutcome("ok")
        return RegisterOutcome("failed", result)

    def wait_for_listing(self, unique_id: str, since: float, timeout: float) -> bool:
        key = str(unique_id)
        with self._cond:
            return self._cond.wait_for(lambda: self._listed_at.get(key, float("-inf")) > since, timeout)
