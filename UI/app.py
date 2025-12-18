import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from src.models.appdirs import resource_path, get_resource_dir, get_templates_dir, get_static_dir, migrate_data_files, get_characters_dir
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
from src.models.sort_feedback import get_sort_feedback_manager
from src.models.sort_feedback_sync import SortFeedbackSyncService
import psutil
import json
import sys
import multiprocessing
import logging
import re
from pathlib import Path
from urllib.parse import urlparse
from utils.logging_setup import setup_logging, set_logging_level
import secrets
import time
import shutil
import subprocess
import tempfile
from datetime import datetime
import requests
from networking.protos import _PacketCommand_pb2
from update import UpdateManager, UpdateError
from utils.asset_updater import AssetUpdater
from utils.tshark_cleanup import schedule_tshark_cleanup
from utils.game_window_watcher import GameWindowWatcherProcess
from utils.active_ping import ActivePingService

try:
    import pystray
except ImportError:  # pragma: no cover - optional dependency safeguard
    pystray = None

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - optional dependency safeguard
    Image = None
    ImageDraw = None

from src.models.game_data import item_data_manager
from src.models.icon_pak import icon_store, canonical_icon_path

from src.models.character import save_packet_data
from src.models.item import Item
from src.models.game_overlay import overlay_manager, register_overlay_logging
from src.models.hotkeys import GlobalHotkeyManager, HotkeyError, format_hotkey_display
from src.models.loot import (
    extract_loot_state_filter,
    format_loot_state_label,
)

from dotenv import load_dotenv
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import
from src.quest_service import QuestService, RARITY_ORDER
from src.system_tray import SystemTray

# Global cache for version check
version_cache = None
version_cache_timestamp = 0
VERSION_CACHE_DURATION = 6 * 60 * 60  # 6 hours in seconds

APP_VERSION = "3.7.1"
UPDATE_MANIFEST_URL = os.environ.get(
    "DND_UPDATE_MANIFEST",
    "https://github.com/Beelzebub2/DnDTools/releases/latest/download/update-manifest.json",
)
UPDATE_CACHE_DURATION = 5 * 60
AUTO_UPDATE_SILENT = os.environ.get("DND_UPDATE_SILENT", "1").lower() not in {"0", "false", "no", "off"}
SORT_CANCEL_NOTIFICATION_MESSAGE = (
    "Sort canceled. Refresh your character data. If switching tabs doesn't update, move any item in the stash and switch tabs again."
)

GAME_PROCESS_NAMES = (
    "DungeonCrawler.exe",
    "DarkAndDarker.exe",
)
GAME_WINDOW_TITLES = (
    "Dark and Darker  ",
    "Dark and Darker",
)
GAME_MONITOR_POLL_SECONDS = 2.5
EXCLUDED_WINDOW_TITLES = (
    "Dark and Darker Stash Organizer",
)
GAME_PROCESS_CACHE_SECONDS = 5.0
WINDOW_FALLBACK_SCAN_SECONDS = 10.0
PID_NAME_CACHE_SECONDS = 10.0

if sys.platform.startswith('win'):
    EVENT_SYSTEM_FOREGROUND = 0x0003
    EVENT_OBJECT_CREATE = 0x8000
    EVENT_OBJECT_DESTROY = 0x8001
    EVENT_OBJECT_SHOW = 0x8002
    OBJID_WINDOW = 0x00000000
    WINEVENT_OUTOFCONTEXT = 0x0000
    WINEVENT_SKIPOWNPROCESS = 0x0002
    WINEVENT_SKIPOWNTHREAD = 0x0004
else:
    EVENT_SYSTEM_FOREGROUND = EVENT_OBJECT_CREATE = EVENT_OBJECT_DESTROY = EVENT_OBJECT_SHOW = OBJID_WINDOW = 0
    WINEVENT_OUTOFCONTEXT = WINEVENT_SKIPOWNPROCESS = WINEVENT_SKIPOWNTHREAD = 0


@dataclass(frozen=True)
class GameWindowState:
    game_running: bool
    window_found: bool
    window_title: Optional[str]
    window_visible: bool
    window_focused: bool
    window_rect: Optional[Tuple[int, int, int, int]]

    def to_log_dict(self) -> Dict[str, object]:
        rect_payload: Optional[Dict[str, int]] = None
        if self.window_rect:
            left, top, right, bottom = self.window_rect
            rect_payload = {
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "width": right - left,
                "height": bottom - top,
            }

        return {
            "game_running": self.game_running,
            "window_found": self.window_found,
            "window_title": self.window_title,
            "window_visible": self.window_visible,
            "window_focused": self.window_focused,
            "window_rect": rect_payload,
        }







def _clear_character_storage() -> dict[str, object]:
    removed_files: list[str] = []
    failed_files: list[str] = []

    data_dir = Path(get_characters_dir())
    if data_dir.exists():
        for candidate in data_dir.glob('*.json'):
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

# Initialize logging first
# Check developer mode from settings before initializing logging
try:
    dev_mode = settings_manager.get('developerMode', False)
    initial_level = logging.INFO if dev_mode else logging.WARNING
except Exception:
    initial_level = logging.WARNING

setup_logging(initial_level)
register_overlay_logging()
logger = logging.getLogger(__name__)
settings_manager.set_logger(logger)

update_manager = UpdateManager(
    current_version=APP_VERSION,
    manifest_url=UPDATE_MANIFEST_URL,
    cache_duration=UPDATE_CACHE_DURATION,
    auto_update_silent=AUTO_UPDATE_SILENT,
    logger=logger,
)

# Migrate data files to new structure
try:
    migrate_data_files()
except Exception as e:
    logger.error(f"Failed to migrate data files: {e}")

quest_service = QuestService(logger)
CHARACTER_STORAGE_PROTECTED_FILES = set(quest_service.protected_filenames)

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


