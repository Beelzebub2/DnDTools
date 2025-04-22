import webview
from flask import Flask, render_template, jsonify, request, send_from_directory
import os
from src.models.stash_manager import StashManager
from src.models.stash_preview import ItemDataManager

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

# Initialize StashManager with explicit path
stash_manager = StashManager(app_dir)

class Api:
    def __init__(self):
        self.stash_manager = stash_manager
        # Capture setup
        self.capture_settings = {
            'interface': os.getenv('CAPTURE_INTERFACE', 'Ethernet'),
            'port_range': (
                int(os.getenv('CAPTURE_PORT_LOW', 20200)),
                int(os.getenv('CAPTURE_PORT_HIGH', 20300))
            )
        }
        self.packet_capture = PacketCapture(
            interface=self.capture_settings['interface'],
            port_range=self.capture_settings['port_range']
        )
        self.capture_thread = None
        self.capture_running = False

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
        # {'name': 'Arcane Hood', 'rarity': '3', 'properties': ['s_Agility', 's_ArmorPenetration', 's_Dexterity']}
        return self.stash_manager.search_items(query)

    def set_capture_settings(self, interface, port_low, port_high):
        self.capture_settings = {'interface': interface, 'port_range': (port_low, port_high)}
        self.packet_capture = PacketCapture(interface, (port_low, port_high))
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

def main():
    api = Api()
    window = webview.create_window('Dark and Darker Stash Organizer', 
                                 server,
                                 js_api=api,
                                 width=1200,
                                 height=800,
                                 min_size=(800, 600))
    webview.start(debug=True)

if __name__ == '__main__':
    main()