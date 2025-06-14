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
from networking.protos import _PacketCommand_pb2

from src.models.character import save_packet_data
from src.models.item import Item

from dotenv import load_dotenv
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import

APP_VERSION = "2.1.4"

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

def handle_character(message):
    save_packet_data(message)
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
            # Notify JS of restore
            if self.window:
                self.window.evaluate_js(
                    'window.dispatchEvent(new CustomEvent("windowStateChanged", { detail: { maximized: false } }));'
                )
        else:
            self.window.maximize()
            self.is_maximized = True
            # Notify JS of maximize
            if self.window:
                self.window.evaluate_js(
                    'window.dispatchEvent(new CustomEvent("windowStateChanged", { detail: { maximized: true } }));'
                )

    def close_window(self):
        """Properly save capture state before closing the window"""
        try:
            # Ensure the packet capture saves its current state before shutdown
            if hasattr(self, 'packet_capture'):
                # Call shutdown instead of directly stopping to ensure state is saved properly
                self.packet_capture.shutdown()
                
            # Small delay to ensure state file is written
            time.sleep(0.2)
        except Exception as e:
            logger.error(f"Error during window close: {e}")
        finally:
            self.force_close_window()

    def force_close_window(self):
        # Stop packet capture if running to avoid shutdown delays
        try:
            if hasattr(self, 'packet_capture') and self.packet_capture.running:
                self.packet_capture.stop_capture_switch()
        except Exception as e:
            logger.error(f"Error stopping packet capture on close: {e}")
        # Add a short delay to allow threads to clean up
        time.sleep(0.2)
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
        import requests, sys, os, subprocess, tempfile, traceback
        logger.info("Starting download_and_launch_update")
        try:
            # First try with GitHub API
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
            logger.info(f"Available assets: {[a.get('name') for a in release_data.get('assets', [])]}")
            
            asset = next((a for a in release_data.get('assets', []) if a.get('name') == 'DnDTools.exe'), None)
            if not asset:
                error_msg = "Could not find DnDTools.exe in the latest release"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
                
            logger.info(f"Found asset at {asset.get('browser_download_url')}")
            temp_dir = tempfile.gettempdir()
            new_exe_path = os.path.join(temp_dir, "DnDTools_new.exe")
            
            logger.info(f"Downloading to: {new_exe_path}")
            r = requests.get(
                asset['browser_download_url'], 
                headers={'User-Agent': 'DnDTools-Updater'}, 
                stream=True,
                timeout=60  # Longer timeout for file download
            )
            
            if not r.ok:
                error_msg = f"Download failed with status code: {r.status_code}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
                
            # Track download size
            total_size = int(r.headers.get('content-length', 0))
            logger.info(f"Download size: {total_size} bytes")
            
            # Download with progress tracking
            downloaded_size = 0
            with open(new_exe_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if total_size > 0:
                            percent = int((downloaded_size / total_size) * 100)
                            if percent % 10 == 0:  # Log every 10%
                                logger.info(f"Download progress: {percent}% ({downloaded_size}/{total_size})")
            
            logger.info(f"Download complete. File size: {os.path.getsize(new_exe_path)} bytes")
            
            # Verify file exists and has content
            if not os.path.exists(new_exe_path) or os.path.getsize(new_exe_path) < 1000000:  # Expect exe to be at least 1MB
                error_msg = f"Downloaded file appears invalid. Size: {os.path.getsize(new_exe_path) if os.path.exists(new_exe_path) else 'file not found'}"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}
                
            current_path = sys.executable
            logger.info(f"Current executable path: {current_path}")
            
            try:
                # Show a warning notification to the user that the app will restart for update
                if self.window:
                    logger.info("Showing update restart notification")
                    self.window.evaluate_js('''
                        (function() {
                            const popup = document.createElement('div');
                            popup.style.position = 'fixed';
                            popup.style.top = '50%';
                            popup.style.left = '50%';
                            popup.style.transform = 'translate(-50%, -50%)';
                            popup.style.backgroundColor = '#222';
                            popup.style.color = '#e4c869';
                            popup.style.padding = '20px';
                            popup.style.borderRadius = '10px';
                            popup.style.boxShadow = '0 0 20px rgba(0,0,0,0.5)';
                            popup.style.zIndex = '99999';
                            popup.style.textAlign = 'center';
                            popup.style.minWidth = '300px';
                            popup.innerHTML = `
                                <div style="font-size: 24px; margin-bottom: 10px;">
                                    <span class="material-icons" style="font-size:36px;vertical-align:middle;margin-right:10px;">system_update_alt</span>
                                    Update Ready
                                </div>
                                <p style="margin: 15px 0;">Application will restart to apply updates...</p>
                                <div id="update-countdown" style="font-size: 18px; font-weight: bold;">3</div>
                            `;
                            document.body.appendChild(popup);
                            
                            let count = 3;
                            const interval = setInterval(() => {
                                count--;
                                if (count <= 0) {
                                    clearInterval(interval);
                                }
                                document.getElementById('update-countdown').textContent = count;
                            }, 1000);
                        })();
                    ''')
                    
                    # Wait 3 seconds to show the notification
                    import time
                    time.sleep(3)
                    
                logger.info(f"Launching updater: {new_exe_path} /update {current_path}")
                subprocess.Popen([new_exe_path, "/update", current_path])
                
                if self.window:
                    self.window.destroy()
                logger.info("Window destroyed, exiting application")
                os._exit(0)
            except Exception as e:
                error_msg = f"Failed to launch updater: {str(e)}"
                logger.error(error_msg)
                logger.error(traceback.format_exc())
                return {"success": False, "error": error_msg}
                
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
    """Download the latest release of DnDTools from dndtools.me API or direct GitHub API as fallback."""
    try:
        # First try dndtools.me API
        logger.info("Attempting to fetch release information from dndtools.me API")
        response = requests.get('https://dndtools.me/api/github/latest-release', 
                               headers={'User-Agent': 'DnDTools-Updater'}, 
                               timeout=10)
        
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
        
        # Debug log the response structure for troubleshooting (limited to reduce log size)
        logger.info(f"Release data received with keys: {list(release_data.keys())}")
        
        # Find DnDTools.exe asset
        asset = None
        for a in release_data.get('assets', []):
            if a.get('name') == 'DnDTools.exe':
                asset = a
                logger.info(f"Found DnDTools.exe asset with URL: {a.get('browser_download_url', 'No URL found')}")
                break
                
        if not asset:
            error_msg = "Could not find DnDTools.exe in the latest release"
            logger.error(f"{error_msg}, available assets: {[a.get('name') for a in release_data.get('assets', [])]}")
            return jsonify({'error': error_msg, 'details': release_data}), 404
            
        # Download the asset
        logger.info(f"Downloading asset from: {asset['browser_download_url']}")
        file_data = download_github_release_asset(asset['browser_download_url'])
        if not file_data:
            error_msg = "Failed to download update file"
            logger.error(error_msg)
            return jsonify({'error': error_msg}), 500
            
        logger.info("Update file downloaded successfully, sending to client")
        return send_file(
            file_data,
            as_attachment=True,
            download_name='DnDTools_new.exe',
            mimetype='application/octet-stream'
        )
        
    except requests.exceptions.Timeout:
        error_msg = "Connection timed out while fetching update information"
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 504
        
    except requests.exceptions.ConnectionError:
        error_msg = "Connection error while fetching update information. Please check your internet connection."
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 503
        
    except ValueError as e:
        error_msg = f"Invalid JSON response: {str(e)}"
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 500
        
    except Exception as e:
        error_msg = f"Error downloading update: {str(e)}"
        logger.error(error_msg, exc_info=True)  # Log full traceback for debugging
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
    def restart():
        import time
        time.sleep(0.5)
        python = sys.executable
        os.execl(python, python, *sys.argv)
    import threading
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
        # Only trigger a reload if necessary (e.g., after file changes)
        if not api.stash_manager._is_loaded:
            api.stash_manager._load_data()
            
        # Check if capture should auto-start based on previous state
        try:
            if api.packet_capture.should_auto_start():
                logger.info("Auto-starting capture based on previous state")
                # Start the capture switch which will set running=True and start the thread
                api.packet_capture.start_capture_switch()
                
                # Update UI to reflect running state
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
                        }, 500);
                    ''')
            else:
                logger.info("Not auto-starting capture - previous state was stopped or already running")
        except Exception as ce:
            logger.error(f"Failed to restore capture state: {ce}")
            
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
        import subprocess, time
        time.sleep(1.5)
        subprocess.Popen([sys.executable] + sys.argv[2:])
        sys.exit(0)
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
    window.expose(api.set_sort_order)
    api.set_window(window)
    def on_loaded():
        api.set_initial_window_state()
        # Start background initialization after UI is ready
        threading.Thread(target=background_init, daemon=True).start()
    webview.start(on_loaded, debug=True)

if __name__ == '__main__':
    main()