@server.context_processor
def inject_desktop_preferences():
    return {
        'close_to_tray_enabled': settings_manager.get('closeToTrayEnabled', True),
        'developer_mode_enabled': settings_manager.get('developerMode', False),
    }

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

    def shutdown(self, persist_running_state: Optional[bool] = None):
        with self._lock:
            if persist_running_state is None:
                desired = self._desired_running or self._packet_capture.is_active()
            else:
                desired = bool(persist_running_state)
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
        self.sort_feedback_manager = get_sort_feedback_manager()
        settings = self.settings_manager.reload()
        self._developer_mode_enabled = bool(settings.get('developerMode', False))
        self._apply_logging_preferences(self._developer_mode_enabled)

        # Apply persisted sort order preference if available
        try:
            stored_order = settings.get('stashSortOrder', Item.copy_sort_order())
            normalized_order = Item.normalize_sort_order(stored_order)
            Item.sort_order = Item.copy_sort_order(normalized_order)
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
        try:
            self.hotkey_manager: Optional[GlobalHotkeyManager] = GlobalHotkeyManager(logger)
        except HotkeyError as creation_err:
            self.hotkey_manager = None
            logger.error("Global hotkeys disabled: %s", creation_err)
        else:
            try:
                self._setup_global_hotkeys()
            except HotkeyError as hotkey_err:
                logger.error("Failed to register initial global hotkeys: %s", hotkey_err)
        self.is_maximized = False
        self.original_size = None
        self.original_position = None
        self.current_sort_event = None
        self._current_char_id = None
        self._current_stash_id = None
        self._capture_shutdown_completed = False
        self.asset_updater: Optional[AssetUpdater] = None
        self._close_to_tray_enabled = bool(settings.get('closeToTrayEnabled', True))
        self.tray_manager: Optional[SystemTray] = None
        self._allow_window_close = False
        self._closing_subscription_attached = False
        self._initialize_tray_manager()
        self._game_process_cache = False
        self._game_process_cache_timestamp = 0.0
        self._last_window_fallback_scan = 0.0
        self._active_ping_service = ActivePingService(
            settings_manager=self.settings_manager,
            app_version=APP_VERSION,
            logger=logger,
        )
        self._active_ping_service.set_developer_mode(self._developer_mode_enabled)
        self._initialize_game_monitor()
        self._active_ping_service.start()
        self._sort_feedback_sync_service: Optional[SortFeedbackSyncService] = None
        try:
            self._sort_feedback_sync_service = SortFeedbackSyncService(
                feedback_manager=self.sort_feedback_manager,
                settings_manager=self.settings_manager,
                app_version=APP_VERSION,
                logger=logger,
            )
        except Exception as exc:
            logger.debug("Sort feedback sync unavailable: %s", exc, exc_info=True)

    def _update_closing_overlay(self, message):
        if not self.window:
            return
        try:
            safe_message = json.dumps(message or "")
            script = (
                "(function () {"
                " try {"
                "   const overlay = document.getElementById('closing-overlay');"
                "   if (overlay && !overlay.classList.contains('active')) {"
                "     overlay.classList.add('active');"
                "   }"
                "   if (window.updateClosingStatus) {"
                f"     window.updateClosingStatus({safe_message});"
                "   } else {"
                "     const statusEl = document.getElementById('closing-overlay-status');"
                f"     if (statusEl) {{ statusEl.textContent = {safe_message}; }}"
                "   }"
                " } catch (err) {"
                "   console.error('Unable to update closing overlay', err);"
                " }"
                "})();"
            )
            self.window.evaluate_js(script)
        except Exception as overlay_err:
            logger.debug(f"Unable to update closing overlay: {overlay_err}")

    def is_close_to_tray_enabled(self) -> bool:
        return bool(getattr(self, '_close_to_tray_enabled', True))

    def set_close_to_tray_enabled(self, enabled: bool) -> None:
        self._close_to_tray_enabled = bool(enabled)

    def _initialize_tray_manager(self):
        icon_candidates = [
            Path(resource_path('logo.ico')),
            Path(resource_path('logo.png')),
        ]
        icon_path = None
        for candidate in icon_candidates:
            try:
                if candidate and candidate.is_file():
                    icon_path = candidate
                    break
            except Exception:
                continue

        logger.info(f"Tray icon candidates: {[str(c) for c in icon_candidates]}")
        logger.info(f"Selected icon path: {icon_path}")

        try:
            manager = SystemTray(
                app_name="DnDTools",
                app_version=APP_VERSION,
                icon_path=icon_path,
                on_restore=self.restore_from_tray,
                on_quit=self.shutdown_application,
                capture_controller=self.capture_controller,
            )
        except Exception as exc:
            logger.warning("System tray initialization failed: %s", exc, exc_info=True)
            return

        if not manager.available:
            logger.info("System tray integration disabled (pystray unavailable).")
            return

        self.tray_manager = manager
        self.tray_manager.start()
        # Initial state update is handled by the tray itself on click, but we can force one if needed
        # self._update_tray_capture_state(self.capture_controller.state())

    def _apply_logging_preferences(self, developer_mode_enabled: bool) -> None:
        target_level = logging.INFO if developer_mode_enabled else logging.WARNING
        try:
            set_logging_level(target_level)
        except Exception as exc:
            logger.debug("Failed to adjust logging level: %s", exc, exc_info=True)

    def _initialize_game_monitor(self):
        self._game_monitor_stop = threading.Event()
        self._game_monitor_thread: Optional[threading.Thread] = None
        self._game_was_running = False
        self._last_logged_game_state: Optional[GameWindowState] = None
        self._game_window_watcher_process: Optional[GameWindowWatcherProcess] = None
        self._game_window_queue: Optional[multiprocessing.Queue] = None
        self._cached_watcher_state: Optional[Dict[str, object]] = None

        if sys.platform.startswith('win'):
            try:
                self._game_window_queue = multiprocessing.Queue()
                self._game_window_watcher_process = GameWindowWatcherProcess(
                    result_queue=self._game_window_queue,
                    target_process_names=list(GAME_PROCESS_NAMES),
                    target_window_titles=list(GAME_WINDOW_TITLES),
                    excluded_window_titles=list(EXCLUDED_WINDOW_TITLES),
                    log_level=logger.getEffectiveLevel()
                )
                self._game_window_watcher_process.start()
            except Exception as exc:
                logger.debug("Unable to start game window watcher process: %s", exc, exc_info=True)

        try:
            thread = threading.Thread(
                target=self._monitor_game_presence,
                name='GamePresenceMonitor',
                daemon=True,
            )
            thread.start()
        except Exception as exc:  # pragma: no cover - defensive safeguard for optional feature
            logger.warning("Game window monitor unavailable: %s", exc, exc_info=True)
        else:
            self._game_monitor_thread = thread

    def _monitor_game_presence(self):  # pragma: no cover - background thread
        stop_event = getattr(self, '_game_monitor_stop', None)
        if not stop_event:
            return

        while not stop_event.is_set():
            loop_started = time.perf_counter()
            
            # Drain queue to get latest state
            if self._game_window_queue:
                try:
                    while True:
                        # Non-blocking get
                        new_state = self._game_window_queue.get_nowait()
                        self._cached_watcher_state = new_state
                except multiprocessing.queues.Empty:
                    pass
                except Exception as exc:
                    logger.debug("Error reading game window queue: %s", exc)

            try:
                state = self._collect_game_window_state()
                previously_running = getattr(self, '_game_was_running', False)
                if state.game_running and not previously_running:
                    self._log_game_monitor(logging.INFO, "Detected Dark and Darker launch. Restoring DnDTools window.")
                    self._restore_window_after_game_launch()
                self._game_was_running = state.game_running
                self._log_game_state_if_needed(state)
            except Exception as exc:
                logger.debug("Game monitor loop error: %s", exc, exc_info=True)
            finally:
                elapsed = time.perf_counter() - loop_started
                # If watcher is active, we can poll faster because we just check queue
                if self._game_window_watcher_process and self._game_window_watcher_process.is_alive():
                    wait_time = max(0.1, 0.5 - elapsed)
                else:
                    wait_time = max(0.25, GAME_MONITOR_POLL_SECONDS - elapsed)
                stop_event.wait(wait_time)

    def _log_game_state_if_needed(self, state: GameWindowState) -> None:
        if not self._should_log_game_state():
            self._last_logged_game_state = None
            return

        if state != getattr(self, '_last_logged_game_state', None):
            logger.info("Game window state: %s", state.to_log_dict())
            self._last_logged_game_state = state

    def _should_log_game_state(self) -> bool:
        try:
            return bool(self.settings_manager.get('developerMode', False))
        except Exception:
            return False

    def _log_game_monitor(self, level: int, message: str, *args, **kwargs) -> None:
        if level < logging.WARNING and not self._should_log_game_state():
            return
        logger.log(level, message, *args, **kwargs)

    def _collect_game_window_state(self) -> GameWindowState:
        info = getattr(self, '_cached_watcher_state', None)
        watcher_alive = self._game_window_watcher_process and self._game_window_watcher_process.is_alive()

        if not info and not watcher_alive:
            info = self._locate_game_window()
        
        if watcher_alive:
            # If watcher is alive, we trust it. If info is None, game is not running/visible.
            game_running = bool(info)
        else:
            game_running = self._is_game_process_running()

        if not info:
            return GameWindowState(
                game_running=game_running,
                window_found=False,
                window_title=None,
                window_visible=False,
                window_focused=False,
                window_rect=None,
            )

        return GameWindowState(
            game_running=True,
            window_found=True,
            window_title=info.get('title'),
            window_visible=bool(info.get('visible', False)),
            window_focused=bool(info.get('focused', False)),
            window_rect=info.get('rect'),
        )

    def _locate_game_window(self) -> Optional[Dict[str, object]]:
        info = self._locate_window_via_win32()
        if info:
            return info
        return self._locate_window_via_pygetwindow()

    def _locate_window_via_win32(self) -> Optional[Dict[str, object]]:
        if not sys.platform.startswith('win'):
            return None
        try:
            import win32gui  # type: ignore
        except Exception:
            return None

        for title in GAME_WINDOW_TITLES:
            try:
                hwnd = win32gui.FindWindow(None, title)
            except Exception:
                continue
            if not hwnd:
                continue
            try:
                actual_title = win32gui.GetWindowText(hwnd) or title
                if actual_title in EXCLUDED_WINDOW_TITLES:
                    continue
                rect = win32gui.GetWindowRect(hwnd)
                visible = bool(win32gui.IsWindowVisible(hwnd))
                focused = hwnd == win32gui.GetForegroundWindow()
                return {
                    'hwnd': hwnd,
                    'title': actual_title,
                    'rect': rect,
                    'visible': visible,
                    'focused': focused,
                }
            except Exception:
                continue
        return None

    def _locate_window_via_pygetwindow(self) -> Optional[Dict[str, object]]:
        now = time.monotonic()
        last_scan = getattr(self, '_last_window_fallback_scan', 0.0)
        if now - last_scan < WINDOW_FALLBACK_SCAN_SECONDS:
            return None

        try:
            import pygetwindow as gw  # type: ignore
        except Exception:
            return None

        self._last_window_fallback_scan = now

        candidates: List = []
        try:
            for title in GAME_WINDOW_TITLES:
                windows = gw.getWindowsWithTitle(title)
                if windows:
                    for window in windows:
                        if getattr(window, 'title', None) in EXCLUDED_WINDOW_TITLES:
                            continue
                        candidates.append(window)
        except Exception:
            candidates = []

        if not candidates:
            try:
                titles = gw.getAllTitles()
            except Exception:
                titles = []
            for title in titles:
                if not title or title in EXCLUDED_WINDOW_TITLES:
                    continue
                if 'dark and darker' in title.lower():
                    try:
                        win_list = gw.getWindowsWithTitle(title)
                    except Exception:
                        continue
                    if win_list:
                        for window in win_list:
                            if getattr(window, 'title', None) in EXCLUDED_WINDOW_TITLES:
                                continue
                            candidates.append(window)

        for window in candidates:
            try:
                rect = (window.left, window.top, window.right, window.bottom)
                title = getattr(window, 'title', None)
                if title in EXCLUDED_WINDOW_TITLES:
                    continue
                return {
                    'hwnd': getattr(window, '_hWnd', None),
                    'title': title,
                    'rect': rect,
                    'visible': bool(getattr(window, 'isVisible', False)),
                    'focused': bool(getattr(window, 'isActive', False)),
                }
            except Exception:
                continue
        return None

    def _is_game_process_running(self) -> bool:
        now = time.monotonic()
        cached_ts = getattr(self, '_game_process_cache_timestamp', 0.0)
        if now - cached_ts < GAME_PROCESS_CACHE_SECONDS:
            return bool(getattr(self, '_game_process_cache', False))

        is_running = False
        try:
            for proc in psutil.process_iter(['name', 'exe']):
                try:
                    name = (proc.info.get('name') or '').strip()
                    exe = os.path.basename(proc.info.get('exe') or '')
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                normalized = name or exe
                if not normalized:
                    continue
                if normalized in GAME_PROCESS_NAMES:
                    is_running = True
                    break
        except Exception:
            is_running = False

        self._game_process_cache = is_running
        self._game_process_cache_timestamp = now
        return is_running

    def _restore_window_after_game_launch(self) -> None:
        window = getattr(self, 'window', None)
        if not window:
            return
        try:
            restored = bool(self.restore_from_tray())
        except Exception:
            restored = False
        if not restored:
            try:
                self.bring_window_to_front()
            except Exception:
                logger.debug("Unable to bring window to front after game launch detection", exc_info=True)

    def _stop_game_monitor(self) -> None:
        stop_event = getattr(self, '_game_monitor_stop', None)
        if stop_event:
            stop_event.set()
        thread = getattr(self, '_game_monitor_thread', None)
        if thread and thread.is_alive():
            try:
                thread.join(timeout=1.5)
            except Exception:
                logger.debug("Game monitor thread join failed", exc_info=True)
        
        watcher_process = getattr(self, '_game_window_watcher_process', None)
        if watcher_process:
            try:
                watcher_process.terminate()
                watcher_process.join(timeout=1.0)
            except Exception:
                logger.debug("Failed to stop game window watcher process", exc_info=True)

    def _update_tray_capture_state(self, state: Optional[dict] = None):
        if not self.tray_manager:
            return
        # New tray implementation queries state directly from controller
        self.tray_manager.update_menu()

    def _handle_native_close_event(self, *_, **__):  # pragma: no cover - GUI event hook
        if self._allow_window_close or not self.is_close_to_tray_enabled():
            return True
        self.hide_to_tray()
        return False

    def hide_to_tray(self):
        if not self.is_close_to_tray_enabled():
            return False
        window = self.window
        if not window:
            return False

        # Windows-specific native hide to avoid pywebview issues
        if sys.platform == 'win32':
            try:
                native = window.native
                hwnd = native.Handle if hasattr(native, 'Handle') else native
                if isinstance(hwnd, int):
                    ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                    if self.tray_manager:
                        self.tray_manager.notify_minimized()
                    return True
            except Exception as e:
                logger.debug(f"Failed to hide window via ctypes: {e}")

        try:
            if hasattr(window, 'hide'):
                window.hide()
            else:
                window.minimize()
        except Exception as exc:
            logger.debug("Failed to hide window to tray: %s", exc)
            return False

        if window:
            try:
                window.evaluate_js(
                    "window.hideClosingOverlay && window.hideClosingOverlay();"
                )
            except Exception:
                pass

        if self.tray_manager:
            self.tray_manager.notify_minimized()
        return True

    def restore_from_tray(self):
        window = self.window
        if not window:
            return False

        try:
            if hasattr(window, 'show'):
                window.show()
        except Exception:
            pass

        try:
            window.restore()
        except Exception:
            pass

        brought = self.bring_window_to_front()
        # if self.tray_manager:
        #     self.tray_manager.mark_window_visible()
        return brought

    def handle_assets_updated(self, metadata: Optional[dict] = None) -> None:
        """Refresh local caches after runtime asset downloads complete."""
        try:
            item_data_manager.reload()
        except Exception as exc:  # pragma: no cover - defensive safeguard
            logger.warning("Failed to reload item metadata after asset update: %s", exc, exc_info=True)

        try:
            quest_service.refresh_items_index()
        except Exception as exc:  # pragma: no cover - defensive safeguard
            logger.warning("Failed to refresh quest item index after asset update: %s", exc, exc_info=True)

        if not self.window:
            return

        detail_json = json.dumps(metadata or {})
        try:
            self.window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent('assetsUpdated', {{ detail: {detail_json} }}));"
            )
        except Exception as exc:
            logger.debug("Unable to dispatch assetsUpdated event: %s", exc)

    def prepare_for_update(self) -> dict[str, object]:
        context: dict[str, object] = {
            'capture_should_resume': False,
        }

        capture_controller = getattr(self, 'capture_controller', None)
        if capture_controller:
            try:
                capture_state = capture_controller.state()
                context['capture_should_resume'] = bool(
                    capture_state.get('desiredRunning') or capture_state.get('running')
                )
            except Exception as exc:
                logger.debug(
                    "Unable to snapshot capture state before update: %s",
                    exc,
                    exc_info=True,
                )

        if self.current_sort_event and not self.current_sort_event.is_set():
            try:
                self.current_sort_event.set()
            except Exception:
                pass

        try:
            self.overlay_manager.hide()
        except Exception as exc:
            logger.debug("Failed to hide overlay before update: %s", exc, exc_info=True)

        if self.window:
            try:
                self.window.evaluate_js(
                    """
                    (function () {
                        const overlay = document.getElementById('closing-overlay');
                        if (overlay && !overlay.classList.contains('active')) {
                            overlay.classList.add('active');
                        }
                        if (window.updateClosingStatus) {
                            window.updateClosingStatus('Preparing update...');
                        }
                    })();
                    """
                )
            except Exception as exc:
                logger.debug(
                    "Failed to prime closing overlay for update: %s",
                    exc,
                    exc_info=True,
                )

        if capture_controller:
            try:
                success, state = capture_controller.stop()
                if not success:
                    logger.warning(
                        "Packet capture reported still running during update preparation: %s",
                        state.get('lastError'),
                    )
                self._update_tray_capture_state(state)
            except Exception as exc:
                logger.error("Failed to stop packet capture before update: %s", exc, exc_info=True)

        try:
            self._update_closing_overlay("Preparing installer...")
        except Exception as exc:
            logger.debug(
                "Unable to update closing overlay message before installer launch: %s",
                exc,
                exc_info=True,
            )

        return context

    def resume_after_update_failure(self, context: Optional[dict], error_message: str) -> None:
        message = error_message or "Automatic update failed."

        try:
            self._update_closing_overlay(f"Update failed: {message}")
        except Exception as exc:
            logger.debug("Unable to update closing overlay after update failure: %s", exc, exc_info=True)

        if self.window:
            try:
                message_json = json.dumps(message)
                script = (
                    "(function () {"
                    " const overlay = document.getElementById('closing-overlay');"
                    " if (overlay) { overlay.classList.remove('active'); }"
                    " if (window.showNotification) { "
                    "window.showNotification(" + message_json + ", 'error', { duration: 6000 });"
                    " }"
                    "})();"
                )
                self.window.evaluate_js(script)
            except Exception as exc:
                logger.debug("Failed to notify UI about update failure: %s", exc, exc_info=True)

        capture_should_resume = bool(context.get('capture_should_resume')) if context else False
        capture_controller = getattr(self, 'capture_controller', None)
        if capture_should_resume and capture_controller:
            try:
                running, state = capture_controller.start()
                self._update_tray_capture_state(state)
                if not running:
                    logger.warning(
                        "Packet capture failed to resume after update failure: %s",
                        state.get('lastError'),
                    )
            except Exception as exc:
                logger.error("Unable to resume packet capture after update failure: %s", exc, exc_info=True)

    def _save_settings(self, settings):
        """Save settings to file with validation, reporting, and clear error handling."""
        result: dict[str, object] = {
            'success': False,
            'errors': [],
            'warnings': [],
        }

        try:
            try:
                previous_settings = self.settings_manager.data
            except Exception:
                previous_settings = {}

            try:
                updated_settings = self.settings_manager.update(settings)
            except (IOError, OSError) as exc:
                logger.error("Error writing settings file: %s", exc)
                result['errors'].append('Unable to write settings file. Check disk permissions and try again.')
                result['settings'] = self.settings_manager.data
                return result
            except ValueError as exc:
                logger.error("Invalid settings data: %s", exc)
                result['errors'].append(str(exc))
                result['settings'] = self.settings_manager.data
                return result

            success = True

            if self.hotkey_manager:
                try:
                    self._setup_global_hotkeys()
                except HotkeyError as hotkey_err:  # pragma: no cover - defensive safeguard
                    success = False
                    logger.error("Failed to register global hotkeys: %s", hotkey_err)
                    result['errors'].append(str(hotkey_err))
                    fallback_sort = previous_settings.get('sortHotkey') or 'ctrl+f11'
                    fallback_cancel = previous_settings.get('cancelHotkey') or 'ctrl+f12'
                    try:
                        self.settings_manager.update({
                            'sortHotkey': fallback_sort,
                            'cancelHotkey': fallback_cancel,
                        })
                        try:
                            self._setup_global_hotkeys()
                        except HotkeyError as revert_err:
                            logger.error("Failed to restore previous hotkeys: %s", revert_err)
                    except Exception as revert_err:  # pragma: no cover - defensive safeguard
                        logger.error("Failed to revert hotkeys after registration failure: %s", revert_err)
            else:
                result['warnings'].append('Global hotkeys are not available on this system.')

            new_interface = updated_settings.get('interface')
            previous_interface = previous_settings.get('interface') if isinstance(previous_settings, dict) else None
            interface_changed = bool(new_interface) and new_interface != previous_interface

            if new_interface:
                self.capture_settings['interface'] = new_interface

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
                    success = False
                    logger.error("Failed to apply capture interface change: %s", capture_err)
                    if self.window:
                        error_msg = str(capture_err).replace('"', '\\"')
                        self.window.evaluate_js(
                            f"showNotification('Failed to switch capture interface: {error_msg}', 'error');"
                        )

                    revert_interface = previous_interface or ''
                    try:
                        self.settings_manager.update({'interface': revert_interface})
                        self.capture_settings['interface'] = revert_interface or self.capture_settings.get('interface')
                    except Exception as revert_err:  # pragma: no cover - defensive safeguard
                        logger.error("Failed to revert interface after error: %s", revert_err)
                    result['errors'].append('Capture interface could not be switched. Previous interface restored.')

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
                if not resolved and new_wireshark_path:
                    result['warnings'].append('Wireshark path could not be verified. Using the provided value.')

            previous_dev_mode = None
            if isinstance(previous_settings, dict) and 'developerMode' in previous_settings:
                previous_dev_mode = bool(previous_settings.get('developerMode'))

            new_dev_mode = bool(updated_settings.get('developerMode', False))
            if previous_dev_mode is None or new_dev_mode != previous_dev_mode:
                self._developer_mode_enabled = new_dev_mode
                self._apply_logging_preferences(new_dev_mode)
                try:
                    self._active_ping_service.set_developer_mode(new_dev_mode)
                except Exception as exc:
                    logger.debug("Unable to update active ping developer mode flag: %s", exc)

            previous_close_to_tray = None
            if isinstance(previous_settings, dict) and 'closeToTrayEnabled' in previous_settings:
                previous_close_to_tray = bool(previous_settings.get('closeToTrayEnabled'))

            new_close_to_tray_value = bool(updated_settings.get('closeToTrayEnabled', True))
            self.set_close_to_tray_enabled(new_close_to_tray_value)

            if self.window and (previous_close_to_tray is None or previous_close_to_tray != new_close_to_tray_value):
                js_bool = 'true' if new_close_to_tray_value else 'false'
                try:
                    self.window.evaluate_js(
                        f"window.setCloseToTrayEnabled && window.setCloseToTrayEnabled({js_bool});"
                    )
                except Exception as exc:
                    logger.debug("Unable to propagate close-to-tray toggle: %s", exc)

            if self._sort_feedback_sync_service:
                try:
                    self._sort_feedback_sync_service.apply_settings(updated_settings)
                except Exception as exc:
                    logger.debug("Unable to propagate feedback sync settings: %s", exc, exc_info=True)

            result['settings'] = self.settings_manager.data
            result['success'] = success and not result['errors']

            if result['success']:
                logger.info("Settings saved successfully")
            else:
                logger.warning("Settings save completed with issues: %s", '; '.join(result['errors']))

            return result
        except Exception as exc:  # pragma: no cover - defensive safeguard
            logger.error("Unexpected error saving settings: %s", exc, exc_info=True)
            result['errors'].append('Unexpected error while saving settings. No changes were applied.')
            result['settings'] = self.settings_manager.data
            result['success'] = False
            return result

    def _setup_global_hotkeys(self):
        if not self.hotkey_manager:
            raise HotkeyError("Global hotkey manager is not available on this system")

        sort_hotkey = self.settings_manager.get('sortHotkey', 'ctrl+f11') or 'ctrl+f11'
        cancel_hotkey = self.settings_manager.get('cancelHotkey', 'ctrl+f12') or 'ctrl+f12'

        bindings = {
            'sort': (sort_hotkey, self._trigger_sort_current),
            'cancel': (cancel_hotkey, self._trigger_cancel_sort),
        }

        canonical = self.hotkey_manager.apply_bindings(bindings)
        logger.info(
            "Registered hotkeys -> sort: %s | cancel: %s",
            canonical.get('sort', '<none>'),
            canonical.get('cancel', '<none>'),
        )
        return canonical
        
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
            return {"success": True, "path": detected }

        return {"success": False, "error": "Wireshark installation not found in common locations."}

    @property
    def packet_capture(self):
        return self.capture_controller.packet_capture
        
    def set_window(self, window):
        """Set the window reference for JavaScript evaluation"""
        self.window = window
        # Do NOT access window.width/height/x/y here!
        # These will be set after the window is loaded
        # if self.tray_manager:
        #     self.tray_manager.mark_window_visible()

        events = getattr(window, 'events', None)
        if events and not self._closing_subscription_attached:
            try:
                events.closing += self._handle_native_close_event
                self._closing_subscription_attached = True
            except Exception as exc:
                logger.debug("Unable to bind window closing event: %s", exc)

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
            if hasattr(self.window, 'show'):
                self.window.show()
        except Exception as exc:
            logger.debug(f"Failed to restore window before foreground request: {exc}")

        # Windows-specific force show via ctypes (bypasses threading issues)
        if sys.platform == 'win32':
            try:
                # Handle both int HWND and .NET object with Handle property
                native = self.window.native
                hwnd = native.Handle if hasattr(native, 'Handle') else native
                
                if isinstance(hwnd, int):
                    # SW_RESTORE = 9
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    success = True
            except Exception as e:
                logger.debug(f"Failed to force window to front via ctypes: {e}")

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

        def _drag_worker():
            try:
                import ctypes

                user32 = ctypes.windll.user32
                hwnd = getattr(self.window, 'hwnd', None)
                if not hwnd:
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
                logger.error("Failed to initiate native drag: %s", exc)
                return False

        threading.Thread(target=_drag_worker, daemon=True).start()
        return True

    def _trigger_sort_current(self):
        """Triggered by global hotkey to sort current stash"""
        logger.info(f"Sort hotkey activated: {self.settings_manager.get('sortHotkey')}")
        current_char_id = self._current_char_id
        current_stash_id = self._current_stash_id

        if self.current_sort_event and not self.current_sort_event.is_set():
            logger.info("Sort hotkey pressed while a sort is already running; ignoring duplicate trigger")
            if self.window:
                try:
                    self.window.evaluate_js(
                        "showNotification('A sort is already running. Press the cancel hotkey to stop it.', 'info');"
                    )
                except Exception:
                    logger.debug("Unable to surface duplicate sort warning to UI", exc_info=True)
            return

        if not (current_char_id and current_stash_id):
            logger.warning("Sort hotkey pressed with no active character/stash context")
            if self.window:
                try:
                    self.window.evaluate_js(
                        "showNotification('Open a character stash before triggering the sort hotkey.', 'warning');"
                    )
                except Exception:
                    logger.debug("Unable to surface missing context warning to UI", exc_info=True)
            return

        logger.info(f"Scheduling sort for character {current_char_id}, stash {current_stash_id}")
        cancel_event = threading.Event()
        self.current_sort_event = cancel_event
        threading.Thread(target=self._sort_worker, args=(cancel_event,), daemon=True).start()

    def _sort_worker(self, cancel_event: threading.Event):
        """Background worker for sorting current stash"""
        if cancel_event.is_set():
            logger.info("Sort worker aborted before start because cancel was requested")
            return

        if self.window:
            self.window.evaluate_js('window.dispatchEvent(new Event("sortingStarted"))')
        result = self.sort_stash(
            self._current_char_id,
            self._current_stash_id,
            cancel_event=cancel_event,
            pack_mode=self.get_pack_mode(),
            stack_mode=self.get_stack_mode(),
        )
        if self.window:
            try:
                self.window.evaluate_js('window.dispatchEvent(new Event("sortingEnded"))')
            except Exception as exc:
                logger.debug("Failed to dispatch sortingEnded event after worker sort: %s", exc, exc_info=True)

            try:
                error_text = ""
                if isinstance(result, dict):
                    error_text = result.get('error') or ""

                if isinstance(error_text, str) and 'cancel' in error_text.lower():
                    cancel_detail = json.dumps({
                        "source": "worker",
                        "message": SORT_CANCEL_NOTIFICATION_MESSAGE,
                    })
                    self.window.evaluate_js(
                        f'window.dispatchEvent(new CustomEvent("sortCancelled", {{ detail: {cancel_detail} }}))'
                    )
            except Exception as exc:
                logger.debug("Failed to dispatch worker sortCancelled event: %s", exc, exc_info=True)
        # Optionally, communicate result back to UI
        
    def _trigger_cancel_sort(self):
        """Triggered by global hotkey to cancel current sort operation"""
        logger.info(f"Cancel hotkey activated: {self.settings_manager.get('cancelHotkey')}")
        if self.current_sort_event and not self.current_sort_event.is_set():
            self.current_sort_event.set()
            logger.info("Sort operation cancelled")
            if self.window:
                try:
                    self.window.evaluate_js('window.dispatchEvent(new Event("sortingEnded"))')
                except Exception as exc:
                    logger.debug("Failed to dispatch sortingEnded event after cancel: %s", exc, exc_info=True)

                cancel_detail = json.dumps({
                    "source": "hotkey",
                    "message": SORT_CANCEL_NOTIFICATION_MESSAGE,
                })

                try:
                    self.window.evaluate_js(
                        f'window.dispatchEvent(new CustomEvent("sortCancelled", {{ detail: {cancel_detail} }}))'
                    )
                except Exception as exc:
                    logger.debug("Failed to dispatch sortCancelled event: %s", exc, exc_info=True)
        else:
            logger.debug("Cancel hotkey pressed but no active sort was running")
            if self.window:
                try:
                    self.window.evaluate_js(
                        "showNotification('No sort is currently running.', 'info');"
                    )
                except Exception:
                    logger.debug("Unable to surface idle cancel notification to UI", exc_info=True)

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

    def get_character_stash_previews(self, character_id, stash_ids=None):
        return self.stash_manager.get_character_stash_previews(character_id, stash_ids=stash_ids)

    def start_capture_switch(self):
        success, state = self.capture_controller.start()
        self._update_tray_capture_state(state)
        return success, state

    def stop_capture_switch(self):
        success, state = self.capture_controller.stop()
        self._update_tray_capture_state(state)
        return success, state

    def restart_capture_switch(self):
        """Stop capture if running and start it again"""
        success, state = self.capture_controller.restart()
        self._initial_restart_done = True
        self._update_tray_capture_state(state)
        return success, state

    def get_capture_state(self):
        """Get current capture state including if initial restart was done"""
        state = self.capture_controller.state()
        self._update_tray_capture_state(state)
        state["initialRestartDone"] = self._initial_restart_done
        return state

    def sort_stash(self, character_id, stash_id, pack_mode=None, stack_mode=None, cancel_event=None):
        """Sort a specific stash for a character"""
        if cancel_event is None:
            cancel_event = threading.Event()

        self.current_sort_event = cancel_event
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
            if cancel_event.is_set():
                return {"success": False, "error": "Sort cancelled"}

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
                cancel_event=cancel_event,
                pack_mode=pack_mode,
                stack_mode=stack_mode,
                overlay_session=overlay_session,
            )

            error_msg = None
            session_summary = None
            if isinstance(result, tuple):
                if len(result) == 3:
                    success, error_msg, session_summary = result
                else:
                    success, error_msg = result
            else:
                success = bool(result)

            payload = {"success": success, "error": error_msg, "session": session_summary}

            if self.window and session_summary:
                try:
                    summary_json = json.dumps(session_summary)
                    self.window.evaluate_js(
                        f"window.dispatchEvent(new CustomEvent('sortSessionCompleted', {{ detail: {summary_json} }}));"
                    )
                except Exception as exc:
                    logger.debug("Failed to emit sort session event: %s", exc, exc_info=True)

            return payload
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
        """Default close handler that minimizes the UI to the system tray."""
        if self.hide_to_tray():
            return {"hidden": True}
        self.shutdown_application()
        return {"hidden": False}

    def shutdown_application(self):
        """Fully stop capture threads and exit the application."""
        self._allow_window_close = True
        try:
            self._active_ping_service.stop()
        except Exception as exc:
            logger.debug("Active ping service stop failed: %s", exc)
        if self._sort_feedback_sync_service:
            try:
                self._sort_feedback_sync_service.stop()
            except Exception as exc:
                logger.debug("Sort feedback sync service stop failed: %s", exc)
        self._stop_game_monitor()
        try:
            self._update_closing_overlay("Stopping capture...")
            packet_capture = None
            should_resume = False

            if hasattr(self, 'capture_controller') and self.capture_controller:
                controller = self.capture_controller
                try:
                    state = controller.state()
                    should_resume = bool(
                        state.get('desiredRunning') or state.get('running')
                    )
                except Exception as state_exc:
                    logger.debug(
                        "Unable to snapshot capture state before shutdown: %s",
                        state_exc,
                        exc_info=True,
                    )

                try:
                    controller.shutdown(persist_running_state=should_resume)
                finally:
                    packet_capture = controller.packet_capture
            elif hasattr(self, 'packet_capture') and self.packet_capture:
                packet_capture = self.packet_capture
                try:
                    should_resume = bool(
                        packet_capture.is_active()
                        or getattr(packet_capture, 'running', False)
                    )
                except Exception:
                    should_resume = False

                try:
                    packet_capture.shutdown(persist_running_state=should_resume)
                except Exception as exc:
                    logger.error("Error shutting down capture: %s", exc, exc_info=True)

            if packet_capture:
                try:
                    packet_capture._terminate_capture_processes(timeout=5.0)
                except Exception as proc_exc:
                    logger.debug(
                        "Failed to terminate capture helpers on shutdown: %s",
                        proc_exc,
                        exc_info=True,
                    )

            self._capture_shutdown_completed = True

            # Give helper threads/processes a moment to exit cleanly
            time.sleep(0.35)
        except Exception as exc:
            logger.error(f"Error during window close: {exc}", exc_info=True)
        finally:
            try:
                self._update_closing_overlay("Capture stopped. Closing application...")
            except Exception:
                pass
            self.force_close_window()
            
    def force_close_window(self):
        # Quick shutdown without delays
        self._allow_window_close = True
        try:
            self._active_ping_service.stop()
        except Exception as exc:
            logger.debug("Active ping service stop failed: %s", exc)
        self._stop_game_monitor()
        try:
            if getattr(self, 'hotkey_manager', None):
                self.hotkey_manager.shutdown()
        except Exception as exc:
            logger.debug("Failed to shut down hotkey manager: %s", exc)
        finally:
            self.hotkey_manager = None

        try:
            already_shutdown = getattr(self, '_capture_shutdown_completed', False)
            if not already_shutdown:
                if hasattr(self, 'capture_controller') and self.capture_controller:
                    try:
                        self.capture_controller.stop_capture_switch()
                    except Exception:
                        pass
                elif hasattr(self, 'packet_capture') and self.packet_capture:
                    try:
                        self.packet_capture.stop_capture_switch()
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error stopping packet capture on close: {e}")
        
        # Start exit timer as a fallback to ensure shutdown even if UI hangs
        threading.Timer(2.0, lambda: os._exit(0)).start()

        # Remove delay - close immediately
        try:
            if getattr(self, 'tray_manager', None):
                try:
                    self.tray_manager.stop()
                except Exception as exc:
                    logger.debug("Tray manager shutdown failed: %s", exc)
            
            if self.window:
                # Only destroy window if on main thread to avoid GIL/COM issues
                if threading.current_thread() is threading.main_thread():
                    self.window.destroy()
                else:
                    # If on background thread (e.g. Tray), post a close message to the main thread
                    try:
                        if sys.platform == 'win32':
                            native = self.window.native
                            hwnd = native.Handle if hasattr(native, 'Handle') else native
                            if isinstance(hwnd, int):
                                # WM_CLOSE = 0x0010
                                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
                            else:
                                # Fallback: just hide and let timer kill it
                                self.window.minimize()
                        else:
                            # Non-Windows fallback
                            self.window.destroy()
                    except Exception as e:
                        logger.debug(f"Failed to post close message: {e}")
        finally:
            self._capture_shutdown_completed = False
        
    def set_sort_order(self, order):
        try:
            normalized = Item.normalize_sort_order(order)
            Item.sort_order = Item.copy_sort_order(normalized)
            self.settings_manager.update({'stashSortOrder': Item.copy_sort_order(normalized)})
            return True
        except Exception as exc:
            logger.error(f"Failed to update stash sort order: {exc}")
            return False

    def get_sort_order(self):
        return Item.copy_sort_order()

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

    def submit_sort_feedback(self, session_id: str, success: bool, note: Optional[str] = None) -> bool:
        if not session_id:
            return False
        manager = get_sort_feedback_manager()
        try:
            return manager.record_user_feedback(session_id, success, note)
        except Exception as exc:
            logger.debug("Unable to persist sort feedback: %s", exc, exc_info=True)
            return False

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


