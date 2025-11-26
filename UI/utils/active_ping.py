"""Background worker that reports client activity to the Active User Ping API."""
from __future__ import annotations

import logging
import os
import platform
import random
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import requests


def _parse_env_float(var_name: str, default: float) -> float:
    value = os.environ.get(var_name)
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class ActivePingConfig:
    enabled: bool
    url: str
    interval_seconds: float
    jitter_seconds: float
    timeout_seconds: float


def _load_config() -> ActivePingConfig:
    url = (os.environ.get("DND_ACTIVITY_PING_URL", "https://dndtools.rrmtools.uk/api/activity/ping") or "").strip()
    enabled_flag = os.environ.get("DND_ACTIVITY_PING_ENABLED", "1").lower() not in {"0", "false", "no", "off"}
    interval_seconds = max(30.0, _parse_env_float("DND_ACTIVITY_PING_INTERVAL", 75.0))
    jitter_seconds = max(0.0, _parse_env_float("DND_ACTIVITY_PING_JITTER", 15.0))
    timeout_seconds = max(1.0, _parse_env_float("DND_ACTIVITY_PING_TIMEOUT", 6.0))
    enabled = bool(enabled_flag and url)
    return ActivePingConfig(
        enabled=enabled,
        url=url,
        interval_seconds=interval_seconds,
        jitter_seconds=jitter_seconds,
        timeout_seconds=timeout_seconds,
    )


class ActivePingService:
    """Manages heartbeat transmission to the Active User Ping API."""

    def __init__(
        self,
        *,
        settings_manager,
        app_version: str,
        logger: Optional[logging.Logger] = None,
        session_factory: Optional[Callable[[], requests.Session]] = None,
    ) -> None:
        self._settings_manager = settings_manager
        self._app_version = app_version
        self._logger = logger or logging.getLogger(__name__)
        self._session_factory = session_factory or requests.Session
        self._config = _load_config()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client_id = ""
        self._session_id = ""
        self._failure_logged = False
        self._developer_mode = False
        self._lock = threading.Lock()
        self._platform_name = platform.system().lower()
        self._platform_version = platform.release()

    def start(self) -> bool:
        if not self._config.enabled:
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._client_id = self._ensure_client_id()
            self._session_id = self._generate_session_id()
            self._stop_event.clear()
            thread = threading.Thread(target=self._run_loop, name="ActiveUserPing", daemon=True)
            self._thread = thread
            thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass
        with self._lock:
            self._thread = None

    def reload_config(self) -> None:
        with self._lock:
            self._config = _load_config()

    def set_developer_mode(self, enabled: bool) -> None:
        self._developer_mode = bool(enabled)

    def _run_loop(self) -> None:
        session = self._session_factory()
        headers = {'User-Agent': f"DnDTools/{self._app_version}"}
        try:
            while not self._stop_event.is_set():
                self._send_ping(session, headers)
                wait_time = self._config.interval_seconds
                if self._config.jitter_seconds > 0:
                    wait_time += random.uniform(0.0, self._config.jitter_seconds)
                if self._stop_event.wait(wait_time):
                    break
        finally:
            session.close()

    def _send_ping(self, session: requests.Session, headers: dict[str, str]) -> None:
        payload = {
            'clientId': self._client_id,
            'sessionId': self._session_id,
            'platform': self._platform_name,
            'platformVersion': self._platform_version,
            'appVersion': self._app_version,
            'userAgent': f"DnDTools/{self._app_version}",
        }
        try:
            response = session.post(
                self._config.url,
                json=payload,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log_fn = self._logger.info if not self._failure_logged else self._logger.debug
            log_fn("Active user ping failed: %s", exc)
            self._failure_logged = True
            return

        self._failure_logged = False

        if not self._developer_mode:
            return

        try:
            if 'application/json' not in (response.headers.get('Content-Type', '') or '').lower():
                return
            payload_json = response.json()
        except (ValueError, AttributeError):
            return

        active_count = payload_json.get('activeCount')
        if active_count is not None:
            self._logger.debug("Active user ping acknowledged (active=%s)", active_count)

    def _ensure_client_id(self) -> str:
        try:
            stored = str(self._settings_manager.get('activityClientId') or '').strip()
        except Exception as exc:
            self._logger.debug("Unable to read activity client id from settings: %s", exc)
            stored = ''
        if stored:
            return stored
        generated = f"dndtools-client-{uuid.uuid4().hex}"
        try:
            self._settings_manager.update({'activityClientId': generated})
        except Exception as exc:
            self._logger.debug("Unable to persist activity client id: %s", exc)
        return generated

    def _generate_session_id(self) -> str:
        return f"session-{uuid.uuid4().hex}"
