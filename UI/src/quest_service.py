"""Quest-related data access and caching utilities for the UI server."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

import requests

from src.models.appdirs import get_quests_dir, resource_path


RARITY_ORDER = {
    "Poor": 0,
    "Common": 1,
    "Uncommon": 2,
    "Rare": 3,
    "Epic": 4,
    "Legendary": 5,
    "Unique": 6,
    "Mythic": 7,
    "Artifact": 8,
}

DARKERDB_QUESTS_API_URL = "https://api.darkerdb.com/v2/quests"
DARKERDB_API_VERSION = "2026-08-03"
DARKERDB_API_KEY_ENV_NAMES = (
    "DARKERDB_API_KEY",
    "DNDTOOLS_DARKERDB_API_KEY",
)

# Compatibility for the last v1-generated items.json shipped before DarkerDB
# renamed these v2 archetypes. Native ``darkerdb_archetype`` metadata always
# takes precedence once the item snapshot has refreshed.
ITEM_ARCHETYPE_FALLBACKS = {
    "PearlShining": ("ShiningPearl",),
    "PearlBlemished": ("BlemishedPearl",),
    "LuckPotionSmall": ("LuckPotion_3001",),
    "LuckPotionLarge": ("LuckPotion_4001", "LuckPotion_5001"),
    "BrimstoneOres": ("Brimstone",),
}

_QUEST_ID_MODIFIERS = (
    "daily_equipment",
    "seasonal",
    "weekly",
    "daily",
    "final",
    "tuto",
    "extra",
)


class QuestServiceError(RuntimeError):
    """Base class for actionable quest-catalog failures."""


class QuestAuthenticationError(QuestServiceError):
    """Raised when DarkerDB credentials are missing or insufficient."""


class QuestPayloadError(QuestServiceError):
    """Raised when the upstream response cannot safely replace the cache."""


def get_darkerdb_api_key() -> str:
    """Resolve a DarkerDB key without ever placing it in a request URL."""
    for name in DARKERDB_API_KEY_ENV_NAMES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _pascalize_slug(value: object) -> str:
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", str(value or "")) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _humanize_canonical_id(value: object, prefix: str = "") -> str:
    text = str(value or "").strip()
    if prefix and text.lower().startswith(prefix.lower()):
        text = text[len(prefix):]
    text = re.sub(r"_\d+$", "", text)
    return " ".join(part.capitalize() for part in text.split("_") if part)


def _canonical_item_to_game_id(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.lower().startswith("id.item."):
        return text

    slug = text[len("id.item."):]
    parts = [part for part in slug.split("_") if part]
    numeric_suffix = parts.pop() if parts and parts[-1].isdigit() else None
    game_id = _pascalize_slug("_".join(parts))
    if numeric_suffix:
        game_id = f"{game_id}_{numeric_suffix}"
    return game_id or None


def _canonical_quest_to_game_id(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.lower().startswith("id.quest."):
        return text

    slug = text[len("id.quest."):]
    match = re.match(r"^(?P<stem>.+)_(?P<number>\d+)$", slug)
    if not match:
        return _pascalize_slug(slug) or None

    stem = match.group("stem")
    number = match.group("number")
    modifier = ""
    for candidate in _QUEST_ID_MODIFIERS:
        suffix = f"_{candidate}"
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            modifier = "_".join(_pascalize_slug(part) for part in candidate.split("_"))
            break

    result = _pascalize_slug(stem)
    if modifier:
        result = f"{result}_{modifier}"
    return f"{result}_{number}" if result else None


def _merchant_from_quest_id(value: object) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("id.quest."):
        text = text[len("id.quest."):]
    text = re.sub(r"_\d+$", "", text)
    return " ".join(part.capitalize() for part in text.split("_") if part)


def _random_reward_metadata(value: object) -> dict[str, str]:
    slug = str(value or "").lower()
    match = re.search(r"quest_([a-z_]+?)_(poor|common|uncommon|rare|epic|legendary|unique|mythic|artifact)(?:_|$)", slug)
    if not match:
        return {}
    return {
        "item_type": " ".join(part.capitalize() for part in match.group(1).split("_")),
        "rarity": match.group(2).capitalize(),
    }


def normalize_darkerdb_v2_quest(raw: dict) -> dict:
    """Translate a DarkerDB v2 quest row to the UI's established contract."""
    if not isinstance(raw, dict):
        raise QuestPayloadError("DarkerDB returned a non-object quest row")

    raw_id = raw.get("id")
    is_v2 = str(raw_id or "").lower().startswith("id.quest.") or any(
        isinstance(entry, dict) and "content_type" in entry
        for entry in (raw.get("objectives") or [])
    )
    if not is_v2:
        return dict(raw)

    quest_id = _canonical_quest_to_game_id(raw_id)
    merchant = _merchant_from_quest_id(raw_id)
    objectives: list[dict] = []
    dungeon_names: list[str] = []

    for entry in raw.get("objectives") or []:
        if not isinstance(entry, dict):
            continue
        content_type = str(entry.get("content_type") or "Objective").strip()
        objective_type = " ".join(part.capitalize() for part in content_type.split("_"))
        objective: dict = {
            "type": objective_type,
            "count": entry.get("content_count"),
        }

        target = entry.get("target_archetype")
        target_kind = str(entry.get("target_kind") or "").lower()
        if target_kind == "item":
            objective["item_id"] = _canonical_item_to_game_id(target)
        elif target_kind == "monster":
            objective["monster"] = _humanize_canonical_id(target, "id.monster.")
        elif target_kind == "module":
            objective["module"] = _humanize_canonical_id(target, "id.module.")
        elif target_kind == "props":
            objective["interact"] = _humanize_canonical_id(target, "id.props.")

        if objective_type == "Props" and not objective.get("interact"):
            content_name = _humanize_canonical_id(entry.get("id"), "id.quest_content.")
            objective["interact"] = re.sub(r"^(Open|Interact|Use)\s+", "", content_name)

        for field in ("rarity", "item_type"):
            if entry.get(field) is not None:
                objective[field] = " ".join(
                    part.capitalize()
                    for part in str(entry.get(field)).replace("-", "_").split("_")
                    if part
                )
        for field in ("icon", "icon_url"):
            if entry.get(field) is not None:
                objective[field] = entry.get(field)

        dungeon_tags = entry.get("dungeon_tags") or []
        if isinstance(dungeon_tags, list):
            for dungeon_tag in dungeon_tags:
                dungeon_name = _humanize_canonical_id(dungeon_tag, "id.dungeon.")
                if dungeon_name and dungeon_name not in dungeon_names:
                    dungeon_names.append(dungeon_name)

        objectives.append(objective)

    rewards: list[dict] = []
    for reward_group in raw.get("rewards") or []:
        if not isinstance(reward_group, dict):
            continue
        entries = reward_group.get("entries")
        if not isinstance(entries, list):
            # Be liberal for proxies that already flatten v2 reward rows.
            entries = [reward_group]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            reward_type_raw = str(entry.get("type") or "Reward")
            reward: dict = {
                "type": " ".join(part.capitalize() for part in reward_type_raw.split("_")),
                "count": entry.get("count"),
            }
            if entry.get("item_id"):
                reward["item_id"] = _canonical_item_to_game_id(entry.get("item_id"))
            if entry.get("merchant_id"):
                reward["merchant"] = _humanize_canonical_id(entry.get("merchant_id"), "id.merchant.")
            if reward_type_raw.lower() == "random":
                reward.update(_random_reward_metadata(entry.get("random_reward_id")))
            for field in ("random_reward_id", "item_skin_id", "emote_id", "action_skin_id", "icon", "icon_url"):
                if entry.get(field) is not None:
                    reward[field] = entry.get(field)
            rewards.append(reward)

    prerequisite = _canonical_quest_to_game_id(raw.get("chapter_id"))
    result = {
        "id": quest_id,
        "source_id": raw_id,
        "title": raw.get("title") or quest_id or "Unknown Quest",
        "chapter": raw.get("chapter_title"),
        "chapter_id": prerequisite,
        "prerequisite": prerequisite,
        "dungeons": dungeon_names,
        "merchant": merchant,
        "text": raw.get("description"),
        "completion_text": raw.get("completion_text"),
        "objectives": objectives,
        "rewards": rewards,
        "order": raw.get("order"),
        "is_repeatable": bool(raw.get("is_repeatable")),
        "is_daily": bool(raw.get("is_daily")),
        "patch": raw.get("patch"),
        "season": raw.get("season"),
    }
    return result


