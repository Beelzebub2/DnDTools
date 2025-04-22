import json
import os
from typing import Dict, List, Optional
import glob
from datetime import datetime

class StashManager:
    def __init__(self, base_dir: str):
        print(f"Initializing StashManager with base_dir: {base_dir}")
        self.data_dir = os.path.join(base_dir, "data")
        self.characters_dir = os.path.join(self.data_dir, "characters")
        print(f"Data dir: {self.data_dir}")
        print(f"Characters dir: {self.characters_dir}")
        
        # Ensure directories exist
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.characters_dir, exist_ok=True)
        
        self.characters_cache = {}
        self._load_data()

    def _load_data(self):
        """Load character summaries and stash data from the characters directory"""
        self.characters_cache.clear()
        print(f"\nLoading characters from: {self.characters_dir}")
        
        char_files = glob.glob(os.path.join(self.characters_dir, "*.json"))
        print(f"Found {len(char_files)} character files: {char_files}")

        for file_path in char_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    print(f"\nReading file: {file_path}")
                    char_data = json.load(f)
                    
                    # Check if we have the required fields
                    char_id = char_data.get('characterId')
                    if not char_id:
                        print(f"Warning: No characterId in {file_path}")
                        continue
                        
                    # Extract stash data if available (may not be in character summary files)
                    stashes = {}
                    if 'stashes' in char_data:
                        stashes = char_data['stashes']
                    elif 'inventory' in char_data:
                        stashes = {'inventory': char_data['inventory']}
                        
                    # Map the file structure to the expected API format
                    self.characters_cache[char_id] = {
                        'id': char_id,
                        'nickname': char_data.get('characterName', 'Unknown'),
                        'class': char_data.get('characterClass', 'Unknown'),
                        'level': char_data.get('level', 1),
                        'lastUpdate': char_data.get('lastUpdated', 
                            datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()),
                        'stashes': stashes
                    }
                    print(f"Added character to cache: {char_id}")
            except Exception as e:
                print(f"Error loading character file {file_path}: {str(e)}")
                import traceback
                traceback.print_exc()

        print(f"\nLoaded {len(self.characters_cache)} characters")
        # Debug output each character
        for char_id, char_data in self.characters_cache.items():
            print(f"Character: {char_id}, Name: {char_data['nickname']}, Class: {char_data['class']}")

    def get_characters(self) -> List[Dict]:
        """Get list of all characters"""
        print(f"Characters cache: {self.characters_cache}")
        return list(self.characters_cache.values())

    def get_character_stashes(self, character_id: str) -> Dict:
        """Get all stashes for a specific character (empty for summary files)"""
        char = self.characters_cache.get(character_id)
        if char:
            return char.get('stashes', {})
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
                'stashCount': len(char['stashes'])
            }
        return None

    def search_items(self, query: str) -> List[Dict]:
        """Search for items across all character stashes (empty for summary files)"""
        return []