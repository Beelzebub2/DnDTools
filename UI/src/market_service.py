import os
import re
import threading
import time
from datetime import datetime, timezone

import requests


DARKERDB_BASE_URL = "https://api.darkerdb.com"
MARKET_LISTINGS_PATH = "/v2/market"
PRICE_CHECK_PATH = "/v2/price-checks"
DARKERDB_API_VERSION = "2026-08-03"
MARKET_CACHE_DURATION = 300
MARKET_CACHE_MAX_ENTRIES = 500
USER_AGENT = "DnDTools-MarketProxy/2.0"

_market_price_cache = {}
_market_cache_lock = threading.RLock()

_RARITY_NORMALIZE = {
    "poor": "Poor",
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "epic": "Epic",
    "legend": "Legendary",
    "legendary": "Legendary",
    "unique": "Unique",
    "mythic": "Mythic",
    "artifact": "Artifact",
}


def clear_market_cache():
    with _market_cache_lock:
        _market_price_cache.clear()


def get_darkerdb_api_key():
    # Do not treat a generic API_KEY as a DarkerDB credential. Desktop
    # environments commonly contain unrelated provider keys, and forwarding
    # one of those to DarkerDB would disclose it to the wrong service.
    for name in ("DARKERDB_API_KEY", "DNDTOOLS_DARKERDB_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def normalize_rarity(rarity):
    if not rarity:
        return ""
    return _RARITY_NORMALIZE.get(str(rarity).lower(), rarity)


def _attribute_key(name):
    value = str(name or "").strip()
    value = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", value)
    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"[^a-zA-Z0-9_]", "", value)
    return value.lower()


