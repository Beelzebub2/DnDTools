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
  stacks.
- **Items with rolls** are priced off **one real listing — never a point inside a price range**.
  For each of your random rolls the anchor is the cheapest listing with that stat at your level
  or the nearest similar weaker level (within 1.5× of your roll), or anything stronger that is
  even cheaper. So your copy is never dearer than one at least as good, nor than a slightly
  weaker one — a low price for a fast sale.
  - Rolls far weaker or far stronger than yours are no comparison: a small roll isn't priced
    like a god roll, and one expensive listing can't drag the price up.
  - The **best roll**'s anchor sets the base.
  - **Extra rolls** add a bonus when market data shows the two stats sell together (for example
    Physical Power + Physical Weapon Damage), otherwise a small share of their premium.
  - A listing that is **at least as good on every stat** caps the price.
  - Base stats count with a small tolerance (Armor 29 ≈ 30).
- **Lowballs are ignored**: an ask under half of what copies no better than it typically ask.
  A cheap weak roll is a real price level; a strong roll dumped for pennies is not — it rarely
  lasts, so the lister doesn't follow it down.
- Then your **Undercut %** is applied (Fast 10% / Balanced 3% / Max 1% presets).
- Items are skipped when a merchant pays more than the market (after the 5% / min 15g fee), when
  they'd sell below your minimum price, or when the fee would eat most of the price.
- **Re-check before listing** (on by default) repeats the search right before each listing:
  - a small drop (up to 20%) → the lower price is used; approved prices are never raised;
  - a bigger drop → the item is skipped so you can price it again and review it;
  - no longer worth listing (for example a merchant now pays more) → skipped;
  - a price you edited, an incomplete search, or an uncertain fresh price → your approved price
    is kept. Your own listings never count as competition.

Confidence: **High** — comparable listings on both sides of your roll; **Medium** — one side
only; **Low** — few or no comparable listings (marked ⚠️ for you to check).

## Listing flow and safety

For each item: find a free spot (turning pages as needed) → **List an Item** → stash tab → the
item → quantity (stacks) → price → **Create Listing** → **Yes**. Each listing is confirmed by the
game's own messages (`ITEM_REGISTER_RES` result 1, then the item appearing in My Listings); if
that doesn't happen the run stops and the item is marked *unconfirmed*.

The run stops immediately on: Ctrl+F12, the game losing focus, the mouse moving before Create
Listing (checked right before the fee is charged), a fail code from the game (non-tradable items
are skipped instead), no free spots, or My Listings not having been seen recently.

Before the first click of any run, and after every market search, My Listings is re-opened
and the game must confirm it (and which page it shows — it reopens on the page last used);
otherwise the run stops. Pages are turned with the arrows, each turn confirmed by the game. The market search
must show the item that was meant to be selected, or the run stops before listing anything.
Every page turn in My Listings must be confirmed by the game before a spot is clicked, and after
a listing the game refuses (for example an untradable item) My Listings is re-confirmed before
the next item. If a run is cancelled while the "Would you like to list the item?" dialog is up,
the lister clicks **No** — unless another window is in front, in which case the stop message
tells you to click No yourself. Collecting gold and crawling also stop when you move the mouse.

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
- **Deep crawl** reads many pages per rarity (the market sorts by price per unit; the game serves
  about one page per second). When a crawl reads a rarity to its last page, listings from before
  that crawl that didn't show up again are marked *likely sold* (or cancelled), so repeating
  full crawls — say once a day — builds up real sale data, not just asking prices.
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
