# Auto Market Lister — Design

Date: 2026-09-27
Branch: `feature/market-lister` (fork of Beelzebub2/DnDTools; upstream PR once proven)

## Goal

List many stash/inventory items on the Dark and Darker Marketplace in one run,
priced for a fast sale from DarkerDB data, by driving the real mouse and
keyboard. The user sets rules, reviews a plan, presses Start, and can abort at
any time.

Success for v1: from the "My Listings" screen, the tool lists every item in the
approved plan into free listing spots, confirms each one via packets, and stops
cleanly on any error — at every resolution DnDTools' sorter supports.

## Non-goals (v1)

- Navigating Trade → Marketplace → My Listings (user opens it; we verify via packets).
- Stackable items (quantity box / per-unit pricing needs more research).
- Relisting expired items, re-pricing, undercutting existing listings.
- Sending packets. All game actions are real mouse/keyboard input.
- Screen reading / OCR.

## In-game flow (from user screenshots, 16:9)

Marketplace → **My Listings** tab shows:

- **Left panel:** listing spots, 10 per page, pages `1 / N` with a ➤ next-page
  arrow. Filled spots (item, time left, price) come first, then empty
  `List an Item (… Status)` rows. Clicking an empty row opens the listing form.
- **Centre panel (listing form):** item box + quantity box, `Search` button,
  `Selling Price` field, fee text (5% of price, min 15g), `Create Listing`
  (disabled until an item and price are set).
- **Right panel:** inventory or a stash tab grid. Tab icons between centre and
  right panels: [inventory, stash tabs…, +]. Left-click an item to select it.

Per item: click empty spot row → click tab icon → left-click item → click
Selling Price → clear + type price → click Create Listing.

## Architecture

All new code inside `UI/`, following existing conventions (services in
`src/`, models in `src/models/`, page = `templates/X.html` + `static/js/X.js`,
routes in `app.py`).

### 1. `src/models/market_rules.py` (pure, no I/O)

- `ListerRules` dataclass: `source_tabs` (inventory and/or StashType ints),
  `min_rarity` (1–8), `min_price`, `undercut_pct` (default 10),
  `min_listings` (default 3), `max_items_per_run` (default 20),
  `exclude_item_ids` (set), `min_net_ratio` (default 0.5).
- `select_candidates(items, rules) -> list[Candidate]`: filter by tab, rarity,
  tradable, not stackable (`maxStackSize > 1` or `itemCount > 1` → skip), not
  excluded.
- `compute_price(price_check, vendor_price, rules) -> PriceDecision`:
  - no data / `num_listings < min_listings` → skip `"not enough market data"`
  - `base = min(lowest_ask, avg_price)` (whichever exist)
  - `price = floor(base * (1 - undercut_pct/100))`
  - `fee = max(15, ceil(price * 0.05))`
  - `price - fee <= vendor_price` → skip `"vendor pays more"`
  - `(price - fee) / price < min_net_ratio` → skip `"fee too high"`
  - `price < min_price` → skip `"below min price"`
- Returns immutable results; every skip carries a human-readable reason.

### 2. `src/market_lister.py` (service)

- `build_plan(character_id, rules) -> Plan`: reads processed stash items via
  `StashManager`, fetches prices through existing
  `market_service.fetch_price_check` (cached, 50 ms spacing on misses like the
  bulk endpoint), applies rules, caps to
  `min(max_items_per_run, free_spots)`. `Plan` = ordered entries
  `{itemUniqueId, name, rarity, tab, slotId, width, height, price, fee, reason}`
  plus `skipped` list and `stash_data_age_s`.
- Plan order: grouped by tab (fewest tab clicks), then slot order.
- Warns when stash data is stale (> 5 min since last character packet).

### 3. `src/models/marketplace_state.py` (packet tracking)

