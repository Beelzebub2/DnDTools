"""Train the Item Worth model on the local market history and report its accuracy.

Usage (from UI/):  .venv/Scripts/python scripts/train_worth_model.py
Holds out 20% of listings to measure accuracy, then trains on everything and saves
<data dir>/worth_model.json, which DnDTools uses to value items.
"""
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.appdirs import get_data_dir  # noqa: E402
from src.models.market_model import save_model  # noqa: E402
from src.models.worth_model import evaluate, train  # noqa: E402
from scripts.market_patterns_report import ITEMS_JSON  # noqa: E402

LISTING_DAYS = 7  # a Marketplace listing lasts a week
DAY_S = 86400.0


@dataclass(frozen=True)
class AgedListing:
    item_id: str
    rarity: int
    price: int
    item_count: int
    base: tuple
    rolls: tuple
    age_days: float  # how long it had been listed when first seen


def load_listings(db_path):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = db.execute("SELECT item_id, rarity, price, item_count, base, rolls, first_seen, expires_at FROM listings")
    return [AgedListing(i, r, p, c, tuple(map(tuple, json.loads(b))), tuple(map(tuple, json.loads(ro))),
                        max(0.0, LISTING_DAYS - (expires - first) / DAY_S))
            for i, r, p, c, b, ro, first, expires in rows]


def item_groups():
    """item id -> slot (gear) or item type (everything else): the model's pricing groups."""
    with open(ITEMS_JSON, encoding="utf-8") as fh:
        items = json.load(fh)
    return {item_id: meta.get("slot_type") or meta.get("item_type") or "other" for item_id, meta in items.items()}


def main():
    data_dir = get_data_dir()
    listings = load_listings(os.path.join(data_dir, "market_history.sqlite"))
    groups = item_groups()
    started = time.time()
    result = evaluate(listings, groups)
    print(f"Held out {result['tested']} of {result['tested'] + result['trained']} listings:")
    print(f"  model:        typical error {result['model_mdape']}%, within ±25%: {result['model_within_25']}%")
    print(f"  item median:  typical error {result['baseline_mdape']}%, within ±25%: {result['baseline_within_25']}%")
    model = train(listings, groups)
    save_model(os.path.join(data_dir, "worth_model.json"), {**model.to_dict(), "evaluation": result})
    print(f"Trained on {model.listings} listings in {time.time() - started:.0f}s -> worth_model.json")


if __name__ == "__main__":
    main()
