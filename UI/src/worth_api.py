"""Flask blueprint for Item Worth: what items are worth for their exact rolls (local market model)."""
from dataclasses import dataclass
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from src.market_lister import stat_pairs
from src.models.market_rules import CURRENCY_ITEM_PREFIXES, ListerRules, listing_fee
from src.models.roll_pricing import price_from_market
from src.models.worth_model import similar

SIMILAR_LIMIT = 5
MAX_QUANTITY = 999
MAX_ITEM_ID = 128
NO_MODEL = ("No value model yet — crawl the market, then press \"Analyze market data\" on the Market page.")


@dataclass
class WorthDeps:
    model: Callable[[], Any]                   # the trained WorthModel, or None
    stashes: Callable[[str], dict]             # character id -> {stash id: [enhanced item dicts]}
    live_rows: Callable[[str], list]           # item id -> recent live MarketRows
    rules: Callable[[], ListerRules]           # the lister's pricing rules (undercut, minimums)
    sold_rows: Callable[[str], list] = lambda item_id: []
    train: Callable[[], dict] = lambda: {}     # retrain now -> held-out accuracy
    info: Callable[[], dict] = lambda: {}      # {"listings": n, "evaluation": {...}} of the saved model


def _estimate_dict(est) -> dict:
    return {"value": round(est.value), "low": round(est.low), "high": round(est.high),
            "confidence": est.confidence, "typical": round(est.typical), "listings": est.listings,
            "rolls": [{"stat": r.stat, "value": r.value, "quality": r.quality, "effect_pct": r.effect_pct}
                      for r in est.rolls],
            "pairs": [{"stats": list(p.stats), "effect_pct": p.effect_pct} for p in est.pairs]}


def verdict(value, merchant) -> str:
    """"merchant" when a merchant pays at least what a listing nets after the fee, else "list"."""
    if not value:
        return "merchant" if merchant else "unknown"
    return "merchant" if merchant >= value - listing_fee(value) else "list"


def value_item(model, item: dict) -> dict:
    """One stash item with its worth; gold and silver are marked as currency and not valued."""
    item_id = str(item.get("itemId") or "")
    quantity = max(int(item.get("itemCount") or 1), 1)
    merchant = int(item.get("vendor_price") or 0) * quantity
    row = {"unique_id": str(item.get("itemUniqueId") or ""), "name": item.get("name") or item_id,
           "item_id": item_id, "slot_id": int(item.get("slotId") or 0), "quantity": quantity, "merchant": merchant}
    if item_id.startswith(CURRENCY_ITEM_PREFIXES):
        return {**row, "currency": True}
    if not item_id:
        return {**row, "value": None, "confidence": None, "known": False, "verdict": verdict(None, merchant)}
    est = model.predict(item_id, stat_pairs(item.get("sp")), quantity=quantity)
    value = round(est.value)
    return {**row, "value": value, "low": round(est.low), "high": round(est.high), "confidence": est.confidence,
            "known": model.knows(item_id), "verdict": verdict(value, merchant)}


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def create_worth_blueprint(deps: WorthDeps) -> Blueprint:
    bp = Blueprint("worth", __name__)

    @bp.get("/api/worth/model")
    def model_info():
        return jsonify({"success": True, "trained": deps.model() is not None, **deps.info()})

    @bp.post("/api/worth/train")
    def train_model():
        return jsonify({"success": True, "accuracy": deps.train()})

    @bp.get("/api/worth/character/<character_id>")
    def character_worth(character_id):
        model = deps.model()
        if model is None:
            return _error(NO_MODEL, 409)
        stashes, grand_value, grand_merchant = {}, 0, 0
        for stash_id, items in (deps.stashes(str(character_id)[:64]) or {}).items():
            valued = [value_item(model, item) for item in items]
            worth = sum(v.get("value") or 0 for v in valued if not v.get("currency"))
            merchant = sum(v["merchant"] for v in valued if not v.get("currency"))
            stashes[str(stash_id)] = {"items": valued, "value": worth, "merchant": merchant}
            grand_value, grand_merchant = grand_value + worth, grand_merchant + merchant
        return jsonify({"success": True, "stashes": stashes, "value": grand_value, "merchant": grand_merchant})

    @bp.post("/api/worth/item")
    def item_worth():
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else {}
        item_id = str(payload.get("item_id") or "").strip()[:MAX_ITEM_ID]
        if not item_id:
            return _error("item_id is required")
        model = deps.model()
        if model is None:
            return _error(NO_MODEL, 409)
        quantity = payload.get("quantity", 1)
        quantity = quantity if isinstance(quantity, int) and not isinstance(quantity, bool) else 1
        quantity = min(max(quantity, 1), MAX_QUANTITY)
        vendor = payload.get("vendor_price", 0)
        vendor = vendor if isinstance(vendor, (int, float)) and not isinstance(vendor, bool) and vendor > 0 else 0
        rolls, base = stat_pairs(payload.get("rolls")), stat_pairs(payload.get("base"))
        est = model.predict(item_id, rolls, quantity=quantity)
        live = deps.live_rows(item_id)
        suggestion = price_from_market(item_id, base, rolls, vendor, [], live, deps.rules(), quantity=quantity,
                                       model_value=est.value)
        units = [r.price / max(r.count, 1) for r in live]
        return jsonify({
            "success": True, "item_id": item_id, "quantity": quantity, "known": model.knows(item_id),
            "estimate": _estimate_dict(est),
            "suggested": suggestion.price if suggestion.ok else None,
            "suggested_reason": None if suggestion.ok else suggestion.reason,
            "lowest_ask": round(min(units)) if units else None,
            "merchant": round(vendor * quantity), "verdict": verdict(round(est.value), round(vendor * quantity)),
            "market": {"live": len(live), "likely_sold": len(deps.sold_rows(item_id))},
            "similar": [{"price": r.price, "count": r.count, "rolls": [list(x) for x in r.rolls]}
                        for r in similar(model, item_id, rolls, live, SIMILAR_LIMIT)],
        })

    return bp
