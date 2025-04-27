from src.models.appdirs import resource_path, get_resource_dir, get_templates_dir, get_static_dir
from src.models.game_data import item_data_manager
import webview
from flask import Flask, render_template, jsonify, request, send_from_directory
import os
import threading
from src.models.stash_manager import StashManager
import psutil
import json
import sys
import logging
from utils.logging_setup import setup_logging

from dotenv import load_dotenv
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import

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
        if hasattr(self, '_current_char_id') and hasattr(self, '_current_stash_id'):
            logger.info(f"Scheduling sort for character {self._current_char_id}, stash {self._current_stash_id}")
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
        # Check if there are unsaved changes using JavaScript
        result = self.window.evaluate_js('''
            (function() {
                if(window.hasUnsavedChanges && typeof window.createUnsavedChangesModal === "function") {
                    window.createUnsavedChangesModal(
                        function() { window.pywebview.api.force_close_window(); },
                        function() { window.pywebview.api.force_close_window(); },
                        function() { }
                    );
                    return false;
                }
                return true;
            })()
        ''')
        
        # If no unsaved changes (result is True), close the window immediately
        if result:
            self.force_close_window()
        # Otherwise, the modal will handle closing when appropriate

    def force_close_window(self):
        self.window.destroy()

# Initialize API
api = Api()

# JSON API endpoints
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

@server.route('/api/character/<character_id>/current-stash/<stash_id>', methods=['POST'])
def api_set_current_stash(character_id, stash_id):
    api._current_char_id = character_id
    api._current_stash_id = stash_id
    return jsonify({'success': True})

@server.route('/')
def index():
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

def main():
    # Remove any print statements and use logging instead
    logger.info("Starting DnDTools application")
    
    # Migrate settings before initializing API
    migrate_settings()
    
    # Use the global api instance
    # Perform initial restart if needed (only once)
    if api.packet_capture.running and not api._initial_restart_done:
        api.restart_capture_switch()
    
    # Create window with minimal JS API exposure first
    window = webview.create_window('Dark and Darker Stash Organizer',
                                 server,
                                 width=1200,
                                 height=800,
                                 min_size=(800, 600),
                                 frameless=True,
                                 easy_drag=False)
    
    # Expose all API methods
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
    
    api.set_window(window)
    
    # Set initial window state after window is loaded
    def on_loaded():
        api.set_initial_window_state()
    
    webview.start(on_loaded, debug=False)

if __name__ == '__main__':
    main()