# Auto Market Lister Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Market" page to DnDTools that builds a rules-based, DarkerDB-priced listing plan for stash/inventory items and lists them on the Dark and Darker Marketplace by driving the real mouse and keyboard, confirming each listing via captured packets.

**Architecture:** Pure, unit-tested core modules (rules/pricing, screen scaling + marketplace layout, packet state, plan builder, click runner) with all Windows input behind a small driver interface; a Flask blueprint + background job exposes it; a new page (template/js/css) plugs into DnDTools' existing router and sidebar "Market" slot.

**Tech Stack:** Python 3.11+ (dev venv on 3.13 OK), Flask 3, protobuf 6 (generated `networking/protos/*_pb2.py`), pytest, vanilla JS, ctypes `SendInput` via existing `src/models/macros.py`.

**Spec:** `docs/superpowers/specs/2026-09-27-market-lister-design.md`

## Global Constraints

- All paths below are relative to repo root `DnDTools-src/`; Python code lives under `UI/`, tests under `UI/tests/`, run from `UI/`: `python -m pytest tests -q`.
- `UI/tests/conftest.py` stubs `ctypes`, `win32*`, `pyautogui`, `pygetwindow`, `keyboard` and replaces `src.models.macros` with a fake. **New core modules must not import `ctypes`, `win32*`, or `src.models.macros` at module level.** Only `src/models/marketplace_input.py` (Task 6) touches `macros`.
- Base UI resolution is 1920×1080; scaling rules must match `macros._scaled_layout` exactly (≤16:9 independent axes; wider than 16:9 → scale by height + centred pillarbox; lengths scale by the Y factor, min 1.0).
- Stash ids are strings of the game `inventoryId`: `"2"` = inventory bag (10×5), stash tabs `"4"`,`"5"`–`"9"`,`"20"`,`"30"` (12×20).
- Spots per page: 10. Listing fee: `max(15, ceil(price * 0.05))`.
- Register success result code: `1` (assumed; verified in Task 9).
- Item-level register fail codes that skip-and-continue: `662` (non-trade looted), `666` (non-tradable). Every other fail code stops the run.
- Defaults: `undercut_pct=10`, `min_listings=3`, `max_items_per_run=20`, `min_net_ratio=0.5`, `min_rarity=4` (Rare), `min_price=100`, register timeout 5 s, listing-confirm timeout 3 s, stale data warning > 300 s.
- Settings keys: `marketListerRules`, `marketplaceCalibrationOverride` (`{"<w>x<h>": {"points": {key: [dx, dy]}, "lengths": {key: d}}}`).
- No packets are ever sent; no OCR. Commit after every task (conventional commits, no attribution trailers).
- Stackable items (`max_stack_size > 1`) are skipped in v1.
- The spec's "tradable only" rule is enforced by the game, not pre-filtered: `SItem.tradable` is omitted from captured JSON when 0, so it can't be trusted. Non-tradable items hit fail code 666/662 and are skipped item-by-item.
- The never-sell list (`exclude_item_ids`) is part of the rules model and API but has no editor in v1; users untick rows in the review table instead.
- Listing capacity assumed 4 pages × 10 spots (Legendary status, per screenshots); `MAX_PAGES = 4` in the runner.

## Review Focus

- Price data present but `lowest_ask`/`avg_price` missing or zero → item skipped with "no market data", never priced at 0 (Task 1 test `test_compute_price_ignores_zero_reference_prices`).
- Stash tab not present in the user's `stashTabMapping` (e.g. mapping has 0 for that box) → plan skips the item with "stash tab not mapped" instead of clicking the wrong icon (Task 2 test `test_tab_icon_index_unmapped_returns_none` + Task 4 test `test_build_plan_skips_unmapped_tabs`).
- My Listings packet never seen this session (user not on My Listings) → Start refused with a clear message, no clicks (Task 5 test `test_run_refuses_without_listings_snapshot`).
- DarkerDB key missing / rate limited mid-plan → plan build returns a clear error (missing key) or a partial plan with a warning (rate limited), never a crash (Task 4 tests `test_build_plan_missing_key_raises` / `test_build_plan_rate_limited_returns_partial`).
- User edits a price in the review table to junk (negative, text, > 1,000,000) → rejected by the API with 400 before any clicking (Task 7 test `test_start_rejects_invalid_price`).

---

## File Structure

| File | Responsibility |
|---|---|
| `UI/src/models/market_rules.py` (new) | `ListerRules`, candidate selection, price decision, fee. Pure. |
| `UI/src/models/screen_scaling.py` (new) | Shared 1920×1080 → resolution scaling. Pure. |
| `UI/src/models/marketplace_layout.py` (new) | Marketplace screen points, spot/page math, tab icon + item centre maths, calibration deltas. Pure. |
| `UI/src/models/marketplace_state.py` (new) | Thread-safe tracker for MY_ITEM_LIST_RES / ITEM_REGISTER_RES + waits. |
| `UI/src/market_lister.py` (new) | Plan builder (`build_plan`, `PlanEntry`, `Plan`, `PlanError`). |
| `UI/src/models/marketplace_runner.py` (new) | Click sequence runner (`MarketplaceRunner`, `RunReport`). Pure w.r.t. input (driver injected). |
| `UI/src/models/marketplace_input.py` (new) | Real `MacrosInputDriver` + `current_layout()` (only file importing `macros`). |
| `UI/src/market_lister_job.py` (new) | Background job: runs runner/hover test in a thread, status, cancel. |
| `UI/src/market_lister_api.py` (new) | Flask blueprint for `/api/market-lister/*`. |
| `UI/src/models/macros.py` (modify) | `_scaled_layout` delegates to `screen_scaling`. |
| `UI/src/models/stash_manager.py` (modify) | Public `get_enhanced_stashes()` + `get_character_data_age()`. |
| `UI/app.py` (modify) | Register capture handlers, blueprint, `/market` route, Ctrl+F12 cancel hook. |
| `UI/templates/market_lister.html`, `UI/static/js/market_lister.js`, `UI/static/css/market_lister.css` (new) | Page. |
| `UI/templates/base.html`, `UI/static/js/router.js` (modify) | Enable sidebar "Market" link; register page script. |
| `UI/tests/test_market_rules.py`, `test_marketplace_layout.py`, `test_marketplace_state.py`, `test_market_lister_plan.py`, `test_marketplace_runner.py`, `test_market_lister_api.py` (new) | Tests. |

---

### Task 1: Dev environment + rules and pricing

**Files:**
- Create: `UI/src/models/market_rules.py`
- Create: `UI/tests/test_market_rules.py`
- Create: `UI/requirements-dev.txt`

**Interfaces:**
- Produces:
  - `INVENTORY_STASH_ID: str = "2"`
  - `listing_fee(price: int) -> int`
  - `rarity_id(value: int | str | None) -> int`
  - `@dataclass(frozen=True) ListerRules(source_stash_ids: tuple[str, ...]=("2",), min_rarity: int=4, min_price: int=100, undercut_pct: float=10.0, min_listings: int=3, max_items_per_run: int=20, exclude_item_ids: frozenset[str]=frozenset(), min_net_ratio: float=0.5)` with `from_dict(data: dict) -> ListerRules` and `to_dict() -> dict`
  - `@dataclass(frozen=True) Skip(name: str, stash_id: str, slot_id: int, reason: str)`
  - `@dataclass(frozen=True) Candidate(stash_id: str, item: dict)`
  - `select_candidates(stashes: dict[str, list[dict]], rules: ListerRules) -> tuple[list[Candidate], list[Skip]]`
  - `@dataclass(frozen=True) PriceDecision(ok: bool, price: int | None, fee: int, reason: str)`
  - `compute_price(price_check: dict | None, vendor_price: int, rules: ListerRules) -> PriceDecision`

Items are DnDTools "enhanced" item dicts (from `StashManager._process_stash_items`): keys `name, itemId, itemUniqueId, slotId, itemCount, rarity, width, height, pp, sp, vendor_price, max_stack_size`. Price checks are `market_service.fetch_price_check` result dicts: keys `success, has_data, avg_price, lowest_ask, num_listings, error_code`.

- [ ] **Step 1: Create the dev venv**

```bash
cd UI
py -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest
```

Create `UI/requirements-dev.txt`:

```
-r requirements.txt
pytest
```

Add `UI/.venv/` to the repo `.gitignore` if not already ignored (`git check-ignore UI/.venv` prints nothing → append `UI/.venv/`).

Run: `.venv/Scripts/python -m pytest tests -q`
Expected: existing suite passes (note any pre-existing failures in the task report; do not fix them).

- [ ] **Step 2: Write the failing tests**

`UI/tests/test_market_rules.py`:

```python
from src.models.market_rules import (
    Candidate, ListerRules, compute_price, listing_fee, rarity_id, select_candidates,
)


def _item(**overrides):
    item = {
        "name": "Riveted Gloves", "itemId": "RivetedGloves_5001", "itemUniqueId": "111",
        "slotId": 3, "itemCount": 1, "rarity": 5, "width": 2, "height": 2,
        "pp": [], "sp": [], "vendor_price": 20, "max_stack_size": 1,
    }
    item.update(overrides)
    return item


def _check(**overrides):
    check = {"success": True, "has_data": True, "avg_price": 1000, "lowest_ask": 900, "num_listings": 12}
    check.update(overrides)
    return check


def test_listing_fee_has_15_gold_minimum():
    assert listing_fee(100) == 15
    assert listing_fee(500) == 25
    assert listing_fee(301) == 16  # ceil(15.05)


def test_rarity_id_accepts_names_and_ints():
    assert rarity_id(5) == 5
    assert rarity_id("Epic") == 5
    assert rarity_id("legend") == 6
    assert rarity_id(None) == 0
    assert rarity_id("nonsense") == 0


def test_rules_from_dict_clamps_and_defaults():
    rules = ListerRules.from_dict({"undercut_pct": 150, "max_items_per_run": 0, "min_rarity": "Rare",
                                   "source_stash_ids": [2, "4"], "exclude_item_ids": ["A"]})
    assert rules.undercut_pct == 90.0
    assert rules.max_items_per_run == 1
    assert rules.min_rarity == 4
    assert rules.source_stash_ids == ("2", "4")
    assert rules.exclude_item_ids == frozenset({"A"})
    assert ListerRules.from_dict(rules.to_dict()) == rules


def test_select_candidates_filters_with_reasons():
    stashes = {
        "2": [_item(), _item(itemUniqueId="222", rarity=2, slotId=5),
              _item(itemUniqueId="333", max_stack_size=5, slotId=7)],
        "4": [_item(itemUniqueId="444", itemId="Excluded_1", slotId=0)],
        "5": [_item(itemUniqueId="555")],
    }
    rules = ListerRules(source_stash_ids=("2", "4"), exclude_item_ids=frozenset({"Excluded_1"}))
    candidates, skipped = select_candidates(stashes, rules)
    assert candidates == [Candidate("2", stashes["2"][0])]
    reasons = {s.slot_id: s.reason for s in skipped}
    assert reasons == {5: "below minimum rarity", 7: "stackable items not supported yet", 0: "on your never-sell list"}


def test_select_candidates_orders_by_source_then_slot():
    stashes = {"4": [_item(itemUniqueId="b", slotId=9), _item(itemUniqueId="a", slotId=1)],
               "2": [_item(itemUniqueId="c", slotId=4)]}
    rules = ListerRules(source_stash_ids=("2", "4"))
    candidates, _ = select_candidates(stashes, rules)
    assert [c.item["itemUniqueId"] for c in candidates] == ["c", "a", "b"]


def test_compute_price_undercuts_lower_reference():
    decision = compute_price(_check(), vendor_price=20, rules=ListerRules())
    assert decision.ok and decision.price == 810 and decision.fee == 41  # 900 * 0.9


def test_compute_price_skips_without_data():
    assert compute_price(None, 0, ListerRules()).reason == "no market data"
    assert compute_price(_check(success=False), 0, ListerRules()).reason == "no market data"
    assert compute_price(_check(num_listings=1), 0, ListerRules()).reason == "not enough market data"


def test_compute_price_ignores_zero_reference_prices():
    assert compute_price(_check(lowest_ask=0, avg_price=None), 0, ListerRules()).reason == "no market data"
    assert compute_price(_check(lowest_ask=0), 0, ListerRules()).price == 900  # falls back to avg 1000


def test_compute_price_skip_reasons():
    rules = ListerRules(min_price=100)
    assert compute_price(_check(lowest_ask=90, avg_price=90), 0, rules).reason == "below min price"
    assert compute_price(_check(), vendor_price=900, rules=rules).reason == "vendor pays more"
    cheap = ListerRules(min_price=1, min_net_ratio=0.5)
    assert compute_price(_check(lowest_ask=30, avg_price=30), 0, cheap).reason == "fee too high"  # 27 - 15
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_market_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.models.market_rules'`

