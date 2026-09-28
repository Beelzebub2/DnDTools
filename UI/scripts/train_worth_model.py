"""Train the Item Worth model on the local market history and report its accuracy.

Usage (from UI/):  .venv/Scripts/python scripts/train_worth_model.py
Holds out 20% of listings to measure accuracy, then trains on everything and saves
<data dir>/worth_model.json, which DnDTools uses to value items.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.appdirs import get_data_dir  # noqa: E402
from src.models.market_history import MarketHistory  # noqa: E402
from src.models.market_model import save_model  # noqa: E402
from src.models.worth_model import evaluate, train  # noqa: E402
from scripts.market_patterns_report import ITEMS_JSON  # noqa: E402


def item_groups():
    """item id -> slot (gear) or item type (everything else): the model's pricing groups."""
    with open(ITEMS_JSON, encoding="utf-8") as fh:
        items = json.load(fh)
    return {item_id: meta.get("slot_type") or meta.get("item_type") or "other" for item_id, meta in items.items()}


def main():
    data_dir = get_data_dir()
    listings = MarketHistory(os.path.join(data_dir, "market_history.sqlite")).worth_listings()
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
