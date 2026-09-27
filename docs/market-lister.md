# Market Lister

The **Market** page (sidebar → Tools → Market) prices your stash and inventory items from the live
Dark and Darker Marketplace and lists them for you by moving the mouse through the game's own
Marketplace screens. It never sends packets to the game — it only clicks, and it reads the game's
replies through DnDTools' existing (read-only) packet capture.

> **Use at your own risk.** Automated input may break Dark and Darker's Terms of Service. Keep the
> game in front while it runs. Press **Ctrl+F12**, move the mouse, or switch windows to stop it.

## Quick start

1. Start **Capture** in DnDTools and log in (entering the lobby saves your stash data).
2. In the game open **Trade → Marketplace → My Listings**. The Market page shows
   *"My Listings detected … free listing spots"*.
3. Pick the tabs to sell from and click **Build plan**.
4. Click **Price from game**. For each ticked item the lister opens the listing form, uses the
   game's **Search**, reads the results and goes back — nothing is listed.
5. Review the table: every price shows the item's rolls, what it was compared against and a
   confidence badge. Edit prices, untick anything you want to keep.
6. **Dry run** walks the listing steps without committing; **Start listing** lists for real.

## How prices are worked out

Market listings of the same item and rarity are read from the game (up to 10 pages, cheapest
first) plus anything saved in the local market history from the last 6 hours.

- **Items without rolls** (jewelry, treasure, potions): the cheapest real listing, per unit for
  stacks. Lone lowball listings (under half the average) are ignored — they rarely survive anyway.
- **Items with rolls** use *price ladders*. For each of your random rolls, the listings carrying
  that stat are ordered by roll value; your roll is placed between the nearest weaker roll and
  the nearest equal-or-better one, so a small roll isn't priced like a god roll.
  - The **best roll** sets the base price.
  - **Extra good rolls** each add a share (50% by default, learned from market data when there's
    enough) of their own premium over a plain copy.
  - A listing that is **at least as good on every stat** caps the price.
  - Base stats count with a small tolerance (Armor 29 ≈ 30).
- Then your **Undercut %** is applied (Fast 10% / Balanced 3% / Max 1% presets).
- Items are skipped when a merchant pays more than the market (after the 5% / min 15g fee), when
  they'd sell below your minimum price, or when the fee would eat most of the price.
- **Re-check before listing** (on by default) repeats the search right before each listing: if
  the market dropped the lower price is used; if the item is no longer worth listing it's skipped.
  Approved prices are never raised.

Confidence: **High** — comparable listings on both sides of your roll; **Medium** — one side
only; **Low** — few or no comparable listings (marked ⚠️ for you to check).

## Listing flow and safety

For each item: find a free spot (turning pages as needed) → **List an Item** → stash tab → the
item → quantity (stacks) → price → **Create Listing** → **Yes**. Each listing is confirmed by the
game's own messages (`ITEM_REGISTER_RES` result 1, then the item appearing in My Listings); if
that doesn't happen the run stops and the item is marked *unconfirmed*.

The run stops immediately on: Ctrl+F12, the game losing focus, the mouse moving before Create
Listing (checked right before the fee is charged), a fail code from the game (non-tradable items
are skipped instead), no free spots, or My Listings not having been seen recently. It re-opens
the Marketplace by itself only if you were in it within the last 10 minutes.

## Collecting sold gold

Sold listings keep their gold in My Listings until you press **Transfer All Items**, and the game
**destroys uncollected payouts after 7 days**. The Market page shows a banner when sales are
waiting; **Collect** transfers all of them (expired items come back to your stash too).
DnDTools also pops up a notification when the game reports a sale.

## Market data (local history)

Every Marketplace page DnDTools sees is saved to `%LOCALAPPDATA%/DnDTools/data/market_history.sqlite`
— items, rarities, prices, stack sizes, base stats and every random roll — including pages you
browse yourself. Listings that vanish well before their expiry time are marked as *likely sold*.

- **Update market data** reads only new listings (stops once pages are already known).
- **Deep crawl** reads many pages per rarity (the market sorts by price per unit).
- **Look up market prices** searches every saved item by name: listings, cheapest, typical and
  highest price per unit, and what a merchant pays.
- `UI/scripts/market_patterns_report.py` prints cross-item patterns (which stats add value, how
  much extra good rolls add, rarity price steps, price habits, below-merchant deals) and saves
  `market_model.json`, which pricing reads.

## Calibration

Positions are defined for 1920×1080 and scaled to your resolution (ultrawide and windowed mode
included). **Hover test** moves the mouse (no clicks) over each Marketplace spot for a second; if
one is off, nudge it in the Calibration panel and save.

## Stash tabs

The tab icons on the Marketplace screen follow your stash ids in ascending order, so the lister
maps them automatically from your character data. Locked tabs (e.g. an unpaid seasonal stash)
can't be listed from — untick them in "List from".
