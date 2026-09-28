"""Tracks Marketplace packets so the lister can confirm each listing."""
import threading
import time
from dataclasses import dataclass

from src.models.roll_pricing import MarketRow, stat_name

REGISTER_SUCCESS = 1
MAX_SNAPSHOT_AGE_S = 120
# currentPage base (0 or 1) unverified — 0 never mistakes page 2 for page 1;
# flip to 1 if the in-game dry run refuses on page 1.
FIRST_PAGE = 0
ITEM_LEVEL_FAIL_CODES = frozenset({662, 666})
ITEM_ID_PREFIX = "Id_Item_"
MY_ITEM_LISTING, MY_ITEM_EXPIRED, MY_ITEM_SOLD = 1, 2, 3  # myItemState (3 = sold, verified in game)
PAYOUT_STATES = frozenset({MY_ITEM_EXPIRED, MY_ITEM_SOLD})
FAIL_CODE_MESSAGES = {
    650: "Marketplace general error",
    655: "Maximum number of listings reached",
    656: "Price was not set (typing may have failed)",
    653: "Not enough inventory space",
    657: "Not enough gold for the listing fee",
    658: "Not enough space in your stash/inventory to take the item back",
    660: "Price is above the maximum allowed",
    662: "Item was looted in a raid and can't be traded",
    663: "Squires can't list items",
    664: "Not enough play time to list items",
    665: "Can't list while matchmaking",
    666: "Item is not tradable",
}


def _stats(properties) -> tuple:
    return tuple((stat_name(p.propertyTypeId), int(p.propertyValue)) for p in properties)


def _market_row(info) -> MarketRow:
    item = info.item
    return MarketRow(str(item.itemId).split(ITEM_ID_PREFIX)[-1], int(info.price),
                     _stats(item.primaryPropertyArray), _stats(item.secondaryPropertyArray),
                     str(info.listingId), max(int(item.itemCount), 1))


def describe_fail_code(code: int) -> str:
    return FAIL_CODE_MESSAGES.get(code, f"Marketplace error {code}")


@dataclass(frozen=True)
class ListingsSnapshot:
    received_at: float
    available: tuple  # free spot order indexes (availableOrderIndexes)
    current_page: int = FIRST_PAGE
    payouts: tuple = ()  # (order_index, state, item_id, price) for sold / expired listings awaiting transfer

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
        self._listing_state = {}  # itemUniqueId(str) -> latest myItemState
        self._own_listings = {}   # listingId(str) -> latest myItemState, for excluding our own asks
        self._register_result = None
        self._item_list = None  # (received_at, [MarketRow], currentPage, maxPage)
        self._transfer_result = None

    def now(self) -> float:
        return self._clock()

    def handle_my_item_list(self, message) -> None:
        received = self._clock()
        with self._cond:
            self._snapshot = ListingsSnapshot(
                received_at=received,
                available=tuple(int(i) for i in message.availableOrderIndexes),
                current_page=int(message.currentPage),
                payouts=tuple(
                    (int(info.orderIndex), int(info.myItemState),
                     str(info.itemInfo.item.itemId).split(ITEM_ID_PREFIX)[-1], int(info.itemInfo.price))
                    for info in message.myItemInfos if int(info.myItemState) in PAYOUT_STATES),
            )
            for info in message.myItemInfos:
                key = str(info.itemInfo.item.itemUniqueId)
                state = int(info.myItemState) or MY_ITEM_LISTING
                self._listed_at[key] = received
                self._listing_state[key] = state
                self._own_listings[str(info.itemInfo.listingId)] = state
            self._cond.notify_all()

    def handle_item_list(self, message) -> None:
        """S2C_MARKETPLACE_ITEM_LIST_RES: one page of View Market search results."""
        received = self._clock()
        rows = [_market_row(info) for info in message.itemInfos]
        with self._cond:
            self._item_list = (received, rows, int(message.currentPage), int(message.maxPage))
            self._cond.notify_all()

    def last_item_page(self):
        """(currentPage, maxPage) of the latest search result page, or None."""
        with self._cond:
            return None if self._item_list is None else self._item_list[2:4]

    def wait_for_item_list(self, since: float, timeout: float):
        """MarketRows from the first search result page received after `since`."""
        with self._cond:
            self._cond.wait_for(lambda: self._item_list is not None and self._item_list[0] > since, timeout)
            if self._item_list is None or self._item_list[0] <= since:
                return None
            return list(self._item_list[1])

    def handle_transfer_res(self, message) -> None:
        """S2C_MARKETPLACE_TRANSFER_ITEMS_RES after "Transfer All Items" on a sold / expired listing."""
        with self._cond:
            self._transfer_result = int(message.result)
            self._cond.notify_all()

    def begin_transfer(self) -> None:
        with self._cond:
            self._transfer_result = None

    def wait_for_transfer(self, timeout: float):
        """The transfer result code, or None if the game did not answer in time."""
        with self._cond:
            self._cond.wait_for(lambda: self._transfer_result is not None, timeout)
            return self._transfer_result

    def handle_register_res(self, message) -> None:
        with self._cond:
            self._register_result = int(message.result)
            self._cond.notify_all()

    def snapshot(self):
        with self._cond:
            return self._snapshot

    def own_listing_ids(self) -> frozenset:
        """Listing ids of our own active listings — never compare our prices against them."""
        with self._cond:
            return frozenset(k for k, state in self._own_listings.items() if state == MY_ITEM_LISTING)

    def wait_for_fresh_snapshot(self, since: float, timeout: float):
        """A My Listings snapshot received after `since`, or None if none arrived in time."""
        with self._cond:
            self._cond.wait_for(lambda: self._snapshot is not None and self._snapshot.received_at > since, timeout)
            snapshot = self._snapshot
        return snapshot if snapshot is not None and snapshot.received_at > since else None

    def wait_for_snapshot(self, since: float, timeout: float):
        """The My Listings snapshot, waiting up to `timeout` for one received after `since`."""
        with self._cond:
            self._cond.wait_for(lambda: self._snapshot is not None and self._snapshot.received_at > since, timeout)
            return self._snapshot

    def listed_ids(self) -> frozenset:
        with self._cond:
            # Only items still up for sale; expired items come back to the stash and can be relisted.
            return frozenset(k for k, state in self._listing_state.items() if state == MY_ITEM_LISTING)

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
