import json
import os
from typing import Dict, List, Optional
import glob
from datetime import datetime
from .stash_preview import parse_stashes, ItemDataManager, StashPreviewGenerator, ItemInfo, get_item_rarity_from_id, get_item_name_from_id

class StashManager:
    def __init__(self, resource_dir: str):
        import sys
        
        # For frozen EXE, use the EXE's directory for dynamic data (data/output)
        # but use _MEIPASS (resource_dir) for static resources
        if getattr(sys, 'frozen', False):
            # Get EXE directory for data/output
            exe_dir = os.path.dirname(sys.executable)
            self.data_dir = os.path.join(exe_dir, "data")
            self.output_dir = os.path.join(exe_dir, "output")
        else:
            # In development, everything lives under resource_dir
            self.data_dir = os.path.join(resource_dir, "data")
            self.output_dir = os.path.join(resource_dir, "output")
            
        # Initialize item data from the resource directory
        self.item_data = ItemDataManager(resource_dir).item_data
        print(f"Data dir: {self.data_dir}")
        
        # Ensure directories exist
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.characters_cache = {}
        self._load_data()
        self.preview_generator = StashPreviewGenerator(resource_dir=resource_dir)

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
        if (char):
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

    def search_items(self, query: str) -> List[Dict]:
        """Search for items across all character stashes (empty for summary files)"""
        keywords = query.lower().replace(" ", "").split(",")
        priority = 0
        output = []
        for char in self.get_characters():
            for stash_id, stash in char.get('stashes', []).items():
                for item in stash:
                    rarity = get_item_rarity_from_id(item.get("itemId", ""))
                    name = get_item_name_from_id(item.get("itemId", ""))

                    data = item.get("data", {})
                    effect_str = "DesignDataItemPropertyType:Id_ItemPropertyType_Effect_"
                    pp = [(p["propertyTypeId"].replace(effect_str, ""), p["propertyValue"]) for p in data.get("primaryPropertyArray", [])]
                    sp = [(p["propertyTypeId"].replace(effect_str, ""), p["propertyValue"]) for p in data.get("secondaryPropertyArray", [])]

                    search_str = (str(pp) + str(sp) + rarity + name).lower().replace(" ", "")

                    hit = True
                    for keyword in keywords:
                        if keyword not in search_str:
                            hit = False

                    itemCount = item.get("itemCount", 1)
                    slotId = item.get("slotId", 0)

                    item = {
                        "name": name,
                        "rarity": rarity,
                        "pp": pp,
                        "sp": sp
                    }

                    if hit:
                        result = {
                            'nickname': char['nickname'],
                            'id': char['id'],
                            'class': char['class'], 
                            'level': char['level'],
                            "itemCount": itemCount,
                            "slotId": slotId,
                            'item': item,
                            'stash_id': stash_id
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

    def sort_stash(self, character_id, stash_id, cancel_event=None):
        """Sort a stash with optional cancellation support"""
        # Use cache for stash items
        print(f"Sorting stash {stash_id} for character {character_id}")
        char = self.characters_cache.get(str(character_id))
        if not char:
            return False, "Character not found"
        stash_items = char.get('stashes', {}).get(str(stash_id))
        if not stash_items:
            return False, "Stash not found"
        # Load inventory items from raw packet data
        file_path = os.path.join(self.data_dir, f"{character_id}.json")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            char_base = raw.get('characterDataBase', {})
            inv_items = char_base.get('CharacterItemList', []) or []
        except:
            inv_items = []
        # Create Storage instances
        stash = Storage(StashType.STORAGE.value, stash_items)
        inventory = Storage(StashType.BAG.value, inv_items)
        # Perform sorting
        sorter = StashSorter(stash, inventory)
        if cancel_event and cancel_event.is_set():
            return False, "Sort cancelled"
        success = sorter.sort(cancel_event)
        if cancel_event and cancel_event.is_set():
            return False, "Sort cancelled"
        # On success, generate previews
        if success:
            self._generate_previews(character_id)
        return success, None

    def _get_character(self, character_id):
        """Get character data from data file"""
        try:
            file_path = os.path.join(self.data_dir, f"{character_id}.json")
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    packet_data = json.load(f)
                    return packet_data.get("characterDataBase", {})
            return None
        except Exception as e:
            print(f"Error reading character data: {str(e)}")
            return None
            
    def _save_character(self, character_id, char_data):
        """Save character data back to file"""
        try:
            file_path = os.path.join(self.data_dir, f"{character_id}.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump({"characterDataBase": char_data}, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving character data: {str(e)}")
            return False