def dedupe_quests(quests: list[dict]) -> list[dict]:
    """Deduplicate paginated/cache rows by stable id while preserving order."""
    deduped: list[dict] = []
    positions: dict[str, int] = {}

    def richness(quest: dict) -> int:
        return (
            len(quest.get("objectives") or []) * 4
            + len(quest.get("rewards") or []) * 2
            + int(bool(quest.get("title")))
            + int(bool(quest.get("text") or quest.get("description")))
        )

    for raw in quests:
        if not isinstance(raw, dict):
            continue
        quest = dict(raw)
        quest_id = str(quest.get("id") or "").strip()
        if not quest_id:
            deduped.append(quest)
            continue
        if quest_id not in positions:
            positions[quest_id] = len(deduped)
            deduped.append(quest)
            continue
        existing_index = positions[quest_id]
        if richness(quest) > richness(deduped[existing_index]):
            deduped[existing_index] = quest
    return deduped


def fetch_darkerdb_quests(
    *,
    api_key: str = "",
    api_url: str = DARKERDB_QUESTS_API_URL,
    session=None,
    timeout: float = 15,
    page_size: int = 200,
    max_pages: int = 100,
) -> tuple[list[dict], dict]:
    """Fetch, validate, paginate, deduplicate, and normalize the v2 catalog."""
    url = str(api_url or DARKERDB_QUESTS_API_URL).strip()
    parsed_url = urlsplit(url)
    is_official_v2 = parsed_url.hostname == "api.darkerdb.com" and parsed_url.path.startswith("/v2/")
    if is_official_v2 and not api_key:
        raise QuestAuthenticationError(
            "DarkerDB v2 quests require an API key with the darkerdb.data scope. "
            "Set DARKERDB_API_KEY or configure DND_QUESTS_API_URL to a trusted first-party proxy."
        )

    client = session or requests
    headers = {
        "User-Agent": "DnDTools-QuestTracker/2.0",
        "X-API-Version": DARKERDB_API_VERSION,
    }
    if api_key:
        headers["X-Api-Key"] = api_key

    quests: list[dict] = []
    cursor: Optional[str] = None
    seen_cursors: set[str] = set()
    metadata: dict = {"api_version": DARKERDB_API_VERSION}

    for _page_number in range(1, max_pages + 1):
        params: dict[str, object] = {"limit": max(1, min(int(page_size), 200)), "locale": "en"}
        if cursor:
            params["cursor"] = cursor

        response = client.get(url, params=params, headers=headers, timeout=timeout)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == 401:
            raise QuestAuthenticationError(
                "DarkerDB rejected the quest API key. Configure a valid DARKERDB_API_KEY."
            )
        if status_code == 403:
            raise QuestAuthenticationError(
                "The DarkerDB API key does not have the required darkerdb.data scope."
            )
        if status_code == 410:
            raise QuestServiceError(
                "The configured quest endpoint is retired. Use the DarkerDB v2 quests API or a current proxy."
            )
        if status_code == 429:
            retry_after = (getattr(response, "headers", {}) or {}).get("Retry-After")
            suffix = f" Retry after {retry_after} seconds." if retry_after else ""
            raise QuestServiceError(f"DarkerDB quest API rate limit reached.{suffix}")
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            raise QuestPayloadError("DarkerDB returned invalid JSON for quests") from exc
        if not isinstance(payload, dict):
            raise QuestPayloadError("DarkerDB returned an invalid quest response envelope")

        body = payload.get("body")
        if not isinstance(body, list):
            raise QuestPayloadError("DarkerDB quest response body was not a list")
        for row in body:
            quests.append(normalize_darkerdb_v2_quest(row))

        for key in ("version", "build", "patch", "request_id"):
            if payload.get(key) is not None:
                metadata[key] = payload.get(key)

        pagination = payload.get("pagination")
        next_cursor = pagination.get("next") if isinstance(pagination, dict) else None
        if not next_cursor:
            break

        next_text = str(next_cursor).strip()
        if next_text.lower().startswith(("http://", "https://")):
            query = parse_qs(urlsplit(next_text).query)
            next_text = str((query.get("cursor") or [""])[0]).strip()
            if not next_text:
                raise QuestPayloadError("DarkerDB pagination.next URL did not contain a cursor")
        if next_text in seen_cursors:
            raise QuestPayloadError("DarkerDB returned a repeated pagination cursor")
        seen_cursors.add(next_text)
        cursor = next_text
    else:
        raise QuestPayloadError("DarkerDB quest pagination exceeded the safety limit")

    quests = dedupe_quests(quests)
    if not quests:
        raise QuestPayloadError("DarkerDB returned an empty quest catalog")
    metadata["total"] = len(quests)
    return quests, metadata


