from src.models.appdirs import resource_path, get_resource_dir, get_templates_dir, get_static_dir
import webview
from flask import Flask, render_template, jsonify, request, send_from_directory, redirect, url_for, send_file
import os
import threading
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
import tempfile

from dotenv import load_dotenv
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import

APP_VERSION = "1.0.0"

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

# Initialize StashManager with explicit path
stash_manager = StashManager(app_dir)

def on_new_character_callback(character_id):
    # Called from PacketCapture when a new character is saved
    stash_manager.characters_cache = {}
    stash_manager._load_data()
    # Notify UI of data update
    if api.window:
        api.window.evaluate_js('showNotification("New character data received", "success");'
                              'if(window.updateCharacterData) window.updateCharacterData();'
                              'if(window.updateCharacterList) window.updateCharacterList();')
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
            on_new_character=on_new_character_callback
        )
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
            on_new_character=on_new_character_callback
        )
        return True

    def start_capture(self):
        # perform capture synchronously; return True only when valid data file is saved
        import asyncio
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
        else:
            self.window.maximize()
            self.is_maximized = True

    def close_window(self):
        self.force_close_window()

    def force_close_window(self):
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
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True}

    def download_and_launch_update(self):
        """
        Download the latest release exe to a temp file, then launch it with /update <current_path>.
        """
        import requests, sys, os, subprocess, tempfile
        response = requests.get(
            'https://api.github.com/repos/Beelzebub2/DnDTools/releases/latest',
            headers={'User-Agent': 'DnDTools-Updater'}
        )
        if not response.ok:
            return {"success": False, "error": "Could not fetch release information"}
        release_data = response.json()
        asset = next((a for a in release_data['assets'] if a['name'] == 'DnDTools.exe'), None)
        if not asset:
            return {"success": False, "error": "Could not find DnDTools.exe in the latest release"}
        temp_dir = tempfile.gettempdir()
        new_exe_path = os.path.join(temp_dir, "DnDTools_new.exe")
        r = requests.get(asset['browser_download_url'], headers={'User-Agent': 'DnDTools-Updater'}, stream=True)
        if not r.ok:
            return {"success": False, "error": "Failed to download update"}
        with open(new_exe_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        current_path = sys.executable
        try:
            subprocess.Popen([new_exe_path, "/update", current_path])
            if self.window:
                self.window.destroy()
            os._exit(0)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True}

def download_github_release_asset(asset_url):
    """Download a GitHub release asset and return it as a file-like object."""
    headers = {
        'Accept': 'application/octet-stream',
        'User-Agent': 'DnDTools-Updater'
    }
    response = requests.get(asset_url, headers=headers, stream=True)
    if response.ok:
        return io.BytesIO(response.content)
    return None

@server.route('/api/download_update')
def download_update():
    """Download the latest release of DnDTools from dndtools.me API, rate-limited to 10 times per hour."""
    cache_file = os.path.join(tempfile.gettempdir(), 'dndtools_update_check_cache.json')
    now = time.time()
    cache_data = None
    # Try to load cache
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
        except Exception:
            cache_data = None
    # If cache is valid (less than 6 minutes old), use it
    if cache_data and 'timestamp' in cache_data and (now - cache_data['timestamp'] < 360):
        if 'error' in cache_data:
            return jsonify({'error': cache_data['error']}), cache_data.get('status', 400)
        if 'file_data' in cache_data:
            # Not caching binary, so just return error
            return jsonify({'error': 'Update check rate-limited. Try again later.'}), 429
    try:
        # Get latest release info from dndtools.me
        response = requests.get('https://dndtools.me/api/github/latest-release', headers={'User-Agent': 'DnDTools-Updater'})
        if not response.ok:
            cache = {'timestamp': now, 'error': 'Could not fetch release information', 'status': 400}
            with open(cache_file, 'w') as f:
                json.dump(cache, f)
            return jsonify({'error': 'Could not fetch release information'}), 400
        release_data = response.json()
        # Debug log the response structure for troubleshooting
        logger.error(f"Update API response: {json.dumps(release_data)[:1000]}")
        # Find DnDTools.exe asset
        asset = None
        for a in release_data.get('assets', []):
            if a.get('name') == 'DnDTools.exe':
                asset = a
                break
        if not asset:
            cache = {'timestamp': now, 'error': 'Could not find DnDTools.exe in the latest release', 'status': 404}
            with open(cache_file, 'w') as f:
                json.dump(cache, f)
            return jsonify({'error': 'Could not find DnDTools.exe in the latest release', 'details': release_data}), 404
        # Download the asset
        file_data = download_github_release_asset(asset['browser_download_url'])
        if not file_data:
            cache = {'timestamp': now, 'error': 'Failed to download update', 'status': 500}
            with open(cache_file, 'w') as f:
                json.dump(cache, f)
            return jsonify({'error': 'Failed to download update'}), 500
        # Save only timestamp to cache (not binary)
        cache = {'timestamp': now}
        with open(cache_file, 'w') as f:
            json.dump(cache, f)
        return send_file(
            file_data,
            as_attachment=True,
            download_name='DnDTools_new.exe',
            mimetype='application/octet-stream'
        )
    except Exception as e:
        logger.error(f"Error downloading update: {str(e)}")
        cache = {'timestamp': now, 'error': str(e), 'status': 500}
        with open(cache_file, 'w') as f:
            json.dump(cache, f)
        return jsonify({'error': str(e)}), 500

