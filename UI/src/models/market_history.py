"""Local SQLite history of every Marketplace listing DnDTools has seen.

Fed from captured S2C_MARKETPLACE_ITEM_LIST_RES / MY_ITEM_LIST_RES packets. It lets the
lister price from more than the listings on screen right now, learn roll ranges, spot
listings that vanished before expiring (most likely sold) and track our own sales.
"""
import json
import sqlite3
import threading
import time
from dataclasses import dataclass

from src.models.roll_pricing import MarketRow, stat_name

ITEM_ID_PREFIX = "Id_Item_"
MS_PER_S = 1000.0
VANISH_MARGIN_S = 600  # a listing gone >10 min before its expiry did not simply expire
MY_STATE_LISTING = 1
LISTING_DAYS = 7  # a Marketplace listing lasts a week
DAY_S = 86400.0
MY_STATE_SOLD = 3
BUSY_TIMEOUT_S = 5.0  # wait this long for another connection (e.g. an analysis script) to finish writing

@dataclass(frozen=True)
class AgedListing:
    item_id: str
    rarity: int
    price: int
    item_count: int
    base: tuple
    rolls: tuple
    age_days: float  # how long it had been listed when first seen


_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    rarity INTEGER NOT NULL,
    price INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    base TEXT NOT NULL,
    rolls TEXT NOT NULL,
    seller TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    expires_at REAL NOT NULL,
    vanished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_listings_item ON listings(item_id, last_seen);
