"""Print cross-item market patterns from the local history and save the learned model.

Usage (from UI/):  .venv/Scripts/python scripts/market_patterns_report.py [--json]
Writes <data dir>/market_model.json, which the lister's pricing reads.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.appdirs import get_data_dir  # noqa: E402
from src.models.market_model import model_from_report, save_model  # noqa: E402
from src.models.market_patterns import Listing, analyze  # noqa: E402

REPORT_PAIRS = 10  # pairs printed; the model keeps more for pricing
ITEMS_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "items.json")


def load_listings(db_path):
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT item_id, rarity, price, item_count, base, rolls, seller FROM listings").fetchall()
    return [Listing(i, r, p, c, tuple(map(tuple, json.loads(b))), tuple(map(tuple, json.loads(ro))), s)
            for i, r, p, c, b, ro, s in rows]


def load_items():
    with open(ITEMS_JSON, encoding="utf-8") as fh:
        items = json.load(fh)
    vendor = {item_id: meta.get("vendor_price", 0) for item_id, meta in items.items()}
    types = {item_id: meta.get("item_type") or "other" for item_id, meta in items.items()}
    return vendor, types


def print_report(report):
    print(f"Listings analysed: {report['listings']} across {report['items']} items\n")
    print("Most valuable random stats (price uplift from worst to best roll, within the same item):")
    for stat, p in list(report["stat_premiums"].items())[:15]:
        print(f"  {stat:28} {p['per_quality']:+7.1f}%   (present {p['present']:+.1f}%, n={p['support']})")
    print("\nLeast valuable stats:")
    for stat, p in list(report["stat_premiums"].items())[-8:]:
        print(f"  {stat:28} {p['per_quality']:+7.1f}%   (n={p['support']})")
    print("\nBy item type:")
    for item_type, premiums in report["stat_premiums_by_type"].items():
        top = [f"{s} {p['per_quality']:+.0f}%" for s, p in list(premiums.items())[:6]]
        if top:
            print(f"  [{item_type}] most valuable: {', '.join(top)}")
    print("\nGood rolls (top 30% of range) vs price, relative to the item's median:")
    for k, v in report["good_roll_counts"].items():
        print(f"  {k} good rolls: {v['median_uplift']:+.1f}%  (n={v['support']})")
    print(f"  => each extra good roll adds ~{report['extra_good_roll_factor']} of the first one's uplift")
    print("\nStat pairs worth more together:")
    for pair in report["pair_synergies"][:REPORT_PAIRS]:
        print(f"  {pair['pair']:45} {pair['synergy']:+.1f}%  (n={pair['support']})")
    print("\nRarity price steps (median, same item):")
    for step, v in report["rarity_steps"].items():
        print(f"  {step}: x{v['median_ratio']}  ({v['archetypes']} items)")
    print(f"\nPrice habits: {report['price_habits']}")
    print(f"Lowball listings (<50% of item median): {report['lowball_share']}")
    print("\nListed below merchant value (buy & vendor):")
    for deal in report["below_vendor"]:
        print(f"  {deal['item']:30} {deal['price']}g  (merchant pays {deal['vendor']}g, +{deal['gain']}g)")
    if report["seller_concentration"]:
        print(f"\nTop sellers' share: {report['seller_concentration']}")


def main():
    data_dir = get_data_dir()
    vendor, types = load_items()
    report = analyze(load_listings(os.path.join(data_dir, "market_history.sqlite")), vendor, types)
    save_model(os.path.join(data_dir, "market_model.json"), model_from_report(report))
    if "--json" in sys.argv:
        print(json.dumps({k: v for k, v in report.items() if k != "roll_ranges"}, indent=1))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
