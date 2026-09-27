"""market_model.json: what pricing learns from the market pattern analysis.

Written by the Market page's "Analyze market data" and scripts/market_patterns_report.py,
read by pricing. Saved atomically so pricing never reads half a file.
"""
import contextlib
import json
import os
import tempfile
import time

MODEL_KEYS = ("stat_premiums", "good_roll_counts", "extra_good_roll_factor", "roll_ranges", "pair_synergies")
MIN_PAIR_SUPPORT = 10   # listings carrying a stat pair before its bonus is trusted for pricing
REPLACE_ATTEMPTS = 5    # Windows refuses to replace a file someone is reading: retry briefly
REPLACE_RETRY_S = 0.1
PAIR_SEPARATOR = " + "


def model_from_report(report: dict) -> dict:
    return {key: report[key] for key in MODEL_KEYS if key in report}


def save_model(path: str, model: dict) -> None:
    """Write through a temporary file and an atomic rename."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".market_model.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(model, fh, indent=1)
        _replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _replace(source, target):
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_S * (attempt + 1))


def load_model(path: str) -> dict:
    """The saved model, or {} when there is none (or it can't be read)."""
    try:
        with open(path, encoding="utf-8") as fh:
            model = json.load(fh)
    except (OSError, ValueError):
        return {}
    return model if isinstance(model, dict) else {}


def pair_bonuses(model: dict, min_support: int = MIN_PAIR_SUPPORT) -> dict:
    """{frozenset({stat_a, stat_b}): percent} for pairs that sell for more together."""
    bonuses = {}
    for entry in model.get("pair_synergies") or []:
        try:
            first, second = str(entry["pair"]).split(PAIR_SEPARATOR)
            percent, support = float(entry["synergy"]), int(entry["support"])
        except (KeyError, TypeError, ValueError):
            continue
        if support >= min_support and percent > 0:
            bonuses[frozenset({first.strip(), second.strip()})] = percent
    return bonuses


def extra_roll_share(model: dict, default: float) -> float:
    """Learned share of an extra good roll's premium (0..1), else `default`."""
    try:
        return min(max(float(model["extra_good_roll_factor"]), 0.0), 1.0)
    except (KeyError, TypeError, ValueError):
        return default