- [ ] **Step 4: Implement `UI/src/models/market_rules.py`**

```python
"""Pure rules and pricing for the auto market lister (no I/O)."""
import math
from dataclasses import dataclass, field

INVENTORY_STASH_ID = "2"
LISTING_FEE_RATE = 0.05
LISTING_FEE_MIN = 15
MAX_UNDERCUT_PCT = 90.0

_RARITY_IDS = {
    "poor": 1, "common": 2, "uncommon": 3, "rare": 4, "epic": 5,
    "legend": 6, "legendary": 6, "unique": 7, "artifact": 8,
}


def listing_fee(price: int) -> int:
    return max(LISTING_FEE_MIN, math.ceil(price * LISTING_FEE_RATE))


def rarity_id(value) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    return _RARITY_IDS.get(str(value).strip().lower(), 0)


def _clamp(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


@dataclass(frozen=True)
class ListerRules:
    source_stash_ids: tuple = (INVENTORY_STASH_ID,)
    min_rarity: int = 4
    min_price: int = 100
    undercut_pct: float = 10.0
    min_listings: int = 3
    max_items_per_run: int = 20
    exclude_item_ids: frozenset = field(default_factory=frozenset)
    min_net_ratio: float = 0.5

    @classmethod
    def from_dict(cls, data: dict) -> "ListerRules":
        data = data if isinstance(data, dict) else {}
        d = cls()
        sources = data.get("source_stash_ids") or d.source_stash_ids
        return cls(
            source_stash_ids=tuple(str(s) for s in sources),
            min_rarity=rarity_id(data.get("min_rarity", d.min_rarity)) or d.min_rarity,
            min_price=int(_clamp(data.get("min_price"), 0, 10_000_000, d.min_price)),
            undercut_pct=_clamp(data.get("undercut_pct"), 0, MAX_UNDERCUT_PCT, d.undercut_pct),
            min_listings=int(_clamp(data.get("min_listings"), 0, 1000, d.min_listings)),
            max_items_per_run=int(_clamp(data.get("max_items_per_run"), 1, 40, d.max_items_per_run)),
            exclude_item_ids=frozenset(str(i) for i in data.get("exclude_item_ids") or ()),
            min_net_ratio=_clamp(data.get("min_net_ratio"), 0, 1, d.min_net_ratio),
        )

    def to_dict(self) -> dict:
        return {
            "source_stash_ids": list(self.source_stash_ids),
            "min_rarity": self.min_rarity,
            "min_price": self.min_price,
            "undercut_pct": self.undercut_pct,
            "min_listings": self.min_listings,
            "max_items_per_run": self.max_items_per_run,
            "exclude_item_ids": sorted(self.exclude_item_ids),
            "min_net_ratio": self.min_net_ratio,
        }


@dataclass(frozen=True)
class Skip:
    name: str
    stash_id: str
    slot_id: int
    reason: str


@dataclass(frozen=True)
class Candidate:
    stash_id: str
    item: dict


def _skip_reason(item: dict, rules: ListerRules):
    if int(item.get("max_stack_size") or 1) > 1:
        return "stackable items not supported yet"
    if str(item.get("itemId", "")) in rules.exclude_item_ids:
        return "on your never-sell list"
    if rarity_id(item.get("rarity")) < rules.min_rarity:
        return "below minimum rarity"
    return None


def select_candidates(stashes: dict, rules: ListerRules):
    candidates, skipped = [], []
    for stash_id in rules.source_stash_ids:
        items = sorted(stashes.get(stash_id) or [], key=lambda i: int(i.get("slotId", 0)))
        for item in items:
            reason = _skip_reason(item, rules)
            if reason:
                skipped.append(Skip(item.get("name", "?"), stash_id, int(item.get("slotId", 0)), reason))
            else:
                candidates.append(Candidate(stash_id, item))
    return candidates, skipped


@dataclass(frozen=True)
class PriceDecision:
    ok: bool
    price: int | None
    fee: int
    reason: str


def _no(reason: str) -> PriceDecision:
    return PriceDecision(False, None, 0, reason)


def _reference_price(price_check: dict):
    refs = [p for p in (price_check.get("lowest_ask"), price_check.get("avg_price"))
            if isinstance(p, (int, float)) and not isinstance(p, bool) and p > 0]
    return min(refs) if refs else None


def compute_price(price_check, vendor_price: int, rules: ListerRules) -> PriceDecision:
    if not price_check or not price_check.get("success") or not price_check.get("has_data"):
        return _no("no market data")
    if int(price_check.get("num_listings") or 0) < rules.min_listings:
        return _no("not enough market data")
    reference = _reference_price(price_check)
    if reference is None:
        return _no("no market data")
    price = math.floor(reference * (1 - rules.undercut_pct / 100))
    if price < max(rules.min_price, 1):
        return _no("below min price")
    fee = listing_fee(price)
    net = price - fee
    if net <= int(vendor_price or 0):
        return _no("vendor pays more")
    if net / price < rules.min_net_ratio:
        return _no("fee too high")
    return PriceDecision(True, price, fee, "ok")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_market_rules.py -q`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add UI/src/models/market_rules.py UI/tests/test_market_rules.py UI/requirements-dev.txt .gitignore
git commit -m "feat: add market lister rules and pricing"
```

---

### Task 2: Screen scaling + marketplace layout

**Files:**
- Create: `UI/src/models/screen_scaling.py`
- Create: `UI/src/models/marketplace_layout.py`
- Modify: `UI/src/models/macros.py` (`_scaled_layout`, ~lines 162-200)
- Create: `UI/tests/test_marketplace_layout.py`

**Interfaces:**
- Consumes: `INVENTORY_STASH_ID` from Task 1.
- Produces:
  - `screen_scaling.Scale(sx: float, sy: float, offset_x: float)`; `scale_for(resolution: tuple[int,int]) -> Scale`; `scale_point(x: float, y: float, scale: Scale) -> tuple[int,int]`; `scale_length(value: float, scale: Scale) -> float`
  - `marketplace_layout.SPOTS_PER_PAGE = 10`
  - `spot_location(order_index: int) -> tuple[int, int]` (page, row)
  - `tab_icon_index(stash_id: str, tab_mapping: list[int]) -> int | None` (0 = inventory icon)
  - `@dataclass(frozen=True) MarketplaceLayout(points: dict[str, tuple[int,int]], lengths: dict[str, float])` with methods `spot_row(row: int)`, `tab_icon(icon_index: int)`, `item_centre(stash_id: str, slot_id: int, width: int, height: int)`, `point(key: str)` — all return `tuple[int,int]`; `hover_targets() -> list[tuple[str, tuple[int,int]]]`
  - `build_layout(resolution: tuple[int,int], window_origin: tuple[int,int]=(0,0), calibration: dict | None=None) -> MarketplaceLayout`
  - Point keys: `spot_row_origin, next_page_arrow, tab_icon_origin, inv_grid_origin, stash_grid_origin, price_field, create_listing_button`; length keys: `spot_row_spacing, tab_icon_spacing, cell`.

- [ ] **Step 1: Write the failing tests**

`UI/tests/test_marketplace_layout.py`:

```python
import pytest

