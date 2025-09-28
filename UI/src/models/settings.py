import copy
import json
import logging
import os
import threading
from typing import Any, Dict, Optional

from src.models.appdirs import get_settings_file, resource_path


class SettingsManager:
    """Centralized settings accessor with thread-safe load/save helpers."""

    def __init__(
        self,
        settings_file: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._settings_file = settings_file or get_settings_file()
        self._logger = logger or logging.getLogger(__name__)
        self._defaults = self._build_defaults()
        self._data: Dict[str, Any] = {}
        self.reload()

    def _build_defaults(self) -> Dict[str, Any]:
        return {
            "interface": os.getenv("CAPTURE_INTERFACE", "Ethernet"),
            "sortHotkey": "ctrl+alt+s",
            "cancelHotkey": "ctrl+alt+x",
            "sortSpeed": 0.2,
            "resolution": "Auto",
        }

    def set_logger(self, logger: Optional[logging.Logger]) -> None:
        if logger:
            self._logger = logger

    @property
    def path(self) -> str:
        return self._settings_file

    @property
    def data(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

    def get_sort_speed(self) -> float:
        value = self.get("sortSpeed", self._defaults["sortSpeed"])
        try:
            speed = float(value)
            if speed <= 0:
                raise ValueError("sortSpeed must be positive")
            return speed
        except (TypeError, ValueError):
            return float(self._defaults["sortSpeed"])

    def reload(self) -> Dict[str, Any]:
        with self._lock:
            loaded = self._read_from_disk()
            merged = self._apply_defaults(loaded)
            self._data = self._normalize(merged)
            return copy.deepcopy(self._data)

    def update(self, updates: Dict[str, Any], persist: bool = True) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise ValueError("Settings updates must be provided as a dictionary")

        with self._lock:
            merged = self._data.copy()
            merged.update(updates)
            merged = self._apply_defaults(merged)
            normalized = self._normalize(merged)

            if persist:
                self._write_to_disk(normalized)

            self._data = normalized

            return copy.deepcopy(self._data)

    def save(self) -> None:
        with self._lock:
            self._write_to_disk(self._data)

    def _apply_defaults(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        merged = self._defaults.copy()
        merged.update(settings or {})
        return merged

    def _normalize(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        normalized = settings.copy()

        for key in ("sortHotkey", "cancelHotkey"):
            if key in normalized and normalized[key] is not None:
                normalized[key] = str(normalized[key]).lower()

        sort_speed = normalized.get("sortSpeed", self._defaults["sortSpeed"])
        try:
            sort_speed_value = float(sort_speed)
            if sort_speed_value <= 0:
                raise ValueError
            normalized["sortSpeed"] = sort_speed_value
        except (TypeError, ValueError):
            normalized["sortSpeed"] = self._defaults["sortSpeed"]

        if not normalized.get("interface"):
            normalized["interface"] = self._defaults["interface"]

        resolution = normalized.get("resolution")
        if resolution:
            normalized["resolution"] = str(resolution)
        else:
            normalized["resolution"] = self._defaults["resolution"]

        return normalized

    def _read_from_disk(self) -> Dict[str, Any]:
        if not os.path.exists(self._settings_file):
            self._logger.info("Settings file not found at %s, using defaults", self._settings_file)
            return {}

        try:
            with open(self._settings_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if not isinstance(data, dict):
                    raise ValueError("Settings file must contain a JSON object")
                self._logger.debug("Settings loaded from %s", self._settings_file)
                return data
        except json.JSONDecodeError as exc:
            self._logger.error("Invalid JSON in settings file: %s", exc)
        except OSError as exc:
            self._logger.error("Unable to read settings file: %s", exc)
        except Exception as exc:  # pragma: no cover - safety net
            self._logger.error("Unexpected error loading settings: %s", exc)

        return {}

    def _write_to_disk(self, settings: Dict[str, Any]) -> None:
        directory = os.path.dirname(self._settings_file)
        os.makedirs(directory, exist_ok=True)
        temp_file = f"{self._settings_file}.tmp"

        try:
            with open(temp_file, "w", encoding="utf-8") as handle:
                json.dump(settings, handle, indent=2, ensure_ascii=False)
            os.replace(temp_file, self._settings_file)
            self._logger.debug("Settings saved to %s", self._settings_file)
        except OSError as exc:
            self._logger.error("Failed to write settings file: %s", exc)
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            raise

    @classmethod
    def migrate_from_legacy(
        cls,
        logger: Optional[logging.Logger] = None,
        defer_heavy_operations: bool = False,
    ) -> None:
        legacy_path = resource_path("settings.json")
        target_path = get_settings_file()
        log = logger or logging.getLogger(__name__)

        if not os.path.exists(legacy_path) or os.path.exists(target_path):
            return

        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(legacy_path, "r", encoding="utf-8") as source:
                content = json.load(source)
            with open(target_path, "w", encoding="utf-8") as dest:
                json.dump(content, dest, indent=2, ensure_ascii=False)

            if defer_heavy_operations:
                threading.Timer(5.0, lambda: log.info("Deferred settings migration complete")).start()
                log.info("Settings migration scheduled for later")
            else:
                log.info("Migrated legacy settings to %s", target_path)
        except Exception as exc:  # pragma: no cover - safeguard
            log.error("Failed to migrate legacy settings: %s", exc)


def get_settings_manager() -> SettingsManager:
    return settings_manager


settings_manager = SettingsManager()