New handlers registered in the `capture_info` map (app.py ~674). The capture
BPF filter only sees **server→client** packets (`tcp src portrange … and dst
host <local>`), so client requests such as `C2S_…_REGISTER_REQ` are not
available; widening the filter is out of scope (CPU regression risk, #121).

- `S2C_MARKETPLACE_MY_ITEM_LIST_RES` → `totalItemCount` (used spots),
  `availableOrderIndexes`, and every listed item's
  `myItemInfos[].itemInfo.item.itemUniqueId` + `itemInfo.price`. Also proves
  the user is on My Listings.
- `S2C_MARKETPLACE_ITEM_REGISTER_RES` → `result` (`1` assumed success —
  verify in first manual test; fail codes 650–668).

Exposes thread-safe `wait_for_register(timeout) -> RegisterOutcome`
(`ok` / `failed(code)` / `timeout`) and
`wait_for_listing(unique_id, since, timeout) -> bool`: true once a
MY_ITEM_LIST_RES received after `since` contains that `itemUniqueId`.
This replaces the request-side uniqueId check: after a successful register,
if the planned item does not show up in My Listings within 3 s the run stops
("listed something but couldn't confirm it was <item> — check My Listings
and recalibrate").

### 4. `src/models/marketplace_layout.py` (coordinates)

Base positions at 1920×1080 (measured from screenshots, refined by
calibration), scaled with the same rules as `macros._scaled_layout`
(independent axes ≤16:9; height-scale + pillarbox for ultrawide; windowed
client-origin offset; `MANUAL_OVERRIDES` hook):

| Key | Base (1920×1080) |
|---|---|
| `spot_row_origin` (centre of row 0) | (298, 516) |
| `spot_row_spacing` | 50 |
| `spots_per_page` | 10 |
| `next_page_arrow` | (374, 1025) |
| `tab_icon_origin` (inventory icon) | (1315, 196) |
| `tab_icon_spacing` | 46.5 |
| `inv_grid_origin` (top-left corner) | (1443, 622) |
| `stash_grid_origin` (top-left corner) | (1369, 184) |
| `market_jump` (cell size) | 41.3 |
| `price_field` | (960, 618) |
| `create_listing_button` | (960, 968) |

Stash tab icon index uses the existing `stashTabMapping` (icon 0 = inventory,
icons 1.. = stash tabs in mapping order). User calibration deltas stored in
settings under `marketplaceCalibrationOverride`, per resolution.

Refactor: extract the shared scaling helper from `macros._scaled_layout` so
both layouts use one implementation (no duplication).

### 5. `src/models/marketplace_macros.py` (clicker)

Uses existing `macros.py` primitives (SendInput move/click/key), cancel event,
and `SortSafetyMonitor`.

```
run(plan, layout, state, dry_run):
  require state.on_my_listings (fresh MY_ITEM_LIST_RES) else abort
  for entry in plan:
    idx = next free spot → page = idx // 10, row = idx % 10
    click ➤ until page reached; click spot row
    click tab icon for entry.tab
    left-click item centre (grid origin + jump*(pos + size/2))
    click price field; ctrl+a; backspace; type digits
    if dry_run: record + continue (no Create Listing) 
    click Create Listing
    outcome = state.wait_for_register(timeout=5s)
    timeout → STOP
    failed with item-level code (662 non-trade looted, 666 non-tradable)
      → record item failed, continue (next item re-opens a spot row)
    failed with any other code → STOP with fail-code text
    ok → state.wait_for_listing(entry.itemUniqueId, since=click time, 3s)
      not seen → STOP "couldn't confirm"
    record success; update used-spot count
```

Per-step delay from `sortSpeed` + jitter (existing). Any cancel, focus loss,
cursor deviation, timeout, mismatch, or fail code → stop immediately and
report. Never retries a failed Create Listing automatically.

Spot index: v1 assumes filled spots are packed first (as in screenshots), so
the next free row = used count. `availableOrderIndexes` is logged to verify
this assumption during testing; if it proves wrong, use the lowest available
index instead.

### 6. Calibration

v1 uses a lightweight calibration on the Market Lister page instead of
extending the 900-line native `calibration_overlay.py`:

- **Hover test**: moves the cursor (no clicks) to each marketplace point in
  turn — spot row 0 and 9, ➤ arrow, inventory icon, first stash tab icon,
  inventory cell (0,0) and (9,4), stash cell (0,0) and (11,19), price field,
  Create Listing — pausing ~1 s on each, so the user sees where it lands.
- **Nudge offsets**: per point group, X/Y pixel offsets (and cell-size /
  spacing deltas) the user adjusts until the hover test lands correctly.
  Saved per resolution in settings under `marketplaceCalibrationOverride`
  as `{"3840x2160": {"points": {key: [dx, dy]}, "lengths": {key: d}}}`.

A drag-to-calibrate overlay mode can follow in v2 if nudging proves tedious.

### 7. UI: Market Lister page

`templates/market_lister.html` + `static/js/market_lister.js` +
`static/css/market_lister.css`, extending `base.html`, registered in
`router.js` `PAGE_SCRIPTS`, and wired to the sidebar's existing disabled
**Market (Soon)** link under Tools (enable it, point it at `/market`).
Styling reuses the Quest page's header/toolbar patterns. Endpoints live in a
Flask blueprint `src/market_lister_api.py` registered by `app.py`:

- `POST /api/market-lister/plan` (rules → plan + skipped)
- `POST /api/market-lister/start` (approved entries with edited prices, `dry_run`)
- `POST /api/market-lister/cancel`
- `GET  /api/market-lister/status` (progress, per-item results, marketplace state)

Page: rules form → **Build plan** → review table (editable price, untick,
skip reasons collapsed) → **Dry run** / **Start** → live progress + results.
Shows DarkerDB key missing / stale stash / not on My Listings warnings.
Rules persist in settings (`marketListerRules`).

## Error handling

| Situation | Behaviour |
|---|---|
| No DarkerDB key | Plan build fails with message linking to setup |
| Rate limited | Stop plan build, show partial plan |
| Not on My Listings (no recent MY_ITEM_LIST_RES) | Refuse to start |
| No free spots | Refuse to start / stop run, report remaining |
| Register fail code | Stop, show decoded reason (e.g. insufficient gold, non-tradable) |
| No register response in 5 s | Stop ("listing not confirmed — check the game") |
| Register ok but planned item not in My Listings within 3 s | Stop ("couldn't confirm — check My Listings and recalibrate") |
| Item-level fail (662 non-trade looted, 666 non-tradable) | Mark item failed, continue |
| Focus loss / mouse moved / Ctrl+F12 | Stop (existing safety monitor) |

## Testing

- **Unit (pytest, `UI/tests/`)**: `market_rules` (every skip path, rounding,
  fee), plan ordering/capping, spot→page/row math, layout scaling at 1080p,
  1440p, 4K, 720p, ultrawide, windowed offset, calibration deltas.
- **Clicker with fake input**: stub macros primitives + fake
  `marketplace_state`; assert exact click sequence, dry-run skips Create
  Listing, and each stop condition halts the run.
- **Packet handlers**: feed MY_ITEM_LIST_RES / REGISTER_RES messages built
  with the real generated `MarketPlace_pb2` classes.
- **Manual in-game**: calibrate at 4K fullscreen → dry run → list 1 cheap item
  → list a 5-item plan across two tabs → verify page-2 behaviour.

Run: `cd UI && python -m pytest tests`.

## Risks

- **ToS/ban risk**: automated input on the Marketplace may violate the game's
  terms. Mitigations are human-like delays and small per-run caps, but risk is
  the user's. Must be stated in the UI and any upstream PR.
- **UI changes** by game patches break coordinates → calibration + uniqueId
  check stop the run instead of misclicking silently.
- **Stale stash data** (items moved since last capture) → age warning +
  uniqueId check.
- **Exclusive fullscreen focus quirks** (4K user) → reuse sorter's Alt-tap /
  activation path.
