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
                    raw_stashes = parse_stashes(packet_data, self.item_data)
                    # Convert stash keys to strings for consistent lookup
                    stashes = {str(k): v for k, v in raw_stashes.items()}
                        
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
                    name = query.get("name")
                    # TODO implement rarity and properties and make result include stash
                    rarity = query.get("rarity")
                    if name:
                        if item["name"] == name.replace(" ", ""):
                            result = {
                                'nickname': char['nickname'],
                                'id': char['id'],
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
            try:
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
            except Exception as e:
                print(f"Error generating preview for stash {stash_id}: {str(e)}")
                preview_paths[stash_id] = "/static/img/error.png"  # Fallback image
        
        return preview_paths

    def sort_stash(self, character_id, stash_id):
        """Sort items in a specific stash for a character"""
        print(f"sort_stash called with character_id={character_id}, stash_id={stash_id}")
        char = self.characters_cache.get(character_id)
        print(f"Available characters in cache: {list(self.characters_cache.keys())}")
        if not char:
            return False, "Character not found"

        stashes = char.get('stashes', {})
        print(f"Character's stashes keys: {list(stashes.keys())}")
        stash_items = stashes.get(str(stash_id), [])
        print(f"Retrieved stash_items length: {len(stash_items)} for key {stash_id}")
        if not stash_items:
            return False, "Stash not found or empty"
            
        try:
            # Create storage objects for sorting
            from .sort import Storage, StashSorter, StashType
            
            # Convert stash_id to int and get corresponding StashType
            stash_id_int = int(stash_id)
            
            # Try to get the StashType directly by value
            try:
                stash_type = StashType(stash_id_int).value
            except ValueError:
                # Handle purchased storage special case
                if stash_id_int >= StashType.PURCHASED_STORAGE_0.value and stash_id_int <= StashType.PURCHASED_STORAGE_4.value:
                    stash_type = stash_id_int  # Use the ID directly since it matches the enum values
                else:
                    return False, f"Invalid stash ID: {stash_id}"
            
            # temp
            import time
            time.sleep(3)

            # Create a storage object for the stash to be sorted
            stash = Storage(stash_type, stash_items)
            
            # Get bag items for overflow handling - this matches your working example
            bag_items = stashes.get(str(StashType.BAG.value), [])
            # Create an inventory storage with the character's actual bag contents
            inv = Storage(StashType.BAG.value, bag_items)
            
            # Create and run sorter
            sorter = StashSorter(stash, inv)
            success = sorter.sort()
            
            if not success:
                return False, "Not enough space to sort items"

            # Check if items were moved to the bag (overflow)
            if len(inv.get_items()) > len(bag_items):
                return False, "Some items could not be placed back in the stash"
                
            # Update the stash in our cache with the sorted items
            stashes[str(stash_id)] = stash.get_items()
            # Also update bag if items were moved there
            stashes[str(StashType.BAG.value)] = inv.get_items()
            
            char['stashes'] = stashes
            return True, None
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"Error while sorting: {str(e)}"