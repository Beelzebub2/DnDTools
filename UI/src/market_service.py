import os
import re
import time
from datetime import datetime, timezone

import requests


DARKERDB_BASE_URL = "https://api.darkerdb.com"
PRICE_CHECK_PATH = "/v1/price-check"
MARKET_LISTINGS_PATH = "/v2/market"
MARKET_CACHE_DURATION = 300
USER_AGENT = "DnDTools-MarketProxy/1.1"

_market_price_cache = {}

_RARITY_NORMALIZE = {
    "legend": "Legendary",
}


def clear_market_cache():
    _market_price_cache.clear()


def get_darkerdb_api_key():
    for name in ("DARKERDB_API_KEY", "DNDTOOLS_DARKERDB_API_KEY", "API_KEY"):
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


def build_cache_key(item_name, rarity, pp=None, sp=None):
    parts = [str(item_name or ""), normalize_rarity(rarity)]
    for prefix, props in (("p", pp), ("s", sp)):
        for prop_name, prop_value in sorted(props or [], key=lambda x: str(x[0])):
            parts.append(f"{prefix}:{_attribute_key(prop_name)}={prop_value}")
    return "|".join(parts)


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


def darkerdb_headers(api_key=None):
    headers = {"User-Agent": USER_AGENT}
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
    listings = (payload or {}).get("body") or []
    price_rows = []
    for row in listings:
        price = float(row.get("price_per_unit") or row.get("price") or 0)
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
    api_key = get_darkerdb_api_key()
    if not api_key:
        return _missing_key_result(item_name, rarity)

    normalized_rarity = normalize_rarity(rarity)
    cache_key = build_cache_key(item_name, normalized_rarity, pp, sp)
    now = time.time()
    cached = _market_price_cache.get(cache_key)
    if cached and now - cached["timestamp"] < MARKET_CACHE_DURATION:
        result = dict(cached["data"])
        result["cache"] = "hit"
        return result

    client = session or requests
    params = build_price_check_params(item_name, normalized_rarity, pp, sp, api_key=api_key)
    url = f"{DARKERDB_BASE_URL}{PRICE_CHECK_PATH}"
    response = client.get(url, params=params, headers=darkerdb_headers(api_key), timeout=timeout)
    rate_limit = _rate_limit_from_headers(response.headers)
    if not response.ok:
        if response.status_code not in {404, 422}:
            error_code = "rate_limited" if response.status_code == 429 else "request_failed"
            return _request_error_result(
                item_name,
                normalized_rarity,
                response.status_code,
                error_code=error_code,
                rate_limit=rate_limit,
            )

        listing_result = fetch_market_listing_estimate(
            item_name=item_name,
            rarity=normalized_rarity,
            item_id=item_id,
            archetype=archetype,
            session=client,
            timeout=timeout,
        )
        _market_price_cache[cache_key] = {"timestamp": now, "data": listing_result}
        return listing_result

    result = normalize_price_check_response(
        response.json(),
        item_name=item_name,
        rarity=normalized_rarity,
        fetched_at=now,
        rate_limit=rate_limit,
    )
    _market_price_cache[cache_key] = {"timestamp": now, "data": result}
    return result


def fetch_market_listing_estimate(*, item_name, rarity="", item_id=None, archetype=None, session=None, timeout=10):
    api_key = get_darkerdb_api_key()
    if not api_key:
        return _missing_key_result(item_name, rarity)

    darkerdb_item_id = to_darkerdb_item_id(item_id)
    darkerdb_archetype = to_darkerdb_archetype(item_id=item_id, archetype=archetype)
    if not darkerdb_item_id and not darkerdb_archetype:
        return _request_error_result(item_name, rarity, 404, message="No DarkerDB item id available for market lookup.")

    client = session or requests
    params = {"limit": 8, "has_sold": True}
    if darkerdb_item_id:
        params["item_id"] = darkerdb_item_id
    elif darkerdb_archetype:
        params["archetype"] = darkerdb_archetype
    if rarity:
        params["rarity"] = normalize_rarity(rarity).lower()

    response = client.get(
        f"{DARKERDB_BASE_URL}{MARKET_LISTINGS_PATH}",
        params=params,
        headers=darkerdb_headers(api_key),
        timeout=timeout,
    )
    rate_limit = _rate_limit_from_headers(response.headers)
    if not response.ok:
        return _request_error_result(
            item_name,
            rarity,
            response.status_code,
            error_code="rate_limited" if response.status_code == 429 else "request_failed",
            rate_limit=rate_limit,
        )

    return normalize_market_listings_response(
        response.json(),
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

    params = {"limit": max(1, min(int(limit or 10), 50)), "has_sold": has_sold}
    if item_id:
        params["item_id"] = item_id
    if archetype:
        params["archetype"] = archetype
    if rarity:
        params["rarity"] = normalize_rarity(rarity)
    if price:
        params["price"] = price

    client = session or requests
    response = client.get(
        f"{DARKERDB_BASE_URL}{MARKET_LISTINGS_PATH}",
        params=params,
        headers=darkerdb_headers(api_key),
        timeout=timeout,
    )
    rate_limit = _rate_limit_from_headers(response.headers)
    if not response.ok:
        return _request_error_result(
            item_id or archetype,
            rarity,
            response.status_code,
            error_code="rate_limited" if response.status_code == 429 else "request_failed",
            rate_limit=rate_limit,
        )

    payload = response.json() or {}
    return {
        "success": True,
        "status": "ready",
        "listings": payload.get("body") or [],
        "pagination": payload.get("pagination") or {},
        "rate_limit": rate_limit,
        "source": "DarkerDB",
    }
