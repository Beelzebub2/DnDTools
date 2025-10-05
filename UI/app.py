from typing import Optional

from src.models.appdirs import resource_path, get_resource_dir, get_templates_dir, get_static_dir, get_data_dir
from src.models.settings import (
    settings_manager,
    SettingsManager,
    resolve_tshark_executable,
)
import webview
from flask import Flask, render_template, jsonify, request, send_from_directory, redirect, url_for, send_file
import os
import threading
import asyncio
from src.models.stash_manager import StashManager
import psutil
import json
import sys
import logging
import re
from pathlib import Path
from urllib.parse import urlparse
from utils.logging_setup import setup_logging
import secrets
import time
import shutil
import subprocess
import tempfile
import hashlib
from datetime import datetime
import requests
from networking.protos import _PacketCommand_pb2

from src.models.icon_pak import icon_store, canonical_icon_path

from src.models.character import save_packet_data
from src.models.item import Item
from src.models.game_overlay import overlay_manager, register_overlay_logging

from dotenv import load_dotenv
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import

# Global cache for version check
version_cache = None
version_cache_timestamp = 0
VERSION_CACHE_DURATION = 6 * 60 * 60  # 6 hours in seconds

APP_VERSION = "3.4.7"
UPDATE_MANIFEST_URL = os.environ.get(
    "DND_UPDATE_MANIFEST",
    "https://github.com/Beelzebub2/DnDTools/releases/download/latest/update-manifest.json",
)
UPDATE_CACHE_DURATION = 5 * 60
AUTO_UPDATE_SILENT = os.environ.get("DND_UPDATE_SILENT", "1").lower() not in {"0", "false", "no", "off"}

# Update state tracking
_update_cache = None
_update_cache_timestamp = 0
_update_state = {
    "in_progress": False,
    "last_error": None,
}
_update_lock = threading.RLock()

# Quest tracking cache
QUESTS_API_URL = "https://api.darkerdb.com/v1/quests"
QUESTS_PAGE_SIZE = 100
# Quest data should persist until explicitly refreshed or cleared.
# Using None disables automatic expiration.
QUESTS_CACHE_DURATION = None
QUESTS_CACHE_FILE = os.path.join(get_data_dir(), 'quests_cache.json')
QUESTS_PROGRESS_FILE = os.path.join(get_data_dir(), 'quests_progress.json')
DATA_DIR = Path(get_data_dir())
CHARACTER_STORAGE_PROTECTED_FILES = {
    Path(QUESTS_CACHE_FILE).name.lower(),
    Path(QUESTS_PROGRESS_FILE).name.lower(),
}
_quests_cache: Optional[list[dict]] = None
_quests_cache_timestamp = 0.0
_quests_lock = threading.RLock()
_items_index: Optional[dict[str, dict]] = None

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

MERCHANT_EXACT_ALIASES = {
    'goblin merchant final': 'Goblin Merchant',
    'huntress daily': 'Huntress',
    'huntress daily equipment': 'Huntress',
    'huntress seasonal': 'Huntress',
    'huntress weekly': 'Huntress',
    'tavern master final': 'Tavern Master',
    'the collector final': 'The Collector',
    'weaponsmith extra': 'Weaponsmith',
}

MERCHANT_PREFIX_ALIASES = {
    'goblin merchant': 'Goblin Merchant',
    'huntress': 'Huntress',
    'tavern master': 'Tavern Master',
    'the collector': 'The Collector',
    'weaponsmith': 'Weaponsmith',
}


def _normalize_merchant_name(name: Optional[str]) -> str:
    if not name:
        return ''

    cleaned = ' '.join(str(name).strip().split())
    lowered = cleaned.lower()

    if lowered in MERCHANT_EXACT_ALIASES:
        return MERCHANT_EXACT_ALIASES[lowered]

    for prefix, canonical in MERCHANT_PREFIX_ALIASES.items():
        if lowered.startswith(prefix):
            return canonical

    return cleaned