def normalize_market_properties(value, *, max_count=64):
    """Validate the compact ``[[name, value], ...]`` roll representation."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > max_count:
        raise ValueError("Market properties must be a bounded array")
    normalized = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise ValueError("Each market property must contain a name and value")
        name = str(entry[0] or "").strip()
        prop_value = entry[1]
        if not name or len(name) > 128 or not isinstance(prop_value, (str, int, float, bool)):
            raise ValueError("Market property contains an invalid name or value")
        if isinstance(prop_value, str) and len(prop_value) > 128:
            raise ValueError("Market property value is too long")
        normalized.append([name, prop_value])
    return normalized


def build_cache_key(item_name, rarity, pp=None, sp=None, *, item_id=None, archetype=None):
    parts = [str(item_name or ""), normalize_rarity(rarity)]
    identity = str(item_id or "").strip()
    if identity:
        parts.append(f"id:{identity.lower()}")
    else:
        family = str(archetype or "").strip()
        if family:
            parts.append(f"archetype:{family.lower()}")
    for prefix, props in (("p", pp), ("s", sp)):
        for prop_name, prop_value in sorted(props or [], key=lambda x: _attribute_key(x[0])):
            parts.append(f"{prefix}:{_attribute_key(prop_name)}={prop_value}")
    return "|".join(parts)


def _get_cached_market_result(cache_key, now):
    with _market_cache_lock:
        stale_keys = [
            key
            for key, entry in _market_price_cache.items()
            if now - float(entry.get("timestamp") or 0) >= MARKET_CACHE_DURATION
        ]
        for key in stale_keys:
            _market_price_cache.pop(key, None)
        cached = _market_price_cache.get(cache_key)
        return dict(cached["data"]) if cached else None


def _cache_market_result(cache_key, result, now):
    with _market_cache_lock:
        _market_price_cache[cache_key] = {"timestamp": now, "data": dict(result)}
        while len(_market_price_cache) > MARKET_CACHE_MAX_ENTRIES:
            oldest_key = min(
                _market_price_cache,
                key=lambda key: float(_market_price_cache[key].get("timestamp") or 0),
            )
            _market_price_cache.pop(oldest_key, None)


def build_price_check_params(item_name, rarity="", pp=None, sp=None, api_key=None):
    params = {"item": item_name}

    normalized_rarity = normalize_rarity(rarity)
    if normalized_rarity:
        params["rarity"] = normalized_rarity

    for prop_name, prop_value in pp or []:
        params[f"primary[{_attribute_key(prop_name)}]"] = prop_value
    for prop_name, prop_value in sp or []:
        params[f"secondary[{_attribute_key(prop_name)}]"] = prop_value

    return params


def build_v2_price_check_params(item_id, pp=None, sp=None):
    """Build the current exact-price-check query without leaking credentials."""
    params = {"item_id": to_darkerdb_item_id(item_id)}
    for props in (pp, sp):
        for prop_name, prop_value in props or []:
            if prop_value is None or prop_value == "":
                continue
            params[f"attributes[{_attribute_key(prop_name)}]"] = prop_value
    return params


def darkerdb_headers(api_key=None):
    headers = {
        "User-Agent": USER_AGENT,
        "X-API-Version": DARKERDB_API_VERSION,
    }
    key = api_key if api_key is not None else get_darkerdb_api_key()
    if key:
        headers["X-Api-Key"] = key
    return headers


def _rate_limit_from_headers(headers):
    return {
        "limit": headers.get("X-RateLimit-Limit"),
        "remaining": headers.get("X-RateLimit-Remaining"),
        "reset": headers.get("X-RateLimit-Reset"),
        "retry_after": headers.get("Retry-After"),
    }


def _parse_timestamp(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _freshness(updated_at, fetched_at):
    observed = _parse_timestamp(updated_at)
    if observed is None:
        return "unknown"
    age_seconds = max(0, fetched_at - observed.timestamp())
    if age_seconds <= 900:
        return "fresh"
    if age_seconds <= 86400:
        return "recent"
    return "stale"


def _confidence(sample_count):
    count = int(sample_count or 0)
    if count >= 12:
        return "high"
    if count >= 4:
        return "medium"
    if count >= 1:
        return "low"
    return "none"


def normalize_price_check_response(payload, *, item_name, rarity="", fetched_at=None, rate_limit=None):
    fetched = fetched_at if fetched_at is not None else time.time()
    body = (payload or {}).get("body") or {}
    market_price = body.get("market_price")
    num_sold = body.get("num_similar_sold_recently", body.get("num_listings", 0)) or 0
    has_data = market_price is not None
    rounded_price = round(float(market_price)) if has_data else None
    updated_at = body.get("updated_at") or body.get("last_updated") or body.get("observed_at")

    return {
        "success": True,
        "status": "ready",
        "has_data": has_data,
        "item_name": item_name,
        "rarity": normalize_rarity(rarity),
        "avg_price": rounded_price,
        "min_price": rounded_price,
        "max_price": rounded_price,
        "recent_price": rounded_price,
        "num_listings": int(num_sold),
        "confidence": _confidence(num_sold),
        "freshness": _freshness(updated_at, fetched),
        "updated_at": updated_at,
        "fetched_at": datetime.fromtimestamp(fetched, tz=timezone.utc).isoformat(),
        "quality": body.get("quality"),
        "relative_quality": body.get("relative_quality"),
        "source": "DarkerDB",
        "rate_limit": rate_limit or {},
        "cache": "miss",
    }


def normalize_v2_price_check_response(payload, *, item_name, rarity="", fetched_at=None, rate_limit=None):
    """Map DarkerDB's v2 exact valuation onto the desktop UI contract."""
    fetched = fetched_at if fetched_at is not None else time.time()
    envelope = payload if isinstance(payload, dict) else {}
    body = envelope.get("body") if isinstance(envelope.get("body"), dict) else {}
    valuation = body.get("valuation") if isinstance(body.get("valuation"), dict) else {}
    market = body.get("market") if isinstance(body.get("market"), dict) else {}
    item = body.get("item") if isinstance(body.get("item"), dict) else {}
    similar_sales = body.get("similar_sales") if isinstance(body.get("similar_sales"), list) else []
    similar_listings = body.get("similar_listings") if isinstance(body.get("similar_listings"), list) else []

    fair_value = valuation.get("fair_value")
    quick_list = valuation.get("quick_list")
    lowest_ask = valuation.get("lowest_ask")
    has_data = any(value is not None for value in (fair_value, quick_list, lowest_ask))

    def rounded(value):
        try:
            return round(float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    sample_count = len(similar_sales)
    if sample_count == 0:
        sample_count = int(market.get("sales_30d") or market.get("inferred_sales_30d") or 0)
    updated_at = envelope.get("timestamp")
    confidence = str(valuation.get("confidence") or _confidence(sample_count)).lower()

    return {
        "success": True,
        "status": "ready",
        "has_data": has_data,
        "item_name": item.get("name") or item_name,
        "item_id": item.get("item_id"),
        "rarity": normalize_rarity(item.get("rarity") or rarity),
        "avg_price": rounded(fair_value),
        "min_price": rounded(valuation.get("low")),
        "max_price": rounded(valuation.get("high")),
        "recent_price": rounded(quick_list if quick_list is not None else lowest_ask),
        "lowest_ask": rounded(lowest_ask),
        "num_listings": sample_count,
        "active_listings": int(market.get("active_listings") or len(similar_listings)),
        "confidence": confidence,
        "freshness": _freshness(updated_at, fetched),
        "updated_at": updated_at,
        "fetched_at": datetime.fromtimestamp(fetched, tz=timezone.utc).isoformat(),
        "quality": None,
        "relative_quality": None,
        "selection": body.get("selection") or {},
        "source": "DarkerDB price checks",
        "rate_limit": rate_limit or {},
        "cache": "miss",
    }


def _missing_key_result(item_name=None, rarity=""):
    return {
        "success": False,
        "status": "disabled",
        "has_data": False,
        "error_code": "missing_api_key",
        "error": "DarkerDB API key is not configured.",
        "item_name": item_name,
        "rarity": normalize_rarity(rarity),
    }


def _camel_to_snake(value):
    value = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(value or ""))
    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"[^a-zA-Z0-9_]", "", value)
    return value.lower().strip("_")


