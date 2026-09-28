# Sell to Merchant — design

**Goal:** one click sells the items the market lister decided are better sold to a merchant, and
confirms every sale from the game's own reply. This is the last step to "everything in the stash is
gold": the lister lists what the market pays well for, and this sells the rest.

## What goes to a merchant

The lister's plan already explains every skipped item. A skipped item is *merchant-bound* when its
reason is one of:

- `vendor pays more` — a merchant pays at least what a listing nets after the fee;
- `below min price` — the market price is under the lister's minimum;
- `below minimum rarity` — below the lister's rarity setting;
- `a merchant sells it for Ng` — merchants sell it cheaply, so the market can't beat them.

Gold and silver are never sold. Nothing on the never-sell list, nothing already listed, and nothing
the pricing run did not reach is offered. Each skip now carries the item's unique id, so the page can
offer those rows; the server re-reads the character's stash to find each item's *current* tab and
slot (never trusting positions sent by the page).

The page shows a "Sell to merchant" card: one checkbox per merchant-bound item with what the merchant
pays. Items the merchant pays 0g for start unticked.

## Which merchant

Version 1 sells to **The Collector**. In the manual run on 2026-09-27 he took every kind of loot we
tried: treasure (rings, goblets, bangles, crowns), gems, crafting materials (Bone Powder, Blue
Eyeballs), a Ghostdust Pouch, a Sling Stone, bolts and arrows. A merchant pays the same for an item
as any other. An item he refuses stays in the stash and is reported as "not taken"; routing per item
type to other merchants can come later if that ever happens.

## The flow in the game

Start: the game shows the lobby (any top tab, e.g. Merchants & Workshops).

1. Click the lobby's **Merchants & Workshops** tab, then The Collector's card.
2. Wait for the merchant's quest list (`S2C_MERCHANT_QUEST_LIST_INFO_RES`). Its quest ids name the
   merchant (`Id_Quest_TheCollector_01`), so a wrong or missing merchant stops the run before anything
   is touched.
3. Click the **Sell** tab, then **Sell** above the Sell box, so Make Deal never runs from its
   neighbour, the **Buyback** (repurchase) view.
4. Put items into the 10×6 sell box: open each item's stash tab (the same calibrated icons and grid
   the lister uses — the right-hand panel is identical on both screens) and drag the item into cells
   reserved for it. Items that don't fit wait for the next batch.
5. Click **Make Deal** and wait for `S2C_MERCHANT_STOCK_SELL_BACK_RES`:
   - an item in `deleteUniqueIds` is **sold** (the merchant's price × stack size in gold);
   - a staged item that was not deleted was **not taken** by the merchant;
   - a deleted item that was *not* staged means the wrong item was dragged: the run stops and names
     it, and the item can be bought back from the merchant's Buyback tab.
6. Repeat 4–5 for the next batch, then press Escape to leave the merchant.

A **dry run** stages the first batch without Make Deal, then presses Escape (which puts the items
back), so positions can be checked safely.

## Safety

The same guards as the lister: focus loss and mouse interference stop the run (the sorter's safety
monitor), Cancel stops between drags, and no Make Deal is clicked after any stop. No reply to Make
Deal within the timeout stops the run with "check the Buyback tab". Stash data older than the lister's
stale threshold produces a warning, as in the listing plan.

## Pieces

| Piece | Role |
|---|---|
| `src/models/merchant_seller.py` | pure rules: merchant-bound reasons, stash lookup, sell-box packing, sale outcome |
| `src/models/merchant_state.py` | merchant packets: which merchant opened, sell replies |
| `src/models/merchant_runner.py` | the click / drag flow above |
| `src/models/marketplace_layout.py` | new points: Merchants tab, merchant cards, Sell tab, Make Deal, sell box |
| `src/models/marketplace_input.py` | real drag and Escape (scan code; the game ignores virtual-key Escape) |
| `src/market_lister_api.py` | `POST /api/market-lister/merchant-plan` and `/merchant-sell` |
| `src/market_lister_job.py` | `sell_to_merchant` job mode (`merchant` / `merchant_dry_run`) |
| page | "Sell to merchant" card under the plan |

## Not in version 1

Routing items to different merchants by type, automatic buyback, and reading the sell box from the
screen.
