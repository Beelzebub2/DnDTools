from src.models.appdirs import resource_path, get_resource_dir, get_templates_dir, get_static_dir
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
import io
from networking.protos import _PacketCommand_pb2

from src.models.character import save_packet_data
from src.models.item import Item

from dotenv import load_dotenv
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import

APP_VERSION = "3.2.1"

# Initialize logging first
setup_logging()
logger = logging.getLogger(__name__)

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

def handle_character(message):
    save_packet_data(message)
    # Called from PacketCapture when a new character is saved
    stash_manager.force_reload()  # Use force_reload to ensure data is refreshed
    
    # Extract character information for visual effect
    char_data = message.characterDataBase
    char_class = char_data.characterClass.replace("DesignDataPlayerCharacter:Id_PlayerCharacter_", "")
    char_nickname = char_data.nickName.originalNickName if hasattr(char_data.nickName, 'originalNickName') else "Unknown"
    
    # Notify UI of data update with character capture animation
    if api.window:
        api.window.evaluate_js(f'''
            showNotification("New character data received", "success");
            if(window.showCharacterCaptureAnimation) window.showCharacterCaptureAnimation("{char_class}", "{char_nickname}");
            if(window.updateCharacterData) window.updateCharacterData();
            if(window.updateCharacterList) window.updateCharacterList();
        ''')
    return True

