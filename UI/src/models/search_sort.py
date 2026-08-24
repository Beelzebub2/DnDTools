"""Stable ordering helpers for item-search API results."""

from __future__ import annotations

from typing import Any, Mapping

from .item import Item


def _numeric_then_text(value: Any) -> tuple[int, Any]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value or "").casefold())


def search_result_sort_key(result: Mapping[str, Any]) -> tuple:
    """Return a deterministic, user-facing key for a stash search result."""

    item = result.get("item") or {}
    return (
        str(item.get("name") or "").casefold(),
        -Item._rarity_rank(item.get("rarity")),
        tuple(
            (str(name).casefold(), str(value).casefold())
            for name, value in (item.get("pp") or [])
        ),
        tuple(
            (str(name).casefold(), str(value).casefold())
            for name, value in (item.get("sp") or [])
        ),
        str(result.get("nickname") or "").casefold(),
        str(result.get("id") or "").casefold(),
        _numeric_then_text(result.get("stash_id")),
        _numeric_then_text(result.get("slotId")),
    )


__all__ = ["search_result_sort_key"]
