from src.models.appdirs import resource_path, get_resource_dir, get_templates_dir, get_static_dir
from src.models.settings import settings_manager, SettingsManager
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
from utils.logging_setup import setup_logging
import secrets
import time
import shutil
import subprocess
import requests
from networking.protos import _PacketCommand_pb2

from src.models.character import save_packet_data
from src.models.item import Item

from dotenv import load_dotenv
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import

# Global cache for version check
version_cache = None
version_cache_timestamp = 0
VERSION_CACHE_DURATION = 6 * 60 * 60  # 6 hours in seconds

APP_VERSION = "3.3.5"

# Initialize logging first
setup_logging()
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

    def __init__(self, initial_settings, capture_info):
        self._lock = threading.RLock()
        self._capture_info = capture_info
        self._settings = {
            "interface": initial_settings.get("interface", "Ethernet"),
            "port_range": initial_settings.get("port_range", (20200, 20300)),
        }
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
        )
        capture.capture_info = self._capture_info
        return capture

    def _state_dict(self):
        running = self._packet_capture.is_active()
        return {
            "running": running,
            "interface": self._settings["interface"],
            "portRange": {
                "low": self._settings["port_range"][0],
                "high": self._settings["port_range"][1],
            },
            "desired": self._desired_running,
            "lastError": self._last_error,
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
        self.capture_controller = CaptureController(self.capture_settings, capture_info)
        # Normalize settings from controller (ensures tuple types)
        self.capture_settings = self.capture_controller.settings()
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
        sort_hotkey = self.settings_manager.get('sortHotkey', 'ctrl+alt+s')
        logger.info(f"Registering sort hotkey: {sort_hotkey}")
        keyboard.add_hotkey(sort_hotkey, self._trigger_sort_current, suppress=True)
        
        # Setup cancel hotkey
        cancel_hotkey = self.settings_manager.get('cancelHotkey', 'ctrl+alt+x')
        logger.info(f"Registering cancel hotkey: {cancel_hotkey}")
        keyboard.add_hotkey(cancel_hotkey, self._trigger_cancel_sort, suppress=True)
        
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
        try:
            # Create new event for this sort operation
            self.current_sort_event = threading.Event()

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
            
            result = self.stash_manager.sort_stash(
                character_id, 
                stash_id, 
                cancel_event=self.current_sort_event,
                pack_mode=pack_mode,
                stack_mode=stack_mode
            )
            
            # Handle tuple result with error message
            if isinstance(result, tuple):
                success, error_msg = result
                return {"success": success, "error": error_msg}
            
            # Handle boolean result
            return {"success": bool(result)}
            
        except Exception as e:
            logger.error(f"Error in sort_stash: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
        finally:
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
    """Get the latest version from dndtools.me API with fallback to GitHub API."""
    global version_cache, version_cache_timestamp
    
    current_time = time.time()
    
    # Check if we have a valid cached response
    if version_cache and (current_time - version_cache_timestamp) < VERSION_CACHE_DURATION:
        logger.info("Returning cached version information")
        return version_cache
    
    # Cache expired or not set, fetch new data
    try:
        # First try dndtools.me API
        logger.info("Attempting to fetch version information from dndtools.me API")
        response = requests.get(
            'https://dndtools.me/api/github/latest-release', 
            headers={'User-Agent': 'DnDTools-Updater'},
            timeout=10
        )
        
        # If dndtools.me fails, try GitHub API directly
        if not response.ok:
            logger.warning(f"dndtools.me API failed with status {response.status_code}, trying GitHub API directly")
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
    """Get the latest version from dndtools.me API with fallback to GitHub API."""
    return jsonify(get_version_info())

@server.route('/api/local_version')
def api_local_version():
    """Return the local version of the app."""
    return jsonify({'version': APP_VERSION})

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
    return render_template('settings.html')

@server.route('/record')
def record():
    return render_template('record.html')

@server.route('/character/<character_id>')
def character(character_id):
    return render_template('character.html')

@server.route('/search')
def search():
    return render_template('search.html')

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
    """Proxy endpoint to fetch market price from dndtools.me and avoid CORS issues."""
    global market_price_cache
    current_time = time.time()
    
    # Check if we have a cached response that's still valid
    if item_id in market_price_cache:
        cached_data = market_price_cache[item_id]
        if current_time - cached_data['timestamp'] < PRICE_CACHE_EXPIRY:
            return jsonify(cached_data['data'])
    
    # No valid cache, fetch from API
    try:
        url = f'https://dndtools.me/api/market/price/{item_id}'
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
        'get_capture_state', 'set_sort_order'
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