from src.models.marketplace_layout import (
    SPOTS_PER_PAGE, build_layout, spot_location, tab_icon_index,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_marketplace_layout.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.models.marketplace_layout'`

- [ ] **Step 3: Implement `UI/src/models/screen_scaling.py`**

```python
"""Shared 1920x1080-base scaling used by the sorter and the market lister."""
from dataclasses import dataclass

BASE_RESOLUTION = (1920, 1080)
STANDARD_ASPECT = 16.0 / 9.0


@dataclass(frozen=True)
class Scale:
    sx: float
    sy: float
    offset_x: float


def scale_for(resolution) -> Scale:
    w, h = resolution
    if (w / max(1, h)) > (STANDARD_ASPECT + 0.01):
        s = h / BASE_RESOLUTION[1]
        return Scale(s, s, (w - h * STANDARD_ASPECT) / 2.0)
    return Scale(w / BASE_RESOLUTION[0], h / BASE_RESOLUTION[1], 0.0)


def scale_point(x, y, scale: Scale):
    return int(round(x * scale.sx + scale.offset_x)), int(round(y * scale.sy))


def scale_length(value, scale: Scale) -> float:
    return max(value * scale.sy, 1.0)
```

- [ ] **Step 4: Implement `UI/src/models/marketplace_layout.py`**

```python
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
}
BASE_LENGTHS = {"spot_row_spacing": 50.0, "tab_icon_spacing": 46.5, "cell": 41.3}


def spot_location(order_index: int):
    return divmod(int(order_index), SPOTS_PER_PAGE)


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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_marketplace_layout.py -q`
Expected: 11 passed

- [ ] **Step 6: Make `macros._scaled_layout` use the shared helper**

In `UI/src/models/macros.py` add near the other imports:

```python
from src.models.screen_scaling import scale_for, scale_length, scale_point
```

Replace the whole body of `_scaled_layout(resolution)` with:

```python
def _scaled_layout(resolution):
    scale = scale_for(resolution)

    def point(key):
        base = BASE_LAYOUT[key]
        return Point(*scale_point(base.x, base.y, scale))

    return {
        'stash': point('stash'),
        'inv': point('inv'),
        'jump': scale_length(BASE_LAYOUT['jump'], scale),
        'stash_tab_origin': point('stash_tab_origin'),
        'stash_tab_spacing': scale_length(BASE_LAYOUT['stash_tab_spacing'], scale),
    }
```

`macros` is stubbed under pytest, so verify equivalence by hand with the real module in a throwaway Python session (Windows, venv):

```bash
cd UI
.venv/Scripts/python -c "from src.models import macros as m; print({r: m._scaled_layout(r) for r in [(1920,1080),(2560,1440),(3840,2160),(3440,1440),(1366,768)]})"
```

Expected: 4K `stash` = `(2756, 398)`, `jump` = `81.0`; 1440p `jump` = `54.0`; 3440×1440 `stash.x` = `round(1378*1.3333 + 440)` = `2277`. Compare with `git stash`-ed original output if in doubt; values must be identical.

- [ ] **Step 7: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest tests -q`
Expected: all pass

```bash
git add UI/src/models/screen_scaling.py UI/src/models/marketplace_layout.py UI/src/models/macros.py UI/tests/test_marketplace_layout.py
git commit -m "feat: add marketplace layout with shared screen scaling"
```

---

### Task 3: Marketplace packet state

**Files:**
- Create: `UI/src/models/marketplace_state.py`
- Create: `UI/tests/test_marketplace_state.py`
- Modify: `UI/app.py` (`_init_capture_controller` `capture_info` dict, ~line 674; module-level instance near other singletons)

**Interfaces:**
- Produces:
  - `REGISTER_SUCCESS = 1`; `ITEM_LEVEL_FAIL_CODES = frozenset({662, 666})`; `FAIL_CODE_MESSAGES: dict[int, str]`; `describe_fail_code(code: int) -> str`
  - `@dataclass(frozen=True) ListingsSnapshot(received_at: float, used: int, available: tuple[int, ...])`
  - `@dataclass(frozen=True) RegisterOutcome(status: str, fail_code: int | None = None)` — status `"ok" | "failed" | "timeout"`
  - `class MarketplaceState(clock=time.monotonic)` with `handle_my_item_list(message)`, `handle_register_res(message)`, `snapshot() -> ListingsSnapshot | None`, `begin_register() -> None`, `wait_for_register(timeout: float) -> RegisterOutcome`, `wait_for_listing(unique_id: str, since: float, timeout: float) -> bool`, `now() -> float`
  - `app.py` module global `marketplace_state = MarketplaceState()`

- [ ] **Step 1: Write the failing tests**

`UI/tests/test_marketplace_state.py`:

```python
import threading

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


def test_my_item_list_records_used_and_available():
    clock = FakeClock()
    state = MarketplaceState(clock=clock)
    state.handle_my_item_list(_my_list(2, available=[2, 3, 4]))
    snap = state.snapshot()
    assert (snap.used, snap.available, snap.received_at) == (2, (2, 3, 4), 100.0)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_marketplace_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.models.marketplace_state'`

- [ ] **Step 3: Implement `UI/src/models/marketplace_state.py`**

```python
"""Tracks Marketplace packets so the lister can confirm each listing."""
import threading
import time
from dataclasses import dataclass

REGISTER_SUCCESS = 1
ITEM_LEVEL_FAIL_CODES = frozenset({662, 666})
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
    used: int
    available: tuple


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

    def now(self) -> float:
        return self._clock()

    def handle_my_item_list(self, message) -> None:
        received = self._clock()
        with self._cond:
            self._snapshot = ListingsSnapshot(
                received_at=received,
                used=int(message.totalItemCount),
                available=tuple(int(i) for i in message.availableOrderIndexes),
            )
            for info in message.myItemInfos:
                self._listed_at[str(info.itemInfo.item.itemUniqueId)] = received
            self._cond.notify_all()

    def handle_register_res(self, message) -> None:
        with self._cond:
            self._register_result = int(message.result)
            self._cond.notify_all()

    def snapshot(self):
        with self._cond:
            return self._snapshot

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_marketplace_state.py -q`
Expected: 7 passed

- [ ] **Step 5: Register the handlers in `UI/app.py`**

At **top level** (column 0, not inside the `if _is_child_process: … else:` block around lines 210-320), immediately before the first route decorator (`@server.route('/api/download_update')`, ~line 2721), add:

```python
# ── Market lister ──
from src.models.marketplace_state import MarketplaceState

marketplace_state = MarketplaceState()
```

In `_init_capture_controller`, add two entries to the `capture_info` dict after the quest handlers:

```python
            # Market lister confirmation handlers
            _PacketCommand_pb2.PacketCommand.S2C_MARKETPLACE_MY_ITEM_LIST_RES: marketplace_state.handle_my_item_list,
            _PacketCommand_pb2.PacketCommand.S2C_MARKETPLACE_ITEM_REGISTER_RES: marketplace_state.handle_register_res,
```

Run: `.venv/Scripts/python -c "import ast,sys; ast.parse(open('app.py',encoding='utf-8').read())"`
Expected: no output (syntax OK). Then `.venv/Scripts/python -m pytest tests -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add UI/src/models/marketplace_state.py UI/tests/test_marketplace_state.py UI/app.py
git commit -m "feat: track marketplace listing packets"
```

---

### Task 4: Plan builder + stash access

**Files:**
- Create: `UI/src/market_lister.py`
- Create: `UI/tests/test_market_lister_plan.py`
- Modify: `UI/src/models/stash_manager.py` (add two public methods after `get_character_stashes`, ~line 466)

**Interfaces:**
- Consumes: Task 1 (`ListerRules`, `select_candidates`, `compute_price`, `Skip`), Task 2 (`tab_icon_index`).
- Produces:
  - `MAX_LISTING_PRICE = 1_000_000`; `STALE_DATA_SECONDS = 300`
  - `class PlanError(Exception)` with `.code: str`
  - `@dataclass(frozen=True) PlanEntry(unique_id: str, name: str, rarity: int, stash_id: str, slot_id: int, width: int, height: int, price: int, fee: int, vendor_price: int)` with `to_dict() -> dict` and `from_dict(data: dict) -> PlanEntry` (raises `ValueError` on invalid input)
  - `@dataclass(frozen=True) Plan(entries: tuple[PlanEntry, ...], skipped: tuple[Skip, ...], warnings: tuple[str, ...])` with `to_dict() -> dict`
  - `build_plan(stashes: dict, rules: ListerRules, price_lookup: Callable[[dict], dict], *, tab_mapping: list[int], free_spots: int | None, data_age_s: float | None, pause: Callable[[], None] = lambda: None) -> Plan`
  - `StashManager.get_enhanced_stashes(character_id: str, stash_ids: Iterable[str]) -> dict[str, list[dict]]`
  - `StashManager.get_character_data_age(character_id: str) -> float | None`

- [ ] **Step 1: Write the failing tests**

`UI/tests/test_market_lister_plan.py`:

```python
import pytest

from src.market_lister import MAX_LISTING_PRICE, PlanEntry, PlanError, build_plan
from src.models.market_rules import ListerRules

MAPPING = [4, 20, 5, 6, 7, 8, 9, 30]


def _item(uid, slot, **kw):
    item = {"name": f"Item {uid}", "itemId": f"Id_{uid}", "itemUniqueId": uid, "slotId": slot,
            "itemCount": 1, "rarity": 5, "width": 1, "height": 1, "pp": [], "sp": [],
            "vendor_price": 10, "max_stack_size": 1}
    item.update(kw)
    return item


def _ok(price=1000):
    return {"success": True, "has_data": True, "avg_price": price, "lowest_ask": price, "num_listings": 10}


def _plan(stashes, lookup, rules=None, **overrides):
    kwargs = {"tab_mapping": MAPPING, "free_spots": 38, "data_age_s": 10.0, **overrides}
    return build_plan(stashes, rules or ListerRules(source_stash_ids=("2", "4")), lookup, **kwargs)


def test_build_plan_prices_and_orders_entries():
    stashes = {"2": [_item("a", 3)], "4": [_item("b", 0, width=2, height=3)]}
    plan = _plan(stashes, lambda item: _ok())
    assert [e.unique_id for e in plan.entries] == ["a", "b"]
    b = plan.entries[1]
    assert (b.stash_id, b.slot_id, b.width, b.height, b.price, b.fee) == ("4", 0, 2, 3, 900, 45)
    assert plan.warnings == ()


def test_build_plan_records_price_skips():
    stashes = {"2": [_item("a", 0), _item("b", 1)]}
    plan = _plan(stashes, lambda item: _ok() if item["itemUniqueId"] == "a" else {"success": True, "has_data": False})
    assert [e.unique_id for e in plan.entries] == ["a"]
    assert [(s.name, s.reason) for s in plan.skipped] == [("Item b", "no market data")]


def test_build_plan_caps_to_free_spots_and_max_items():
    stashes = {"2": [_item(str(i), i) for i in range(6)]}
    plan = _plan(stashes, lambda item: _ok(), free_spots=2)
    assert len(plan.entries) == 2
    assert any("free listing spots" in w for w in plan.warnings)
    rules = ListerRules(source_stash_ids=("2",), max_items_per_run=3)
    assert len(_plan(stashes, lambda item: _ok(), rules=rules, free_spots=None).entries) == 3


def test_build_plan_skips_unmapped_tabs():
    stashes = {"9": [_item("x", 0)]}
    rules = ListerRules(source_stash_ids=("9",))
    plan = _plan(stashes, lambda item: _ok(), rules=rules, tab_mapping=[4, 20, 5, 6, 7, 8, 0, 30])
    assert plan.entries == ()
    assert plan.skipped[0].reason == "stash tab not mapped in DnDTools settings"


def test_build_plan_warns_on_stale_data():
    plan = _plan({"2": []}, lambda item: _ok(), data_age_s=900.0)
    assert any("minutes old" in w for w in plan.warnings)


def test_build_plan_missing_key_raises():
    with pytest.raises(PlanError) as exc:
        _plan({"2": [_item("a", 0)]}, lambda item: {"success": False, "error_code": "missing_api_key"})
    assert exc.value.code == "missing_api_key"


def test_build_plan_rate_limited_returns_partial():
    calls = []

    def lookup(item):
        calls.append(item["itemUniqueId"])
        return _ok() if len(calls) == 1 else {"success": False, "error_code": "rate_limited"}

    plan = _plan({"2": [_item("a", 0), _item("b", 1), _item("c", 2)]}, lookup)
    assert [e.unique_id for e in plan.entries] == ["a"]
    assert calls == ["a", "b"]
    assert any("rate limit" in w.lower() for w in plan.warnings)


def test_plan_entry_round_trip_and_validation():
    entry = PlanEntry("a", "Item", 5, "2", 3, 1, 1, 900, 45, 10)
    assert PlanEntry.from_dict(entry.to_dict()) == entry
    for bad in (0, -5, "abc", MAX_LISTING_PRICE + 1, None, 12.5):
        with pytest.raises(ValueError):
            PlanEntry.from_dict({**entry.to_dict(), "price": bad})
    with pytest.raises(ValueError):
        PlanEntry.from_dict({**entry.to_dict(), "slot_id": -1})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_market_lister_plan.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.market_lister'`

- [ ] **Step 3: Implement `UI/src/market_lister.py`**

```python
"""Builds the auto market lister's reviewable listing plan."""
from dataclasses import asdict, dataclass

from src.models.market_rules import Skip, compute_price, listing_fee, rarity_id, select_candidates
from src.models.marketplace_layout import tab_icon_index

MAX_LISTING_PRICE = 1_000_000
STALE_DATA_SECONDS = 300


class PlanError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _positive_int(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is out of range")
    return value


@dataclass(frozen=True)
class PlanEntry:
    unique_id: str
    name: str
    rarity: int
    stash_id: str
    slot_id: int
    width: int
    height: int
    price: int
    fee: int
    vendor_price: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PlanEntry":
        if not isinstance(data, dict) or not str(data.get("unique_id") or "").strip():
            raise ValueError("entry is missing unique_id")
        price = _positive_int(data.get("price"), "price", 1, MAX_LISTING_PRICE)
        return cls(
            unique_id=str(data["unique_id"]),
            name=str(data.get("name") or "?")[:128],
            rarity=_positive_int(data.get("rarity", 0), "rarity", 0, 8),
            stash_id=str(data.get("stash_id") or ""),
            slot_id=_positive_int(data.get("slot_id"), "slot_id", 0, 239),
            width=_positive_int(data.get("width", 1), "width", 1, 4),
            height=_positive_int(data.get("height", 1), "height", 1, 4),
            price=price,
            fee=listing_fee(price),
            vendor_price=_positive_int(data.get("vendor_price", 0), "vendor_price", 0),
        )


@dataclass(frozen=True)
class Plan:
    entries: tuple
    skipped: tuple
    warnings: tuple

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "skipped": [asdict(s) for s in self.skipped],
            "warnings": list(self.warnings),
        }


def _entry(candidate, decision) -> PlanEntry:
    item = candidate.item
    return PlanEntry(
        unique_id=str(item.get("itemUniqueId")), name=item.get("name", "?"),
        rarity=rarity_id(item.get("rarity")), stash_id=candidate.stash_id,
        slot_id=int(item.get("slotId", 0)), width=int(item.get("width") or 1),
        height=int(item.get("height") or 1), price=decision.price, fee=decision.fee,
        vendor_price=int(item.get("vendor_price") or 0),
    )


def _skip(candidate, reason) -> Skip:
    return Skip(candidate.item.get("name", "?"), candidate.stash_id, int(candidate.item.get("slotId", 0)), reason)


def _limit(rules, free_spots):
    return rules.max_items_per_run if free_spots is None else min(rules.max_items_per_run, max(free_spots, 0))


def build_plan(stashes, rules, price_lookup, *, tab_mapping, free_spots, data_age_s, pause=lambda: None) -> Plan:
    candidates, skipped = select_candidates(stashes, rules)
    warnings, entries = [], []
    if data_age_s is not None and data_age_s > STALE_DATA_SECONDS:
        warnings.append(f"Stash data is {int(data_age_s // 60)} minutes old — reopen your character to refresh.")
    limit = _limit(rules, free_spots)
    for candidate in candidates:
        if len(entries) >= limit:
            break
        if tab_icon_index(candidate.stash_id, tab_mapping) is None:
            skipped.append(_skip(candidate, "stash tab not mapped in DnDTools settings"))
            continue
        check = price_lookup(candidate.item)
        pause()
        error = (check or {}).get("error_code")
        if error == "missing_api_key":
            raise PlanError(error, "Add your DarkerDB API key (DARKERDB_API_KEY) to price items.")
        if error == "rate_limited":
            warnings.append("DarkerDB rate limit hit — plan is partial. Try again in a minute.")
            break
        decision = compute_price(check, int(candidate.item.get("vendor_price") or 0), rules)
        if decision.ok:
            entries.append(_entry(candidate, decision))
        else:
            skipped.append(_skip(candidate, decision.reason))
    if free_spots is not None and len(entries) >= free_spots and len(candidates) > len(entries):
        warnings.append(f"Only {free_spots} free listing spots — some items were left out.")
    return Plan(tuple(entries), tuple(skipped), tuple(warnings))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_market_lister_plan.py -q`
Expected: 8 passed

- [ ] **Step 5: Add stash accessors to `UI/src/models/stash_manager.py`**

Insert after `get_character_stashes` (keep `os`/`time` imports — both already imported at the top; add `import time` / `import os` if missing):

```python
    def get_enhanced_stashes(self, character_id: str, stash_ids) -> Dict[str, List[Dict]]:
        """Return enhanced item dicts for the requested stash ids (market lister)."""
        raw = self.get_character_stashes(character_id)
        result = {}
        for stash_id in stash_ids:
            items = raw.get(str(stash_id)) or []
            _, enhanced = self._process_stash_items(str(stash_id), items)
            result[str(stash_id)] = enhanced
        return result

    def get_character_data_age(self, character_id: str) -> Optional[float]:
        """Seconds since this character's capture file was last written."""
        self._ensure_loaded()
        with self._cache_lock:
            char = self.characters_cache.get(str(character_id))
        path = (char or {}).get('file_path')
        if not path or not os.path.exists(path):
            return None
        return max(0.0, time.time() - os.path.getmtime(path))
```

Run: `.venv/Scripts/python -m pytest tests -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add UI/src/market_lister.py UI/tests/test_market_lister_plan.py UI/src/models/stash_manager.py
git commit -m "feat: build market lister plans from stash and DarkerDB prices"
```

---

### Task 5: Click runner

**Files:**
- Create: `UI/src/models/marketplace_runner.py`
- Create: `UI/tests/test_marketplace_runner.py`

**Interfaces:**
- Consumes: Task 2 (`MarketplaceLayout`, `spot_location`, `tab_icon_index`), Task 3 (`MarketplaceState`, `ITEM_LEVEL_FAIL_CODES`, `describe_fail_code`), Task 4 (`PlanEntry`).
- Produces:
  - `class InputDriver(Protocol)`: `click(x: int, y: int) -> None`, `move_to(x: int, y: int) -> None`, `clear_and_type(text: str) -> None`
  - `class Safety(Protocol)`: `checkpoint() -> bool`, `snapshot_position() -> None`, `reason` (str | None)
  - `@dataclass(frozen=True) ItemResult(unique_id: str, name: str, status: str, message: str = "")` — status `"listed" | "dry_run" | "failed"`
  - `@dataclass(frozen=True) RunReport(results: tuple[ItemResult, ...], stopped_reason: str | None)` with `to_dict()`
  - `class MarketplaceRunner(driver, layout, state, *, tab_mapping, is_cancelled, pause, safety=None, register_timeout=5.0, confirm_timeout=3.0)` with `run(entries: list[PlanEntry], dry_run: bool = False, on_progress: Callable[[ItemResult], None] | None = None) -> RunReport`

- [ ] **Step 1: Write the failing tests**

`UI/tests/test_marketplace_runner.py`:

```python
from networking.protos import MarketPlace_pb2

from src.market_lister import PlanEntry
from src.models.marketplace_layout import build_layout
from src.models.marketplace_runner import MarketplaceRunner
from src.models.marketplace_state import MarketplaceState, RegisterOutcome

MAPPING = [4, 20, 5, 6, 7, 8, 9, 30]
LAYOUT = build_layout((1920, 1080))


class FakeDriver:
    def __init__(self):
        self.actions = []
        self.on_create = None

    def click(self, x, y):
        self.actions.append(("click", (x, y)))
        if (x, y) == LAYOUT.point("create_listing_button") and self.on_create:
            self.on_create()

    def move_to(self, x, y):
        self.actions.append(("move", (x, y)))

    def clear_and_type(self, text):
        self.actions.append(("type", text))


class ScriptedState(MarketplaceState):
    """Answers register / listing waits from a script instead of packets."""

    def __init__(self, used=2, outcomes=(), confirm=True):
        super().__init__()
        msg = MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(totalItemCount=used)
        self.handle_my_item_list(msg)
        self.outcomes = list(outcomes)
        self.confirm = confirm

    def wait_for_register(self, timeout):
        return self.outcomes.pop(0) if self.outcomes else RegisterOutcome("ok")

    def wait_for_listing(self, unique_id, since, timeout):
        return self.confirm


def _entry(uid, stash="2", slot=0, price=900):
    return PlanEntry(uid, f"Item {uid}", 5, stash, slot, 1, 1, price, 45, 10)


def _runner(driver, state, cancelled=lambda: False):
    return MarketplaceRunner(driver, LAYOUT, state, tab_mapping=MAPPING, is_cancelled=cancelled, pause=lambda: None)


def test_run_clicks_full_sequence_for_one_item():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=2)).run([_entry("a", stash="4", slot=13)])
    assert report.stopped_reason is None
    assert [r.status for r in report.results] == ["listed"]
    assert driver.actions == [
        ("click", LAYOUT.spot_row(2)),
        ("click", LAYOUT.tab_icon(1)),
        ("click", LAYOUT.item_centre("4", 13, 1, 1)),
        ("click", LAYOUT.point("price_field")),
        ("type", "900"),
        ("click", LAYOUT.point("create_listing_button")),
    ]


def test_run_turns_pages_when_spots_full():
    driver = FakeDriver()
    _runner(driver, ScriptedState(used=19)).run([_entry("a"), _entry("b", slot=1)])
    clicks = [a[1] for a in driver.actions if a[0] == "click"]
    arrow = LAYOUT.point("next_page_arrow")
    assert clicks[0] == arrow and clicks[1] == LAYOUT.spot_row(9)   # index 19 → page 1, row 9
    assert clicks.count(arrow) == 2                                  # index 20 → page 2
    assert clicks[clicks.index(arrow, 1) + 1] == LAYOUT.spot_row(0)


def test_dry_run_never_clicks_create_listing():
    driver = FakeDriver()
    report = _runner(driver, ScriptedState(used=0)).run([_entry("a"), _entry("b", slot=1)], dry_run=True)
    assert [r.status for r in report.results] == ["dry_run", "dry_run"]
    assert ("click", LAYOUT.point("create_listing_button")) not in driver.actions
    assert ("click", LAYOUT.spot_row(1)) in driver.actions  # second item uses next spot


def test_run_refuses_without_listings_snapshot():
    driver = FakeDriver()
    report = _runner(driver, MarketplaceState()).run([_entry("a")])
    assert report.results == ()
    assert "My Listings" in report.stopped_reason
    assert driver.actions == []


def test_run_stops_on_timeout_and_general_failure():
    for outcome, text in ((RegisterOutcome("timeout"), "not confirmed"), (RegisterOutcome("failed", 657), "gold")):
        report = _runner(FakeDriver(), ScriptedState(outcomes=[outcome])).run([_entry("a"), _entry("b", slot=1)])
        assert len(report.results) == 1 and report.results[0].status == "failed"
        assert text in report.stopped_reason.lower()


def test_run_continues_after_item_level_failure():
    state = ScriptedState(outcomes=[RegisterOutcome("failed", 666), RegisterOutcome("ok")])
    report = _runner(FakeDriver(), state).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["failed", "listed"]
    assert report.stopped_reason is None


def test_run_stops_when_listing_not_confirmed():
    report = _runner(FakeDriver(), ScriptedState(confirm=False)).run([_entry("a"), _entry("b", slot=1)])
    assert len(report.results) == 1
    assert "couldn't confirm" in report.stopped_reason


def test_run_stops_when_cancelled():
    flag = {"cancel": False}
    driver = FakeDriver()
    driver.on_create = lambda: flag.update(cancel=True)
    report = _runner(driver, ScriptedState(), cancelled=lambda: flag["cancel"]).run([_entry("a"), _entry("b", slot=1)])
    assert [r.status for r in report.results] == ["listed"]
    assert report.stopped_reason == "Cancelled"


def test_run_stops_when_no_free_spots():
    report = _runner(FakeDriver(), ScriptedState(used=40)).run([_entry("a")])
    assert "No free listing spots" in report.stopped_reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_marketplace_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.models.marketplace_runner'`

- [ ] **Step 3: Implement `UI/src/models/marketplace_runner.py`**

```python
"""Drives the Marketplace 'List an Item' flow one plan entry at a time."""
from dataclasses import asdict, dataclass
from typing import Protocol

from src.models.marketplace_layout import spot_location, tab_icon_index
from src.models.marketplace_state import ITEM_LEVEL_FAIL_CODES, describe_fail_code

MAX_PAGES = 4


class InputDriver(Protocol):
    def click(self, x: int, y: int) -> None: ...
    def move_to(self, x: int, y: int) -> None: ...
    def clear_and_type(self, text: str) -> None: ...


class _NullSafety:
    reason = None

    def checkpoint(self) -> bool:
        return True

    def snapshot_position(self) -> None:
        return None


@dataclass(frozen=True)
class ItemResult:
    unique_id: str
    name: str
    status: str
    message: str = ""


@dataclass(frozen=True)
class RunReport:
    results: tuple
    stopped_reason: str | None

    def to_dict(self) -> dict:
        return {"results": [asdict(r) for r in self.results], "stopped_reason": self.stopped_reason}


class _Stop(Exception):
    pass


class MarketplaceRunner:
    def __init__(self, driver, layout, state, *, tab_mapping, is_cancelled, pause,
                 safety=None, register_timeout=5.0, confirm_timeout=3.0):
        self._driver = driver
        self._layout = layout
        self._state = state
        self._tab_mapping = list(tab_mapping)
        self._is_cancelled = is_cancelled
        self._pause = pause
        self._safety = safety or _NullSafety()
        self._register_timeout = register_timeout
        self._confirm_timeout = confirm_timeout
        self._page = 0

    def run(self, entries, dry_run=False, on_progress=None) -> RunReport:
        snapshot = self._state.snapshot()
        if snapshot is None:
            return RunReport((), "Open Trade → Marketplace → My Listings in the game first.")
        results, used, self._page = [], snapshot.used, 0
        try:
            for entry in entries:
                result = self._list_one(entry, used, dry_run)
                results.append(result)
                if on_progress:
                    on_progress(result)
                if result.status in ("listed", "dry_run"):
                    used += 1
        except _Stop as stop:
            return RunReport(tuple(results + getattr(stop, "results", [])), str(stop))
        return RunReport(tuple(results), None)

    def _check(self):
        if self._is_cancelled():
            reason = self._safety.reason
            raise _Stop(f"Stopped for safety: {reason}" if reason else "Cancelled")

    def _click(self, point):
        self._check()
        self._driver.click(*point)
        self._pause()

    def _go_to_spot(self, index):
        page, row = spot_location(index)
        if page >= MAX_PAGES:
            raise _Stop("No free listing spots left.")
        while self._page < page:
            self._click(self._layout.point("next_page_arrow"))
            self._page += 1
        self._click(self._layout.spot_row(row))

    def _fill_form(self, entry):
        icon = tab_icon_index(entry.stash_id, self._tab_mapping)
        if icon is None:
            raise _Stop(f"Stash tab for {entry.name} is not mapped in DnDTools settings.")
        self._click(self._layout.tab_icon(icon))
        self._click(self._layout.item_centre(entry.stash_id, entry.slot_id, entry.width, entry.height))
        self._click(self._layout.point("price_field"))
        self._check()
        self._driver.clear_and_type(str(entry.price))
        self._pause()

    def _stop_with(self, result, message):
        stop = _Stop(message)
        stop.results = [result]
        return stop

    def _submit(self, entry):
        self._state.begin_register()
        since = self._state.now()
        self._check()
        self._driver.click(*self._layout.point("create_listing_button"))
        outcome = self._state.wait_for_register(self._register_timeout)
        if outcome.status == "timeout":
            fail = ItemResult(entry.unique_id, entry.name, "failed", "no response")
            raise self._stop_with(fail, "Listing not confirmed by the game — check the Marketplace.")
        if outcome.status == "failed":
            message = describe_fail_code(outcome.fail_code)
            fail = ItemResult(entry.unique_id, entry.name, "failed", message)
            if outcome.fail_code in ITEM_LEVEL_FAIL_CODES:
                return fail
            raise self._stop_with(fail, message)
        if not self._state.wait_for_listing(entry.unique_id, since, self._confirm_timeout):
            fail = ItemResult(entry.unique_id, entry.name, "failed", "not seen in My Listings")
            raise self._stop_with(
                fail, f"Listed something but couldn't confirm it was {entry.name} — check My Listings and recalibrate.")
        self._pause()
        return ItemResult(entry.unique_id, entry.name, "listed", f"{entry.price}g")

    def _list_one(self, entry, used, dry_run) -> ItemResult:
        if not self._safety.checkpoint():
            raise _Stop(f"Stopped for safety: {self._safety.reason or 'game lost focus'}")
        self._go_to_spot(used)
        self._fill_form(entry)
        self._safety.snapshot_position()
        if dry_run:
            return ItemResult(entry.unique_id, entry.name, "dry_run", f"would list at {entry.price}g")
        return self._submit(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_marketplace_runner.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add UI/src/models/marketplace_runner.py UI/tests/test_marketplace_runner.py
git commit -m "feat: add marketplace listing click runner"
```

---

### Task 6: Real input driver + current layout

**Files:**
- Create: `UI/src/models/marketplace_input.py`

**Interfaces:**
- Consumes: `macros.move_mouse`, `macros.mouse_down`, `macros.mouse_up`, `macros.send_key`, `macros.release_modifiers`, `macros.get_current_resolution`, `macros.get_game_window_mode`, `macros.get_window_area_pos`, `macros.WINDOW_MODE`, `macros.DEFAULT_STASH_TAB_MAPPING`, `macros.settings_manager`, Task 2 `build_layout`.
- Produces:
  - `class MacrosInputDriver` implementing `InputDriver` (`click`, `move_to`, `clear_and_type`)
  - `current_layout() -> MarketplaceLayout`
  - `current_tab_mapping() -> list[int]`
  - `resolution_key() -> str` (e.g. `"3840x2160"`)
  - `make_pause(is_cancelled: Callable[[], bool]) -> Callable[[], None]`

This file wraps Windows input and is not unit-tested (conftest replaces `macros`); it is verified manually in Task 9.

- [ ] **Step 1: Implement `UI/src/models/marketplace_input.py`**

```python
"""Real Windows input + live layout for the market lister (not unit-tested)."""
import random
import time

from src.models import macros
from src.models.marketplace_layout import build_layout

CLICK_HOLD_SECONDS = 0.04
KEY_GAP_SECONDS = 0.03
MIN_STEP_DELAY = 0.15
STEP_JITTER = 0.07
VK_BACK = 0x08
VK_A = 0x41
VK_DIGIT_0 = 0x30


class MacrosInputDriver:
    def move_to(self, x, y):
        macros.move_mouse(x, y)

    def click(self, x, y):
        macros.move_mouse(x, y)
        time.sleep(CLICK_HOLD_SECONDS)
        macros.mouse_down()
        time.sleep(CLICK_HOLD_SECONDS)
        macros.mouse_up()

    def _tap(self, vk):
        macros.send_key(vk)
        time.sleep(KEY_GAP_SECONDS)
        macros.send_key(vk, key_up=True)
        time.sleep(KEY_GAP_SECONDS)

    def clear_and_type(self, text):
        macros.send_key(macros.VK_CONTROL)
        self._tap(VK_A)
        macros.send_key(macros.VK_CONTROL, key_up=True)
        self._tap(VK_BACK)
        for ch in text:
            if not ch.isdigit():
                raise ValueError("price must be digits only")
            self._tap(VK_DIGIT_0 + int(ch))
        macros.release_modifiers()


def resolution_key():
    w, h = macros.get_current_resolution()
    return f"{w}x{h}"


def _window_origin(resolution):
    if macros.get_game_window_mode() != macros.WINDOW_MODE:
        return (0, 0)
    area = macros.get_window_area_pos()
    if area and (area[2], area[3]) == tuple(resolution):
        return (area[0], area[1])
    return (0, 0)


def current_layout():
    resolution = macros.get_current_resolution()
    overrides = macros.settings_manager.get('marketplaceCalibrationOverride') or {}
    calibration = overrides.get(f"{resolution[0]}x{resolution[1]}") if isinstance(overrides, dict) else None
    return build_layout(resolution, _window_origin(resolution), calibration)


def current_tab_mapping():
    mapping = macros.settings_manager.get('stashTabMapping')
    if isinstance(mapping, list) and len(mapping) == len(macros.DEFAULT_STASH_TAB_MAPPING):
        return mapping
    return list(macros.DEFAULT_STASH_TAB_MAPPING)


def make_pause(is_cancelled):
    def pause():
        delay = max(MIN_STEP_DELAY, macros.settings_manager.get_sort_speed()) + random.uniform(0, STEP_JITTER)
        end = time.perf_counter() + delay
        while time.perf_counter() < end:
            if is_cancelled():
                return
            time.sleep(0.01)
    return pause
```

- [ ] **Step 2: Smoke-check imports on Windows**

Run: `.venv/Scripts/python -c "from src.models import marketplace_input as m; print(m.resolution_key(), m.current_layout().point('price_field'))"`
Expected: prints your resolution (e.g. `3840x2160 (1920, 1236)`). No mouse movement happens.

- [ ] **Step 3: Commit**

```bash
git add UI/src/models/marketplace_input.py
git commit -m "feat: add real input driver for market lister"
```

---

### Task 7: Background job + API blueprint + app wiring

**Files:**
- Create: `UI/src/market_lister_job.py`
- Create: `UI/src/market_lister_api.py`
- Create: `UI/tests/test_market_lister_api.py`
- Modify: `UI/app.py` (blueprint registration after `server = Flask(...)`/routes, `/market` route next to `/quests` ~line 3934, `_trigger_cancel_sort` ~line 2047)

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `ListerJob(runner_factory: Callable[[threading.Event], MarketplaceRunner], hover_factory: Callable[[threading.Event], Callable[[], None]])` with `start(entries, dry_run) -> bool`, `hover_test() -> bool`, `cancel() -> bool`, `status() -> dict` (`{"state": "idle"|"running"|"done", "mode": "list"|"dry_run"|"hover"|None, "results": [...], "stopped_reason": str|None}`), `is_running() -> bool`
  - `@dataclass ListerDeps(get_stashes, get_data_age, price_lookup, state, settings_get, settings_update, tab_mapping, resolution_key, layout_targets, job, pause)` — see code
  - `create_market_lister_blueprint(deps: ListerDeps) -> Blueprint` with routes:
    - `GET /api/market-lister/rules`, `POST /api/market-lister/rules`
    - `POST /api/market-lister/plan` body `{character_id, rules?}`
    - `POST /api/market-lister/start` body `{entries: [...], dry_run: bool}`
    - `POST /api/market-lister/cancel`
    - `GET /api/market-lister/status`
    - `GET /api/market-lister/calibration`, `POST /api/market-lister/calibration` body `{points: {key: [dx, dy]}, lengths: {key: d}}`
    - `POST /api/market-lister/hover-test`

- [ ] **Step 1: Write the failing tests**

`UI/tests/test_market_lister_api.py`:

```python
import threading
import time

import pytest
from flask import Flask
from networking.protos import MarketPlace_pb2

from src.market_lister_api import ListerDeps, create_market_lister_blueprint
from src.market_lister_job import ListerJob
from src.models.marketplace_runner import ItemResult, RunReport
from src.models.marketplace_state import MarketplaceState

ENTRY = {"unique_id": "a", "name": "Gloves", "rarity": 5, "stash_id": "2", "slot_id": 0,
         "width": 1, "height": 1, "price": 900, "fee": 45, "vendor_price": 10}


class FakeRunner:
    def __init__(self, event):
        self.event = event

    def run(self, entries, dry_run=False, on_progress=None):
        results = [ItemResult(e.unique_id, e.name, "dry_run" if dry_run else "listed") for e in entries]
        for r in results:
            on_progress(r)
        return RunReport(tuple(results), None)


def _wait_done(job):
    for _ in range(100):
        if not job.is_running():
            return
        time.sleep(0.01)


@pytest.fixture
def client_and_deps():
    settings = {}
    state = MarketplaceState()
    state.handle_my_item_list(MarketPlace_pb2.SS2C_MARKETPLACE_MY_ITEM_LIST_RES(totalItemCount=2))
    job = ListerJob(runner_factory=FakeRunner, hover_factory=lambda event: (lambda: None))
    item = {"name": "Gloves", "itemId": "G_1", "itemUniqueId": "a", "slotId": 0, "itemCount": 1,
            "rarity": 5, "width": 1, "height": 1, "pp": [], "sp": [], "vendor_price": 10, "max_stack_size": 1}
    deps = ListerDeps(
        get_stashes=lambda cid, ids: {"2": [item]} if cid == "c1" else {},
        get_data_age=lambda cid: 5.0,
        price_lookup=lambda it: {"success": True, "has_data": True, "avg_price": 1000,
                                 "lowest_ask": 1000, "num_listings": 9},
        state=state,
        settings_get=lambda key, default=None: settings.get(key, default),
        settings_update=lambda updates: settings.update(updates),
        tab_mapping=lambda: [4, 20, 5, 6, 7, 8, 9, 30],
        resolution_key=lambda: "1920x1080",
        job=job,
        pause=lambda: None,
    )
    app = Flask(__name__)
    app.register_blueprint(create_market_lister_blueprint(deps))
    return app.test_client(), deps, settings


def test_rules_round_trip(client_and_deps):
    client, _, settings = client_and_deps
    assert client.get("/api/market-lister/rules").get_json()["undercut_pct"] == 10.0
    client.post("/api/market-lister/rules", json={"undercut_pct": 25})
    assert settings["marketListerRules"]["undercut_pct"] == 25.0


def test_plan_returns_entries_and_listing_info(client_and_deps):
    client, _, _ = client_and_deps
    data = client.post("/api/market-lister/plan", json={"character_id": "c1"}).get_json()
    assert data["success"] is True
    assert data["plan"]["entries"][0]["price"] == 900
    assert data["listings"] == {"seen": True, "used": 2}


def test_plan_requires_character(client_and_deps):
    client, _, _ = client_and_deps
    assert client.post("/api/market-lister/plan", json={}).status_code == 400


def test_start_runs_job_and_reports_status(client_and_deps):
    client, deps, _ = client_and_deps
    resp = client.post("/api/market-lister/start", json={"entries": [ENTRY], "dry_run": True})
    assert resp.status_code == 200
    _wait_done(deps.job)
    status = client.get("/api/market-lister/status").get_json()
    assert status["state"] == "done" and status["mode"] == "dry_run"
    assert status["results"][0]["status"] == "dry_run"


def test_start_rejects_invalid_price(client_and_deps):
    client, _, _ = client_and_deps
    for bad in (-1, "abc", 5_000_000):
        resp = client.post("/api/market-lister/start", json={"entries": [{**ENTRY, "price": bad}]})
        assert resp.status_code == 400


class SlowRunner:
    def __init__(self, gate):
        self.gate = gate

    def run(self, entries, dry_run=False, on_progress=None):
        self.gate.wait(2)
        return RunReport((), None)


def test_start_rejects_empty_and_busy(client_and_deps):
    client, deps, _ = client_and_deps
    assert client.post("/api/market-lister/start", json={"entries": []}).status_code == 400
    gate = threading.Event()
    deps.job._runner_factory = lambda event: SlowRunner(gate)
    client.post("/api/market-lister/start", json={"entries": [ENTRY]})
    assert client.post("/api/market-lister/start", json={"entries": [ENTRY]}).status_code == 409
    gate.set()
    _wait_done(deps.job)


def test_calibration_saved_per_resolution(client_and_deps):
    client, _, settings = client_and_deps
    client.post("/api/market-lister/calibration", json={"points": {"price_field": [3, -2]}, "lengths": {"cell": 0.5}})
    assert settings["marketplaceCalibrationOverride"]["1920x1080"]["points"]["price_field"] == [3, -2]
    assert client.get("/api/market-lister/calibration").get_json()["calibration"]["lengths"]["cell"] == 0.5


def test_calibration_rejects_unknown_keys(client_and_deps):
    client, _, _ = client_and_deps
    assert client.post("/api/market-lister/calibration", json={"points": {"evil": [1, 1]}}).status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_market_lister_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.market_lister_api'`

- [ ] **Step 3: Implement `UI/src/market_lister_job.py`**

```python
"""Runs one market lister operation at a time on a background thread."""
import logging
import threading
from dataclasses import asdict

logger = logging.getLogger(__name__)


class ListerJob:
    def __init__(self, runner_factory, hover_factory):
        self._runner_factory = runner_factory
        self._hover_factory = hover_factory
        self._lock = threading.Lock()
        self._thread = None
        self._event = None
        self._status = {"state": "idle", "mode": None, "results": [], "stopped_reason": None}

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            return {**self._status, "results": list(self._status["results"])}

    def cancel(self) -> bool:
        with self._lock:
            if self._event is None or self._thread is None or not self._thread.is_alive():
                return False
            self._event.set()
            return True

    def _launch(self, mode, target) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._event = threading.Event()
            self._status = {"state": "running", "mode": mode, "results": [], "stopped_reason": None}
            self._thread = threading.Thread(target=target, args=(self._event,), daemon=True, name="MarketLister")
            self._thread.start()
            return True

    def _finish(self, stopped_reason):
        with self._lock:
            self._status = {**self._status, "state": "done", "stopped_reason": stopped_reason}

    def _record(self, result):
        with self._lock:
            self._status["results"].append(asdict(result))

    def start(self, entries, dry_run) -> bool:
        def target(event):
            try:
                report = self._runner_factory(event).run(entries, dry_run=dry_run, on_progress=self._record)
                self._finish(report.stopped_reason)
            except Exception as exc:  # never leave the job stuck in "running"
                logger.exception("Market lister run failed")
                self._finish(f"Unexpected error: {exc}")
        return self._launch("dry_run" if dry_run else "list", target)

    def hover_test(self) -> bool:
        def target(event):
            try:
                self._hover_factory(event)()
                self._finish("Cancelled" if event.is_set() else None)
            except Exception as exc:
                logger.exception("Hover test failed")
                self._finish(f"Unexpected error: {exc}")
        return self._launch("hover", target)
```

- [ ] **Step 4: Implement `UI/src/market_lister_api.py`**

```python
"""Flask blueprint for the auto market lister page."""
from dataclasses import dataclass
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from src.market_lister import PlanEntry, PlanError, build_plan
from src.models.market_rules import ListerRules
from src.models.marketplace_layout import BASE_LENGTHS, BASE_POINTS

RULES_KEY = "marketListerRules"
CALIBRATION_KEY = "marketplaceCalibrationOverride"
MAX_CALIBRATION_PX = 400
TOTAL_SPOTS = 40


@dataclass
class ListerDeps:
    get_stashes: Callable[[str, list], dict]
    get_data_age: Callable[[str], Any]
    price_lookup: Callable[[dict], dict]
    state: Any
    settings_get: Callable[..., Any]
    settings_update: Callable[[dict], Any]
    tab_mapping: Callable[[], list]
    resolution_key: Callable[[], str]
    job: Any
    pause: Callable[[], None]


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def _valid_delta(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return False
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) <= MAX_CALIBRATION_PX for v in value)


def _clean_calibration(payload):
    points = payload.get("points") or {}
    lengths = payload.get("lengths") or {}
    if not isinstance(points, dict) or not isinstance(lengths, dict):
        raise ValueError("points and lengths must be objects")
    for key, value in points.items():
        if key not in BASE_POINTS or not _valid_delta(value, 2):
            raise ValueError(f"invalid point offset: {key}")
    for key, value in lengths.items():
        if key not in BASE_LENGTHS or not _valid_delta([value], 1):
            raise ValueError(f"invalid length offset: {key}")
    return {"points": {k: list(v) for k, v in points.items()}, "lengths": dict(lengths)}


def create_market_lister_blueprint(deps: ListerDeps) -> Blueprint:
    bp = Blueprint("market_lister", __name__)

    def current_rules():
        return ListerRules.from_dict(deps.settings_get(RULES_KEY) or {})

    @bp.get("/api/market-lister/rules")
    def get_rules():
        return jsonify(current_rules().to_dict())

    @bp.post("/api/market-lister/rules")
    def save_rules():
        rules = ListerRules.from_dict(request.get_json(silent=True) or {})
        deps.settings_update({RULES_KEY: rules.to_dict()})
        return jsonify(rules.to_dict())

    @bp.post("/api/market-lister/plan")
    def plan():
        payload = request.get_json(silent=True) or {}
        character_id = str(payload.get("character_id") or "").strip()
        if not character_id:
            return _error("Pick a character first.")
        rules = ListerRules.from_dict(payload["rules"]) if isinstance(payload.get("rules"), dict) else current_rules()
        snapshot = deps.state.snapshot()
        free = None if snapshot is None else max(TOTAL_SPOTS - snapshot.used, 0)
        try:
            result = build_plan(
                deps.get_stashes(character_id, list(rules.source_stash_ids)), rules, deps.price_lookup,
                tab_mapping=deps.tab_mapping(), free_spots=free,
                data_age_s=deps.get_data_age(character_id), pause=deps.pause,
            )
        except PlanError as exc:
            return _error(str(exc), 424)
        listings = {"seen": snapshot is not None, "used": None if snapshot is None else snapshot.used}
        return jsonify({"success": True, "plan": result.to_dict(), "listings": listings})

    @bp.post("/api/market-lister/start")
    def start():
        payload = request.get_json(silent=True) or {}
        raw = payload.get("entries")
        if not isinstance(raw, list) or not raw:
            return _error("Nothing to list.")
        try:
            entries = [PlanEntry.from_dict(e) for e in raw]
        except ValueError as exc:
            return _error(str(exc))
        if not deps.job.start(entries, bool(payload.get("dry_run"))):
            return _error("The lister is already running.", 409)
        return jsonify({"success": True})

    @bp.post("/api/market-lister/cancel")
    def cancel():
        return jsonify({"success": deps.job.cancel()})

    @bp.get("/api/market-lister/status")
    def status():
        snapshot = deps.state.snapshot()
        return jsonify({**deps.job.status(),
                        "listings": {"seen": snapshot is not None,
                                     "used": None if snapshot is None else snapshot.used}})

    @bp.get("/api/market-lister/calibration")
    def get_calibration():
        overrides = deps.settings_get(CALIBRATION_KEY) or {}
        key = deps.resolution_key()
        return jsonify({"resolution": key, "calibration": overrides.get(key) or {"points": {}, "lengths": {}}})

    @bp.post("/api/market-lister/calibration")
    def save_calibration():
        try:
            cleaned = _clean_calibration(request.get_json(silent=True) or {})
        except ValueError as exc:
            return _error(str(exc))
        overrides = dict(deps.settings_get(CALIBRATION_KEY) or {})
        overrides[deps.resolution_key()] = cleaned
        deps.settings_update({CALIBRATION_KEY: overrides})
        return jsonify({"success": True})

    @bp.post("/api/market-lister/hover-test")
    def hover_test():
        if not deps.job.hover_test():
            return _error("The lister is already running.", 409)
        return jsonify({"success": True})

    return bp
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_market_lister_api.py -q`
Expected: 8 passed

- [ ] **Step 6: Wire into `UI/app.py`**

After the `/quests` route (~line 3934) add the page route:

```python
@server.route('/market')
def market_lister_page():
    return render_template('market_lister.html')
```

Directly below `marketplace_state = MarketplaceState()` (Task 3, top level, before the first `@server.route`), add the block below. `settings_manager`, `market_service` and `time` are imported at the top of `app.py`; `stash_manager` and a real Flask `server` only exist in the main process, so the blueprint registration is guarded by the existing `_is_child_process` flag:

```python
from src.market_lister_api import ListerDeps, create_market_lister_blueprint
from src.market_lister_job import ListerJob


def _lister_price_lookup(item):
    from src.models.game_data import ItemDataManager
    rarity = item.get('rarity')
    rarity_name = ItemDataManager.id_to_rarity(rarity) if isinstance(rarity, int) else (rarity or '')
    return market_service.fetch_price_check(
        item.get('name', ''), rarity_name or '', pp=item.get('pp') or None, sp=item.get('sp') or None,
        item_id=item.get('itemId') or None,
    )


class _MonitoredRunner:
    """Starts the sorter's safety monitor for exactly one lister run."""

    def __init__(self, runner, monitor):
        self._runner = runner
        self._monitor = monitor

    def run(self, *args, **kwargs):
        self._monitor.start()
        try:
            return self._runner.run(*args, **kwargs)
        finally:
            self._monitor.stop()


def _lister_runner_factory(event):
    from src.models import marketplace_input
    from src.models.marketplace_runner import MarketplaceRunner
    from src.models.sort_safety import SortSafetyMonitor
    _focus_game_window()
    monitor = SortSafetyMonitor(event)
    runner = MarketplaceRunner(
        marketplace_input.MacrosInputDriver(), marketplace_input.current_layout(), marketplace_state,
        tab_mapping=marketplace_input.current_tab_mapping(), is_cancelled=event.is_set,
        pause=marketplace_input.make_pause(event.is_set), safety=monitor,
    )
    return _MonitoredRunner(runner, monitor)


def _lister_hover_factory(event):
    from src.models import marketplace_input
    driver = marketplace_input.MacrosInputDriver()

    def hover():
        _focus_game_window()
        for _name, point in marketplace_input.current_layout().hover_targets():
            if event.is_set():
                return
            driver.move_to(*point)
            event.wait(1.0)
    return hover


def _focus_game_window():
    import pygetwindow as gw
    from src.models import macros
    windows = [w for w in gw.getAllWindows() if w.title == "Dark and Darker  "]
    if not windows:
        raise RuntimeError("Dark and Darker window not found.")
    windows[0].activate()
    if macros.get_game_window_mode() == 0:
        time.sleep(1.0)
    macros.tap_alt()
    macros.release_modifiers()


market_lister_job = ListerJob(_lister_runner_factory, _lister_hover_factory)


def _lister_resolution_key():
    from src.models import marketplace_input
    return marketplace_input.resolution_key()


def _lister_tab_mapping():
    from src.models import marketplace_input
    return marketplace_input.current_tab_mapping()


if not _is_child_process:
    server.register_blueprint(create_market_lister_blueprint(ListerDeps(
        get_stashes=stash_manager.get_enhanced_stashes,
        get_data_age=stash_manager.get_character_data_age,
        price_lookup=_lister_price_lookup,
        state=marketplace_state,
        settings_get=settings_manager.get,
        settings_update=settings_manager.update,
        tab_mapping=_lister_tab_mapping,
        resolution_key=_lister_resolution_key,
        job=market_lister_job,
        pause=lambda: time.sleep(0.05),
    )))
```

`settings_manager.update(updates)` persists by default.

In `_trigger_cancel_sort` (~line 2047) add as the first statement of the method body:

```python
        market_lister_job.cancel()
```

- [ ] **Step 7: Verify and commit**

Run: `.venv/Scripts/python -c "import ast; ast.parse(open('app.py',encoding='utf-8').read())"` → no output.
Run: `.venv/Scripts/python -m pytest tests -q` → all pass.

```bash
git add UI/src/market_lister_job.py UI/src/market_lister_api.py UI/tests/test_market_lister_api.py UI/app.py
git commit -m "feat: add market lister API, background job and app wiring"
```

---

### Task 8: Market page UI

**Files:**
- Create: `UI/templates/market_lister.html`
- Create: `UI/static/js/market_lister.js`
- Create: `UI/static/css/market_lister.css`
- Modify: `UI/templates/base.html` (Tools section, the disabled `data-page="analytics"` "Market" link ~line 561)
- Modify: `UI/static/js/router.js` (`PAGE_SCRIPTS`, ~line 23)

**Interfaces:**
- Consumes: `/api/characters` (list of `{id, nickname, class, level}`), all `/api/market-lister/*` endpoints from Task 7, global `showNotification(message, type, options)` from `app.js`, `window.__pageCleanup` array used by the router.
- Produces: page at `/market`.

- [ ] **Step 1: Enable the sidebar link in `base.html`**

Replace the disabled Market `<li>`:

```html
                    <li>
                        <a href="#" class="nav-link disabled" data-page="analytics">
                            <span class="material-icons">analytics</span>
                            <span class="nav-text">Market</span>
                            <span class="coming-soon">Soon</span>
                            <div class="nav-indicator"></div>
                        </a>
                    </li>
```

with:

```html
                    <li>
                        <a href="{{ url_for('market_lister_page') }}" class="nav-link" data-page="market">
                            <span class="material-icons">storefront</span>
                            <span class="nav-text">Market</span>
                            <div class="nav-indicator"></div>
                        </a>
                    </li>
```

- [ ] **Step 2: Register the page script in `router.js`**

Add to `PAGE_SCRIPTS` after `'/quests'`:

```javascript
        '/market': '/static/js/market_lister.js',
```

- [ ] **Step 3: Create `UI/templates/market_lister.html`**

```html
{% extends "base.html" %}

{% block head %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/market_lister.css') }}">
{% endblock %}

{% block content %}
<div class="ml-container">
    <div class="ml-header">
        <div class="ml-header-icon" aria-hidden="true"><span class="material-icons">storefront</span></div>
        <div class="ml-header-text">
            <h1>Market Lister</h1>
            <p>Price items from DarkerDB and list them on the Marketplace automatically.</p>
        </div>
    </div>

    <div class="ml-warning" role="note">
        <span class="material-icons" aria-hidden="true">warning</span>
        <span>This moves your mouse and clicks in the game. Automated input may break Dark and Darker's Terms of Service — use at your own risk. Press <kbd>Ctrl</kbd>+<kbd>F12</kbd> to stop at any time.</span>
    </div>

    <div class="ml-status" id="mlListingStatus">Open Trade → Marketplace → My Listings in the game so DnDTools can see your listing spots.</div>

    <section class="ml-card">
        <h2>Rules</h2>
        <div class="ml-rules">
            <label>Character <select id="mlCharacter"></select></label>
            <label>Min rarity
                <select id="mlMinRarity">
                    <option value="2">Common</option><option value="3">Uncommon</option>
                    <option value="4">Rare</option><option value="5">Epic</option>
                    <option value="6">Legendary</option><option value="7">Unique</option>
                </select>
            </label>
            <label>Min price (g) <input type="number" id="mlMinPrice" min="0"></label>
            <label>Undercut % <input type="number" id="mlUndercut" min="0" max="90"></label>
            <label>Max items per run <input type="number" id="mlMaxItems" min="1" max="40"></label>
            <fieldset class="ml-sources">
                <legend>List from</legend>
                <label><input type="checkbox" value="2" class="mlSource"> Inventory</label>
                <label><input type="checkbox" value="4" class="mlSource"> Storage</label>
                <label><input type="checkbox" value="5" class="mlSource"> Purchased 1</label>
                <label><input type="checkbox" value="6" class="mlSource"> Purchased 2</label>
                <label><input type="checkbox" value="7" class="mlSource"> Purchased 3</label>
                <label><input type="checkbox" value="8" class="mlSource"> Purchased 4</label>
                <label><input type="checkbox" value="9" class="mlSource"> Purchased 5</label>
                <label><input type="checkbox" value="30" class="mlSource"> Shared Stash</label>
                <label><input type="checkbox" value="20" class="mlSource"> Shared Seasonal</label>
            </fieldset>
        </div>
        <div class="ml-actions">
            <button type="button" class="ml-btn" id="mlBuildPlan"><span class="material-icons">playlist_add_check</span>Build plan</button>
        </div>
    </section>

    <section class="ml-card" id="mlPlanCard" hidden>
        <h2>Review plan</h2>
        <ul class="ml-warnings" id="mlWarnings"></ul>
        <table class="ml-table">
            <thead><tr><th></th><th>Item</th><th>From</th><th>Price (g)</th><th>Fee</th></tr></thead>
            <tbody id="mlPlanRows"></tbody>
        </table>
        <details class="ml-skipped"><summary id="mlSkippedSummary">Skipped</summary><ul id="mlSkipped"></ul></details>
        <div class="ml-actions">
            <button type="button" class="ml-btn ml-btn-secondary" id="mlDryRun"><span class="material-icons">visibility</span>Dry run</button>
            <button type="button" class="ml-btn" id="mlStart"><span class="material-icons">sell</span>Start listing</button>
            <button type="button" class="ml-btn ml-btn-danger" id="mlCancel" hidden><span class="material-icons">stop</span>Stop</button>
        </div>
        <ol class="ml-results" id="mlResults"></ol>
    </section>

    <section class="ml-card">
        <details>
            <summary><h2 class="ml-inline">Calibration</h2></summary>
            <p class="ml-muted">Hover test moves the mouse (no clicks) over each Marketplace spot for 1 second. If a point is off, nudge it here and test again. Saved for <span id="mlResolution"></span>.</p>
            <div class="ml-calibration" id="mlCalibration"></div>
            <div class="ml-actions">
                <button type="button" class="ml-btn ml-btn-secondary" id="mlHoverTest"><span class="material-icons">ads_click</span>Hover test</button>
                <button type="button" class="ml-btn" id="mlSaveCalibration"><span class="material-icons">save</span>Save offsets</button>
            </div>
        </details>
    </section>
</div>
{% endblock %}
```

- [ ] **Step 4: Create `UI/static/js/market_lister.js`**

```javascript
/* global showNotification */
(() => {
    const API = '/api/market-lister';
    const POLL_MS = 500;
    const STASH_NAMES = { 2: 'Inventory', 4: 'Storage', 5: 'Purchased 1', 6: 'Purchased 2', 7: 'Purchased 3',
        8: 'Purchased 4', 9: 'Purchased 5', 20: 'Shared Seasonal', 30: 'Shared Stash' };
    const POINT_KEYS = ['spot_row_origin', 'next_page_arrow', 'tab_icon_origin', 'inv_grid_origin',
        'stash_grid_origin', 'price_field', 'create_listing_button'];
    const LENGTH_KEYS = ['spot_row_spacing', 'tab_icon_spacing', 'cell'];
    const $ = (id) => document.getElementById(id);
    let plan = null;
    let pollTimer = null;
    let disposed = false;
    let watching = false; // only announce completion for runs started from this page view

    const notify = (msg, type = 'info') => {
        if (typeof showNotification === 'function') showNotification(msg, type, { duration: 5000 });
    };

    const api = async (path, options = {}) => {
        const response = await fetch(API + path, {
            headers: { 'Content-Type': 'application/json' }, ...options,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
    };
    const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

    const text = (tag, value, className) => {
        const el = document.createElement(tag);
        el.textContent = value;
        if (className) el.className = className;
        return el;
    };

    const readRules = () => ({
        min_rarity: Number($('mlMinRarity').value),
        min_price: Number($('mlMinPrice').value),
        undercut_pct: Number($('mlUndercut').value),
        max_items_per_run: Number($('mlMaxItems').value),
        source_stash_ids: [...document.querySelectorAll('.mlSource:checked')].map((c) => c.value),
    });

    const fillRules = (rules) => {
        $('mlMinRarity').value = String(rules.min_rarity);
        $('mlMinPrice').value = rules.min_price;
        $('mlUndercut').value = rules.undercut_pct;
        $('mlMaxItems').value = rules.max_items_per_run;
        document.querySelectorAll('.mlSource').forEach((c) => {
            c.checked = rules.source_stash_ids.includes(c.value);
        });
    };

    const loadCharacters = async () => {
        const response = await fetch('/api/characters');
        const characters = await response.json();
        const select = $('mlCharacter');
        select.replaceChildren(...characters.map((c) => {
            const option = text('option', `${c.nickname} (${c.class} ${c.level})`);
            option.value = c.id;
            return option;
        }));
    };

    const renderListingStatus = (listings) => {
        const el = $('mlListingStatus');
        if (listings && listings.seen) {
            el.textContent = `My Listings detected — ${listings.used} of 40 spots used.`;
            el.classList.add('ok');
        }
    };

    const renderPlan = () => {
        $('mlPlanCard').hidden = false;
        $('mlWarnings').replaceChildren(...plan.warnings.map((w) => text('li', w)));
        $('mlPlanRows').replaceChildren(...plan.entries.map((entry, index) => {
            const row = document.createElement('tr');
            const include = document.createElement('input');
            include.type = 'checkbox';
            include.checked = true;
            include.dataset.index = index;
            include.className = 'mlInclude';
            const price = document.createElement('input');
            price.type = 'number';
            price.min = '1';
            price.value = entry.price;
            price.dataset.index = index;
            price.className = 'mlPrice';
            const cells = [include, text('span', entry.name), text('span', STASH_NAMES[entry.stash_id] || entry.stash_id),
                price, text('span', `${entry.fee}g`)];
            row.replaceChildren(...cells.map((c) => { const td = document.createElement('td'); td.append(c); return td; }));
            return row;
        }));
        $('mlSkippedSummary').textContent = `Skipped (${plan.skipped.length})`;
        $('mlSkipped').replaceChildren(...plan.skipped.map((s) => text('li', `${s.name} — ${s.reason}`)));
        $('mlResults').replaceChildren();
    };

    const selectedEntries = () => [...document.querySelectorAll('.mlInclude:checked')].map((box) => {
        const index = Number(box.dataset.index);
        const price = document.querySelector(`.mlPrice[data-index="${index}"]`);
        return { ...plan.entries[index], price: Number(price.value) };
    });

    const setRunning = (running) => {
        $('mlStart').disabled = running;
        $('mlDryRun').disabled = running;
        $('mlBuildPlan').disabled = running;
        $('mlHoverTest').disabled = running;
        $('mlCancel').hidden = !running;
    };

    const renderResults = (status) => {
        $('mlResults').replaceChildren(...status.results.map((r) => text('li', `${r.name}: ${r.status}${r.message ? ` — ${r.message}` : ''}`, `ml-result-${r.status}`)));
    };

    const poll = async () => {
        if (disposed) return;
        try {
            const status = await api('/status');
            renderResults(status);
            renderListingStatus(status.listings);
            const running = status.state === 'running';
            setRunning(running);
            if (running) {
                pollTimer = setTimeout(poll, POLL_MS);
            } else if (status.state === 'done' && watching) {
                watching = false;
                notify(status.stopped_reason || 'Market lister finished.', status.stopped_reason ? 'warning' : 'success');
            }
        } catch (error) {
            notify(error.message, 'error');
            setRunning(false);
        }
    };

    const buildPlan = async () => {
        $('mlBuildPlan').disabled = true;
        try {
            const rules = readRules();
            await post('/rules', rules);
            const data = await post('/plan', { character_id: $('mlCharacter').value, rules });
            plan = data.plan;
            renderListingStatus(data.listings);
            renderPlan();
        } catch (error) {
            notify(error.message, 'error');
        } finally {
            $('mlBuildPlan').disabled = false;
        }
    };

    const start = async (dryRun) => {
        const entries = selectedEntries();
        if (!entries.length) { notify('Tick at least one item.', 'warning'); return; }
        try {
            await post('/start', { entries, dry_run: dryRun });
            notify(dryRun ? 'Dry run started — switching to the game…' : 'Listing started — switching to the game…');
            watching = true;
            setRunning(true);
            pollTimer = setTimeout(poll, POLL_MS);
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const renderCalibration = (data) => {
        $('mlResolution').textContent = data.resolution;
        const cal = data.calibration || { points: {}, lengths: {} };
        const rows = POINT_KEYS.map((key) => {
            const [dx, dy] = cal.points[key] || [0, 0];
            const row = document.createElement('div');
            row.className = 'ml-cal-row';
            row.innerHTML = `<span></span><label>X <input type="number" data-point="${key}" data-axis="0"></label><label>Y <input type="number" data-point="${key}" data-axis="1"></label>`;
            row.firstChild.textContent = key.replaceAll('_', ' ');
            row.querySelector('[data-axis="0"]').value = dx;
            row.querySelector('[data-axis="1"]').value = dy;
            return row;
        }).concat(LENGTH_KEYS.map((key) => {
            const row = document.createElement('div');
            row.className = 'ml-cal-row';
            row.innerHTML = `<span></span><label>± <input type="number" step="0.1" data-length="${key}"></label>`;
            row.firstChild.textContent = key.replaceAll('_', ' ');
            row.querySelector('input').value = cal.lengths[key] || 0;
            return row;
        }));
        $('mlCalibration').replaceChildren(...rows);
    };

    const saveCalibration = async () => {
        const points = {};
        document.querySelectorAll('[data-point]').forEach((input) => {
            const key = input.dataset.point;
            points[key] = points[key] || [0, 0];
            points[key][Number(input.dataset.axis)] = Number(input.value) || 0;
        });
        const lengths = {};
        document.querySelectorAll('[data-length]').forEach((input) => { lengths[input.dataset.length] = Number(input.value) || 0; });
        try {
            await post('/calibration', { points, lengths });
            notify('Calibration saved.', 'success');
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const hoverTest = async () => {
        try {
            await post('/hover-test');
            watching = true;
            setRunning(true);
            pollTimer = setTimeout(poll, POLL_MS);
        } catch (error) {
            notify(error.message, 'error');
        }
    };

    const init = async () => {
        try {
            await loadCharacters();
            fillRules(await api('/rules'));
            renderCalibration(await api('/calibration'));
            await poll();
        } catch (error) {
            notify(error.message, 'error');
        }
        $('mlBuildPlan').addEventListener('click', buildPlan);
        $('mlDryRun').addEventListener('click', () => start(true));
        $('mlStart').addEventListener('click', () => start(false));
        $('mlCancel').addEventListener('click', () => post('/cancel').catch((e) => notify(e.message, 'error')));
        $('mlHoverTest').addEventListener('click', hoverTest);
        $('mlSaveCalibration').addEventListener('click', saveCalibration);
    };

    window.__pageCleanup = window.__pageCleanup || [];
    window.__pageCleanup.push(() => {
        disposed = true;
        clearTimeout(pollTimer);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
```

- [ ] **Step 5: Create `UI/static/css/market_lister.css`**

```css
.ml-container { display: flex; flex-direction: column; gap: 18px; padding: 8px; }
.ml-header { display: flex; align-items: center; gap: 20px; padding: 24px 28px; border: 1px solid var(--border-color);
    border-radius: 16px; background: linear-gradient(135deg, rgba(25, 25, 25, 0.95) 0%, rgba(20, 20, 20, 0.9) 100%); }
.ml-header-icon { width: 56px; height: 56px; border-radius: 14px; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, rgba(228, 200, 105, 0.2) 0%, rgba(228, 200, 105, 0.05) 100%);
    border: 1px solid rgba(228, 200, 105, 0.2); color: var(--accent-gold); }
.ml-header-icon .material-icons { font-size: 30px; }
.ml-header-text h1 { font-size: 1.8rem; margin-bottom: 6px; }
.ml-header-text p, .ml-muted { margin: 0; color: var(--text-secondary); }
.ml-warning { display: flex; gap: 10px; align-items: center; padding: 12px 16px; border-radius: 12px;
    border: 1px solid rgba(228, 150, 80, 0.4); background: rgba(228, 150, 80, 0.08); color: var(--text-primary); }
.ml-status { padding: 10px 16px; border-radius: 10px; background: var(--bg-secondary); color: var(--text-secondary); }
.ml-status.ok { color: var(--accent-gold); }
.ml-card { padding: 20px 24px; border: 1px solid var(--border-color); border-radius: 16px; background: var(--bg-secondary); }
.ml-card h2 { font-size: 1.2rem; margin: 0 0 14px; }
.ml-inline { display: inline; }
.ml-rules { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px 18px; }
.ml-rules label { display: flex; flex-direction: column; gap: 6px; color: var(--text-secondary); }
.ml-rules input, .ml-rules select, .ml-table input, .ml-cal-row input { padding: 8px 10px; border-radius: 8px;
    border: 1px solid var(--border-color); background: var(--bg-tertiary); color: var(--text-primary); }
.ml-sources { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 8px 16px; border: 1px solid var(--border-color);
    border-radius: 10px; padding: 10px 14px; }
.ml-sources label { flex-direction: row; align-items: center; }
.ml-actions { display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }
.ml-btn { display: flex; align-items: center; gap: 8px; padding: 10px 18px; border-radius: 12px; cursor: pointer; font-weight: 600;
    border: 1px solid rgba(228, 200, 105, 0.3); background: rgba(228, 200, 105, 0.1); color: var(--accent-gold); }
.ml-btn:hover:not(:disabled) { background: rgba(228, 200, 105, 0.2); }
.ml-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.ml-btn-secondary { color: var(--text-primary); border-color: var(--border-color); background: var(--bg-tertiary); }
.ml-btn-danger { color: #ff8a80; border-color: rgba(255, 138, 128, 0.4); background: rgba(255, 138, 128, 0.08); }
.ml-table { width: 100%; border-collapse: collapse; }
.ml-table th, .ml-table td { padding: 8px 10px; border-bottom: 1px solid var(--border-color); text-align: left; }
.ml-table input[type="number"] { width: 110px; }
.ml-warnings { color: var(--accent-gold); margin: 0 0 10px; }
.ml-skipped { margin-top: 12px; color: var(--text-secondary); }
.ml-results { margin-top: 14px; }
.ml-result-listed { color: #9ccc65; }
.ml-result-failed { color: #ff8a80; }
.ml-result-dry_run { color: var(--text-secondary); }
.ml-calibration { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.ml-cal-row { display: grid; grid-template-columns: 200px repeat(2, 120px); gap: 10px; align-items: center; text-transform: capitalize; }
.ml-cal-row input { width: 80px; }
```

- [ ] **Step 6: Manual UI check**

Run the app from source (Windows):

```bash
cd UI
.venv/Scripts/python app.py
```

Expected: sidebar Tools shows **Market** (no "Soon"); clicking it swaps to the Market Lister page without a full reload; characters populate; saved rules load; Calibration shows your resolution (e.g. `3840x2160`). With the game closed, **Build plan** with a DarkerDB key set returns a plan table (or the missing-key error notification without a key). Navigate away and back — no duplicate event handlers (click Build plan once → one request in the dev tools network tab).

- [ ] **Step 7: Commit**

```bash
git add UI/templates/market_lister.html UI/static/js/market_lister.js UI/static/css/market_lister.css UI/templates/base.html UI/static/js/router.js
git commit -m "feat: add Market Lister page to DnDTools UI"
```

---

### Task 9: In-game verification

**Files:**
- Modify: `UI/src/models/marketplace_layout.py` (`BASE_POINTS`/`BASE_LENGTHS` only if hover test shows defaults are off at 16:9)
- Modify: `UI/src/models/marketplace_state.py` (`REGISTER_SUCCESS` only if packets show a different success code)
- Create: `docs/superpowers/notes/2026-09-27-market-lister-verification.md`

**Interfaces:** none new.

- [ ] **Step 1: Enable Developer Mode and capture**

In DnDTools Settings enable **Developer Mode**; start Capture. In game: Trade → Marketplace → My Listings. Open **Packet Viewer** and confirm an `S2C_MARKETPLACE_MY_ITEM_LIST_RES` appeared. On the Market page the status line should read "My Listings detected — N of 40 spots used."

- [ ] **Step 2: Hover test**

Click **Hover test**. Watch the cursor visit all 11 points. For any point that misses, adjust its offsets, **Save offsets**, and re-run until all land. If every 16:9 resolution would need the same fix, update the base value in `BASE_POINTS`/`BASE_LENGTHS` instead (divide the 4K pixel correction by 2) and adjust the matching assertions in `tests/test_marketplace_layout.py`.

- [ ] **Step 3: Dry run**

Build a plan with one cheap item (Max items per run = 1). Click **Dry run**. Expected: game focuses, the spot row opens the form, the item gets selected, price is typed, **Create Listing is not clicked**, result shows `dry_run`.

- [ ] **Step 4: One real listing**

Click **Start listing** for the same single item. In Packet Viewer find `S2C_MARKETPLACE_ITEM_REGISTER_RES` and note its `result` value. Expected: result `1`, then a new `S2C_MARKETPLACE_MY_ITEM_LIST_RES` containing the item's `itemUniqueId`; page result shows `listed`. If the success value is not `1`, update `REGISTER_SUCCESS`, fix the test in `tests/test_marketplace_state.py`, run `python -m pytest tests -q`, and commit.

Also record in the notes whether filled spots stay packed at the top after listing (the runner assumes next free row = used count) and the `availableOrderIndexes` values seen.

- [ ] **Step 5: Multi-item and page turn**

Plan 5 items across Inventory and one stash tab. Start. Expected: tab switching works, all 5 `listed`. If you have ≥ 10 listings, confirm page 2 is reached via the ➤ arrow. Press **Ctrl+F12** during a second run to confirm it stops with "Cancelled".

- [ ] **Step 6: Write the verification notes and commit**

`docs/superpowers/notes/2026-09-27-market-lister-verification.md` records: resolution/mode tested, calibration offsets needed, observed REGISTER_RES success code, whether spots stay packed, `availableOrderIndexes` sample, any failures and fixes.

```bash
git add docs/superpowers/notes/2026-09-27-market-lister-verification.md UI/src/models/marketplace_layout.py UI/src/models/marketplace_state.py UI/tests
git commit -m "docs: record market lister in-game verification"
```
