import webview
from flask import Flask, render_template, jsonify, request, send_from_directory
import os
from src.models.stash_manager import StashManager
from src.models.stash_preview import ItemDataManager
import psutil
import json

from dotenv import load_dotenv
import sys
sys.path.append(os.path.dirname(__file__))
from src.models.capture import PacketCapture  # Add capture import

# Load environment variables
load_dotenv()

# Initialize Flask with explicit path handling
app_dir = os.path.dirname(os.path.abspath(__file__))
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
        self._setup_global_hotkeys()

    def _load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {
            'interface': os.getenv('CAPTURE_INTERFACE', 'Ethernet'),
            'sortHotkey': 'Ctrl+Alt+S',
            'cancelHotkey': 'Ctrl+Alt+X'
        }

    def _save_settings(self, settings):
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
        sort_hotkey = self.settings.get('sortHotkey', 'Ctrl+Alt+S')
        keyboard.add_hotkey(sort_hotkey.lower(), self._trigger_sort_current)
        
        # Setup cancel hotkey
        cancel_hotkey = self.settings.get('cancelHotkey', 'Ctrl+Alt+X')
        keyboard.add_hotkey(cancel_hotkey.lower(), self._trigger_cancel_sort)
        
    def _trigger_sort_current(self):
        """Triggered by global hotkey to sort current stash"""
        if not hasattr(self, '_current_char_id') or not hasattr(self, '_current_stash_id'):
            return
        self.sort_stash(self._current_char_id, self._current_stash_id)
        
    def _trigger_cancel_sort(self):
        """Triggered by global hotkey to cancel current sort operation"""
        # TODO: Implement sort cancellation
        pass

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
            result = self.stash_manager.sort_stash(character_id, stash_id)
            if isinstance(result, tuple):
                success, error_msg = result
                return {"success": success, "error": error_msg}
            if not result:
                return {"success": False, "error": "Failed to sort stash - not enough space available"}
            return {"success": True}
        except Exception as e:
            print(f"Error in sort_stash: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}

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
    return send_from_directory(os.path.join(app_dir, 'output'), filename)

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
    names = set()
    # path error
    # item_data = ItemDataManager().item_data
    item_data = ItemDataManager.load_json("assets/item-data.json")
    for name, data in item_data.items():
        name = name.removesuffix(".png").replace("_", " ").replace("2", "")
        names.add(name)

    sorted_names = sorted(names)

    return render_template('search.html', names=sorted_names)

@server.route('/api/characters')
def list_characters():
    """List all captured characters"""
    characters = []
    data_dir = "data"
    
    if not os.path.exists(data_dir):
        return jsonify(characters)
        
    for filename in os.listdir(data_dir):
        if filename.endswith('.json'):
            try:
                file_path = os.path.join(data_dir, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                char_data = None
                # Handle different packet types
                if 'characterDataBase' in data:
                    char_data = data['characterDataBase']
                elif 'characterDataList' in data:
                    # Take first character from list for now
                    char_data = data['characterDataList'][0] if data['characterDataList'] else None
                    
                if char_data:
                    character = {
                        'id': char_data.get('characterId', ''),
                        'nickname': char_data.get('nickname', 'Unknown'),
                        'class': char_data.get('className', 'Unknown'),
                        'level': char_data.get('level', 0)
                    }
                    characters.append(character)
            except Exception as e:
                print(f"Error processing {filename}: {e}")
                continue
    
    return jsonify(characters)

# Add these routes after the other API routes
@server.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'GET':
        return jsonify(api.settings)
    data = request.get_json()
    return jsonify({'success': api._save_settings(data)})

def main():
    # from src.models.capture import main
    # main()
    # exit()

    from src.models.sort import main
    main()
    exit()


    # Use the global api instance
    # Perform initial restart if needed (only once)
    if api.packet_capture.running and not api._initial_restart_done:
        api.restart_capture_switch()
    
    window = webview.create_window('Dark and Darker Stash Organizer',
                                 server,
                                 js_api=api,
                                 width=1200,
                                 height=800,
                                 min_size=(800, 600))
    
    webview.start(debug=True)

if __name__ == '__main__':
    main()