class Api:
    def __init__(self):
        self.stash_manager = stash_manager
        # Settings
        from src.models.appdirs import get_settings_file
        self.settings_file = get_settings_file()
        self.settings = self._load_settings()
        # Capture setup
        self.capture_settings = {
            'interface': self.settings.get('interface', os.getenv('CAPTURE_INTERFACE', 'Ethernet')),
            'port_range': (
                int(os.getenv('CAPTURE_PORT_LOW', 20200)),
                int(os.getenv('CAPTURE_PORT_HIGH', 20300))
            )
        }
        self.packet_capture = PacketCapture(
            interface=self.capture_settings['interface'],
            port_range=self.capture_settings['port_range'],
        )
        capture_info = {
            _PacketCommand_pb2.PacketCommand.S2C_LOBBY_CHARACTER_INFO_RES: handle_character,
        }
        self.packet_capture.capture_info = capture_info

        self.capture_thread = None
        self.capture_running = self.packet_capture.running
        self._initial_restart_done = False
        self.window = None
        self._setup_global_hotkeys()
        self.is_maximized = False
        self.original_size = None
        self.original_position = None
        self.current_sort_event = None
        self._current_char_id = None
        self._current_stash_id = None

    def _load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading settings: {e}")
                pass
        return {
            'interface': os.getenv('CAPTURE_INTERFACE', 'Ethernet'),
            'sortHotkey': 'ctrl+alt+s',
            'cancelHotkey': 'ctrl+alt+x',
            'sortSpeed': 0.2,
            'resolution': 'Auto'
        }

    def _save_settings(self, settings):
        # Ensure hotkeys are lowercase for keyboard library
        settings['sortHotkey'] = settings['sortHotkey'].lower()
        settings['cancelHotkey'] = settings['cancelHotkey'].lower()
        if 'sortSpeed' not in settings:
            settings['sortSpeed'] = 0.2
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            self.settings = settings
            self._setup_global_hotkeys()
            logger.info("Settings saved successfully")
            return True
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            return False

    def _setup_global_hotkeys(self):
        import keyboard
        # Remove any existing hotkeys
        keyboard.unhook_all()
        
        # Setup sort hotkey
        sort_hotkey = self.settings.get('sortHotkey', 'ctrl+alt+s')
        logger.info(f"Registering sort hotkey: {sort_hotkey}")
        keyboard.add_hotkey(sort_hotkey, self._trigger_sort_current, suppress=True)
        
        # Setup cancel hotkey
        cancel_hotkey = self.settings.get('cancelHotkey', 'ctrl+alt+x')
        logger.info(f"Registering cancel hotkey: {cancel_hotkey}")
        keyboard.add_hotkey(cancel_hotkey, self._trigger_cancel_sort, suppress=True)
        
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

    def _trigger_sort_current(self):
        """Triggered by global hotkey to sort current stash"""
        logger.info(f"Sort hotkey activated: {self.settings.get('sortHotkey')}")
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
        result = self.sort_stash(self._current_char_id, self._current_stash_id)
        if self.window:
            self.window.evaluate_js('window.dispatchEvent(new Event("sortingEnded"))')
        # Optionally, communicate result back to UI
        
    def _trigger_cancel_sort(self):
        """Triggered by global hotkey to cancel current sort operation"""
        logger.info(f"Cancel hotkey activated: {self.settings.get('cancelHotkey')}")
        if self.current_sort_event and not self.current_sort_event.is_set():
            self.current_sort_event.set()
            logger.info("Sort operation cancelled")
            if self.window:
                self.window.evaluate_js('window.dispatchEvent(new Event("sortingEnded"))')

    def get_characters(self):
        return self.stash_manager.get_characters()

    def get_character_stashes(self, character_id):
        return self.stash_manager.get_character_stashes(character_id)
        
    def get_character_details(self, character_id):
        return self.stash_manager.get_character_details(character_id)

    def get_capture_settings(self):
        """Return current packet capture settings"""
        return self.capture_settings

    def search_items(self, query):
        return self.stash_manager.search_items(query)

    def set_capture_settings(self, interface, port_low, port_high):
        # Stop current capture if running
        if self.packet_capture.running:
            self.packet_capture.stop_capture_switch()
        # Create new PacketCapture with updated settings and callback
        self.capture_settings = {'interface': interface, 'port_range': (port_low, port_high)}
        self.packet_capture = PacketCapture(
            interface,
            (port_low, port_high),
        )
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
        self.packet_capture.start_capture_switch()
        return True

    def stop_capture_switch(self):
        self.packet_capture.stop_capture_switch()
        return True

    def restart_capture_switch(self):
        """Stop capture if running and start it again"""
        if self.packet_capture.running:
            self.packet_capture.stop_capture_switch()
        # Small delay to ensure cleanup
        import time
        time.sleep(0.5)
        self.packet_capture.start_capture_switch()
        self._initial_restart_done = True
        return True

    def get_capture_state(self):
        """Get current capture state including if initial restart was done"""
        return {
            "running": self.packet_capture.running,
            "initialRestartDone": self._initial_restart_done
        }

    def sort_stash(self, character_id, stash_id):
        """Sort a specific stash for a character"""
        try:
            # Create new event for this sort operation
            self.current_sort_event = threading.Event()
            
            result = self.stash_manager.sort_stash(
                character_id, 
                stash_id, 
                cancel_event=self.current_sort_event
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
            # Fast shutdown - save state asynchronously and close immediately
            if hasattr(self, 'packet_capture'):
                # Use a background thread for shutdown to avoid blocking UI
                def async_shutdown():
                    try:
                        self.packet_capture.shutdown()
                    except Exception as e:
                        logger.error(f"Error during async shutdown: {e}")
                
                # Start shutdown in background and don't wait
                threading.Thread(target=async_shutdown, daemon=True).start()
                
        except Exception as e:            logger.error(f"Error during window close: {e}")
        finally:
            # Close immediately without delays
            self.force_close_window()
            
    def force_close_window(self):
        # Quick shutdown without delays
        try:
            if hasattr(self, 'packet_capture') and self.packet_capture.running:
                # Set running to False immediately, let background thread handle cleanup
                self.packet_capture.running = False
                # Remove this line: self.packet_capture._save_state()
        except Exception as e:
            logger.error(f"Error stopping packet capture on close: {e}")
        # Remove delay - close immediately
        self.window.destroy()
        
    def get_executable_path(self):
        """Return the path to the current executable."""
        import sys
        return sys.executable
        
    def launch_updater(self, new_exe_path, old_exe_path):
        """Launch the new exe with /update <old_exe_path> and exit."""
        import os, subprocess
        try:
            subprocess.Popen([os.path.abspath(new_exe_path), "/update", os.path.abspath(old_exe_path)])
            if self.window:
                self.window.destroy()
            os._exit(0)
        except Exception as e:            return {"success": False, "error": str(e)}
        return {"success": True}
        
    def check_for_updates(self):
        """
        Check if a newer version is available on GitHub, but only return version info without downloading.
        """
        import requests, traceback
        logger.info("Checking for updates")
        try:
            # Try with GitHub API
            logger.info("Attempting to fetch release information from GitHub API")            
            response = requests.get(
                'https://api.github.com/repos/Beelzebub2/DnDTools/releases/latest',
                headers={'User-Agent': 'DnDTools-Updater'},
                timeout=15
            )
            
            if not response.ok:
                error_msg = f"GitHub API request failed with status code: {response.status_code}"
                logger.error(error_msg)
                logger.error(f"Response content: {response.text[:500]}")  # Log first 500 chars of response
                return {"success": False, "error": error_msg}
                
            release_data = response.json()
            logger.info(f"Release data received with keys: {list(release_data.keys())}")
            
            # Extract version information
            version = release_data.get('tag_name', '').replace('v', '')
            release_url = release_data.get('html_url')
            
            logger.info(f"Latest version: {version}, URL: {release_url}")
            
            return {
                "success": True, 
                "version": version, 
                "release_url": release_url
            }
                
        except requests.exceptions.Timeout:
            error_msg = "Connection timed out while fetching update information"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
            
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Connection error: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
            
        except ValueError as e:
            error_msg = f"Invalid JSON response: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
            
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return {"success": False, "error": error_msg}
            
        return {"success": True}
    
    def set_sort_order(self, order):
        Item.sort_order = order
        return True

def download_github_release_asset(asset_url):
    """Download a GitHub release asset and return it as a file-like object."""
    headers = {
        'Accept': 'application/octet-stream',
        'User-Agent': 'DnDTools-Updater'
    }
    response = requests.get(asset_url, headers=headers, stream=True, timeout=30)
    if response.ok:
        return io.BytesIO(response.content)
    return None

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

@server.route('/api/version')
def api_version():
    """Get the latest version from dndtools.me API with fallback to GitHub API."""
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
                return jsonify({'version': APP_VERSION, 'error': error_msg}), 400
                
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
        
        return jsonify({
            'version': version,
            'release_url': release_url
        })
        
    except requests.exceptions.Timeout:
        error_msg = "Connection timed out while fetching version information"
        logger.error(error_msg)
        return jsonify({'version': APP_VERSION, 'error': error_msg}), 504
        
    except requests.exceptions.ConnectionError as e:
        error_msg = f"Connection error: {str(e)}"
        logger.error(error_msg)
        return jsonify({'version': APP_VERSION, 'error': error_msg}), 503
        
    except ValueError as e:
        error_msg = f"Invalid JSON response: {str(e)}"
        logger.error(error_msg)
        return jsonify({'version': APP_VERSION, 'error': error_msg}), 500
        
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return jsonify({'version': APP_VERSION, 'error': error_msg}), 500

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
    data = request.get_json() or {}
    return jsonify({'success': api.set_capture_settings(data.get('interface'), data.get('port_low'), data.get('port_high'))})

@server.route('/api/capture/start', methods=['POST'])
def api_capture_start():
    return jsonify({'success': api.start_capture()})

@server.route('/api/record_character/<character_id>', methods=['POST'])
def api_record_character(character_id):
    return jsonify({'success': False, 'error': 'Recording individual characters is no longer supported'})

@server.route('/api/capture/switch/start', methods=['POST'])
def capture_switch_start():
    return jsonify({'success': api.start_capture_switch()})

@server.route('/api/capture/switch/stop', methods=['POST'])
def capture_switch_stop():
    return jsonify({'success': api.stop_capture_switch()})

@server.route('/api/capture/switch/restart', methods=['POST'])
def capture_switch_restart():
    return jsonify({'success': api.restart_capture_switch()})

@server.route('/api/capture/state', methods=['GET'])
def api_capture_state():
    return jsonify(api.get_capture_state())

@server.route('/api/network_interfaces', methods=['GET'])
def api_network_interfaces():
    interfaces = list(psutil.net_if_addrs().keys())
    return jsonify({"interfaces": interfaces})

@server.route('/api/character/<character_id>/stash/<stash_id>/sort', methods=['POST'])
def api_sort_stash(character_id, stash_id):
    result = api.sort_stash(character_id, stash_id)
    return jsonify(result)

@server.route('/api/character/<character_id>/current-stash', methods=['GET'])
def api_get_current_stash(character_id):
    """Get the last selected stash ID for a character"""
    if hasattr(api, '_current_char_id') and api._current_char_id == character_id and hasattr(api, '_current_stash_id') and api._current_stash_id:
        return jsonify({'stashId': api._current_stash_id})
    from flask import session
    stash_id = session.get(f'{character_id}_current_stash_id', None)
    return jsonify({'stashId': stash_id})

@server.route('/api/character/<character_id>/current-stash/<stash_id>', methods=['POST'])
def api_set_current_stash(character_id, stash_id):
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
        return jsonify(api.settings)
    data = request.get_json()
    return jsonify({'success': api._save_settings(data)})

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

def migrate_settings(defer_heavy_operations=False):
    """
    Migrate settings from old location to AppData if they exist
    
    Args:
        defer_heavy_operations: If True, skip intensive operations during startup
    """
    from src.models.appdirs import get_settings_file
    old_settings = resource_path('settings.json')
    new_settings = get_settings_file()
    
    # Only do migration if it's actually needed
    if os.path.exists(old_settings) and not os.path.exists(new_settings):
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(new_settings), exist_ok=True)
            # Copy settings
            with open(old_settings, 'r') as f:
                settings = json.load(f)
            with open(new_settings, 'w') as f:
                json.dump(settings, f, indent=2)
                
            # Only do expensive operations if not deferred
            if not defer_heavy_operations:
                logger.info("Migrating old settings file to new location")
                # You could add additional migration steps here
            else:
                logger.info("Settings migration scheduled for later")
                # Schedule migration for later if needed
                threading.Timer(5.0, lambda: logger.info("Deferred settings migration complete")).start()
            logger.info(f"Settings migrated to: {new_settings}")
        except Exception as e:
            logger.error(f"Error migrating settings: {e}")