def _load_cached_quests_from_disk() -> Optional[tuple[float, list[dict]]]:
    try:
        with open(QUESTS_CACHE_FILE, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to read quests cache from disk: %s", exc, exc_info=True)
        return None

    if not isinstance(payload, dict):
        return None

    timestamp = payload.get('timestamp')
    quests = payload.get('quests')
    if not isinstance(quests, list):
        return None

    try:
        timestamp_value = float(timestamp)
    except (TypeError, ValueError):
        timestamp_value = 0.0

    return timestamp_value, quests


def _save_quests_to_disk(quests: list[dict], timestamp: Optional[float] = None) -> None:
    try:
        with open(QUESTS_CACHE_FILE, 'w', encoding='utf-8') as handle:
            json.dump({
                'version': 1,
                'timestamp': float(timestamp or time.time()),
                'quests': quests,
            }, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Failed to persist quests cache to disk: %s", exc, exc_info=True)


def _default_progress_payload() -> dict:
    return {
        'objectives': {},
        'items': {},
    }


def _sanitize_progress_payload(progress: Optional[dict]) -> dict:
    sanitized = _default_progress_payload()
    if not isinstance(progress, dict):
        return sanitized

    objectives = progress.get('objectives')
    if isinstance(objectives, dict):
        for key, entry in objectives.items():
            if not isinstance(entry, dict):
                continue
            string_key = str(key)
            sanitized_entry: dict[str, object] = {}

            quest_id = entry.get('quest_id')
            if quest_id:
                sanitized_entry['quest_id'] = str(quest_id)

            try:
                objective_index = entry.get('objective_index')
                if objective_index is not None:
                    sanitized_entry['objective_index'] = int(objective_index)
            except (TypeError, ValueError):
                sanitized_entry['objective_index'] = None

            obj_type = entry.get('type')
            if obj_type:
                sanitized_entry['type'] = str(obj_type)

            item_id = entry.get('item_id')
            if item_id:
                sanitized_entry['item_id'] = str(item_id)

            submitted = entry.get('submitted')
            try:
                sanitized_entry['submitted'] = max(0, int(submitted)) if submitted is not None else 0
            except (TypeError, ValueError):
                sanitized_entry['submitted'] = 0

            sanitized_entry['completed'] = bool(entry.get('completed'))

            sanitized['objectives'][string_key] = sanitized_entry

    items = progress.get('items')
    if isinstance(items, dict):
        for key, value in items.items():
            try:
                sanitized['items'][str(key)] = max(0, int(value))
            except (TypeError, ValueError):
                continue

    return sanitized


def _load_quest_progress() -> tuple[dict, Optional[float]]:
    try:
        with open(QUESTS_PROGRESS_FILE, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return _default_progress_payload(), None
    except Exception as exc:
        logger.warning("Failed to read quest progress from disk: %s", exc, exc_info=True)
        return _default_progress_payload(), None

    timestamp = payload.get('timestamp')
    try:
        timestamp_value = float(timestamp)
    except (TypeError, ValueError):
        timestamp_value = None

    progress_payload = payload.get('progress') if isinstance(payload, dict) else None
    sanitized = _sanitize_progress_payload(progress_payload)
    return sanitized, timestamp_value


def _save_quest_progress(progress: dict) -> None:
    sanitized = _sanitize_progress_payload(progress)
    try:
        with open(QUESTS_PROGRESS_FILE, 'w', encoding='utf-8') as handle:
            json.dump({
                'version': 1,
                'timestamp': time.time(),
                'progress': sanitized,
            }, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Failed to persist quest progress to disk: %s", exc, exc_info=True)


def _clear_quest_storage() -> dict[str, bool]:
    results: dict[str, bool] = {
        'quests_cache_removed': False,
        'progress_removed': False,
    }

    for path_key, path in (
        ('quests_cache_removed', QUESTS_CACHE_FILE),
        ('progress_removed', QUESTS_PROGRESS_FILE),
    ):
        try:
            os.remove(path)
            results[path_key] = True
        except FileNotFoundError:
            results[path_key] = False
        except OSError as exc:
            logger.warning("Failed to remove quest storage file %s: %s", path, exc, exc_info=True)
            results[path_key] = False

    global _quests_cache, _quests_cache_timestamp
    with _quests_lock:
        _quests_cache = None
        _quests_cache_timestamp = 0.0

    return results


def _clear_character_storage() -> dict[str, object]:
    removed_files: list[str] = []
    failed_files: list[str] = []

    if DATA_DIR.exists():
        for candidate in DATA_DIR.glob('*.json'):
            try:
                if candidate.name.lower() in CHARACTER_STORAGE_PROTECTED_FILES:
                    continue
                candidate.unlink()
                removed_files.append(candidate.name)
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning("Failed to delete character data file %s: %s", candidate, exc, exc_info=True)
                failed_files.append(candidate.name)

    reload_failed = False
    try:
        stash_manager.force_reload()
    except Exception as exc:
        reload_failed = True
        logger.warning("Failed to reload stash manager after clearing character data: %s", exc, exc_info=True)

    removed_count = len(removed_files)
    failed_count = len(failed_files)

    if failed_count:
        message = 'Failed to delete some character data files'
    elif removed_count:
        message = 'Character data cleared'
    else:
        message = 'No character data found to delete'

    success = failed_count == 0 and not reload_failed

    return {
        'success': success,
        'message': message,
        'removed_count': removed_count,
        'failed_count': failed_count,
        'removed_files': removed_files,
        'failed_files': failed_files,
        'reload_failed': reload_failed,
    }


def _download_manifest_from_url(url: str) -> Optional[dict]:
    try:
        response = requests.get(
            url,
            headers={'User-Agent': 'DnDTools-Updater'},
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.warning("Failed to download update manifest from %s: %s", url, exc)
        return None

    if response.status_code == 404:
        logger.info("Update manifest not found at %s (404)", url)
        return None

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.warning("Unexpected HTTP error retrieving manifest from %s: %s", url, exc)
        return None

    try:
        manifest = response.json()
    except ValueError as exc:
        logger.warning("Manifest at %s is not valid JSON: %s", url, exc)
        return None

    if not isinstance(manifest, dict):
        logger.warning("Manifest at %s must be a JSON object", url)
        return None

    return manifest


def _fetch_manifest_from_github_latest() -> Optional[dict]:
    try:
        api_response = requests.get(
            'https://api.github.com/repos/Beelzebub2/DnDTools/releases/latest',
            headers={'User-Agent': 'DnDTools-Updater'},
            timeout=15,
        )
        api_response.raise_for_status()
        release_data = api_response.json()
    except requests.RequestException as exc:
        logger.warning("Unable to query GitHub releases API: %s", exc)
        return None

    assets = release_data.get('assets') or []
    asset_url = None
    for asset in assets:
        if isinstance(asset, dict) and asset.get('name') == 'update-manifest.json':
            asset_url = asset.get('browser_download_url')
            if asset_url:
                break

    if not asset_url:
        logger.warning("No update-manifest.json asset found in latest GitHub release")
        return None

    try:
        asset_response = requests.get(
            asset_url,
            headers={'User-Agent': 'DnDTools-Updater', 'Accept': 'application/octet-stream'},
            timeout=15,
        )
        asset_response.raise_for_status()
        manifest = asset_response.json()
    except requests.RequestException as exc:
        logger.warning("Failed downloading manifest asset from GitHub: %s", exc)
        return None
    except ValueError as exc:
        logger.warning("GitHub manifest asset is not valid JSON: %s", exc)
        return None

    if not isinstance(manifest, dict):
        logger.warning("GitHub manifest asset must be a JSON object")
        return None

    return manifest


def _normalize_item_name(item_id: str) -> str:
    if not item_id:
        return "Unknown Item"
    return item_id.replace('_', ' ').replace('-', ' ').title()


def _load_items_index() -> dict[str, dict]:
    global _items_index
    if _items_index is not None:
        return _items_index

    items_index: dict[str, dict] = {}
    items_path = resource_path('items.json')

    try:
        with open(items_path, 'r', encoding='utf-8') as handle:
            raw_data = json.load(handle)
    except FileNotFoundError:
        logger.error("Quest tracker is unable to locate items.json at %s", items_path)
        _items_index = items_index
        return _items_index
    except Exception as exc:
        logger.error("Failed to load items data for quest tracker: %s", exc, exc_info=True)
        _items_index = items_index
        return _items_index

    if isinstance(raw_data, dict):
        for item_id, payload in raw_data.items():
            if not isinstance(payload, dict):
                continue
            icon_path = str(payload.get('iconPath') or '').replace('\\', '/').replace('\\', '/')
            items_index[item_id] = {
                'item_id': item_id,
                'name': payload.get('name') or _normalize_item_name(item_id),
                'rarity': payload.get('rarity') or 'Unknown',
                'type': payload.get('type'),
                'iconPath': icon_path if icon_path else None,
            }

    _items_index = items_index
    return _items_index


def _fetch_quests_data(force: bool = False) -> list[dict]:
    global _quests_cache, _quests_cache_timestamp
    now = time.time()

    with _quests_lock:
        if not force and _quests_cache is not None:
            return list(_quests_cache)

    disk_snapshot: Optional[tuple[float, list[dict]]] = None
    if not force:
        disk_snapshot = _load_cached_quests_from_disk()
        if disk_snapshot:
            disk_timestamp, disk_quests = disk_snapshot
            with _quests_lock:
                _quests_cache = list(disk_quests)
                _quests_cache_timestamp = disk_timestamp
            return list(disk_quests)

    quests: list[dict] = []
    next_url = f"{QUESTS_API_URL}?limit={QUESTS_PAGE_SIZE}"
    headers = {
        'User-Agent': 'DnDTools-QuestTracker/1.0'
    }

    pages = 0
    max_pages = 100

    while next_url and pages < max_pages:
        pages += 1
        try:
            response = requests.get(next_url, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            if disk_snapshot:
                logger.warning("Using cached quests from disk due to DarkerDB fetch failure: %s", exc)
                return list(disk_snapshot[1])
            raise

        try:
            payload = response.json()
        except ValueError as exc:
            if disk_snapshot:
                logger.warning("Error parsing quests response, falling back to cached data: %s", exc)
                return list(disk_snapshot[1])
            raise
        body = payload.get('body')
        if isinstance(body, list):
            quests.extend(body)
        else:
            logger.debug("Unexpected quests payload format: %s", type(body))

        pagination = payload.get('pagination') or {}
        next_url = pagination.get('next')

        if not next_url:
            break

    with _quests_lock:
        _quests_cache = list(quests)
        _quests_cache_timestamp = time.time()
        _save_quests_to_disk(_quests_cache, _quests_cache_timestamp)

    return quests


def _build_item_payload(item_info: Optional[dict]) -> dict:
    if not item_info:
        return {
            'item_id': None,
            'name': 'Unknown Item',
            'rarity': 'Unknown',
            'type': None,
            'icon': None,
            'iconPath': None,
        }

    icon_rel = item_info.get('iconPath')
    icon_url = url_for('serve_file', filename=icon_rel) if icon_rel else None
    return {
        'item_id': item_info.get('item_id'),
        'name': item_info.get('name'),
        'rarity': item_info.get('rarity'),
        'type': item_info.get('type'),
        'icon': icon_url,
        'iconPath': icon_rel,
    }

# Initialize logging first
setup_logging()
register_overlay_logging()
logger = logging.getLogger(__name__)
settings_manager.set_logger(logger)

# Load environment variables
load_dotenv()

# Determine base directory for resources
app_dir = resource_path('')
logger.info(f"Base directory: {app_dir}")

# Debug: Print and check template folder
template_folder_path = get_templates_dir()
logger.info(f"Template folder resolved to: {template_folder_path}")
if not os.path.exists(template_folder_path):
    logger.error(f"Template folder does not exist: {template_folder_path}")
else:
    if not os.path.exists(os.path.join(template_folder_path, 'index.html')):
        logger.error(f"index.html not found in template folder: {template_folder_path}")
    else:
        logger.info(f"index.html found in template folder: {template_folder_path}")

# Use get_templates_dir and get_static_dir for Flask app
server = Flask(__name__, 
    static_folder=get_static_dir(),
    template_folder=template_folder_path
)
server.config['JSON_AS_ASCII'] = False
# Set a secure secret key for session
server.secret_key = secrets.token_hex(32)  # Generate a secure random key

# Initialize StashManager with explicit path, but defer actual data loading
stash_manager = StashManager(app_dir, defer_loading=True)

# Cache for frequently accessed data
_cache = {}

def validate_character_id(character_id):
    """Validate character ID format and sanitize input"""
    if not character_id:
        return None
    
    # Basic sanitization - remove whitespace and check for minimum length
    character_id = str(character_id).strip()
    if len(character_id) < 1 or len(character_id) > 100:  # reasonable bounds
        return None
    
    # Check for potentially dangerous characters
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', character_id):
        return None
        
    return character_id

def validate_stash_id(stash_id):
    """Validate stash ID format and sanitize input"""
    if stash_id is None:
        return None
    
    try:
        # Convert to int and validate range
        stash_id = int(stash_id)
        if stash_id < 0:  # negative values not allowed
            return None
        return stash_id
    except (ValueError, TypeError):
        return None


def _normalize_version(value: Optional[str]) -> tuple[int, ...]:
    if not value:
        return ()
    parts = [p for p in re.split(r"[^0-9]+", str(value)) if p]
    normalized = []
    for part in parts:
        try:
            normalized.append(int(part))
        except ValueError:
            continue
    return tuple(normalized)


def _is_remote_newer(remote: str, local: str) -> bool:
    remote_tuple = _normalize_version(remote)
    local_tuple = _normalize_version(local)
    # Pad tuples to the same length for comparison
    length = max(len(remote_tuple), len(local_tuple))
    remote_padded = remote_tuple + (0,) * (length - len(remote_tuple))
    local_padded = local_tuple + (0,) * (length - len(local_tuple))
    return remote_padded > local_padded


def _fetch_update_manifest(force: bool = False) -> Optional[dict]:
    global _update_cache, _update_cache_timestamp

    now = time.time()
    if not force and _update_cache and (now - _update_cache_timestamp) < UPDATE_CACHE_DURATION:
        return _update_cache

    try:
        manifest = _download_manifest_from_url(UPDATE_MANIFEST_URL)
        if manifest is None:
            manifest = _fetch_manifest_from_github_latest()
        if manifest is None:
            raise RuntimeError('Unable to retrieve update manifest from configured sources')

        for key in ('version', 'url'):
            if not manifest.get(key):
                raise ValueError(f'Manifest missing required field: {key}')

        _update_cache = manifest
        _update_cache_timestamp = now
        return manifest
    except Exception as exc:
        logger.error(f"Failed to fetch update manifest: {exc}", exc_info=True)
        with _update_lock:
            _update_state['last_error'] = str(exc)
        return None


def _download_installer(manifest: dict) -> Path:
    url = manifest['url']
    parsed = urlparse(url)
    filename = Path(parsed.path).name or f"DnDTools-Setup-{manifest['version']}.exe"
    target_dir = Path(tempfile.gettempdir()) / "DnDToolsUpdater"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    logger.info(f"Downloading installer from {url} to {target_path}")

    sha256 = hashlib.sha256()
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with target_path.open('wb') as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                sha256.update(chunk)

    expected_sha = manifest.get('sha256')
    if expected_sha:
        digest = sha256.hexdigest()
        if digest.lower() != expected_sha.lower():
            target_path.unlink(missing_ok=True)
            raise ValueError('Downloaded installer checksum mismatch')

    return target_path


def _run_installer(installer_path: Path) -> None:
    launch_mode = "silent" if AUTO_UPDATE_SILENT else "interactive"
    logger.info(
        "Launching installer for update: %s (mode=%s)",
        installer_path,
        launch_mode,
    )
    args = [str(installer_path)]
    if AUTO_UPDATE_SILENT:
        args.extend(
            [
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
            ]
        )
    else:
        args.extend(
            [
                "/SILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
            ]
        )

    popen_kwargs = {
        "close_fds": False,
        "cwd": str(installer_path.parent),
    }
    if os.name == "nt":
        creation_flags = 0
        for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            creation_flags |= getattr(subprocess, flag_name, 0)
        popen_kwargs["creationflags"] = creation_flags

    proc = subprocess.Popen(args, **popen_kwargs)
    logger.info("Installer process started (pid=%s)", proc.pid)


def _perform_update(manifest: dict) -> None:
    installer_path: Optional[Path] = None
    should_exit = False
    try:
        with _update_lock:
            _update_state['in_progress'] = True
            _update_state['last_error'] = None

        installer_path = _download_installer(manifest)
        _run_installer(installer_path)
        should_exit = True
    except Exception as exc:
        logger.error(f"Automatic update failed: {exc}", exc_info=True)
        with _update_lock:
            _update_state['last_error'] = str(exc)
    finally:
        with _update_lock:
            _update_state['in_progress'] = False
        if installer_path and installer_path.exists():
            try:
                installer_path.unlink()
            except OSError:
                logger.debug("Installer still in use, leaving behind temporary file %s", installer_path)

    if should_exit:
        logger.info("Update installer launched. Exiting application in 3 seconds.")
        time.sleep(3)
        os._exit(0)

def handle_alive_packet(message):
    """Handle S2C_ALIVE_RES packets to trigger traffic animation"""
    # Notify UI of alive packet reception for traffic animation
    if api.window:
        api.window.evaluate_js('''
            if(window.triggerTrafficParticle) window.triggerTrafficParticle();
        ''')
    return True

def handle_character(message):
    """Handle S2C_LOBBY_CHARACTER_INFO_RES packets to save character data"""
    from src.models.character import save_packet_data
    
    # Save the character data first
    saved = save_packet_data(message)
    
    if saved:
        # Force reload stash manager data to ensure it's refreshed
        stash_manager.force_reload()
        
        # Extract character information for visual effect
        try:
            char_data = message.characterDataBase
            char_class = char_data.characterClass.replace("DesignDataPlayerCharacter:Id_PlayerCharacter_", "")
            char_nickname = char_data.nickName.originalNickName if hasattr(char_data.nickName, 'originalNickName') else "Unknown"
            
            # Notify UI of data update with character capture animation
            if api.window:
                # Escape quotes in nickname for JavaScript
                escaped_nickname = char_nickname.replace('"', '\\"')
                api.window.evaluate_js(f'''
                    showNotification("New character data received", "success");
                    if(window.showCharacterCaptureAnimation) window.showCharacterCaptureAnimation("{char_class}", "{escaped_nickname}");
                    if(window.updateCharacterData) window.updateCharacterData();
                    if(window.updateCharacterList) window.updateCharacterList();
                ''')
        except Exception as e:
            logger.error(f"Error extracting character info for animation: {e}")
    
    return saved

class CaptureController:
    """Encapsulates PacketCapture lifecycle and user intent state."""

    def __init__(self, initial_settings, capture_info, wireshark_path=None):
        self._lock = threading.RLock()
        self._capture_info = capture_info
        self._settings = {
            "interface": initial_settings.get("interface", "Ethernet"),
            "port_range": initial_settings.get("port_range", (20200, 20300)),
        }
        self._wireshark_path = wireshark_path
        self._packet_capture = self._create_capture()
        self._desired_running = False
        self._last_error = None

        if self._packet_capture.should_auto_start():
            self._desired_running = True
            self._packet_capture.start_capture_switch()

    def _create_capture(self):
        capture = PacketCapture(
            interface=self._settings["interface"],
            port_range=self._settings["port_range"],
            wireshark_path=self._wireshark_path,
        )
        capture.capture_info = self._capture_info
        return capture

    def _state_dict(self):
        running = self._packet_capture.is_active()
        return {
            "running": running,
            "desiredRunning": self._desired_running,
            "lastError": self._last_error,
            "interface": self._settings["interface"],
            "portRange": {
                "low": self._settings["port_range"][0],
                "high": self._settings["port_range"][1],
            },
        }

    def start(self):
        with self._lock:
            self._desired_running = True
            self._packet_capture.start_capture_switch()
            running = self._packet_capture.is_active()
            self._last_error = None if running else "Capture failed to start"
            return running, self._state_dict()

    def stop(self):
        with self._lock:
            self._desired_running = False
            self._packet_capture.stop_capture_switch()
            running = self._packet_capture.is_active()
            success = not running
            self._last_error = None if success else "Capture is still running"
            return success, self._state_dict()

    def restart(self):
        with self._lock:
            desired = self._desired_running or self._packet_capture.is_active()
            self._packet_capture.stop_capture_switch()
            self._packet_capture.start_capture_switch()
            self._desired_running = True if desired else self._desired_running
            running = self._packet_capture.is_active()
            self._last_error = None if running else "Capture failed to restart"
            return running, self._state_dict()

    def update_settings(self, interface, port_low, port_high):
        with self._lock:
            new_interface = interface or self._settings["interface"]
            low = port_low if port_low is not None else self._settings["port_range"][0]
            high = port_high if port_high is not None else self._settings["port_range"][1]
            new_range = (low, high)

            if (
                new_interface == self._settings["interface"]
                and new_range == self._settings["port_range"]
            ):
                return self._state_dict()

            should_resume = self._desired_running
            self._settings = {"interface": new_interface, "port_range": new_range}

            self._packet_capture.stop_capture_switch(persist_running_state=should_resume)
            self._packet_capture = self._create_capture()

            if should_resume:
                self._packet_capture.start_capture_switch()

            return self._state_dict()

    def should_auto_start(self):
        return self._packet_capture.should_auto_start()

    def state(self):
        with self._lock:
            return self._state_dict()

    def settings(self):
        with self._lock:
            return {
                "interface": self._settings["interface"],
                "port_range": self._settings["port_range"],
            }

    def set_wireshark_path(self, wireshark_path):
        with self._lock:
            normalized_new = wireshark_path or ""
            normalized_old = self._wireshark_path or ""
            if normalized_new == normalized_old:
                return self._state_dict()

            self._wireshark_path = normalized_new
            should_resume = self._desired_running or self._packet_capture.is_active()
            try:
                self._packet_capture.stop_capture_switch(persist_running_state=False)
            except Exception as exc:
                logger.debug(f"Failed stopping capture for tshark path change: {exc}")

            self._packet_capture = self._create_capture()

            if should_resume:
                try:
                    self._packet_capture.start_capture_switch()
                except Exception as exc:
                    self._last_error = f"Capture restart failed: {exc}"
                    logger.error(self._last_error)
                else:
                    self._last_error = None
            else:
                self._last_error = None

            return self._state_dict()

    def shutdown(self):
        with self._lock:
            desired = self._desired_running or self._packet_capture.is_active()
            self._desired_running = desired

        self._packet_capture.shutdown(persist_running_state=desired)

    @property
    def packet_capture(self):
        return self._packet_capture

class Api:
    def __init__(self):
        self.stash_manager = stash_manager
        self.settings_manager = settings_manager
        self.overlay_manager = overlay_manager
        settings = self.settings_manager.reload()

        # Apply persisted sort order preference if available
        try:
            Item.sort_order = Item.normalize_sort_order(
                settings.get('stashSortOrder', Item.sort_order)
            )
        except Exception as exc:
            logger.error(f"Failed to restore stash sort order from settings: {exc}")

        self._current_pack_mode = bool(settings.get('stashPackMode', False))
        self._current_stack_mode = bool(settings.get('stashStackMode', False))
        self._wireshark_path = settings.get('wiresharkPath') or ''

        # Capture setup
        interface = self.settings_manager.get('interface') or os.getenv('CAPTURE_INTERFACE', 'Ethernet')
        self.capture_settings = {
            'interface': interface,
            'port_range': (
                int(os.getenv('CAPTURE_PORT_LOW', 20200)),
                int(os.getenv('CAPTURE_PORT_HIGH', 20300))
            )
        }
        capture_info = {
            _PacketCommand_pb2.PacketCommand.S2C_LOBBY_CHARACTER_INFO_RES: handle_character,
            _PacketCommand_pb2.PacketCommand.S2C_ALIVE_RES: handle_alive_packet,
        }
        self.capture_controller = CaptureController(self.capture_settings, capture_info, wireshark_path=self._wireshark_path)
        # Normalize settings from controller (ensures tuple types)
        self.capture_settings = self.capture_controller.settings()
        self._apply_wireshark_path(self._wireshark_path)
        self._initial_restart_done = False
        self.window = None
        self._setup_global_hotkeys()
        self.is_maximized = False
        self.original_size = None
        self.original_position = None
        self.current_sort_event = None
        self._current_char_id = None
        self._current_stash_id = None

    def _update_closing_overlay(self, message):
        if not self.window:
            return
        try:
            safe_message = (message or "").replace('\\', '\\\\').replace('"', '\\"')
            self.window.evaluate_js(
                f"window.updateClosingStatus && window.updateClosingStatus(\"{safe_message}\");"
            )
        except Exception as overlay_err:
            logger.debug(f"Unable to update closing overlay: {overlay_err}")

    def _save_settings(self, settings):
        """Save settings to file with proper error handling and validation"""
        try:
            previous_settings = self.settings_manager.data
            updated_settings = self.settings_manager.update(settings)
            self._setup_global_hotkeys()

            new_interface = updated_settings.get('interface')
            previous_interface = previous_settings.get('interface') if isinstance(previous_settings, dict) else None
            interface_changed = new_interface and new_interface != previous_interface

            self.capture_settings['interface'] = new_interface or self.capture_settings.get('interface')

            if interface_changed:
                try:
                    state = self.capture_controller.update_settings(new_interface, None, None)
                    self.capture_settings = {
                        'interface': state['interface'],
                        'port_range': (state['portRange']['low'], state['portRange']['high'])
                    }

                    if self.window:
                        payload = json.dumps(state)
                        self.window.evaluate_js(
                            f"window.applyCaptureState && window.applyCaptureState({payload}, {{ suppressErrorToast: true }});"
                        )
                        self.window.evaluate_js(
                            "showNotification('Capture interface updated', 'info');"
                        )
                except Exception as capture_err:
                    logger.error(f"Failed to apply capture interface change: {capture_err}")
                    if self.window:
                        error_msg = str(capture_err).replace('"', '\\"')
                        self.window.evaluate_js(
                            f"showNotification('Failed to switch capture interface: {error_msg}', 'error');"
                        )

            previous_wireshark_path = previous_settings.get('wiresharkPath') if isinstance(previous_settings, dict) else None
            new_wireshark_path = updated_settings.get('wiresharkPath')
            if (new_wireshark_path or '') != (previous_wireshark_path or ''):
                resolved = self._apply_wireshark_path(new_wireshark_path, update_capture=True, propagate_state=True)
                if self.window:
                    if resolved:
                        safe_path = resolved.replace('\\', '\\\\').replace('"', '\"')
                        self.window.evaluate_js(
                            f"showNotification('Wireshark path updated: {safe_path}', 'info');"
                        )
                    else:
                        self.window.evaluate_js(
                            "showNotification('Wireshark path cleared. Using system PATH settings.', 'warning');"
                        )

            logger.info("Settings saved successfully")
            return True
            
        except (IOError, OSError) as e:
            logger.error(f"Error writing settings file: {e}")
            return False
        except ValueError as e:
            logger.error(f"Invalid settings data: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error saving settings: {e}")
            return False

    def _setup_global_hotkeys(self):
        import keyboard
        # Remove any existing hotkeys
        keyboard.unhook_all()
        
        # Setup sort hotkey
        sort_hotkey = self.settings_manager.get('sortHotkey', 'ctrl+f11')
        logger.info(f"Registering sort hotkey: {sort_hotkey}")
        keyboard.add_hotkey(sort_hotkey, self._trigger_sort_current, suppress=True)
        
        # Setup cancel hotkey
        cancel_hotkey = self.settings_manager.get('cancelHotkey', 'ctrl+f12')
        logger.info(f"Registering cancel hotkey: {cancel_hotkey}")
        keyboard.add_hotkey(cancel_hotkey, self._trigger_cancel_sort, suppress=True)
        
    def _apply_wireshark_path(self, wireshark_path, update_capture=False, propagate_state=False):
        resolved = resolve_tshark_executable(wireshark_path)
        if resolved:
            try:
                os.environ['PYSHARK_TSHARK_PATH'] = resolved
                bin_dir = os.path.dirname(resolved)
                if bin_dir and os.path.isdir(bin_dir):
                    current_path = os.environ.get('PATH', '')
                    segments = current_path.split(os.pathsep) if current_path else []
                    if bin_dir not in segments:
                        os.environ['PATH'] = os.pathsep.join([bin_dir] + segments) if segments else bin_dir
            except Exception as exc:
                logger.debug(f"Failed to update PATH for tshark: {exc}")
        else:
            os.environ.pop('PYSHARK_TSHARK_PATH', None)

        state = None
        if update_capture and hasattr(self, 'capture_controller') and self.capture_controller:
            state = self.capture_controller.set_wireshark_path(wireshark_path)
            if state:
                try:
                    self.capture_settings = {
                        'interface': state['interface'],
                        'port_range': (state['portRange']['low'], state['portRange']['high'])
                    }
                except Exception:
                    pass
                if self.window and propagate_state:
                    try:
                        payload = json.dumps(state)
                        self.window.evaluate_js(
                            f"window.applyCaptureState && window.applyCaptureState({payload}, {{ suppressErrorToast: true }});"
                        )
                    except Exception as exc:
                        logger.debug(f"Unable to propagate capture state to UI: {exc}")

        self._wireshark_path = wireshark_path or ''
        return resolved

    def select_wireshark_path(self):
        if not self.window:
            return {"success": False, "error": "Window not initialized"}

        current_setting = self.settings_manager.get('wiresharkPath') or ''
        initial_dir = None
        expanded = os.path.expandvars(os.path.expanduser(current_setting)).strip().strip('"') if current_setting else ''
        if expanded:
            if os.path.isdir(expanded):
                initial_dir = expanded
            elif os.path.isfile(expanded):
                initial_dir = os.path.dirname(expanded)

        if not initial_dir:
            default_candidate = os.path.expandvars(r'%ProgramFiles%\Wireshark')
            if os.path.isdir(default_candidate):
                initial_dir = default_candidate

        try:
            selection = self.window.create_file_dialog(
                getattr(webview, "FileDialog", webview).FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG,
                directory=initial_dir,
                allow_multiple=False
            )
            if selection and len(selection) > 0:
                chosen = selection[0]
                return {"success": True, "path": chosen}
            return {"success": False}
        except Exception as exc:
            logger.error(f"Wireshark path dialog failed: {exc}")
            return {"success": False, "error": str(exc)}

    def detect_wireshark_path(self):
        candidates = [
            r"C:\Program Files\Wireshark",
            r"C:\Program Files (x86)\Wireshark",
            r"D:\Program Files\Wireshark",
            r"D:\Program Files (x86)\Wireshark",
            r"E:\Program Files\Wireshark",
            r"E:\Program Files (x86)\Wireshark",
        ]

        env_path = os.environ.get('WIRESHARK_PATH') or os.environ.get('WINDIR', '')
        if env_path:
            env_candidate = os.path.join(env_path, 'Wireshark')
            candidates.append(env_candidate)

        detected = None
        for path in candidates:
            expanded = os.path.expandvars(os.path.expanduser(path))
            tshark_path = resolve_tshark_executable(expanded)
            if tshark_path:
                detected = os.path.dirname(tshark_path)
                break

        if not detected:
            on_path = shutil.which('tshark') or shutil.which('wireshark')
            if on_path:
                detected = os.path.dirname(on_path)

        if detected:
            return {"success": True, "path": detected}

        return {"success": False, "error": "Wireshark installation not found in common locations."}

    @property
    def packet_capture(self):
        return self.capture_controller.packet_capture
        
    def set_window(self, window):
        """Set the window reference for JavaScript evaluation"""
        self.window = window
        # Do NOT access window.width/height/x/y here!
        # These will be set after the window is loaded

    def set_initial_window_state(self):
        # Called after window is loaded and GUI is ready
        if self.window:
            self.original_size = (self.window.width, self.window.height)
            self.original_position = (self.window.x, self.window.y)
            self.bring_window_to_front()

    def bring_window_to_front(self):
        """Attempt to bring the UI window to the foreground across platforms."""
        if not self.window:
            return False

        success = False

        try:
            if hasattr(self.window, 'restore'):
                self.window.restore()
        except Exception as exc:
            logger.debug(f"Failed to restore window before foreground request: {exc}")

        for method_name in ('activate', 'bring_to_front', 'focus'):
            if hasattr(self.window, method_name):
                try:
                    getattr(self.window, method_name)()
                    success = True
                    break
                except Exception as exc:
                    logger.debug(f"Window.{method_name}() failed: {exc}")

        if not success:
            try:
                import pygetwindow as gw  # type: ignore

                title = getattr(self.window, 'title', None) or 'Dark and Darker Stash Organizer'
                candidates = gw.getWindowsWithTitle(title)
                for candidate in candidates:
                    try:
                        candidate.restore()
                        candidate.activate()
                        success = True
                        break
                    except Exception as gw_exc:
                        logger.debug(f"pygetwindow activate failed: {gw_exc}")
            except Exception as exc:
                logger.debug(f"pygetwindow foreground attempt failed: {exc}")

        if not success and sys.platform.startswith('win'):
            try:
                import ctypes

                user32 = ctypes.windll.user32
                hwnd = getattr(self.window, 'hwnd', None)

                if not hwnd:
                    title = getattr(self.window, 'title', None) or 'Dark and Darker Stash Organizer'
                    hwnd = user32.FindWindowW(None, title)

                if hwnd:
                    SW_RESTORE = 9
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
                    success = True
            except Exception as exc:
                logger.debug(f"Win32 foreground attempt failed: {exc}")

        if not success:
            logger.debug("Unable to bring window to front; continuing without foreground focus")

        return success

    def begin_drag(self):
        """Initiate a native window drag so Windows snap/maximize works."""
        if not self.window:
            return False

        if not sys.platform.startswith('win'):
            return False

        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = getattr(self.window, 'hwnd', None)
            if not hwnd:
                # Attempt to resolve window handle by title as a fallback
                title = getattr(self.window, 'title', None) or 'Dark and Darker Stash Organizer'
                hwnd = user32.FindWindowW(None, title)
                if not hwnd:
                    return False

            WM_NCLBUTTONDOWN = 0x00A1
            HTCAPTION = 0x0002
            user32.ReleaseCapture()
            user32.SendMessageW(int(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)
            return True
        except Exception as exc:
            logger.error(f"Failed to initiate native drag: {exc}")
            return False

    def _trigger_sort_current(self):
        """Triggered by global hotkey to sort current stash"""
        logger.info(f"Sort hotkey activated: {self.settings_manager.get('sortHotkey')}")
        current_char_id = self._current_char_id
        current_stash_id = self._current_stash_id
        if current_char_id and current_stash_id:
            logger.info(f"Scheduling sort for character {current_char_id}, stash {current_stash_id}")
            threading.Thread(target=self._sort_worker, daemon=True).start()
        else:
            logger.warning("No current stash selected")

    def _sort_worker(self):
        """Background worker for sorting current stash"""
        if self.window:
            self.window.evaluate_js('window.dispatchEvent(new Event("sortingStarted"))')
        result = self.sort_stash(
            self._current_char_id,
            self._current_stash_id,
            pack_mode=self.get_pack_mode(),
            stack_mode=self.get_stack_mode(),
        )
        if self.window:
            self.window.evaluate_js('window.dispatchEvent(new Event("sortingEnded"))')
        # Optionally, communicate result back to UI
        
    def _trigger_cancel_sort(self):
        """Triggered by global hotkey to cancel current sort operation"""
        logger.info(f"Cancel hotkey activated: {self.settings_manager.get('cancelHotkey')}")
        if self.current_sort_event and not self.current_sort_event.is_set():
            self.current_sort_event.set()
            logger.info("Sort operation cancelled")
            if self.window:
                self.window.evaluate_js('window.dispatchEvent(new Event("sortingEnded"))')

    def get_characters(self):
        return self.stash_manager.get_characters()

    def get_character_details(self, character_id):
        return self.stash_manager.get_character_details(character_id)

    def get_capture_settings(self):
        """Return current packet capture settings"""
        return self.capture_settings

    def search_items(self, query):
        return self.stash_manager.search_items(query)

    def set_capture_settings(self, interface, port_low, port_high):
        state = self.capture_controller.update_settings(interface, port_low, port_high)
        self.capture_settings = {
            'interface': state['interface'],
            'port_range': (state['portRange']['low'], state['portRange']['high'])
        }
        return True

    def start_capture(self):
        # perform capture synchronously; return True only when valid data file is saved
        asyncio.set_event_loop(asyncio.new_event_loop())
        result = self.packet_capture.capture()
        if result:
            # Reload data after successful capture
            self.stash_manager.characters_cache = {}
            self.stash_manager._load_data()
        return result

    def get_character_stash_previews(self, character_id):
        return self.stash_manager.get_character_stash_previews(character_id)

    def start_capture_switch(self):
        success, state = self.capture_controller.start()
        return success, state

    def stop_capture_switch(self):
        success, state = self.capture_controller.stop()
        return success, state

    def restart_capture_switch(self):
        """Stop capture if running and start it again"""
        success, state = self.capture_controller.restart()
        self._initial_restart_done = True
        return success, state

    def get_capture_state(self):
        """Get current capture state including if initial restart was done"""
        state = self.capture_controller.state()
        state["initialRestartDone"] = self._initial_restart_done
        return state

    def sort_stash(self, character_id, stash_id, pack_mode=None, stack_mode=None):
        """Sort a specific stash for a character"""
        self.current_sort_event = threading.Event()
        success = False
        error_msg: Optional[str] = None

        # Resolve context information for overlay heading
        overlay_context = {
            "character": None,
            "character_id": character_id,
            "stash": stash_id,
        }
        try:
            char_details = self.stash_manager.get_character_details(str(character_id))
            if char_details:
                overlay_context["character"] = char_details.get("nickname")
                overlay_context["character_class"] = char_details.get("class")
        except Exception as exc:
            logger.debug(f"Unable to resolve character details for overlay: {exc}")

        overlay_session = self.overlay_manager.begin_sort_session(
            countdown_seconds=1.0,
            context=overlay_context,
        )

        try:
            if pack_mode is None:
                pack_mode = self.get_pack_mode()
            else:
                self.set_pack_mode(pack_mode)
                pack_mode = self.get_pack_mode()

            if stack_mode is None:
                stack_mode = self.get_stack_mode()
            else:
                self.set_stack_mode(stack_mode)
                stack_mode = self.get_stack_mode()

            if overlay_session.wait_for_countdown():
                overlay_session.update_status(
                    "Preparing stash data...", status="info"
                )

            result = self.stash_manager.sort_stash(
                character_id,
                stash_id,
                cancel_event=self.current_sort_event,
                pack_mode=pack_mode,
                stack_mode=stack_mode,
                overlay_session=overlay_session,
            )

            if isinstance(result, tuple):
                success, error_msg = result
            else:
                success = bool(result)

            return {"success": success, "error": error_msg}
        except Exception as exc:
            error_msg = str(exc)
            logger.error(f"Error in sort_stash: {error_msg}", exc_info=True)
            return {"success": False, "error": error_msg}
        finally:
            overlay_session.finish(success, error_msg)
            self.current_sort_event = None

    def minimize(self):
        self.window.minimize()
        
    def toggle_maximize(self):
        if self.is_maximized:
            self.window.restore()
            self.is_maximized = False
            # Notify JS of restore
            if self.window:
                self.window.evaluate_js(
                    'window.dispatchEvent(new CustomEvent("windowStateChanged", { detail: { maximized: false } }));'
                )
        else:
            self.window.maximize()
            self.is_maximized = True                # Notify JS of maximize
            if self.window:
                self.window.evaluate_js(
                    'window.dispatchEvent(new CustomEvent("windowStateChanged", { detail: { maximized: true } }));'
                )

    def close_window(self):
        """Properly save capture state before closing the window"""
        try:
            self._update_closing_overlay("Stopping capture...")
            if hasattr(self, 'capture_controller'):
                self.capture_controller.shutdown()
            elif hasattr(self, 'packet_capture'):
                self.packet_capture.shutdown()
        except Exception as e:
            logger.error(f"Error during window close: {e}")
        finally:
            self._update_closing_overlay("Capture stopped. Closing application...")
            try:
                time.sleep(0.2)
            except Exception:
                pass
            # Close immediately without delays
            self.force_close_window()
            
    def force_close_window(self):
        # Quick shutdown without delays
        try:
            if hasattr(self, 'capture_controller'):
                self.capture_controller.packet_capture.running = False
            elif hasattr(self, 'packet_capture') and self.packet_capture.running:
                self.packet_capture.running = False
        except Exception as e:
            logger.error(f"Error stopping packet capture on close: {e}")
        # Remove delay - close immediately
        self.window.destroy()
        
    def set_sort_order(self, order):
        try:
            normalized = Item.normalize_sort_order(order)
            Item.sort_order = normalized
            self.settings_manager.update({'stashSortOrder': normalized})
            return True
        except Exception as exc:
            logger.error(f"Failed to update stash sort order: {exc}")
            return False

    def get_sort_order(self):
        return list(Item.sort_order)

    def set_pack_mode(self, pack):
        if pack is None:
            return True
        pack_bool = bool(pack)
        previous = getattr(self, '_current_pack_mode', False)
        self._current_pack_mode = pack_bool
        if pack_bool != previous:
            try:
                self.settings_manager.update({'stashPackMode': pack_bool})
            except Exception as exc:
                logger.error(f"Failed to persist pack mode preference: {exc}")
                return False
        return True

    def get_pack_mode(self):
        return bool(getattr(self, '_current_pack_mode', False))

    def set_stack_mode(self, stack):
        if stack is None:
            return True
        stack_bool = bool(stack)
        previous = getattr(self, '_current_stack_mode', False)
        self._current_stack_mode = stack_bool
        if stack_bool != previous:
            try:
                self.settings_manager.update({'stashStackMode': stack_bool})
            except Exception as exc:
                logger.error(f"Failed to persist stack mode preference: {exc}")
                return False
        return True

    def get_stack_mode(self):
        return bool(getattr(self, '_current_stack_mode', False))

@server.route('/api/download_update')
def download_update():
    """Instead of downloading the update, redirect to the latest release page."""
    try:
        # Try with GitHub API to get the release URL
        logger.info("Redirecting to GitHub releases page")
        response = requests.get(
            'https://api.github.com/repos/Beelzebub2/DnDTools/releases/latest',
            headers={'User-Agent': 'DnDTools-Updater'},
            timeout=10
        )
        
        if response.ok:
            release_data = response.json()
            release_url = release_data.get('html_url', 'https://github.com/Beelzebub2/DnDTools/releases/latest')
            logger.info(f"Redirecting to: {release_url}")
            return redirect(release_url)
        else:
            # If GitHub API fails, redirect to the main releases page
            return redirect('https://github.com/Beelzebub2/DnDTools/releases/latest')
            
    except Exception as e:
        error_msg = f"Error redirecting to update page: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return jsonify({'error': error_msg}), 500

def get_version_info():
    """Get the latest version from dndtools.rrmtools.uk API with fallback to GitHub API."""
    global version_cache, version_cache_timestamp
    
    current_time = time.time()
    
    # Check if we have a valid cached response
    if version_cache and (current_time - version_cache_timestamp) < VERSION_CACHE_DURATION:
        logger.info("Returning cached version information")
        return version_cache
    
    # Cache expired or not set, fetch new data
    try:
        # First try dndtools.rrmtools.uk API
        logger.info("Attempting to fetch version information from dndtools.rrmtools.uk API")
        response = requests.get(
            'https://dndtools.rrmtools.uk/api/github/latest-release', 
            headers={'User-Agent': 'DnDTools-Updater'},
            timeout=10
        )
        
        # If dndtools.rrmtools.uk fails, try GitHub API directly
        if not response.ok:
            logger.warning(f"dndtools.rrmtools.uk API failed with status {response.status_code}, trying GitHub API directly")
            response = requests.get(
                'https://api.github.com/repos/Beelzebub2/DnDTools/releases/latest',
                headers={'User-Agent': 'DnDTools-Updater'},
                timeout=10
            )
            if not response.ok:
                error_msg = f"Both APIs failed. GitHub API status: {response.status_code}"
                logger.error(error_msg)
                return {'version': APP_VERSION, 'error': error_msg}
                
        release_data = response.json()
        
        # Log the release data keys for debugging
        logger.info(f"Release data received with keys: {list(release_data.keys())}")
        
        # Try multiple ways to extract the version
        version = APP_VERSION
        
        # First try: tag_name from GitHub API
        if 'tag_name' in release_data:
            version = release_data['tag_name'].replace('v', '')
            logger.info(f"Version extracted from tag_name: {version}")
            
        # Second try: html_url from either API (e.g., .../releases/tag/v2.0.0)
        elif 'html_url' in release_data and '/tag/' in release_data['html_url']:
            tag_part = release_data['html_url'].split('/tag/')[-1]
            version = tag_part.replace('v', '')
            logger.info(f"Version extracted from html_url: {version}")
            
        # Include the release URL for the UI
        release_url = release_data.get('html_url', 'https://github.com/Beelzebub2/DnDTools/releases/latest')
        
        # Cache the successful result
        result = {
            'version': version,
            'release_url': release_url
        }
        
        version_cache = result
        version_cache_timestamp = current_time
        
        return result
        
    except requests.exceptions.Timeout:
        error_msg = "Connection timed out while fetching version information"
        logger.error(error_msg)
        return {'version': APP_VERSION, 'error': error_msg}
        
    except requests.exceptions.ConnectionError as e:
        error_msg = f"Connection error: {str(e)}"
        logger.error(error_msg)
        return {'version': APP_VERSION, 'error': error_msg}
        
    except ValueError as e:
        error_msg = f"Invalid JSON response: {str(e)}"
        logger.error(error_msg)
        return {'version': APP_VERSION, 'error': error_msg}
        
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'version': APP_VERSION, 'error': error_msg}

@server.route('/api/version')
def api_version():
    """Get the latest version from dndtools.rrmtools.uk API with fallback to GitHub API."""
    return jsonify(get_version_info())

@server.route('/api/local_version')
def api_local_version():
    """Return the local version of the app."""
    return jsonify({'version': APP_VERSION})


def _snapshot_update_state() -> dict:
    with _update_lock:
        return {
            'in_progress': _update_state['in_progress'],
            'last_error': _update_state['last_error'],
        }


@server.route('/api/update/check')
def api_update_check():
    manifest = _fetch_update_manifest()
    if not manifest:
        state = _snapshot_update_state()
        state.update(
            {
                'currentVersion': APP_VERSION,
                'latestVersion': APP_VERSION,
                'updateAvailable': False,
                'notes': '',
                'downloadUrl': '',
                'sha256': '',
                'error': 'Unable to retrieve update manifest',
            }
        )
        return jsonify(state), 503

    remote_version = str(manifest.get('version', APP_VERSION))
    update_available = _is_remote_newer(remote_version, APP_VERSION)
    state = _snapshot_update_state()
    state.update(
        {
            'currentVersion': APP_VERSION,
            'latestVersion': remote_version,
            'updateAvailable': update_available,
            'notes': manifest.get('notes', ''),
            'downloadUrl': manifest.get('url', ''),
            'sha256': manifest.get('sha256', ''),
        }
    )
    return jsonify(state)


@server.route('/api/update/status')
def api_update_status():
    return jsonify(_snapshot_update_state())


@server.route('/api/update/apply', methods=['POST'])
def api_update_apply():
    with _update_lock:
        if _update_state['in_progress']:
            return jsonify({'started': False, 'error': 'Update already in progress'}), 409
        _update_state['last_error'] = None

    manifest = _fetch_update_manifest(force=True)
    if not manifest:
        return jsonify({'started': False, 'error': 'Unable to retrieve update manifest'}), 503

    remote_version = str(manifest.get('version', APP_VERSION))
    if not _is_remote_newer(remote_version, APP_VERSION):
        return jsonify({'started': False, 'error': 'Already up to date'}), 400

    threading.Thread(target=_perform_update, args=(manifest,), daemon=True).start()
    return jsonify({'started': True})

# Initialize API
api = Api()

# JSON API endpoint
@server.route('/api/characters')
def api_characters():
    return jsonify(api.get_characters())

@server.route('/api/character/<character_id>/stashes')
def api_character_stashes(character_id):
    return jsonify(api.get_character_stash_previews(character_id))

@server.route('/api/character/<character_id>/details')
def api_character_details(character_id):
    return jsonify(api.get_character_details(character_id) or {}), 200

@server.route('/output/<path:filename>')
def serve_preview(filename):
    from src.models.appdirs import get_output_dir
    output_dir = get_output_dir()
    return send_from_directory(output_dir, filename)

@server.route('/api/search_items')
def api_search_items():
    query = request.args.get('query', '')
    return jsonify(api.search_items(query))


@server.route('/api/quests', methods=['GET'])
def api_quests_list():
    refresh = (request.args.get('refresh') or '').strip().lower()
    merchant_filter = (request.args.get('merchant') or '').strip()
    force_refresh = refresh in {'1', 'true', 'yes', 'force'}

    try:
        quests = _fetch_quests_data(force=force_refresh)
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to contact DarkerDB quests API: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Unable to reach DarkerDB quests API'}), 502
    except Exception as exc:
        logger.error("Unexpected error retrieving quests: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to load quests'}), 500

    items_index = _load_items_index()
    merchant_filter_normalized = _normalize_merchant_name(merchant_filter)
    merchant_filter_lower = merchant_filter_normalized.lower() if merchant_filter_normalized else None

    merchants = sorted(
        {
            _normalize_merchant_name(quest.get('merchant'))
            for quest in quests
            if _normalize_merchant_name(quest.get('merchant'))
        },
        key=lambda name: name.lower()
    )

    enriched_quests: list[dict] = []
    for quest in quests:
        merchant_name_original = quest.get('merchant') or ''
        merchant_name = _normalize_merchant_name(merchant_name_original)
        if merchant_filter_lower and merchant_name.lower() != merchant_filter_lower:
            continue

        objectives_payload: list[dict] = []
        for objective in quest.get('objectives') or []:
            objective_payload = {
                'type': objective.get('type'),
                'count': objective.get('count'),
                'item_id': objective.get('item_id'),
                'monster': objective.get('monster'),
                'interact': objective.get('interact'),
                'module': objective.get('module'),
                'must_escape': objective.get('must_escape', False),
            }
            if objective.get('item_id'):
                objective_payload['item'] = _build_item_payload(items_index.get(objective['item_id']))
            objectives_payload.append(objective_payload)

        rewards_payload: list[dict] = []
        for reward in quest.get('rewards') or []:
            reward_payload = dict(reward)
            item_id = reward.get('item_id')
            if item_id:
                reward_payload['item'] = _build_item_payload(items_index.get(item_id))
            rewards_payload.append(reward_payload)

        enriched_quests.append({
            'id': quest.get('id'),
            'title': quest.get('title') or quest.get('id') or 'Unknown Quest',
            'chapter': quest.get('chapter'),
            'chapter_id': quest.get('chapter_id'),
            'prerequisite': quest.get('prerequisite'),
            'dungeons': quest.get('dungeons') or [],
            'merchant': merchant_name,
            'merchant_original': merchant_name_original,
            'text': quest.get('text'),
            'completion_text': quest.get('completion_text'),
            'objectives': objectives_payload,
            'rewards': rewards_payload,
        })

    return jsonify({
        'success': True,
        'quests': enriched_quests,
        'merchants': merchants,
        'last_updated': datetime.utcnow().isoformat() + 'Z',
        'total': len(enriched_quests),
        'cached': not force_refresh,
    })


@server.route('/api/quests/items', methods=['GET'])
def api_quests_item_requirements():
    refresh = (request.args.get('refresh') or '').strip().lower()
    force_refresh = refresh in {'1', 'true', 'yes', 'force'}

    try:
        quests = _fetch_quests_data(force=force_refresh)
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to contact DarkerDB quests API for item requirements: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Unable to reach DarkerDB quests API'}), 502
    except Exception as exc:
        logger.error("Unexpected error retrieving quest requirements: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to load quest requirements'}), 500

    items_index = _load_items_index()

    aggregated: dict[str, dict] = {}
    for quest in quests:
        quest_id = quest.get('id')
        quest_title = quest.get('title') or quest_id or 'Unknown Quest'
        merchant_name_original = quest.get('merchant')
        merchant_name = _normalize_merchant_name(merchant_name_original)
        dungeons = quest.get('dungeons') or []

        for objective in quest.get('objectives') or []:
            item_id = objective.get('item_id')
            if not item_id:
                continue
            try:
                count = int(objective.get('count') or 0)
            except (TypeError, ValueError):
                count = 0
            if count <= 0:
                continue

            entry = aggregated.setdefault(item_id, {
                'item_id': item_id,
                'total_required': 0,
                'quests': [],
                'merchant_counts': {},
                'dungeons': set(),
            })
            entry['total_required'] += count
            entry['quests'].append({
                'id': quest_id,
                'title': quest_title,
                'merchant': merchant_name,
                'merchant_original': merchant_name_original,
                'count': count,
                'chapter': quest.get('chapter'),
            })
            if merchant_name:
                entry['merchant_counts'][merchant_name] = entry['merchant_counts'].get(merchant_name, 0) + count
            for dungeon in dungeons:
                entry['dungeons'].add(dungeon)

    items_payload: list[dict] = []
    for item_id, entry in aggregated.items():
        item_meta = items_index.get(item_id)
        meta_payload = _build_item_payload(item_meta)
        result_payload = {
            **meta_payload,
            'item_id': item_id,
            'name': meta_payload.get('name') or _normalize_item_name(item_id),
            'rarity': meta_payload.get('rarity') or 'Unknown',
            'type': meta_payload.get('type'),
            'total_required': entry['total_required'],
            'merchants': sorted(
                (
                    {'name': name, 'count': count}
                    for name, count in entry['merchant_counts'].items()
                ),
                key=lambda payload: (-payload['count'], payload['name'])
            ),
            'quests': sorted(
                entry['quests'],
                key=lambda payload: (
                    (payload['merchant'] or '').lower(),
                    (payload['title'] or '').lower()
                )
            ),
            'dungeons': sorted(entry['dungeons']),
        }
        items_payload.append(result_payload)

    items_payload.sort(
        key=lambda payload: (
            RARITY_ORDER.get((payload.get('rarity') or '').title(), 999),
            payload.get('name') or ''
        )
    )

    return jsonify({
        'success': True,
        'items': items_payload,
        'total': len(items_payload),
        'last_updated': datetime.utcnow().isoformat() + 'Z',
        'cached': not force_refresh,
    })


@server.route('/api/quests/items/holdings', methods=['GET'])
def api_quests_item_holdings():
    raw_ids = (request.args.get('ids') or '').strip()
    if not raw_ids:
        return jsonify({'success': False, 'error': 'No item ids provided'}), 400

    item_ids = [item_id.strip() for item_id in raw_ids.split(',') if item_id and item_id.strip()]
    if not item_ids:
        return jsonify({'success': False, 'error': 'No valid item ids provided'}), 400

    try:
        holdings_map = api.stash_manager.get_item_holdings(item_ids)
    except Exception as exc:
        logger.error("Failed to aggregate quest item holdings: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to calculate holdings'}), 500

    sanitized: dict[str, dict] = {}
    for item_id in item_ids:
        entries = holdings_map.get(item_id, []) or []
        characters: list[dict] = []
        total_owned = 0

        for entry in entries:
            entry_total_raw = entry.get('total', 0)
            try:
                entry_total = max(0, int(entry_total_raw))
            except (TypeError, ValueError):
                entry_total = 0
            total_owned += entry_total

            stashes_payload: list[dict] = []
            for stash in entry.get('stashes', []) or []:
                if not isinstance(stash, dict):
                    continue
                count_raw = stash.get('count', 0)
                try:
                    count_value = max(0, int(count_raw))
                except (TypeError, ValueError):
                    count_value = 0
                stashes_payload.append({
                    'stash_id': stash.get('stash_id'),
                    'count': count_value,
                    'slot_id': stash.get('slot_id')
                })

            characters.append({
                'character_id': entry.get('character_id'),
                'character_name': entry.get('character_name'),
                'character_class': entry.get('character_class'),
                'character_level': entry.get('character_level'),
                'last_update': entry.get('last_update'),
                'total': entry_total,
                'stashes': stashes_payload,
            })

        sanitized[item_id] = {
            'total': total_owned,
            'characters': characters
        }

    return jsonify({
        'success': True,
        'items': sanitized
    })


@server.route('/api/quests/progress', methods=['GET', 'POST', 'DELETE'])
def api_quests_progress():
    if request.method == 'GET':
        progress, timestamp = _load_quest_progress()
        last_updated = None
        if timestamp:
            try:
                last_updated = datetime.utcfromtimestamp(timestamp).isoformat() + 'Z'
            except (OverflowError, ValueError):
                last_updated = None
        return jsonify({
            'success': True,
            'progress': progress,
            'last_updated': last_updated,
        })

    if request.method == 'DELETE':
        removed = False
        try:
            os.remove(QUESTS_PROGRESS_FILE)
            removed = True
        except FileNotFoundError:
            removed = False
        except OSError as exc:
            logger.warning("Failed to remove quest progress file: %s", exc, exc_info=True)
            return jsonify({'success': False, 'error': 'Unable to clear quest progress data'}), 500

        return jsonify({
            'success': True,
            'progress': _default_progress_payload(),
            'removed': removed,
        })

    # POST handler
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid JSON payload'}), 400

    progress_payload = payload.get('progress')
    if not isinstance(progress_payload, dict):
        return jsonify({'success': False, 'error': 'Progress payload must be an object'}), 400

    _save_quest_progress(progress_payload)
    return jsonify({'success': True})


@server.route('/api/quests/cache', methods=['DELETE'])
def api_clear_quest_cache():
    results = _clear_quest_storage()
    if not any(results.values()):
        return jsonify({'success': True, 'message': 'Quest cache already empty', 'results': results})
    return jsonify({'success': True, 'results': results})


@server.route('/api/characters/data', methods=['DELETE'])
def api_clear_character_data():
    results = _clear_character_storage()
    status_code = 200 if results.get('success') else 500
    return jsonify(results), status_code

@server.route('/api/capture/settings', methods=['GET', 'POST'])
def api_capture_settings():
    if request.method == 'GET':
        return jsonify(api.get_capture_settings())
    
    try:
        data = request.get_json() or {}
        
        # Validate input data
        interface = data.get('interface', '').strip()
        port_low = data.get('port_low')
        port_high = data.get('port_high')
        
        # Validate port numbers
        if port_low is not None:
            try:
                port_low = int(port_low)
                if not (1 <= port_low <= 65535):
                    return jsonify({'success': False, 'error': 'Port low must be between 1 and 65535'}), 400
            except (ValueError, TypeError):
                return jsonify({'success': False, 'error': 'Invalid port_low value'}), 400
                
        if port_high is not None:
            try:
                port_high = int(port_high)
                if not (1 <= port_high <= 65535):
                    return jsonify({'success': False, 'error': 'Port high must be between 1 and 65535'}), 400
            except (ValueError, TypeError):
                return jsonify({'success': False, 'error': 'Invalid port_high value'}), 400
                
        if port_low is not None and port_high is not None and port_low > port_high:
            return jsonify({'success': False, 'error': 'Port low cannot be greater than port high'}), 400
            
        return jsonify({'success': api.set_capture_settings(interface, port_low, port_high)})
        
    except Exception as e:
        logger.error(f"Error processing capture settings: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@server.route('/api/capture/start', methods=['POST'])
def api_capture_start():
    try:
        return jsonify({'success': api.start_capture()})
    except Exception as e:
        logger.error(f"Error starting capture: {e}")
        return jsonify({'success': False, 'error': 'Failed to start capture'}), 500

@server.route('/api/record_character/<character_id>', methods=['POST'])
def api_record_character(character_id):
    # Validate character_id format (basic validation)
    if not character_id or not character_id.strip():
        return jsonify({'success': False, 'error': 'Invalid character ID'}), 400
    return jsonify({'success': False, 'error': 'Recording individual characters is no longer supported'})

@server.route('/api/capture/switch/start', methods=['POST'])
def capture_switch_start():
    try:
        success, state = api.start_capture_switch()
        return jsonify({'success': success, 'state': state})
    except Exception as e:
        logger.error(f"Error starting capture switch: {e}")
        return jsonify({'success': False, 'error': 'Failed to start capture'}), 500

@server.route('/api/capture/switch/stop', methods=['POST'])
def capture_switch_stop():
    try:
        success, state = api.stop_capture_switch()
        return jsonify({'success': success, 'state': state})
    except Exception as e:
        logger.error(f"Error stopping capture switch: {e}")
        return jsonify({'success': False, 'error': 'Failed to stop capture'}), 500

@server.route('/api/capture/switch/restart', methods=['POST'])
def capture_switch_restart():
    try:
        success, state = api.restart_capture_switch()
        return jsonify({'success': success, 'state': state})
    except Exception as e:
        logger.error(f"Error restarting capture switch: {e}")
        return jsonify({'success': False, 'error': 'Failed to restart capture'}), 500

@server.route('/api/capture/state', methods=['GET'])
def api_capture_state():
    try:
        return jsonify(api.get_capture_state())
    except Exception as e:
        logger.error(f"Error getting capture state: {e}")
        return jsonify({'success': False, 'error': 'Failed to get capture state'}), 500

@server.route('/api/network_interfaces', methods=['GET'])
def api_network_interfaces():
    interfaces = list(psutil.net_if_addrs().keys())
    return jsonify({"interfaces": interfaces})

@server.route('/api/character/<character_id>/stash/<stash_id>/sort', methods=['POST'])
def api_sort_stash(character_id, stash_id):
    # Validate inputs
    character_id = validate_character_id(character_id)
    if not character_id:
        return jsonify({'success': False, 'error': 'Invalid character ID'}), 400
        
    stash_id = validate_stash_id(stash_id)
    if stash_id is None:
        return jsonify({'success': False, 'error': 'Invalid stash ID'}), 400
    payload = request.get_json(silent=True) or {}
    pack_mode = None
    stack_mode = None
    if isinstance(payload, dict):
        if 'pack' in payload:
            raw_pack = payload.get('pack')
            if isinstance(raw_pack, str):
                pack_mode = raw_pack.lower() in {'1', 'true', 'yes', 'on'}
            else:
                pack_mode = bool(raw_pack)
        if 'stack' in payload:
            raw_stack = payload.get('stack')
            if isinstance(raw_stack, str):
                stack_mode = raw_stack.lower() in {'1', 'true', 'yes', 'on'}
            else:
                stack_mode = bool(raw_stack)
    try:
        result = api.sort_stash(character_id, stash_id, pack_mode=pack_mode, stack_mode=stack_mode)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error sorting stash: {e}")
        return jsonify({'success': False, 'error': 'Failed to sort stash'}), 500

@server.route('/api/character/<character_id>/current-stash', methods=['GET'])
def api_get_current_stash(character_id):
    # Validate input
    character_id = validate_character_id(character_id)
    if not character_id:
        return jsonify({'success': False, 'error': 'Invalid character ID'}), 400
        
    try:
        """Get the last selected stash ID for a character"""
        if hasattr(api, '_current_char_id') and api._current_char_id == character_id and hasattr(api, '_current_stash_id') and api._current_stash_id:
            return jsonify({'stashId': api._current_stash_id})
        from flask import session
        stash_id = session.get(f'{character_id}_current_stash_id', None)
        return jsonify({'stashId': stash_id})
    except Exception as e:
        logger.error(f"Error getting current stash: {e}")
        return jsonify({'success': False, 'error': 'Failed to get current stash'}), 500

@server.route('/api/character/<character_id>/current-stash/<stash_id>', methods=['POST'])
def api_set_current_stash(character_id, stash_id):
    # Validate inputs
    character_id = validate_character_id(character_id)
    if not character_id:
        return jsonify({'success': False, 'error': 'Invalid character ID'}), 400
        
    stash_id = validate_stash_id(stash_id)
    if stash_id is None:
        return jsonify({'success': False, 'error': 'Invalid stash ID'}), 400
    """Set the current stash ID for a character"""
    # Update the global variables in the API class
    api._current_char_id = character_id
    api._current_stash_id = stash_id
    
    # Also store in session for persistence across page reloads
    from flask import session
    session[f'{character_id}_current_stash_id'] = stash_id
    
    logger.info(f"Current stash updated to character {character_id}, stash {stash_id}")
    return jsonify({'success': True})

@server.route('/')
def index():
    # if not check_tshark():
    #     return redirect(url_for('installing'))
    return render_template('index.html')

@server.route('/settings')
def settings():
    return render_template('settings.html', app_version=APP_VERSION)

@server.route('/record')
def record():
    return render_template('record.html')

@server.route('/character/<character_id>')
def character(character_id):
    return render_template('character.html')

@server.route('/search')
def search():
    return render_template('search.html')


@server.route('/quests')
def quests():
    return render_template('quest.html')

# Add these routes after the other API routes
@server.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'GET':
        return jsonify(api.settings_manager.data)
    data = request.get_json()
    return jsonify({'success': api._save_settings(data)})


@server.route('/api/pack_mode', methods=['GET'])
def api_get_pack_mode():
    try:
        return jsonify({'success': True, 'pack': api.get_pack_mode()})
    except Exception as exc:
        logger.error(f"Error retrieving pack mode: {exc}")
        return jsonify({'success': False, 'error': 'Failed to get pack mode'}), 500


@server.route('/api/pack_mode', methods=['POST'])
def api_set_pack_mode_route():
    payload = request.get_json(silent=True) or {}
    pack = None
    if isinstance(payload, dict) and 'pack' in payload:
        raw_pack = payload.get('pack')
        if isinstance(raw_pack, str):
            pack = raw_pack.lower() in {'1', 'true', 'yes', 'on'}
        else:
            pack = bool(raw_pack)
    try:
        success = api.set_pack_mode(pack)
        if not success:
            return jsonify({'success': False, 'error': 'Failed to save pack mode'}), 500
        return jsonify({'success': True, 'pack': api.get_pack_mode()})
    except Exception as exc:
        logger.error(f"Error updating pack mode: {exc}")
        return jsonify({'success': False, 'error': 'Failed to set pack mode'}), 500


@server.route('/api/stack_mode', methods=['GET'])
def api_get_stack_mode():
    try:
        return jsonify({'success': True, 'stack': api.get_stack_mode()})
    except Exception as exc:
        logger.error(f"Error retrieving stack mode: {exc}")
        return jsonify({'success': False, 'error': 'Failed to get stack mode'}), 500


@server.route('/api/stack_mode', methods=['POST'])
def api_set_stack_mode_route():
    payload = request.get_json(silent=True) or {}
    stack = None
    if isinstance(payload, dict) and 'stack' in payload:
        raw_stack = payload.get('stack')
        if isinstance(raw_stack, str):
            stack = raw_stack.lower() in {'1', 'true', 'yes', 'on'}
        else:
            stack = bool(raw_stack)
    try:
        success = api.set_stack_mode(stack)
        if not success:
            return jsonify({'success': False, 'error': 'Failed to save stack mode'}), 500
        return jsonify({'success': True, 'stack': api.get_stack_mode()})
    except Exception as exc:
        logger.error(f"Error updating stack mode: {exc}")
        return jsonify({'success': False, 'error': 'Failed to set stack mode'}), 500

@server.route('/assets/<path:filename>')
def serve_file(filename):
    canonical = canonical_icon_path(filename)
    if canonical and canonical.startswith('icons/'):
        stream = icon_store.stream(canonical)
        if stream:
            response = send_file(
                stream,
                mimetype='image/webp',
                download_name=canonical.split('/')[-1],
                conditional=True
            )
            response.cache_control.public = True
            response.cache_control.max_age = 60 * 60 * 24 * 30  # 30 days
            return response

    assets_dir = get_resource_dir()
    return send_from_directory(assets_dir, filename)

@server.route('/api/auto_resolution', methods=['GET'])
def api_auto_resolution():
    from src.models.macros import get_game_resolution
    return jsonify({"resolution": get_game_resolution() or "Not detected"})

@server.route('/api/restart', methods=['POST'])
def api_restart():
    import sys, os
    def restart():
        import time
        time.sleep(0.5)
        python = sys.executable
        os.execl(python, python, *sys.argv)
    import threading
    threading.Thread(target=restart, daemon=True).start()
    return '', 204

def background_init():
    """Perform heavy or slow initialization in the background after UI loads."""
    logger.info("Starting background initialization...")
    try:
        # Check for updates on startup
        try:
            get_version_info()
            logger.info("Version check completed on startup")
        except Exception as e:
            logger.error(f"Failed to check for updates on startup: {e}")
        
        def load_data_async():
            """Load stash data once in a background thread."""
            if getattr(load_data_async, 'is_loading', False):
                logger.info("Data loading already in progress, skipping")
                return

            load_data_async.is_loading = True
            start_time = time.time()

            try:
                if not api.stash_manager._is_loaded:
                    logger.info("Loading stash manager data...")
                    api.stash_manager._load_data()
                    logger.info(
                        f"Stash manager data loaded in {time.time() - start_time:.2f} seconds"
                    )

                if api.window:
                    api.window.evaluate_js('window.dispatchEvent(new Event("dataLoadingDone"));')
            except Exception as e:
                logger.error(f"Background data loading failed: {e}")
                if api.window:
                    error_str = str(e).replace('"', '\\"')
                    api.window.evaluate_js(
                        f'window.dispatchEvent(new CustomEvent("dataLoadingFailed", {{ detail: {{ "error": "{error_str}" }} }}));'
                    )
            finally:
                load_data_async.is_loading = False

        load_data_async.is_loading = False
        threading.Thread(target=load_data_async, daemon=True).start()

        try:
            state = api.capture_controller.state()

            if state.get("desired") and not state.get("running"):
                logger.info("Restoring desired capture state from previous session")
                started, updated_state = api.capture_controller.start()
                if started:
                    state = updated_state
                else:
                    logger.warning("Capture auto-start was requested but failed to activate")

            if api.window:
                state_payload = json.dumps(state)
                api.window.evaluate_js(
                    f"window.applyCaptureState && window.applyCaptureState({state_payload});"
                )

            if state.get("running"):
                api._initial_restart_done = True
        except Exception as ce:
            logger.error(f"Failed to restore capture state: {ce}")

        if api.window:
            api.window.evaluate_js('window.dispatchEvent(new Event("backgroundInitDone"));')
        logger.info("Background initialization complete.")
    except Exception as e:
        logger.error(f"Background initialization failed: {e}")
        error_str = str(e).replace('"', '\\"')
        if api.window:
            api.window.evaluate_js(
                f'window.dispatchEvent(new CustomEvent("backgroundInitFailed", {{ detail: {{ "error": "{error_str}" }} }}));'
            )

def check_tshark():
    # Check if tshark is in PATH
    tshark_path = shutil.which("tshark")
    if not tshark_path:
        custom_path = resolve_tshark_executable(settings_manager.get('wiresharkPath'))
        if custom_path:
            tshark_path = custom_path
    if not tshark_path:
        logger.error("❌ tshark is NOT in the system PATH.")
        return False
    logger.info(f"✅ tshark is found at: {tshark_path}")

    # Check if tshark can run
    try:
        subprocess.run(
            ["tshark", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info("✅ tshark runs successfully.")
        return True
    except Exception as e:
        logger.error(f"❌ tshark was found but failed to run: {e}")
        return False


@server.route('/api/check_npcap')
def check_npcap():
    return jsonify({'installed': check_tshark()})

# Cache for market price data
market_price_cache = {}
PRICE_CACHE_EXPIRY = 600  # 10 minutes in seconds

@server.route('/api/market/price/<item_id>')
def proxy_market_price(item_id):
    """Proxy endpoint to fetch market price from dndtools.rrmtools.uk and avoid CORS issues."""
    global market_price_cache
    current_time = time.time()
    
    # Check if we have a cached response that's still valid
    if item_id in market_price_cache:
        cached_data = market_price_cache[item_id]
        if current_time - cached_data['timestamp'] < PRICE_CACHE_EXPIRY:
            return jsonify(cached_data['data'])
    
    # No valid cache, fetch from API
    try:
        url = f'https://dndtools.rrmtools.uk/api/market/price/{item_id}'
        headers = {"X-Requested-With": "DnDTools"}
        resp = requests.get(url, headers=headers, timeout=5)
        
        if resp.ok:
            # Parse JSON to ensure it's valid before caching
            data = resp.json()
            # Store in cache with timestamp
            market_price_cache[item_id] = {
                'timestamp': current_time,
                'data': data
            }
            return jsonify(data)
        else:
            # Return error response without caching
            return (resp.content, resp.status_code, {'Content-Type': resp.headers.get('Content-Type', 'application/json')})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@server.route('/api/sort_order', methods=['GET', 'POST'])
def api_sort_order():
    if request.method == 'GET':
        return jsonify({'success': True, 'order': api.get_sort_order()})

    data = request.get_json() or {}
    success = api.set_sort_order(data.get('order'))
    response = {'success': success, 'order': api.get_sort_order()}
    return (jsonify(response), 200) if success else (jsonify(response), 500)

def main():
    # --- Updater logic ---
    if len(sys.argv) >= 3 and sys.argv[1] == "/update":
        # Instead of replacing the exe, just start a new instance and exit
        import subprocess
        # Using the global time module
        time.sleep(1.5)
        subprocess.Popen([sys.executable] + sys.argv[2:])
        sys.exit(0)
    # --- End updater logic ---
    
    # Using the global time module
    start_time = time.time()
    logger.info("Starting DnDTools application")
    
    # Preload only essential settings for faster startup
    SettingsManager.migrate_from_legacy(logger=logger, defer_heavy_operations=True)
    refreshed_settings = settings_manager.reload()

    interface_after_migration = refreshed_settings.get('interface')
    if interface_after_migration and interface_after_migration != api.capture_settings.get('interface'):
        try:
            state = api.capture_controller.update_settings(interface_after_migration, None, None)
            api.capture_settings = {
                'interface': state['interface'],
                'port_range': (state['portRange']['low'], state['portRange']['high'])
            }
        except Exception as capture_err:
            logger.error(f"Failed to apply migrated capture interface: {capture_err}")

    api._setup_global_hotkeys()
    
    # Only handle immediate restart if capture is in a known running state
    if (
        api.capture_controller.state()["running"]
        and not api._initial_restart_done
        and not api.capture_controller.should_auto_start()
    ):
        # Schedule restart after UI load instead of doing it now
        threading.Timer(0.5, lambda: api.restart_capture_switch()).start()
    
    # Create window with minimal startup time
    window = webview.create_window(
        'Dark and Darker Stash Organizer',
        server,
        width=1200,
        height=800,
        min_size=(800, 600),
        frameless=True,
        easy_drag=False
    )
    
    # Expose API methods in parallel
    for method_name in [
        'minimize', 'toggle_maximize', 'close_window', 'sort_stash', '_save_settings',
        'start_capture', 'start_capture_switch', 'stop_capture_switch', 'restart_capture_switch',
        'search_items', 'get_characters', 'get_character_details',
        'get_capture_settings', 'set_capture_settings', 'get_character_stash_previews',
        'get_capture_state', 'set_sort_order', 'begin_drag', 'select_wireshark_path', 'detect_wireshark_path'
    ]:
        if hasattr(api, method_name):
            window.expose(getattr(api, method_name))
    
    # Set window reference
    api.set_window(window)
    
    logger.info(f"UI initialization completed in {time.time() - start_time:.2f} seconds")
    
    def on_loaded():
        # Initialize window state
        api.set_initial_window_state()
        # Start background initialization after UI is ready
        threading.Thread(target=background_init, daemon=True).start()
        
    # Start the webview
    webview.start(on_loaded, debug=False)

if __name__ == '__main__':
    main()