@server.route('/api/version')
def api_version():
    """Get the latest version from dndtools.me API."""
    try:
        response = requests.get('https://dndtools.me/api/github/latest-release', headers={'User-Agent': 'DnDTools-Updater'})
        if not response.ok:
            return jsonify({'version': APP_VERSION, 'error': 'Could not fetch latest version'}), 400
        release_data = response.json()
        # Try to get version from html_url (e.g., .../releases/tag/v2.0.0)
        html_url = release_data.get('html_url', '')
        version = APP_VERSION
        if '/tag/' in html_url:
            version = html_url.split('/tag/')[-1]
        return jsonify({'version': version})
    except Exception as e:
        return jsonify({'version': APP_VERSION, 'error': str(e)}), 500

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
    if not check_npcap_installed():
        return redirect(url_for('installing'))
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
    import threading
    def restart():
        # Wait a moment to let the response finish
        import time
        time.sleep(0.5)
        os.execl(sys.executable, sys.executable, *sys.argv)
    threading.Thread(target=restart, daemon=True).start()
    return '', 204

def migrate_settings():
    """Migrate settings from old location to AppData if they exist"""
    from src.models.appdirs import get_settings_file
    old_settings = resource_path('settings.json')
    new_settings = get_settings_file()
    
    if os.path.exists(old_settings) and not os.path.exists(new_settings):
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(new_settings), exist_ok=True)
            # Copy settings
            with open(old_settings, 'r') as f:
                settings = json.load(f)
            with open(new_settings, 'w') as f:
                json.dump(settings, f, indent=2)
            logger.info(f"Settings migrated to: {new_settings}")
        except Exception as e:
            logger.error(f"Error migrating settings: {e}")

def background_init():
    """Perform heavy or slow initialization in the background after UI loads."""
    logger.info("Starting background initialization...")
    try:
        # Example: reload all data, caches, or anything slow
        api.stash_manager.characters_cache = {}
        api.stash_manager._load_data()
        # Notify UI that background loading is done
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

def check_npcap_installed():
    """Check if Npcap/WinPcap is installed using Scapy's configuration"""
    try:
        import scapy.all as scapy
        return bool(scapy.conf.use_pcap)
    except Exception as e:
        logger.error(f"Error checking Npcap installation: {str(e)}")
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
    return jsonify({'installed': check_npcap_installed()})

@server.route('/api/install_npcap', methods=['POST'])
def install_npcap_route():
    success, message = install_npcap()
    return jsonify({'success': success, 'error': message if not success else None})

def main():
    # --- Updater logic ---
    if len(sys.argv) >= 3 and sys.argv[1] == "/update":
        old_exe = sys.argv[2]
        new_exe = sys.executable  # Path to the running updater exe
        time.sleep(1.5)
        try:
            if os.path.exists(old_exe):
                os.remove(old_exe)
        except Exception as e:
            logger.error(f"Failed to remove old exe: {e}")
        try:
            # Move self to old path
            shutil.move(new_exe, old_exe)
            # Relaunch from the old path
            subprocess.Popen([old_exe])
            sys.exit(0)
        except Exception as e:
            logger.error(f"Failed to move or relaunch exe: {e}")
            time.sleep(3)
            sys.exit(1)
    # --- End updater logic ---
    logger.info("Starting DnDTools application")
    migrate_settings()
    if api.packet_capture.running and not api._initial_restart_done:
        api.restart_capture_switch()
    window = webview.create_window('Dark and Darker Stash Organizer',
                                 server,
                                 width=1200,
                                 height=800,
                                 min_size=(800, 600),
                                 frameless=True,
                                 easy_drag=False)
    window.expose(api.minimize)
    window.expose(api.toggle_maximize)
    window.expose(api.close_window)
    window.expose(api.sort_stash)
    window.expose(api._save_settings)
    window.expose(api.start_capture)
    window.expose(api.start_capture_switch)
    window.expose(api.stop_capture_switch)
    window.expose(api.restart_capture_switch)
    window.expose(api.search_items)
    window.expose(api.get_characters)
    window.expose(api.get_character_stashes)
    window.expose(api.get_character_details)
    window.expose(api.get_capture_settings)
    window.expose(api.set_capture_settings)
    window.expose(api.get_character_stash_previews)
    window.expose(api.get_capture_state)
    window.expose(api.get_executable_path)
    window.expose(api.launch_updater)
    window.expose(api.download_and_launch_update)
    api.set_window(window)
    def on_loaded():
        api.set_initial_window_state()
        # Start background initialization after UI is ready
        threading.Thread(target=background_init, daemon=True).start()
    webview.start(on_loaded, debug=True)

if __name__ == '__main__':
    main()