def background_init():
    """Perform heavy or slow initialization in the background after UI loads."""
    logger.info("Starting background initialization...")
    try:
        # Make data loading fully asynchronous and non-blocking
        def load_data_async():
            try:
                # Set a flag to prevent redundant loading
                if hasattr(load_data_async, 'is_loading') and load_data_async.is_loading:
                    logger.info("Data loading already in progress, skipping")
                    return
                
                load_data_async.is_loading = True
                
                start_time = time.time()
                
                # Only load if not already loaded
                if not api.stash_manager._is_loaded:
                    logger.info("Loading stash manager data...")
                    api.stash_manager._load_data()
                    logger.info(f"Stash manager data loaded in {time.time() - start_time:.2f} seconds")
                
                # Release loading flag
                load_data_async.is_loading = False
                
                # Notify UI that data loading is done
                if api.window:
                    api.window.evaluate_js('window.dispatchEvent(new Event("dataLoadingDone"));')
            except Exception as e:
                # Release loading flag on error
                load_data_async.is_loading = False
                logger.error(f"Background data loading failed: {e}")
                if api.window:
                    error_str = str(e).replace('"', '\\"')
                    api.window.evaluate_js(
                        f'window.dispatchEvent(new CustomEvent("dataLoadingFailed", {{ detail: {{ "error": "{error_str}" }} }}));'
                    )
        
        # Initialize loading flag
        load_data_async.is_loading = False
        
        # Start data loading in background thread immediately without waiting
        threading.Thread(target=load_data_async, daemon=True).start()
            
        # Check if capture should auto-start based on previous state
        try:
            if api.packet_capture.should_auto_start():
                logger.info("Auto-starting capture based on previous state")
                # Start the capture switch which will set running=True and start the thread
                api.packet_capture.start_capture_switch()
                
                # Update UI to reflect running state with minimal delay
                if api.window:
                    api.window.evaluate_js('''
                        setTimeout(() => {
                            if (document.getElementById('captureSwitch')) {
                                document.getElementById('captureSwitch').checked = true;
                            }
                            if (document.getElementById('sidebarCaptureIndicator')) {
                                document.getElementById('sidebarCaptureIndicator').classList.add('active');
                                document.getElementById('sidebarCaptureIndicator').classList.remove('stopping');
                            }
                            // Update toggle UI if the function exists
                            if (typeof updateToggleUI === 'function') {
                                updateToggleUI(true);
                            }
                            // Update status text
                            const statusIndicator = document.getElementById('statusIndicator');
                            const captureStatus = document.getElementById('captureStatus');
                            if (statusIndicator) statusIndicator.className = 'status-indicator capturing';
                            if (captureStatus) captureStatus.textContent = 'Capture is running';
                        }, 25); // Even faster startup
                    ''')
            else:
                logger.info("Not auto-starting capture - previous state was stopped or already running")
        except Exception as ce:
            logger.error(f"Failed to restore capture state: {ce}")
            
        # Notify UI that background loading is done immediately
        if api.window:
            api.window.evaluate_js('window.dispatchEvent(new Event("backgroundInitDone"));')
        logger.info("Background initialization complete.")
    except Exception as e:
        logger.error(f"Background initialization failed: {e}")
        # Escape error string for JS
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