CREATE TABLE IF NOT EXISTS scans (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL,
    max_price INTEGER NOT NULL,
    complete INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS merchant_prices (
    item_id TEXT PRIMARY KEY,
    unit_price REAL NOT NULL,
    seen_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS my_listings (
    listing_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    price INTEGER NOT NULL,
    state INTEGER NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    sold_at REAL
);
"""


@dataclass(frozen=True)
class HistoryRow:
    listing_id: str
    item_id: str
    price: int
    item_count: int
    base: tuple
    rolls: tuple
    seller: str
    remain_ms: int


def rarity_of(item_id: str) -> int:
    """'HeaterShield_5001' -> 5; ids without a rarity suffix -> 0."""
    suffix = str(item_id).rsplit("_", 1)[-1]
    return int(suffix[0]) if len(suffix) == 4 and suffix.isdigit() else 0


def _stats(properties) -> tuple:
    return tuple((stat_name(p.propertyTypeId), int(p.propertyValue)) for p in properties)


def rows_from_item_list(message) -> list:
    rows = []
    for info in message.itemInfos:
        item = info.item
        rows.append(HistoryRow(
            listing_id=str(info.listingId),
            item_id=str(item.itemId).split(ITEM_ID_PREFIX)[-1],
            price=int(info.price),
            item_count=max(int(item.itemCount), 1),
            base=_stats(item.primaryPropertyArray),
            rolls=_stats(item.secondaryPropertyArray),
            seller=str(info.nickname.originalNickName) if info.HasField("nickname") else "",
            remain_ms=int(info.remainExpirationTime),
        ))
    return rows


def _to_market_row(record) -> MarketRow:
    listing_id, item_id, price, base, rolls, count = record
    return MarketRow(item_id, int(price), tuple(map(tuple, json.loads(base))),
                     tuple(map(tuple, json.loads(rolls))), listing_id, int(count))


class MarketHistory:
    def __init__(self, path: str, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=BUSY_TIMEOUT_S)
        # WAL lets analysis scripts read while the app keeps recording pages.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def record_rows(self, rows) -> int:
        now = self._clock()
        with self._lock:
            for r in rows:
                self._db.execute(
                    """INSERT INTO listings (listing_id, item_id, rarity, price, item_count, base, rolls, seller,
                                             first_seen, last_seen, expires_at, vanished_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                       ON CONFLICT(listing_id) DO UPDATE SET price=excluded.price, last_seen=excluded.last_seen,
                           expires_at=excluded.expires_at, vanished_at=NULL""",
                    (r.listing_id, r.item_id, rarity_of(r.item_id), r.price, r.item_count,
                     json.dumps(r.base), json.dumps(r.rolls), r.seller, now, now, now + r.remain_ms / MS_PER_S))
            self._db.commit()
        return len(rows)

    def record_item_list(self, message) -> int:
        return self.record_rows(rows_from_item_list(message))

    def note_scan(self, item_id: str, started_at: float, max_price: int, complete: bool) -> int:
        """Record a finished search of one item and mark listings that vanished since.

        Search results are cheapest-first, so any older listing of this item priced below
        the highest price an incomplete scan reached (listings at exactly that price may have
        been cut off mid-page), or any at all if the scan read every page, that did not show
        up again and was not due to expire has most likely been sold.
        """
        now = self._clock()
        with self._lock:
            self._db.execute(
                "INSERT INTO scans (item_id, started_at, finished_at, max_price, complete) VALUES (?, ?, ?, ?, ?)",
                (item_id, started_at, now, int(max_price), 1 if complete else 0))
            cursor = self._db.execute(
                """UPDATE listings SET vanished_at = ?
                   WHERE item_id = ? AND vanished_at IS NULL AND last_seen < ? AND expires_at > ?
                     AND (? = 1 OR price < ?)""",
                (now, item_id, started_at, now + VANISH_MARGIN_S, 1 if complete else 0, int(max_price)))
            self._db.commit()
            return cursor.rowcount

    def note_crawl_pass(self, rarity: int, started_at: float) -> int:
        """After a crawl read every page of one rarity, mark its listings that didn't show up again.

        A listing last seen before the pass started, and not due to expire yet, most likely
        sold (or was cancelled). Returns how many were marked.
        """
        now = self._clock()
        with self._lock:
            cursor = self._db.execute(
                """UPDATE listings SET vanished_at = ?
                   WHERE rarity = ? AND vanished_at IS NULL AND last_seen < ? AND expires_at > ?""",
                (now, int(rarity), started_at, now + VANISH_MARGIN_S))
            self._db.commit()
            return cursor.rowcount

    def record_my_listings(self, message) -> None:
        now = self._clock()
        with self._lock:
            for info in message.myItemInfos:
                item_info = info.itemInfo
                listing_id = str(item_info.listingId)
                state = int(info.myItemState)
                self._db.execute(
                    """INSERT INTO my_listings (listing_id, item_id, price, state, first_seen, last_seen, sold_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(listing_id) DO UPDATE SET state=excluded.state, last_seen=excluded.last_seen,
                           sold_at=COALESCE(my_listings.sold_at, excluded.sold_at)""",
                    (listing_id, str(item_info.item.itemId).split(ITEM_ID_PREFIX)[-1], int(item_info.price),
                     state, now, now, now if state == MY_STATE_SOLD else None))
            self._db.commit()

    def my_listing_ids(self) -> frozenset:
        """Listing ids of our listings still up for sale, from every My Listings page ever seen."""
        with self._lock:
            rows = self._db.execute("SELECT listing_id FROM my_listings WHERE state = ?", (MY_STATE_LISTING,))
            return frozenset(listing_id for (listing_id,) in rows.fetchall())

    def active_rows(self, item_id: str, max_age_s: float) -> list:
        """MarketRows for listings of `item_id` seen recently that have not vanished or expired."""
        now = self._clock()
        with self._lock:
            records = self._db.execute(
                """SELECT listing_id, item_id, price, base, rolls, item_count FROM listings
                   WHERE item_id = ? AND vanished_at IS NULL AND last_seen >= ? AND expires_at > ?""",
                (item_id, now - max_age_s, now)).fetchall()
        return [_to_market_row(r) for r in records]

    def vanished_rows(self, item_id: str) -> list:
        with self._lock:
            records = self._db.execute(
                "SELECT listing_id, item_id, price, base, rolls, item_count FROM listings "
                "WHERE item_id = ? AND vanished_at IS NOT NULL",
                (item_id,)).fetchall()
        return [_to_market_row(r) for r in records]

    def count_seen_before(self, listing_ids, before: float) -> int:
        """How many of these listings were already recorded before `before` (incremental crawls)."""
        ids = [str(i) for i in listing_ids]
        if not ids:
            return 0
        with self._lock:
            return self._db.execute(
                f"SELECT COUNT(*) FROM listings WHERE first_seen < ? AND listing_id IN ({','.join('?' * len(ids))})",
                (before, *ids)).fetchone()[0]

    def price_guide(self, item_ids) -> dict:
        """{item_id: {listings, min_unit, median_unit, max_unit}} over live (unexpired, not vanished) listings."""
        ids = list(dict.fromkeys(str(i) for i in item_ids))
        if not ids:
            return {}
        now = self._clock()
        with self._lock:
            records = self._db.execute(
                f"""SELECT item_id, price * 1.0 / item_count FROM listings
                    WHERE vanished_at IS NULL AND expires_at > ? AND item_id IN ({','.join('?' * len(ids))})""",
                (now, *ids)).fetchall()
        units = {}
        for item_id, unit in records:
            units.setdefault(item_id, []).append(unit)
        guide = {}
        for item_id, values in units.items():
            values.sort()
            guide[item_id] = {"listings": len(values), "min_unit": round(values[0], 1),
                              "median_unit": round(values[len(values) // 2], 1), "max_unit": round(values[-1], 1)}
        return guide

    def pattern_listings(self) -> list:
        """Every saved listing as market_patterns.Listing, for cross-item analysis."""
        from src.models.market_patterns import Listing
        with self._lock:
            records = self._db.execute(
                "SELECT item_id, rarity, price, item_count, base, rolls, seller FROM listings").fetchall()
        return [Listing(i, r, p, c, tuple(map(tuple, json.loads(b))), tuple(map(tuple, json.loads(ro))), s)
                for i, r, p, c, b, ro, s in records]

    def record_merchant_stock(self, message) -> int:
        """S2C_MERCHANT_STOCK_BUY_ITEM_LIST_RES: what a merchant sells and for how much (per unit).

        Several merchants can sell the same item; the cheapest offer is kept."""
        now = self._clock()
        offers = []
        for stock in message.stockList:
            item_id = str(stock.itemInfo.itemId).split(ITEM_ID_PREFIX)[-1]
            count = max(int(stock.itemInfo.itemCount or 1), 1)
            if item_id and stock.finalPrice > 0:
                offers.append((item_id, stock.finalPrice / count, now))
        with self._lock:
            self._db.executemany(
                """INSERT INTO merchant_prices (item_id, unit_price, seen_at) VALUES (?, ?, ?)
                   ON CONFLICT(item_id) DO UPDATE SET unit_price=MIN(unit_price, excluded.unit_price),
                       seen_at=excluded.seen_at""", offers)
            self._db.commit()
        return len(offers)

    def merchant_prices(self) -> dict:
        with self._lock:
            return dict(self._db.execute("SELECT item_id, unit_price FROM merchant_prices").fetchall())

    def merchant_price(self, item_id: str):
        """Cheapest per-unit price a merchant sells this item for, or None if no merchant was seen selling it."""
        with self._lock:
            row = self._db.execute("SELECT unit_price FROM merchant_prices WHERE item_id = ?", (str(item_id),)).fetchone()
        return row[0] if row else None

    def worth_listings(self) -> list:
        """Every saved listing with how long it had been up when first seen (Item Worth training)."""
        with self._lock:
            records = self._db.execute(
                "SELECT item_id, rarity, price, item_count, base, rolls, first_seen, expires_at FROM listings").fetchall()
        return [AgedListing(i, r, p, c, tuple(map(tuple, json.loads(b))), tuple(map(tuple, json.loads(ro))),
                            max(0.0, LISTING_DAYS - (expires - first) / DAY_S))
                for i, r, p, c, b, ro, first, expires in records]

    def known_item_ids(self) -> list:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT DISTINCT item_id FROM listings").fetchall()]

    def summary(self) -> dict:
        with self._lock:
            listings, items, vanished = self._db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT item_id), SUM(vanished_at IS NOT NULL) FROM listings").fetchone()
            sold = self._db.execute("SELECT COUNT(*) FROM my_listings WHERE state = ?", (MY_STATE_SOLD,)).fetchone()[0]
        return {"listings": listings, "items": items, "vanished": vanished or 0, "my_sold": sold}

    def connection(self):
        """Raw read access for offline analysis scripts."""
        return self._db