@server.route('/api/update/check')
def api_update_check():
    include_dev = bool(api.settings_manager.get('includeDevReleases')) if api else False
    channel = 'dev' if include_dev else 'stable'
    payload, error = update_manager.check_for_updates(channel=channel)
    payload['includeDevReleases'] = include_dev
    status_code = 200 if not error else 503
    return jsonify(payload), status_code


@server.route('/api/update/status')
def api_update_status():
    return jsonify(update_manager.snapshot_state())


@server.route('/api/update/apply', methods=['POST'])
def api_update_apply():
    include_dev = bool(api.settings_manager.get('includeDevReleases')) if api else False
    channel = 'dev' if include_dev else 'stable'
    try:
        update_manager.start_update(api, channel=channel)
    except UpdateError as exc:
        status = getattr(exc, 'status_code', 500)
        return jsonify({'started': False, 'error': str(exc)}), status

    return jsonify({'started': True})

# Initialize API globals
api = None
asset_updater = None

def _init_api():
    global api, asset_updater
    if api is not None:
        return

    api = Api()
    asset_updater = AssetUpdater(
        assets_dir=Path(resource_path("")),
        logger=logger,
        window_getter=lambda: api.window,
        on_assets_applied=[api.handle_assets_updated],
        before_asset_replace={
            "icons.pak": (icon_store.invalidate_cache,),
        },
    )
    api.asset_updater = asset_updater