class QuestService:
    """Encapsulates DarkerDB quest interactions and local persistence."""

    QUESTS_API_URL = DARKERDB_QUESTS_API_URL
    QUESTS_PAGE_SIZE = 200
    QUESTS_CACHE_TTL_SECONDS = 6 * 60 * 60
    QUESTS_SNAPSHOT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
    QUESTS_RETRY_COOLDOWN_SECONDS = 5 * 60
    MERCHANT_EXACT_ALIASES = {
        "goblin merchant final": "Goblin Merchant",
        "huntress daily": "Huntress",
        "huntress daily equipment": "Huntress",
        "huntress seasonal": "Huntress",
        "huntress weekly": "Huntress",
        "krampus daily": "Krampus",
        "krampus seasonal": "Krampus",
        "tavern master final": "Tavern Master",
        "tavern master tuto": "Tavern Master",
        "the collector final": "The Collector",
        "valentine daily": "Valentine",
        "valentine seasonal": "Valentine",
        "weaponsmith extra": "Weaponsmith",
    }
    MERCHANT_PREFIX_ALIASES = {
        "goblin merchant": "Goblin Merchant",
        "huntress": "Huntress",
        "krampus": "Krampus",
        "tavern master": "Tavern Master",
        "the collector": "The Collector",
        "valentine": "Valentine",
        "weaponsmith": "Weaponsmith",
    }

    def __init__(self, logger, *, data_dir: Optional[Path] = None, session=None) -> None:
        self._logger = logger
        self._data_dir = Path(data_dir) if data_dir is not None else Path(get_quests_dir())
        self._cache_file = self._data_dir / "quests_cache.json"
        self._progress_file = self._data_dir / "quests_progress.json"
        self._captured_state_file = self._data_dir / "quests_captured.json"
        self._bundled_snapshot_file = Path(resource_path("quests.json"))
        self._session = session

        self._quests_cache: Optional[list[dict]] = None
        self._quests_cache_timestamp: float = 0.0
        self._next_refresh_timestamp: float = 0.0
        self._last_fetch_status: dict[str, object] = {
            "source": "none",
            "cached": False,
            "stale": False,
            "warning": None,
        }
        self._quests_lock = threading.RLock()
        self._items_index: Optional[dict[str, dict]] = None
        self._items_lock = threading.RLock()
        self._captured_lock = threading.RLock()
        self._progress_lock = threading.RLock()

    # ------------------------------------------------------------------
    # General helpers
    # ------------------------------------------------------------------
    @property
    def protected_filenames(self) -> set[str]:
        """Filenames that shouldn't be deleted when clearing character data."""
        return {
            self._cache_file.name.lower(),
            self._progress_file.name.lower(),
            self._captured_state_file.name.lower(),
        }

    @staticmethod
    def _default_progress_payload() -> dict:
        return {"objectives": {}, "items": {}}

    def default_progress_payload(self) -> dict:
        """Return a fresh default progress payload."""
        return self._default_progress_payload()

    def _normalize_merchant_name(self, name: Optional[str]) -> str:
        if not name:
            return ""

        cleaned = " ".join(str(name).strip().split())
        lowered = cleaned.lower()

        if lowered in self.MERCHANT_EXACT_ALIASES:
            return self.MERCHANT_EXACT_ALIASES[lowered]

        for prefix, canonical in self.MERCHANT_PREFIX_ALIASES.items():
            if lowered.startswith(prefix):
                return canonical

        return cleaned

    # ------------------------------------------------------------------
    # Quest cache handling
    # ------------------------------------------------------------------
    def _read_quest_snapshot(self, path: Path, source: str) -> Optional[tuple[float, list[dict], dict]]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning("Failed to read quest snapshot %s: %s", path, exc, exc_info=True)
            return None

        if not isinstance(payload, dict):
            return None

        timestamp = payload.get("timestamp")
        quests = payload.get("quests")
        if not isinstance(quests, list):
            return None

        try:
            timestamp_value = float(timestamp)
        except (TypeError, ValueError):
            timestamp_value = 0.0

        metadata = {
            "source": source,
            "schema_version": payload.get("version"),
            "api_version": payload.get("api_version"),
            "build": payload.get("build"),
            "patch": payload.get("patch"),
        }
        return timestamp_value, dedupe_quests(quests), metadata

    def _load_cached_quests_from_disk(self) -> Optional[tuple[float, list[dict], dict]]:
        return self._read_quest_snapshot(self._cache_file, "disk-cache")

    def _load_bundled_quests(self) -> Optional[tuple[float, list[dict], dict]]:
        return self._read_quest_snapshot(self._bundled_snapshot_file, "asset-snapshot")

    def _save_quests_to_disk(
        self,
        quests: list[dict],
        timestamp: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        temp_path = self._cache_file.with_name(f"{self._cache_file.name}.{os.getpid()}.tmp")
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 2,
                "source": "darkerdb-v2",
                "api_version": DARKERDB_API_VERSION,
                "timestamp": float(time.time() if timestamp is None else timestamp),
                "quests": dedupe_quests(quests),
            }
            for key in ("build", "patch"):
                if metadata and metadata.get(key) is not None:
                    payload[key] = metadata.get(key)
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(temp_path, self._cache_file)
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning("Failed to persist quests cache to disk: %s", exc, exc_info=True)
            try:
                os.remove(temp_path)
            except OSError:
                pass

    @staticmethod
    def _snapshot_age(timestamp: float, now: float) -> float:
        return max(0.0, now - float(timestamp or 0.0))

    def _set_fetch_status(
        self,
        *,
        source: str,
        timestamp: float,
        now: float,
        stale: bool,
        warning: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        self._last_fetch_status = {
            "source": source,
            "cached": source != "network",
            "stale": bool(stale),
            "warning": warning,
            "timestamp": timestamp or None,
            "age_seconds": round(self._snapshot_age(timestamp, now), 3) if timestamp else None,
            "api_version": (metadata or {}).get("api_version") or DARKERDB_API_VERSION,
            "build": (metadata or {}).get("build"),
            "patch": (metadata or {}).get("patch"),
        }

    def get_fetch_status(self) -> dict[str, object]:
        with self._quests_lock:
            return dict(self._last_fetch_status)

    def refresh_snapshot(self) -> None:
        """Forget in-memory data so a newly downloaded quests.json is observed."""
        with self._quests_lock:
            self._quests_cache = None
            self._quests_cache_timestamp = 0.0
            self._next_refresh_timestamp = 0.0

    def fetch_quests(self, force: bool = False) -> list[dict]:
        """Retrieve current quests, with an explicit and observable stale fallback."""
        now = time.time()

        with self._quests_lock:
            if (
                not force
                and self._quests_cache is not None
                and now < self._next_refresh_timestamp
            ):
                return list(self._quests_cache)

            snapshots = [
                snapshot
                for snapshot in (self._load_cached_quests_from_disk(), self._load_bundled_quests())
                if snapshot and snapshot[1]
            ]
            fallback = max(snapshots, key=lambda entry: entry[0]) if snapshots else None
            api_key = get_darkerdb_api_key()
            api_url = (os.getenv("DND_QUESTS_API_URL") or self.QUESTS_API_URL).strip()

            if not force and fallback:
                fallback_timestamp, fallback_quests, fallback_meta = fallback
                age = self._snapshot_age(fallback_timestamp, now)
                max_age = (
                    self.QUESTS_SNAPSHOT_MAX_AGE_SECONDS
                    if fallback_meta.get("source") == "asset-snapshot" and not api_key
                    else self.QUESTS_CACHE_TTL_SECONDS
                )
                if age <= max_age:
                    warning = None
                    if fallback_meta.get("source") == "asset-snapshot" and not api_key:
                        warning = (
                            "Using the distributed quest snapshot. Configure DARKERDB_API_KEY "
                            "with darkerdb.data scope for an on-demand live refresh."
                        )
                    self._quests_cache = list(fallback_quests)
                    self._quests_cache_timestamp = fallback_timestamp
                    self._next_refresh_timestamp = now + max(1.0, max_age - age)
                    self._set_fetch_status(
                        source=str(fallback_meta.get("source")),
                        timestamp=fallback_timestamp,
                        now=now,
                        stale=False,
                        warning=warning,
                        metadata=fallback_meta,
                    )
                    return list(fallback_quests)

            try:
                quests, metadata = fetch_darkerdb_quests(
                    api_key=api_key,
                    api_url=api_url,
                    session=self._session,
                    timeout=15,
                    page_size=self.QUESTS_PAGE_SIZE,
                )
            except (requests.RequestException, QuestServiceError) as exc:
                if not fallback:
                    raise
                fallback_timestamp, fallback_quests, fallback_meta = fallback
                age = self._snapshot_age(fallback_timestamp, now)
                stale_limit = (
                    self.QUESTS_SNAPSHOT_MAX_AGE_SECONDS
                    if fallback_meta.get("source") == "asset-snapshot"
                    else self.QUESTS_CACHE_TTL_SECONDS
                )
                warning = f"Live quest refresh failed: {exc} Showing cached quest data."
                self._logger.warning("%s", warning)
                self._quests_cache = list(fallback_quests)
                self._quests_cache_timestamp = fallback_timestamp
                self._next_refresh_timestamp = now + self.QUESTS_RETRY_COOLDOWN_SECONDS
                self._set_fetch_status(
                    source=str(fallback_meta.get("source")),
                    timestamp=fallback_timestamp,
                    now=now,
                    stale=age > stale_limit,
                    warning=warning,
                    metadata=fallback_meta,
                )
                return list(fallback_quests)

            self._quests_cache = list(quests)
            self._quests_cache_timestamp = now
            self._next_refresh_timestamp = now + self.QUESTS_CACHE_TTL_SECONDS
            self._save_quests_to_disk(self._quests_cache, now, metadata)
            self._set_fetch_status(
                source="network",
                timestamp=now,
                now=now,
                stale=False,
                metadata=metadata,
            )
            return list(quests)

    def clear_cache(self) -> dict[str, bool]:
        results: dict[str, bool] = {"quests_cache_removed": False, "progress_removed": False}

        for path_key, path in (
            ("quests_cache_removed", self._cache_file),
            ("progress_removed", self._progress_file),
        ):
            try:
                os.remove(path)
                results[path_key] = True
            except FileNotFoundError:
                results[path_key] = False
            except OSError as exc:  # pragma: no cover - defensive
                self._logger.warning("Failed to remove quest storage file %s: %s", path, exc, exc_info=True)
                results[path_key] = False

        with self._quests_lock:
            self._quests_cache = None
            self._quests_cache_timestamp = 0.0
            self._next_refresh_timestamp = 0.0

        return results

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------
    def load_items_index(self) -> dict[str, dict]:
        with self._items_lock:
            if self._items_index is not None:
                return self._items_index

            items_index: dict[str, dict] = {}
            items_path = resource_path("items.json")

            try:
                with open(items_path, "r", encoding="utf-8") as handle:
                    raw_data = json.load(handle)
            except FileNotFoundError:
                self._logger.error("Quest tracker is unable to locate items.json at %s", items_path)
                self._items_index = items_index
                return self._items_index
            except Exception as exc:  # pragma: no cover - defensive
                self._logger.error("Failed to load items data for quest tracker: %s", exc, exc_info=True)
                self._items_index = items_index
                return self._items_index

            archetype_candidates: dict[str, list[dict]] = {}
            if isinstance(raw_data, dict):
                for item_id, payload in raw_data.items():
                    if not isinstance(payload, dict):
                        continue
                    icon_path = str(payload.get("iconPath") or "").replace("\\", "/")
                    archetype = (
                        _canonical_item_to_game_id(payload.get("darkerdb_archetype"))
                        or _canonical_item_to_game_id(payload.get("archetype"))
                    )
                    if not archetype:
                        # Older generated catalogs did not persist the upstream
                        # archetype. Grade IDs still follow the stable _N001 form.
                        archetype = re.sub(r"_[1-8]001$", "", item_id)

                    item_info = {
                        "item_id": item_id,
                        "name": payload.get("name") or self._normalize_item_name(item_id),
                        "rarity": payload.get("rarity") or "Unknown",
                        "type": payload.get("type"),
                        "iconPath": icon_path if icon_path else None,
                        "archetype": archetype or item_id,
                        "representative_item_id": item_id,
                        "concrete_item_ids": [item_id],
                    }
                    items_index[item_id] = item_info
                    if archetype:
                        archetype_candidates.setdefault(archetype, []).append(item_info)

            def add_family_entry(archetype: str, candidates: list[dict]) -> None:
                if not candidates:
                    return
                concrete_ids = sorted({str(item["item_id"]) for item in candidates})
                if archetype in items_index:
                    exact = items_index[archetype]
                    exact["concrete_item_ids"] = concrete_ids
                    exact["archetype"] = archetype
                    return

                representative = min(
                    candidates,
                    key=lambda item: (
                        RARITY_ORDER.get(str(item.get("rarity") or "").title(), 999),
                        str(item.get("item_id") or ""),
                    ),
                )
                items_index[archetype] = {
                    **representative,
                    "item_id": archetype,
                    "rarity": "Any",
                    "archetype": archetype,
                    "representative_item_id": representative.get("item_id"),
                    "concrete_item_ids": concrete_ids,
                }

            # Quest objectives intentionally reference an item archetype (for
            # example Bandage), while stash packets contain concrete grade IDs
            # such as Bandage_1001. Add a family entry with representative
            # display metadata and every concrete ID needed for holdings.
            for archetype, candidates in archetype_candidates.items():
                add_family_entry(archetype, candidates)

            # Bridge renamed v2 families while clients still carry the final
            # v1 item snapshot. Do this only when native v2 archetype metadata
            # did not already create the accurate family mapping.
            for archetype, legacy_ids in ITEM_ARCHETYPE_FALLBACKS.items():
                if archetype in items_index:
                    continue
                candidates = [items_index[item_id] for item_id in legacy_ids if item_id in items_index]
                add_family_entry(archetype, candidates)

            self._items_index = items_index
            return self._items_index

    def refresh_items_index(self) -> dict[str, dict]:
        with self._items_lock:
            self._items_index = None
        return self.load_items_index()

    @staticmethod
    def _normalize_item_name(item_id: str) -> str:
        if not item_id:
            return "Unknown Item"
        value = str(item_id).replace("_", " ").replace("-", " ")
        value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
        return " ".join(value.split()).title()

    def normalize_item_name(self, item_id: str) -> str:
        return self._normalize_item_name(item_id)

    def get_concrete_item_ids(self, item_id: str) -> list[str]:
        """Resolve a quest item reference to concrete stash item IDs."""
        normalized = str(item_id or "").strip()
        if not normalized:
            return []
        item_info = self.load_items_index().get(normalized) or {}
        concrete_ids = item_info.get("concrete_item_ids")
        if isinstance(concrete_ids, list):
            resolved = [str(value).strip() for value in concrete_ids if str(value).strip()]
            if resolved:
                return list(dict.fromkeys(resolved))
        return [normalized]

    @staticmethod
    def merge_item_family_holdings(
        holdings_map: dict[str, list[dict]],
        concrete_item_ids: list[str],
    ) -> list[dict]:
        """Merge concrete-grade holdings into one entry per character."""
        def safe_count(value: object) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        by_character: dict[str, dict] = {}
        for concrete_id in concrete_item_ids:
            for raw_entry in holdings_map.get(concrete_id, []) or []:
                if not isinstance(raw_entry, dict):
                    continue
                character_id = str(raw_entry.get("character_id") or "")
                character_key = character_id or str(raw_entry.get("character_name") or "Unknown")
                entry = by_character.setdefault(character_key, {
                    "character_id": raw_entry.get("character_id"),
                    "character_name": raw_entry.get("character_name"),
                    "character_class": raw_entry.get("character_class"),
                    "character_level": raw_entry.get("character_level"),
                    "last_update": raw_entry.get("last_update"),
                    "total": 0,
                    "stashes": [],
                })
                entry["total"] += safe_count(raw_entry.get("total", 0))
                for stash in raw_entry.get("stashes", []) or []:
                    if isinstance(stash, dict):
                        entry["stashes"].append({**stash, "item_id": concrete_id})

        merged = list(by_character.values())
        for entry in merged:
            entry["stashes"].sort(
                key=lambda stash: (
                    -safe_count(stash.get("count", 0)),
                    str(stash.get("stash_id") or ""),
                    str(stash.get("item_id") or ""),
                )
            )
        merged.sort(
            key=lambda entry: (
                -safe_count(entry.get("total", 0)),
                str(entry.get("character_name") or "").lower(),
            )
        )
        return merged

    def build_item_payload(
        self,
        item_info: Optional[dict],
        icon_url_builder: Optional[Callable[[str], str]] = None,
    ) -> dict:
        """Return a serialisable payload for item metadata."""
        if not item_info:
            return {
                "item_id": None,
                "name": "Unknown Item",
                "rarity": "Unknown",
                "type": None,
                "icon": None,
                "iconPath": None,
            }

        icon_rel = item_info.get("iconPath")
        icon_url = item_info.get("icon_url") or item_info.get("icon")
        if icon_rel and icon_url_builder:
            try:
                icon_url = icon_url_builder(icon_rel)
            except Exception:  # pragma: no cover - defensive
                icon_url = None

        return {
            "item_id": item_info.get("item_id"),
            "name": item_info.get("name"),
            "rarity": item_info.get("rarity"),
            "type": item_info.get("type"),
            "icon": icon_url,
            "iconPath": icon_rel,
            "archetype": item_info.get("archetype"),
            "representative_item_id": item_info.get("representative_item_id"),
        }

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------
    def _sanitize_progress_payload(self, progress: Optional[dict]) -> dict:
        sanitized = self._default_progress_payload()
        if not isinstance(progress, dict):
            return sanitized

        objectives = progress.get("objectives")
        if isinstance(objectives, dict):
            for key, entry in objectives.items():
                if not isinstance(entry, dict):
                    continue
                string_key = str(key)
                sanitized_entry: dict[str, object] = {}

                quest_id = entry.get("quest_id")
                if quest_id:
                    sanitized_entry["quest_id"] = str(quest_id)

                try:
                    objective_index = entry.get("objective_index")
                    if objective_index is not None:
                        sanitized_entry["objective_index"] = int(objective_index)
                except (TypeError, ValueError):
                    sanitized_entry["objective_index"] = None

                obj_type = entry.get("type")
                if obj_type:
                    sanitized_entry["type"] = str(obj_type)

                item_id = entry.get("item_id")
                if item_id:
                    sanitized_entry["item_id"] = str(item_id)

                submitted = entry.get("submitted")
                try:
                    sanitized_entry["submitted"] = max(0, int(submitted)) if submitted is not None else 0
                except (TypeError, ValueError):
                    sanitized_entry["submitted"] = 0

                sanitized_entry["completed"] = bool(entry.get("completed"))

                sanitized["objectives"][string_key] = sanitized_entry

        items = progress.get("items")
        if isinstance(items, dict):
            for key, value in items.items():
                try:
                    sanitized["items"][str(key)] = max(0, int(value))
                except (TypeError, ValueError):
                    continue

        return sanitized

    def load_progress_state(self) -> tuple[dict, Optional[float], Optional[int]]:
        progress, timestamp, revision, _active_merchants = self.load_progress_sync_state()
        return progress, timestamp, revision

    def load_progress_sync_state(self) -> tuple[dict, Optional[float], Optional[int], list]:
        """Load the browser-sync fields from one consistent file snapshot."""
        payload = self._read_progress_file()
        if payload is None:
            return self._default_progress_payload(), None, None, []

        timestamp = payload.get("timestamp")
        try:
            timestamp_value = float(timestamp)
        except (TypeError, ValueError):
            timestamp_value = None

        progress_payload = payload.get("progress") if isinstance(payload, dict) else None
        sanitized = self._sanitize_progress_payload(progress_payload)
        revision = self._coerce_progress_revision(payload.get("progress_revision"))
        active_merchants = payload.get("active_merchants")
        if not isinstance(active_merchants, list):
            active_merchants = []
        else:
            active_merchants = list(dict.fromkeys(
                str(merchant_id).strip()
                for merchant_id in active_merchants
                if str(merchant_id).strip()
            ))
        return sanitized, timestamp_value, revision, active_merchants

    def load_progress(self) -> tuple[dict, Optional[float]]:
        progress, timestamp, _revision = self.load_progress_state()
        return progress, timestamp

    def save_progress(
        self,
        progress: dict,
        active_merchants: Optional[list] = None,
        revision: Optional[int] = None,
    ) -> bool:
        sanitized = self._sanitize_progress_payload(progress)
        with self._progress_lock:
            existing = self._read_progress_file_unlocked() or {}
            previous_revision = self._coerce_progress_revision(existing.get("progress_revision"))
            incoming_revision = self._coerce_progress_revision(revision)
            if incoming_revision is not None and previous_revision is not None:
                if incoming_revision <= previous_revision:
                    return False

            if incoming_revision is None:
                incoming_revision = max(
                    int(time.time() * 1000),
                    (previous_revision or 0) + 1,
                )

            payload: dict = {
                "version": 2,
                "timestamp": time.time(),
                "progress_revision": incoming_revision,
                "progress": sanitized,
            }
            if active_merchants is not None:
                payload["active_merchants"] = list(active_merchants)
            elif isinstance(existing.get("active_merchants"), list):
                payload["active_merchants"] = existing["active_merchants"]
            return self._write_progress_file_unlocked(payload)

    def update_progress(self, updater: Callable[[dict], dict]) -> bool:
        """Atomically read, merge, and persist progress from a background source."""
        with self._progress_lock:
            existing = self._read_progress_file_unlocked() or {}
            current = self._sanitize_progress_payload(existing.get("progress"))
            updated = self._sanitize_progress_payload(updater(current))
            previous_revision = self._coerce_progress_revision(existing.get("progress_revision"))
            revision = max(int(time.time() * 1000), (previous_revision or 0) + 1)
            payload = {
                "version": 2,
                "timestamp": time.time(),
                "progress_revision": revision,
                "progress": updated,
            }
            if isinstance(existing.get("active_merchants"), list):
                payload["active_merchants"] = existing["active_merchants"]
            return self._write_progress_file_unlocked(payload)

    def _read_progress_file(self) -> Optional[dict]:
        """Read the raw progress file payload."""
        with self._progress_lock:
            return self._read_progress_file_unlocked()

    def _read_progress_file_unlocked(self) -> Optional[dict]:
        try:
            with open(self._progress_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
                return payload if isinstance(payload, dict) else None
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        except Exception as exc:
            self._logger.warning("Failed to read quest progress from disk: %s", exc, exc_info=True)
            return None

    @staticmethod
    def _coerce_progress_revision(value: object) -> Optional[int]:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if parsed >= 0 else None

    def _write_progress_file_unlocked(self, payload: dict) -> bool:
        temp_path = self._progress_file.with_name(
            f".{self._progress_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._progress_file)
            return True
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning("Failed to persist quest progress to disk: %s", exc, exc_info=True)
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return False

    def save_active_merchants(self, merchant_ids: list) -> None:
        """Persist the active (in-game) merchant ID list."""
        with self._progress_lock:
            existing = self._read_progress_file_unlocked() or {}
            existing["active_merchants"] = list(merchant_ids)
            existing["timestamp"] = time.time()
            existing.setdefault("version", 2)
            self._write_progress_file_unlocked(existing)

    def load_active_merchants(self) -> list:
        """Load the persisted active merchant ID list."""
        existing = self._read_progress_file()
        if existing and isinstance(existing.get("active_merchants"), list):
            return existing["active_merchants"]
        return []

    def clear_progress_file(self) -> bool:
        with self._progress_lock:
            try:
                os.remove(self._progress_file)
                return True
            except FileNotFoundError:
                return False
            except OSError as exc:  # pragma: no cover - defensive
                self._logger.warning("Failed to remove quest progress file: %s", exc, exc_info=True)
                raise

    # ------------------------------------------------------------------
    # Merchant helpers
    # ------------------------------------------------------------------
    def normalize_merchant_name(self, name: Optional[str]) -> str:
        return self._normalize_merchant_name(name)

    # ------------------------------------------------------------------
    # Captured quest state (from packet capture auto-tracking)
    # ------------------------------------------------------------------
    def save_captured_state(self, state: dict) -> None:
        """Persist captured quest state from packet handler to disk."""
        with self._captured_lock:
            try:
                with open(self._captured_state_file, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"version": 1, "timestamp": time.time(), "state": state},
                        handle,
                        ensure_ascii=False,
                        indent=2,
                    )
            except Exception as exc:
                self._logger.warning(
                    "Failed to persist captured quest state: %s", exc, exc_info=True
                )

    def load_captured_state(self) -> tuple[Optional[dict], Optional[float]]:
        """Load captured quest state from disk."""
        with self._captured_lock:
            try:
                with open(self._captured_state_file, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except FileNotFoundError:
                return None, None
            except Exception as exc:
                self._logger.warning(
                    "Failed to read captured quest state: %s", exc, exc_info=True
                )
                return None, None

            if not isinstance(payload, dict):
                return None, None

            timestamp = payload.get("timestamp")
            try:
                timestamp_value = float(timestamp)
            except (TypeError, ValueError):
                timestamp_value = None

            return payload.get("state"), timestamp_value

    def clear_captured_state(self) -> bool:
        """Remove captured quest state file."""
        with self._captured_lock:
            try:
                os.remove(self._captured_state_file)
                return True
            except FileNotFoundError:
                return False
            except OSError as exc:
                self._logger.warning(
                    "Failed to remove captured quest state file: %s", exc, exc_info=True
                )
                return False


__all__ = [
    "DARKERDB_API_VERSION",
    "DARKERDB_QUESTS_API_URL",
    "QuestAuthenticationError",
    "QuestPayloadError",
    "QuestService",
    "QuestServiceError",
    "RARITY_ORDER",
    "dedupe_quests",
    "fetch_darkerdb_quests",
    "get_darkerdb_api_key",
    "normalize_darkerdb_v2_quest",
]