def install_npcap():
    """Install Npcap using the bundled installer with admin privileges (UAC prompt)"""
    try:
        import win32com.shell.shell as shell  # type: ignore
        from win32com.shell import shellcon  # type: ignore
        import win32con
        import time

        npcap_installer = resource_path('npcap-1.82.exe')
        if not os.path.exists(npcap_installer):
            return False, "Npcap installer not found"

        params = '/winpcap_mode=yes'  # Silent install
        rc = shell.ShellExecuteEx(
            lpVerb='runas',  # Request elevation
            lpFile=npcap_installer,
            lpParameters=params,
            nShow=win32con.SW_HIDE,
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS
        )
        process_handle = rc['hProcess']

        # Wait for installation (timeout after 2 minutes)
        from win32event import WaitForSingleObject, WAIT_OBJECT_0, WAIT_TIMEOUT
        result = WaitForSingleObject(process_handle, 120 * 1000)
        if result == WAIT_TIMEOUT:
            return False, "Installation timed out"

        # Give Windows a moment to complete registry updates
        time.sleep(2)
        # Always return success after installer runs
        return True, "Installation complete!"
    except Exception as e:
        if hasattr(e, 'winerror') and e.winerror == 1223:
            return False, "Installation cancelled by user"
        return False, f"Installation failed: {str(e)}"

@server.route('/installing')
def installing():
    return render_template('installing.html')

@server.route('/api/check_npcap')
def check_npcap():
    return jsonify({'installed': check_tshark()})

@server.route('/api/install_npcap', methods=['POST'])
def install_npcap_route():
    success, message = install_npcap()
    return jsonify({'success': success, 'error': message if not success else None})

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

@server.route('/api/sort_order', methods=['POST'])
def api_sort_order():
    data = request.get_json() or {}
    return jsonify({'success': api.set_sort_order(data.get('order'))})

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
    migrate_settings(defer_heavy_operations=True)
    
    # Only handle immediate restart if capture is in a known running state
    if api.packet_capture.running and not api._initial_restart_done:
        # Schedule restart after UI load instead of doing it now
        threading.Timer(0.5, api.restart_capture_switch).start()
    
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
        'search_items', 'get_characters', 'get_character_stashes', 'get_character_details',
        'get_capture_settings', 'set_capture_settings', 'get_character_stash_previews',
        'get_capture_state', 'get_executable_path', 'launch_updater', 'set_sort_order'
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
