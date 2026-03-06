from typing import Dict, Optional, Set, Iterable

LOOT_STATE_VALUE_TO_LABEL: Dict[int, str] = {
    0: "None",
    1: "Supplied",
    2: "Looted",
    3: "Handled",
    4: "Crafted",
    5: "Ally",
}

_LOOT_STATE_ALIAS_MAP: Dict[str, int] = {}
for _value, _label in LOOT_STATE_VALUE_TO_LABEL.items():
    _normalized_label = _label.lower()
    _LOOT_STATE_ALIAS_MAP[str(_value)] = _value
    _LOOT_STATE_ALIAS_MAP[_normalized_label] = _value
    _LOOT_STATE_ALIAS_MAP[_normalized_label.replace(" ", "")] = _value

_LOOT_STATE_ALIAS_MAP.update(
    {
        "none": 0,
        "none_source": 0,
        "nonesource": 0,
        "supplies": 1,
        "loot": 2,
        "handled": 3,
        "handle": 3,
        "craft": 4,
        "crafted": 4,
        "ally": 5,
    }
)


def _normalize_loot_state_key(raw: str) -> str:
    cleaned = raw.lower().strip()
    if not cleaned:
        return ""
    cleaned = cleaned.replace("-", " ").replace("_", " ")
    cleaned = cleaned.replace("only", "")
    return " ".join(part for part in cleaned.split() if part)


def _parse_single_loot_state(raw) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    key = _normalize_loot_state_key(text)
    if not key or key in {"any", "all", "*"}:
        return None
    return _LOOT_STATE_ALIAS_MAP.get(key)


def extract_loot_state_filter(raw) -> Optional[Set[int]]:
    """Return a set of acceptable loot state values or None when unrestricted."""

    if raw is None:
        return None

    if isinstance(raw, (list, tuple, set)):
        collected: Set[int] = set()
        for entry in raw:
            values = extract_loot_state_filter(entry)
            if values is None:
                return None
            collected.update(values)
        return collected or None

    value = _parse_single_loot_state(raw)
    if value is None:
        return None
    return {value}


def format_loot_state_label(value: int) -> str:
    return LOOT_STATE_VALUE_TO_LABEL.get(value, str(value))


def collect_item_loot_state_requirements(
    quests: Iterable[dict],
    item_filter: Optional[Set[str]] = None,
) -> Dict[str, Optional[Set[int]]]:
    """Map item ids to acceptable loot state values based on quest objectives."""

    filtered_items = set(item_filter) if item_filter else None
    requirements: Dict[str, Optional[Set[int]]] = {}

    for quest in quests or []:
        objectives = quest.get("objectives") or []
        for objective in objectives:
            if not isinstance(objective, dict):
                continue

            item_id = objective.get("item_id")
            if not item_id:
                continue
            if filtered_items is not None and item_id not in filtered_items:
                continue

            values = extract_loot_state_filter(
                objective.get("loot_state") or objective.get("lootState")
            )

            if values is None:
                requirements[item_id] = None
                continue

            if not values:
                continue

            current = requirements.get(item_id)
            if current is None and item_id in requirements:
                continue
            if current is None:
                requirements[item_id] = set(values)
            else:
                current.update(values)

    return requirements