def to_darkerdb_item_id(item_id):
    text = str(item_id or "").strip()
    if not text:
        return ""
    if text.startswith("id.item."):
        return text
    return f"id.item.{_camel_to_snake(text)}"


def to_darkerdb_archetype(item_id=None, archetype=None):
    text = str(archetype or "").strip()
    if text.startswith("id.item."):
        return text
    if not text and item_id:
        text = str(item_id).split("_", 1)[0]
    return f"id.item.{_camel_to_snake(text)}" if text else ""


def normalize_market_listings_response(payload, *, item_name, rarity="", fetched_at=None, rate_limit=None):
    fetched = fetched_at if fetched_at is not None else time.time()
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), list):
        return _request_error_result(
            item_name,
            rarity,
            error_code="invalid_response",
            message="DarkerDB returned an invalid market response.",
            rate_limit=rate_limit,
        )
    listings = payload["body"]
    price_rows = []
    for row in listings:
        if not isinstance(row, dict):
            continue
        try:
            price = float(row.get("price_per_unit") or row.get("price") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if price > 0:
            price_rows.append((price, row))
    filtered_rows, outliers_filtered = filter_market_price_outliers(price_rows)
    prices = [price for price, _row in filtered_rows]
    has_data = bool(prices)
    avg_price = round(sum(prices) / len(prices)) if prices else None
    latest = None
    latest_price = None
    for _price, row in filtered_rows:
        candidate = row.get("created_at") or row.get("updated_at")
        if candidate and (latest is None or candidate > latest):
            latest = candidate
            latest_price = _price

    return {
        "success": True,
        "status": "ready",
        "has_data": has_data,
        "item_name": item_name,
        "rarity": normalize_rarity(rarity),
        "avg_price": avg_price,
        "min_price": round(min(prices)) if prices else None,
        "max_price": round(max(prices)) if prices else None,
        "recent_price": round(latest_price if latest_price is not None else prices[0]) if prices else None,
        "num_listings": len(prices),
        "raw_num_listings": len(price_rows),
        "outliers_filtered": outliers_filtered,
        "confidence": _confidence(len(prices)),
        "freshness": _freshness(latest, fetched),
        "updated_at": latest,
        "fetched_at": datetime.fromtimestamp(fetched, tz=timezone.utc).isoformat(),
        "quality": None,
        "relative_quality": None,
        "source": "DarkerDB market listings",
        "rate_limit": rate_limit or {},
        "cache": "miss",
    }


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return 0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def filter_market_price_outliers(price_rows):
    if len(price_rows) < 4:
        return price_rows, 0

    prices = [price for price, _row in price_rows]
    median = _median(prices)
    if median <= 0:
        return price_rows, 0

    high_cutoff = max(median * 4, median + 1000)
    filtered = [(price, row) for price, row in price_rows if price <= high_cutoff]

    # Do not wipe out thin data. If the filter is too aggressive, keep the raw data.
    if len(filtered) < max(3, len(price_rows) // 2):
        return price_rows, 0

    return filtered, len(price_rows) - len(filtered)


def _request_error_result(item_name, rarity, status_code=None, error_code="request_failed", message=None, rate_limit=None):
    return {
        "success": False,
        "status": "error",
        "has_data": False,
        "error_code": error_code,
        "error": message or "Unable to reach DarkerDB market API.",
        "http_status": status_code,
        "item_name": item_name,
        "rarity": normalize_rarity(rarity),
        "rate_limit": rate_limit or {},
    }


def summarize_bulk_price_results(results):
    values = [value for value in (results or {}).values() if isinstance(value, dict)]
    unique_values = []
    seen = set()
    for value in values:
        key = value.get("cache_key") or value.get("simple_key") or id(value)
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(value)

    if not unique_values:
        return {"success": True, "status": "empty"}

    if all(value.get("error_code") == "missing_api_key" for value in unique_values):
        return {
            "success": False,
            "status": "disabled",
            "error_code": "missing_api_key",
            "error": "DarkerDB API key is not configured.",
        }

    if any(value.get("error_code") == "rate_limited" for value in unique_values):
        return {
            "success": False,
            "status": "rate_limited",
            "error_code": "rate_limited",
            "error": "DarkerDB rate limit reached. Try again after the reset window.",
        }

    if any(value.get("success") for value in unique_values):
        return {"success": True, "status": "ready"}

    return {
        "success": False,
        "status": "error",
        "error_code": "request_failed",
        "error": "Unable to reach DarkerDB market data.",
    }


def fetch_price_check(item_name, rarity="", pp=None, sp=None, *, item_id=None, archetype=None, session=None, timeout=10):
    """Value an exact roll via v2 price-checks, with a listings fallback."""
    try:
        pp = normalize_market_properties(pp)
        sp = normalize_market_properties(sp)
    except ValueError as exc:
        return _request_error_result(
            item_name,
            rarity,
            error_code="invalid_request",
            message=str(exc),
        )
    api_key = get_darkerdb_api_key()
    if not api_key:
        return _missing_key_result(item_name, rarity)

    normalized_rarity = normalize_rarity(rarity)
    cache_key = build_cache_key(
        item_name,
        normalized_rarity,
        pp,
        sp,
        item_id=item_id,
        archetype=archetype,
    )
    now = time.time()
    cached = _get_cached_market_result(cache_key, now)
    if cached:
        result = cached
        result["cache"] = "hit"
        return result

    result = None
    darkerdb_item_id = to_darkerdb_item_id(item_id)
    if darkerdb_item_id:
        client = session or requests
        try:
            response = client.get(
                f"{DARKERDB_BASE_URL}{PRICE_CHECK_PATH}",
                params=build_v2_price_check_params(darkerdb_item_id, pp=pp, sp=sp),
                headers=darkerdb_headers(api_key),
                timeout=timeout,
            )
            rate_limit = _rate_limit_from_headers(response.headers)
            if response.ok:
                try:
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("body"), dict):
                        raise ValueError("invalid v2 price-check envelope")
                    result = normalize_v2_price_check_response(
                        payload,
                        item_name=item_name,
                        rarity=normalized_rarity,
                        fetched_at=now,
                        rate_limit=rate_limit,
                    )
                except (TypeError, ValueError, OverflowError):
                    result = _request_error_result(
                        item_name,
                        normalized_rarity,
                        response.status_code,
                        error_code="invalid_response",
                        message="DarkerDB returned an invalid price-check response.",
                        rate_limit=rate_limit,
                    )
            elif response.status_code not in {400, 404, 422}:
                result = _request_error_result(
                    item_name,
                    normalized_rarity,
                    response.status_code,
                    error_code="rate_limited" if response.status_code == 429 else "request_failed",
                    rate_limit=rate_limit,
                )
        except requests.RequestException:
            result = _request_error_result(item_name, normalized_rarity)

    # Some legacy captures have no concrete variant id, and unknown legacy
    # roll names may make an exact quote unavailable. Keep the supported v2
    # listings estimate as a useful fallback for only those cases.
    if result is None:
        result = fetch_market_listing_estimate(
            item_name=item_name,
            rarity=normalized_rarity,
            item_id=item_id,
            archetype=archetype,
            pp=pp,
            sp=sp,
            session=session,
            timeout=timeout,
        )
    if result.get("success"):
        _cache_market_result(cache_key, result, now)
    return result


def fetch_market_listing_estimate(
    *,
    item_name,
    rarity="",
    item_id=None,
    archetype=None,
    pp=None,
    sp=None,
    session=None,
    timeout=10,
):
    api_key = get_darkerdb_api_key()
    if not api_key:
        return _missing_key_result(item_name, rarity)

    darkerdb_item_id = to_darkerdb_item_id(item_id)
    darkerdb_archetype = to_darkerdb_archetype(item_id=item_id, archetype=archetype)
    if not darkerdb_item_id and not darkerdb_archetype:
        return _request_error_result(item_name, rarity, 404, message="No DarkerDB item id available for market lookup.")

    client = session or requests
    params = {"limit": 50, "listing_state": "sold"}
    if darkerdb_item_id:
        params["item_id"] = darkerdb_item_id
    elif darkerdb_archetype:
        params["archetype"] = darkerdb_archetype
    if rarity:
        params["rarity"] = normalize_rarity(rarity).lower()
    for prop_name, prop_value in pp or []:
        params[f"primary[{_attribute_key(prop_name)}]"] = prop_value
    for prop_name, prop_value in sp or []:
        params[f"secondary[{_attribute_key(prop_name)}]"] = prop_value

    try:
        response = client.get(
            f"{DARKERDB_BASE_URL}{MARKET_LISTINGS_PATH}",
            params=params,
            headers=darkerdb_headers(api_key),
            timeout=timeout,
        )
    except requests.RequestException:
        return _request_error_result(item_name, rarity)
    rate_limit = _rate_limit_from_headers(response.headers)
    if not response.ok:
        return _request_error_result(
            item_name,
            rarity,
            response.status_code,
            error_code="rate_limited" if response.status_code == 429 else "request_failed",
            rate_limit=rate_limit,
        )

    try:
        payload = response.json()
    except (TypeError, ValueError):
        return _request_error_result(
            item_name,
            rarity,
            response.status_code,
            error_code="invalid_response",
            message="DarkerDB returned an invalid market response.",
            rate_limit=rate_limit,
        )
    return normalize_market_listings_response(
        payload,
        item_name=item_name,
        rarity=rarity,
        fetched_at=time.time(),
        rate_limit=rate_limit,
    )


def fetch_market_listings(
    *,
    item_id=None,
    archetype=None,
    rarity=None,
    limit=10,
    has_sold=True,
    price=None,
    session=None,
    timeout=10,
):
    api_key = get_darkerdb_api_key()
    if not api_key:
        return _missing_key_result()

    try:
        normalized_limit = max(1, min(int(limit or 10), 50))
    except (TypeError, ValueError, OverflowError):
        normalized_limit = 10
    params = {"limit": normalized_limit}
    if has_sold:
        params["listing_state"] = "sold"
    if item_id:
        params["item_id"] = item_id
    if archetype:
        params["archetype"] = archetype
    if rarity:
        params["rarity"] = normalize_rarity(rarity).lower()
    if price:
        params["price"] = price

    client = session or requests
    try:
        response = client.get(
            f"{DARKERDB_BASE_URL}{MARKET_LISTINGS_PATH}",
            params=params,
            headers=darkerdb_headers(api_key),
            timeout=timeout,
        )
    except requests.RequestException:
        return _request_error_result(item_id or archetype, rarity)
    rate_limit = _rate_limit_from_headers(response.headers)
    if not response.ok:
        return _request_error_result(
            item_id or archetype,
            rarity,
            response.status_code,
            error_code="rate_limited" if response.status_code == 429 else "request_failed",
            rate_limit=rate_limit,
        )

    try:
        payload = response.json()
    except (TypeError, ValueError):
        return _request_error_result(
            item_id or archetype,
            rarity,
            response.status_code,
            error_code="invalid_response",
            message="DarkerDB returned an invalid market response.",
            rate_limit=rate_limit,
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), list):
        return _request_error_result(
            item_id or archetype,
            rarity,
            response.status_code,
            error_code="invalid_response",
            message="DarkerDB returned an invalid market response.",
            rate_limit=rate_limit,
        )
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        pagination = {}
    return {
        "success": True,
        "status": "ready",
        "listings": payload["body"],
        "pagination": pagination,
        "rate_limit": rate_limit,
        "source": "DarkerDB",
    }
