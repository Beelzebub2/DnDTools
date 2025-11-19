"""Quest-related data access and caching utilities for the UI server."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from src.models.appdirs import get_data_dir, resource_path


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


class QuestService:
    """Encapsulates DarkerDB quest interactions and local persistence."""

    QUESTS_API_URL = "https://api.darkerdb.com/v1/quests"
    QUESTS_PAGE_SIZE = 100
    MERCHANT_EXACT_ALIASES = {
        "goblin merchant final": "Goblin Merchant",
        "huntress daily": "Huntress",
        "huntress daily equipment": "Huntress",
        "huntress seasonal": "Huntress",
        "huntress weekly": "Huntress",
        "tavern master final": "Tavern Master",
        "the collector final": "The Collector",
        "weaponsmith extra": "Weaponsmith",
    }
    MERCHANT_PREFIX_ALIASES = {
        "goblin merchant": "Goblin Merchant",
        "huntress": "Huntress",
        "tavern master": "Tavern Master",
        "the collector": "The Collector",
        "weaponsmith": "Weaponsmith",
    }

    def __init__(self, logger) -> None:
        self._logger = logger
        self._data_dir = Path(get_data_dir())
        self._cache_file = self._data_dir / "quests_cache.json"
        self._progress_file = self._data_dir / "quests_progress.json"

        self._quests_cache: Optional[list[dict]] = None
        self._quests_cache_timestamp: float = 0.0
        self._quests_lock = threading.RLock()
        self._items_index: Optional[dict[str, dict]] = None
        self._items_lock = threading.RLock()

    # ------------------------------------------------------------------
    # General helpers
    # ------------------------------------------------------------------
    @property
    def protected_filenames(self) -> set[str]:
        """Filenames that shouldn't be deleted when clearing character data."""
        return {self._cache_file.name.lower(), self._progress_file.name.lower()}

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
    def _load_cached_quests_from_disk(self) -> Optional[tuple[float, list[dict]]]:
        try:
            with open(self._cache_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return None
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning("Failed to read quests cache from disk: %s", exc, exc_info=True)
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

        return timestamp_value, quests

    def _save_quests_to_disk(self, quests: list[dict], timestamp: Optional[float] = None) -> None:
        try:
            with open(self._cache_file, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "version": 1,
                        "timestamp": float(timestamp or time.time()),
                        "quests": quests,
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning("Failed to persist quests cache to disk: %s", exc, exc_info=True)

    def fetch_quests(self, force: bool = False) -> list[dict]:
        """Retrieve quests from cache or DarkerDB."""
        now = time.time()

        with self._quests_lock:
            if not force and self._quests_cache is not None:
                return list(self._quests_cache)

        disk_snapshot: Optional[tuple[float, list[dict]]] = None
        if not force:
            disk_snapshot = self._load_cached_quests_from_disk()
            if disk_snapshot:
                disk_timestamp, disk_quests = disk_snapshot
                with self._quests_lock:
                    self._quests_cache = list(disk_quests)
                    self._quests_cache_timestamp = disk_timestamp
                return list(disk_quests)

        quests: list[dict] = []
        next_url = f"{self.QUESTS_API_URL}?limit={self.QUESTS_PAGE_SIZE}"
        headers = {"User-Agent": "DnDTools-QuestTracker/1.0"}

        pages = 0
        max_pages = 100

        while next_url and pages < max_pages:
            pages += 1
            try:
                response = requests.get(next_url, headers=headers, timeout=15)
                response.raise_for_status()
            except requests.RequestException as exc:
                if disk_snapshot:
                    self._logger.warning(
                        "Using cached quests from disk due to DarkerDB fetch failure: %s", exc
                    )
                    return list(disk_snapshot[1])
                raise

            try:
                payload = response.json()
            except ValueError as exc:
                if disk_snapshot:
                    self._logger.warning(
                        "Error parsing quests response, falling back to cached data: %s", exc
                    )
                    return list(disk_snapshot[1])
                raise

            body = payload.get("body")
            if isinstance(body, list):
                quests.extend(body)
            else:
                self._logger.debug("Unexpected quests payload format: %s", type(body))

            pagination = payload.get("pagination") or {}
            next_url = pagination.get("next")

            if not next_url:
                break

        with self._quests_lock:
            self._quests_cache = list(quests)
            self._quests_cache_timestamp = now
            self._save_quests_to_disk(self._quests_cache, self._quests_cache_timestamp)

        return quests

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

            if isinstance(raw_data, dict):
                for item_id, payload in raw_data.items():
                    if not isinstance(payload, dict):
                        continue
                    icon_path = str(payload.get("iconPath") or "").replace("\\", "/")
                    items_index[item_id] = {
                        "item_id": item_id,
                        "name": payload.get("name") or self._normalize_item_name(item_id),
                        "rarity": payload.get("rarity") or "Unknown",
                        "type": payload.get("type"),
                        "iconPath": icon_path if icon_path else None,
                    }

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
        return item_id.replace("_", " ").replace("-", " ").title()

    def normalize_item_name(self, item_id: str) -> str:
        return self._normalize_item_name(item_id)

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
        icon_url = None
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

    def load_progress(self) -> tuple[dict, Optional[float]]:
        try:
            with open(self._progress_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return self._default_progress_payload(), None
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning("Failed to read quest progress from disk: %s", exc, exc_info=True)
            return self._default_progress_payload(), None

        timestamp = payload.get("timestamp")
        try:
            timestamp_value = float(timestamp)
        except (TypeError, ValueError):
            timestamp_value = None

        progress_payload = payload.get("progress") if isinstance(payload, dict) else None
        sanitized = self._sanitize_progress_payload(progress_payload)
        return sanitized, timestamp_value

    def save_progress(self, progress: dict) -> None:
        sanitized = self._sanitize_progress_payload(progress)
        try:
            with open(self._progress_file, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": 1, "timestamp": time.time(), "progress": sanitized},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning("Failed to persist quest progress to disk: %s", exc, exc_info=True)

    def clear_progress_file(self) -> bool:
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


__all__ = ["QuestService", "RARITY_ORDER"]
