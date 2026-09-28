# Item Worth — design

**Goal:** a GrimVault-style valuation built into DnDTools — what any item is worth for its exact
rolls — using our own algorithm trained on the local market history. No DarkerDB API.

**Approved by the user (2026-09-27):** "we want our own algorithm, base price prediction, train on
our own local db", added as a DnDTools feature (not a separate app).

## Data
`market_history.sqlite` → `listings` (asking prices, every roll, first/last seen, expiry, likely
sold). Currently all Rare and Epic listings (~61k) plus Legendary as it is crawled. Item metadata
from `assets/items.json` (slot, armor type, rarity, merchant price).

## Model (`src/models/worth_model.py`)
Additive model on log price, fitted with ridge regression on sparse one-hot features:

    log(price) = item + Σ rolls [ e(stat,tier) + e(stat,rarity,tier) + e(stat,slot,rarity,tier) ]
                      + Σ pairs e(statA+statB)

- **item**: the plain-copy price level of that exact item (item ids encode rarity).
- **tier**: roll quality within the stat's legal range on that item (observed min–max):
  weak (< 1/3), mid, strong (≥ 2/3). Attributes (1–3) map to one tier per value.
- The three roll levels are fitted together, so ridge shrinks thin slot/rarity cells toward the
  global stat effect (hierarchical shrinkage for free).
- **pairs**: stat pairs seen on ≥ 50 listings.
- Robust: fit, drop listings whose residual is beyond 3 × MAD, refit.
- Stacks and unrolled items: per-unit price; the item term alone.
- Unseen items fall back to the mean item term of their slot + rarity (low confidence).

Stored as plain JSON (`worth_model.json`, atomic save) and evaluated in pure Python, so the app
only needs numpy/scikit-learn when training (both already bundled for the sorter).

## Outputs
`predict(item_id, rolls, quantity)` → value, low–high band (residual spread), confidence, the
typical-copy price, each roll's quality (0–1 in its range) and effect (%), pair bonuses.
`similar(item_id, rolls, listings)` → closest live listings (same item; overlap of stats, then
quality distance).

## Evaluation
Hold-out 20% of listings: median absolute % error of the model vs the item-median baseline, and
the share of predictions within ±25%. Printed by `scripts/train_worth_model.py` and returned by
the Analyze action.

## Where it shows up
1. Market page: "Train value model" with the accuracy numbers; runs after crawls too.
2. API `POST /api/worth/estimate` and `GET /api/worth/character/<id>` (per-item values, totals).
3. Characters view: a value badge per item, tab/stash totals, and a detail panel (value, suggested
   fast-sale price, lowest ask, merchant price, roll-quality bars, why, similar listings).
4. Market lister: the reference price is capped at the model's value (floored at the cheapest
   real listing), so junk rolls can't inherit a price from unrelated stats; the "beats every
   listing" flag only counts stats the model says add value.

## Out of scope (for now)
In-game hover overlay (GrimVault reads the screen), sale-price calibration (needs repeated
crawls for enough "likely sold" data), drop sources and quests.