# JSON API endpoint
@server.route('/api/characters')
def api_characters():
    return jsonify(api.get_characters())

@server.route('/api/character/<character_id>/stashes')
def api_character_stashes(character_id):
    stash_ids_param = request.args.get('stashIds')
    stash_ids = None
    if stash_ids_param:
        stash_ids = [segment.strip() for segment in stash_ids_param.split(',') if segment.strip()]
    return jsonify(api.get_character_stash_previews(character_id, stash_ids=stash_ids))

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
        quests = quest_service.fetch_quests(force=force_refresh)
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to contact DarkerDB quests API: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Unable to reach DarkerDB quests API'}), 502
    except Exception as exc:
        logger.error("Unexpected error retrieving quests: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to load quests'}), 500

    items_index = quest_service.load_items_index()
    merchant_filter_normalized = quest_service.normalize_merchant_name(merchant_filter)
    merchant_filter_lower = merchant_filter_normalized.lower() if merchant_filter_normalized else None

    normalize_merchant = quest_service.normalize_merchant_name
    icon_builder = lambda rel: url_for('serve_file', filename=rel)

    merchants = sorted(
        {
            normalize_merchant(quest.get('merchant'))
            for quest in quests
            if normalize_merchant(quest.get('merchant'))
        },
        key=lambda name: name.lower()
    )

    enriched_quests: list[dict] = []
    for quest in quests:
        merchant_name_original = quest.get('merchant') or ''
        merchant_name = normalize_merchant(merchant_name_original)
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
            loot_state_raw = objective.get('loot_state') or objective.get('lootState')
            if loot_state_raw is not None:
                objective_payload['loot_state'] = loot_state_raw
                parsed_states = extract_loot_state_filter(loot_state_raw)
                if parsed_states:
                    values_list = sorted(parsed_states)
                    objective_payload['loot_state_values'] = values_list
                    objective_payload['loot_state_labels'] = [
                        format_loot_state_label(value) for value in values_list
                    ]
                    if len(values_list) == 1:
                        objective_payload['loot_state_value'] = values_list[0]
                        objective_payload['loot_state_label'] = objective_payload['loot_state_labels'][0]
            if objective.get('item_id'):
                objective_payload['item'] = quest_service.build_item_payload(
                    items_index.get(objective['item_id']),
                    icon_builder,
                )
            objectives_payload.append(objective_payload)

        rewards_payload: list[dict] = []
        for reward in quest.get('rewards') or []:
            reward_payload = dict(reward)
            item_id = reward.get('item_id')
            if item_id:
                reward_payload['item'] = quest_service.build_item_payload(
                    items_index.get(item_id),
                    icon_builder,
                )
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
        quests = quest_service.fetch_quests(force=force_refresh)
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to contact DarkerDB quests API for item requirements: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Unable to reach DarkerDB quests API'}), 502
    except Exception as exc:
        logger.error("Unexpected error retrieving quest requirements: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to load quest requirements'}), 500

    items_index = quest_service.load_items_index()
    normalize_merchant = quest_service.normalize_merchant_name
    icon_builder = lambda rel: url_for('serve_file', filename=rel)

    aggregated: dict[str, dict] = {}
    for quest in quests:
        quest_id = quest.get('id')
        quest_title = quest.get('title') or quest_id or 'Unknown Quest'
        merchant_name_original = quest.get('merchant')
        merchant_name = normalize_merchant(merchant_name_original)
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
                'loot_state_values': set(),
            })
            entry['total_required'] += count

            loot_state_raw = objective.get('loot_state') or objective.get('lootState')
            loot_state_values = extract_loot_state_filter(loot_state_raw)
            if loot_state_values:
                entry['loot_state_values'].update(loot_state_values)

            quest_entry = {
                'id': quest_id,
                'title': quest_title,
                'merchant': merchant_name,
                'merchant_original': merchant_name_original,
                'count': count,
                'chapter': quest.get('chapter'),
                'loot_state': loot_state_raw,
            }
            if loot_state_values:
                sorted_values = sorted(loot_state_values)
                quest_entry['loot_state_values'] = sorted_values
                quest_entry['loot_state_labels'] = [
                    format_loot_state_label(value) for value in sorted_values
                ]
                if len(sorted_values) == 1:
                    quest_entry['loot_state_value'] = sorted_values[0]
                    quest_entry['loot_state_label'] = quest_entry['loot_state_labels'][0]

            entry['quests'].append(quest_entry)
            if merchant_name:
                entry['merchant_counts'][merchant_name] = entry['merchant_counts'].get(merchant_name, 0) + count
            for dungeon in dungeons:
                entry['dungeons'].add(dungeon)

    items_payload: list[dict] = []
    for item_id, entry in aggregated.items():
        item_meta = items_index.get(item_id)
        meta_payload = quest_service.build_item_payload(item_meta, icon_builder)
        result_payload = {
            **meta_payload,
            'item_id': item_id,
            'name': meta_payload.get('name') or quest_service.normalize_item_name(item_id),
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
                    'slot_id': stash.get('slot_id'),
                    'loot_state': stash.get('loot_state')
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
        progress, timestamp = quest_service.load_progress()
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
        try:
            removed = quest_service.clear_progress_file()
        except OSError as exc:
            logger.warning("Failed to remove quest progress file: %s", exc, exc_info=True)
            return jsonify({'success': False, 'error': 'Unable to clear quest progress data'}), 500

        return jsonify({
            'success': True,
            'progress': quest_service.default_progress_payload(),
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

    quest_service.save_progress(progress_payload)
    return jsonify({'success': True})


@server.route('/api/quests/cache', methods=['DELETE'])
def api_clear_quest_cache():
    results = quest_service.clear_cache()
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


@server.route('/api/sort-feedback', methods=['POST'])
def api_sort_feedback():
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid JSON payload'}), 400

    session_id = str(payload.get('sessionId') or '').strip()
    if not session_id:
        return jsonify({'success': False, 'error': 'sessionId is required'}), 400

    raw_success = payload.get('success')
    if isinstance(raw_success, str):
        normalized = raw_success.strip().lower()
        success = normalized in {'1', 'true', 'yes', 'success'}
    else:
        success = bool(raw_success)

    note = payload.get('note')
    accepted = api.submit_sort_feedback(session_id, success, note)
    if not accepted:
        return jsonify({'success': False, 'error': 'Unable to record feedback (session not found).'}), 404

    return jsonify({'success': True})

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
    sort_hotkey = format_hotkey_display(settings_manager.get('sortHotkey', 'ctrl+f11'), 'ctrl+f11')
    cancel_hotkey = format_hotkey_display(settings_manager.get('cancelHotkey', 'ctrl+f12'), 'ctrl+f12')
    return render_template(
        'index.html',
        sort_hotkey_display=sort_hotkey,
        cancel_hotkey_display=cancel_hotkey,
    )

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
    return jsonify(api._save_settings(data))


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


@server.route('/api/sort_order', methods=['GET', 'POST'])
def api_sort_order():
    """Retrieve or persist the user's preferred stash sort ordering."""
    if request.method == 'GET':
        try:
            return jsonify({'success': True, 'order': api.get_sort_order()})
        except Exception as exc:
            logger.error(f"Error retrieving sort order: {exc}")
            return jsonify({'success': False, 'error': 'Failed to fetch sort order'}), 500

    payload = request.get_json(silent=True) or {}
    order = payload.get('order')
    if order is None:
        return jsonify({'success': False, 'error': 'Missing order payload'}), 400

    try:
        success = api.set_sort_order(order)
        if not success:
            return jsonify({'success': False, 'error': 'Failed to save sort order'}), 500
        return jsonify({'success': True, 'order': api.get_sort_order()})
    except Exception as exc:
        logger.error(f"Error updating sort order: {exc}")
        return jsonify({'success': False, 'error': 'Failed to persist sort order'}), 500

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
                
                # Clean up any lingering tshark instances after data is loaded
                protected_pids = ()
                try:
                    capture = getattr(api.capture_controller, 'packet_capture', None)
                    if capture:
                        protected_pids = capture.get_active_helper_pids()
                except Exception as cleanup_err:
                    logger.debug("Unable to determine active capture PIDs: %s", cleanup_err)
                schedule_tshark_cleanup(
                    logger,
                    api.window,
                    delay_seconds=1.0,
                    protected_pids=protected_pids,
                )
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

        try:
            updater = getattr(api, 'asset_updater', None)
            if updater:
                updater.start_async_update()
        except Exception as exc:
            logger.error(f"Failed to start asset updater: {exc}")

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
def main():
    multiprocessing.freeze_support()
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

    # Initialize API context (prevents side effects in multiprocessing workers)
    _init_api()
    
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

    if api.hotkey_manager:
        try:
            api._setup_global_hotkeys()
        except HotkeyError as hotkey_err:
            logger.error("Failed to refresh global hotkeys after settings reload: %s", hotkey_err)
    
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
        easy_drag=False  # Use custom drag region to limit draggable area
    )
    
    # Expose API methods in parallel
    for method_name in [
        'minimize', 'toggle_maximize', 'close_window', 'shutdown_application', 'sort_stash', '_save_settings',
        'start_capture_switch', 'stop_capture_switch', 'restart_capture_switch',
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
