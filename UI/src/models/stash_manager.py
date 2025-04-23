import json
import os
from typing import Dict, List, Optional
import glob
from datetime import datetime
from .stash_preview import parse_stashes, ItemDataManager, StashPreviewGenerator, ItemInfo

class StashManager:
    def __init__(self, base_dir: str):
        print(f"Initializing StashManager with base_dir: {base_dir}")
        self.data_dir = os.path.join(base_dir, "data")
        self.output_dir = os.path.join(base_dir, "output")
        self.item_data = ItemDataManager().item_data
        print(f"Data dir: {self.data_dir}")
        
        # Ensure directories exist
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.characters_cache = {}
        self._load_data()
        self.preview_generator = StashPreviewGenerator()

    def _load_data(self):
        """Load character data from packet data files"""
        self.characters_cache.clear()
        print(f"\nLoading characters from: {self.data_dir}")
        
        # Load all JSON files (packet data) from the data directory
        json_files = glob.glob(os.path.join(self.data_dir, "*.json"))
        print(f"Found {len(json_files)} packet data files")

        for file_path in json_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    print(f"\nReading file: {file_path}")
                    packet_data = json.load(f)
                    
                    # Check if we have valid character data
                    char_data = packet_data.get("characterDataBase", {})
                    if not char_data:
                        continue

                    # Extract basic character info
                    char_id = str(char_data.get("characterId"))
                    if not char_id:
                        print(f"Warning: No characterId in {file_path}")
                        continue

                    # Process stashes using existing parse_stashes function
                    stashes = parse_stashes(packet_data, self.item_data)
                        
                    # Convert raw class name
                    raw_class = char_data.get("characterClass", "")
                    class_name = raw_class.replace("DesignDataPlayerCharacter:Id_PlayerCharacter_", "")

                    # Map to the expected API format
                    self.characters_cache[char_id] = {
                        'id': char_id,
                        'nickname': char_data.get("nickName", {}).get("originalNickName", "Unknown"),
                        'class': class_name,
                        'level': char_data.get("level", 1),
                        'lastUpdate': datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat(),
                        'stashes': stashes,
                        'streamingModeName': char_data.get("nickName", {}).get("streamingModeNickName", ""),
                        'rank': {
                            'name': char_data.get("nickName", {}).get("rankId", "").replace("LeaderboardRankData:Id_LeaderboardRank_", "").replace("_", " "),
                            'fame': char_data.get("nickName", {}).get("fame", 0),
                            'iconType': char_data.get("nickName", {}).get("rankIconType", 1)
                        }
                    }
                    print(f"Added character to cache: {char_id}")

            except Exception as e:
                print(f"Error loading packet data file {file_path}: {str(e)}")
                import traceback
                traceback.print_exc()

        print(f"\nLoaded {len(self.characters_cache)} characters")
        for char_id, char_data in self.characters_cache.items():
            print(f"Character: {char_id}, Name: {char_data['nickname']}, Class: {char_data['class']}")

    def get_characters(self) -> List[Dict]:
        """Get list of all characters"""
        return list(self.characters_cache.values())

    def get_character_stashes(self, character_id: str) -> Dict:
        """Get all stashes for a specific character, ensuring each stash is a list."""
        char = self.characters_cache.get(character_id)
        if char:
            stashes = char.get('stashes', {})
            # Ensure all stash values are lists
            fixed_stashes = {}
            for k, v in stashes.items():
                if isinstance(v, list):
                    fixed_stashes[k] = v
                elif isinstance(v, dict):
                    # If accidentally a dict, convert to list of values
                    fixed_stashes[k] = list(v.values())
                elif v is None:
                    fixed_stashes[k] = []
                else:
                    # fallback: wrap single item
                    fixed_stashes[k] = [v]
            return fixed_stashes
        return {}
        
    def get_character_details(self, character_id: str) -> Optional[Dict]:
        """Get detailed information about a specific character"""
        char = self.characters_cache.get(character_id)
        if char:
            total_items = 0
            for stash in char['stashes'].values():
                if isinstance(stash, list):
                    total_items += len(stash)
                
            return {
                'id': char['id'],
                'nickname': char['nickname'],
                'class': char['class'],
                'level': char['level'],
                'lastUpdate': char['lastUpdate'],
                'totalItems': total_items,
                'stashCount': len(char['stashes']),
                'rank': char['rank'],
                'streamingModeName': char['streamingModeName']
            }
        return None

    def search_items(self, query: Dict) -> List[Dict]:
        """Search for items across all character stashes (empty for summary files)"""

        # query {'name': 'Arcane Hood', 'rarity': '3', 'properties': ['s_Agility', 's_ArmorPenetration', 's_Dexterity']}
        output = []
        for char in self.get_characters():
            for stash in char['stashes'].values():
                for item in stash:
                    result = {
                        'nickname': char['nickname'], 
                        'class': char['class'], 
                        'level': char['level'],
                        'item': item
                    }
                    output.append(result)

        return output

    def get_character_stash_previews(self, character_id):
        """Get preview images for all stashes of a character"""
        stashes = self.get_character_stashes(character_id)
        preview_paths = {}
        
        for stash_id, items in stashes.items():
            # Convert items to ItemInfo objects
            item_infos = [ItemInfo(**item) for item in items]
            # Generate preview
            preview = self.preview_generator.generate_preview(stash_id, item_infos)
            # Save preview
            outname = f"stash_preview_{character_id}_{stash_id}.png"
            outpath = os.path.join(self.output_dir, outname)
            preview.save(outpath)
            # Store relative path for the frontend
            preview_paths[stash_id] = f"/output/{outname}"
        
        return preview_paths