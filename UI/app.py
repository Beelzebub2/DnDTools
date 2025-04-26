from src.models.game_data import item_data_manager
import webview
from flask import Flask, render_template, jsonify, request, send_from_directory
import os
import threading
from src.models.stash_manager import StashManager
import psutil
import json
import sys
import traceback

from dotenv import load_dotenv
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import

# Load environment variables
load_dotenv()

# Determine base directory for resources, support Nuitka onefile
if globals().get('__compiled__', False):
    app_dir = os.getcwd()
    print(f"Running as Nuitka EXE. Base directory: {app_dir}")
    os.chdir(app_dir)
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"Running in development mode. Base directory: {app_dir}")

# Initialize Flask with explicit path handling
server = Flask(__name__, 
    static_folder=os.path.join(app_dir, 'static'),
    template_folder=os.path.join(app_dir, 'templates')
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
        self.settings_file = os.path.join(app_dir, 'settings.json')
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
        self.window = None  # Will store window reference
        self._setup_global_hotkeys()
        self.is_maximized = False
        self.original_size = None
        self.original_position = None
        self.current_sort_event = None  # Add this line
        self._current_char_id = None    # Add this line
        self._current_stash_id = None   # Add this line

    def _load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {
            'interface': os.getenv('CAPTURE_INTERFACE', 'Ethernet'),
            'sortHotkey': 'ctrl+alt+s',
            'cancelHotkey': 'ctrl+alt+x',
            'sortSpeed': 0.2
        }

    def _save_settings(self, settings):
        # Ensure hotkeys are lowercase for keyboard library
        settings['sortHotkey'] = settings['sortHotkey'].lower()
        settings['cancelHotkey'] = settings['cancelHotkey'].lower()
        if 'sortSpeed' not in settings:
            settings['sortSpeed'] = 0.2
        with open(self.settings_file, 'w') as f:
            json.dump(settings, f, indent=2)
        self.settings = settings
        self._setup_global_hotkeys()
        return True

    def _setup_global_hotkeys(self):
        import keyboard
        # Remove any existing hotkeys
        keyboard.unhook_all()
        
        # Setup sort hotkey
        sort_hotkey = self.settings.get('sortHotkey', 'ctrl+alt+s')
        print(f"Registering sort hotkey: {sort_hotkey}")
        keyboard.add_hotkey(sort_hotkey, self._trigger_sort_current, suppress=True)
        
        # Setup cancel hotkey
        cancel_hotkey = self.settings.get('cancelHotkey', 'ctrl+alt+x')
        print(f"Registering cancel hotkey: {cancel_hotkey}")
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
        print(f"Sort hotkey activated: {self.settings.get('sortHotkey')}")
        if hasattr(self, '_current_char_id') and hasattr(self, '_current_stash_id'):
            print(f"Scheduling sort for character {self._current_char_id}, stash {self._current_stash_id}")
            threading.Thread(target=self._sort_worker, daemon=True).start()
        else:
            print("No current stash selected")

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
        print(f"Cancel hotkey activated: {self.settings.get('cancelHotkey')}")
        if self.current_sort_event and not self.current_sort_event.is_set():
            self.current_sort_event.set()
            print("Sort operation cancelled")
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
            print(f"Error in sort_stash: {str(e)}")
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
    output_dir = os.path.join(app_dir, 'output')
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
    assets_dir = os.path.join(app_dir, 'assets')
    return send_from_directory(assets_dir, filename)

def main():
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
    
    # Then expose the API methods we need
    window.expose(api.minimize)
    window.expose(api.toggle_maximize)
    window.expose(api.close_window)
    window.expose(api.sort_stash)
    window.expose(api._save_settings)
    window.expose(api.start_capture)
    window.expose(api.start_capture_switch)
    window.expose(api.stop_capture_switch)
    window.expose(api.restart_capture_switch)
    window.expose(api.search_items)  # Add this line
    
    api.set_window(window)  # Set the window reference in the API instance
    
    # Register a function to run after the window is loaded
    def on_loaded():
        api.set_initial_window_state()
    webview.start(on_loaded, debug=True)

if __name__ == '__main__':
    main()