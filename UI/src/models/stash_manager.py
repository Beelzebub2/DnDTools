from pathlib import Path
import json
import os
from typing import Dict, List, Optional
import glob
from datetime import datetime
from .stash_preview import parse_stashes, StashPreviewGenerator, ItemInfo
from .storage import Storage, StashType
from .sort import StashSorter
from src.models.game_data import item_data_manager
import pygetwindow as gw
from .appdirs import get_data_dir, get_output_dir, resource_path

class StashManager:
    def __init__(self, resource_dir: str):
        self.data_dir = get_data_dir()
        self.output_dir = get_output_dir()
        # Only ensure data directory exists, not output directory
        os.makedirs(self.data_dir, exist_ok=True)
        
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
                    raw_stashes = parse_stashes(packet_data)
                    # Convert stash keys to strings for consistent lookup
                    stashes = {str(k): v for k, v in raw_stashes.items()}
                    # Convert raw class name
                    raw_class = char_data.get("characterClass", "")
                    class_name = raw_class.replace("DesignDataPlayerCharacter:Id_PlayerCharacter_", "")
                    # Map to the expected API format (fix key names here)
                    nickname = char_data.get("nickName", {}).get("originalNickName", "Unknown")
                    streaming_mode_name = char_data.get("nickName", {}).get("streamingModeNickName", "")
                    rank_id = char_data.get("nickName", {}).get("rankId", "Unknown")
                    fame = char_data.get("nickName", {}).get("fame", 0)
                    rank_icon_type = char_data.get("nickName", {}).get("rankIconType", 1)
                    self.characters_cache[char_id] = {
                        'id': char_id,
                        'nickname': nickname,
                        'class': class_name,
                        'level': char_data.get("level", 1),
                        'lastUpdate': datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat(),
                        'stashes': stashes,
                        'streamingModeName': streaming_mode_name,
                        'rank': {
                            'name': rank_id.replace("LeaderboardRankData:Id_LeaderboardRank_", "").replace("_", " "),
                            'fame': fame,
                            'iconType': rank_icon_type
                        }
                    }
                    print(f"Added character to cache: {char_id}")
            except Exception as e:
                print(f"Error loading packet data file {file_path}: {str(e)}")
                import traceback
                traceback.print_exc()
        print(f"\nLoaded {len(self.characters_cache)} characters")
        for char_id, char_data in self.characters_cache.items():
            print(f"Character: {char_data['nickname']}")
            print(f"Class: {char_data['class']}")
            print(f"Level: {char_data['level']}")
            print(f"Rank: {char_data['rank']['name']}")
            print("----------------------------------------")

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
        """Search for items across all character stashes"""
        

        if not query:
            return []
            
        keywords = [k.strip().lower() for k in query.split(",")]
        output = []
        
        for char in self.get_characters():
            for stash_id, stash in char.get('stashes', {}).items():
                if not isinstance(stash, list):
                    continue
                    
                for item in stash:
                    try:
                        design_str = item.get("itemId", "")
                        item_id = item_data_manager.get_item_id_from_design_str(design_str)
                        name = item_data_manager.get_item_name_from_id(item_id)
                        rarity = item_data_manager.get_item_rarity_from_id(item_id)

                        # Extract properties safely
                        data = item.get("data", {})
                        effect_str = "DesignDataItemPropertyType:Id_ItemPropertyType_Effect_"
                        
                        pp = []
                        for p in data.get("primaryPropertyArray", []):
                            if isinstance(p, dict) and "propertyTypeId" in p and "propertyValue" in p:
                                prop_name = p["propertyTypeId"].replace(effect_str, "")
                                pp.append((prop_name, p["propertyValue"]))
                                
                        sp = []
                        for p in data.get("secondaryPropertyArray", []):
                            if isinstance(p, dict) and "propertyTypeId" in p and "propertyValue" in p:
                                prop_name = p["propertyTypeId"].replace(effect_str, "")
                                sp.append((prop_name, p["propertyValue"]))

                        # Build search string including all item data
                        search_parts = [
                            name.lower(),
                            rarity.lower(),
                            *[p[0].lower() for p in pp],
                            *[p[0].lower() for p in sp]
                        ]
                        search_str = " ".join(search_parts)

                        # Check if all keywords match
                        if all(k in search_str for k in keywords):
                            result = {
                                'nickname': char['nickname'],
                                'id': char['id'],
                                'class': char['class'],
                                'level': char['level'],
                                'itemCount': item.get("itemCount", 1),
                                'slotId': item.get("slotId", 0),
                                'item': {
                                    'name': name,
                                    'rarity': rarity,
                                    'pp': pp,
                                    'sp': sp
                                },
                                'stash_id': stash_id
                            }
                            output.append(result)
                    except Exception as e:
                        print(f"Error processing item in search: {str(e)}")
                        continue

        return output

    def get_character_stash_previews(self, character_id):
        """Get detailed item data for all stashes of a character without generating image previews"""
        stashes = self.get_character_stashes(character_id)
        preview_paths = {}  # Keep empty dictionary for backward compatibility
        stash_data = {}
        
        for stash_id, items in stashes.items():
            try:
                # Create enhanced data for interactive grid rendering only
                enhanced_items = []
                for item in items:
                    try:
                        design_str = item.get("itemId", "")
                        item_id = item_data_manager.get_item_id_from_design_str(design_str)
                        name = item_data_manager.get_item_name_from_id(item_id)
                        rarity = item_data_manager.get_item_rarity_from_id(item_id)
                        width, height = item_data_manager.get_item_dimensions_from_id(item_id)
                        img_path = item_data_manager.get_item_image_path_from_id(item_id)
                        
                        # Extract properties from data
                        data = item.get("data", {})
                        effect_str = "DesignDataItemPropertyType:Id_ItemPropertyType_Effect_"
                        
                        pp = []
                        for p in data.get("primaryPropertyArray", []):
                            if isinstance(p, dict) and "propertyTypeId" in p and "propertyValue" in p:
                                prop_name = p["propertyTypeId"].replace(effect_str, "")
                                pp.append([prop_name, p["propertyValue"]])
                                
                        sp = []
                        for p in data.get("secondaryPropertyArray", []):
                            if isinstance(p, dict) and "propertyTypeId" in p and "propertyValue" in p:
                                prop_name = p["propertyTypeId"].replace(effect_str, "")
                                sp.append([prop_name, p["propertyValue"]])
                        
                        # Create URL for image path
                        # Convert path from PathLib object to proper URL format
                        image_url = None
                        if img_path:
                            # Use str(img_path) to convert the Path object to a string
                            # This will give us a relative path like "icons/Armor/BloodwovenGloves_3001.png"
                            image_url = f"/assets/{str(img_path)}"
                            # Replace backslashes with forward slashes for URLs
                            image_url = image_url.replace("\\", "/")
                        
                        # Create enhanced item data for frontend
                        enhanced_item = {
                            'name': name,
                            'itemId': item_id,
                            'slotId': item.get("slotId", 0),
                            'itemCount': item.get("itemCount", 1),
                            'rarity': rarity,
                            'width': width or 1,
                            'height': height or 1,
                            'pp': pp,
                            'sp': sp,
                            'imagePath': image_url,
                            'vendor_price': item_data_manager.data.get(item_id, {}).get("vendor_price", 0)
                        }
                        enhanced_items.append(enhanced_item)
                    except Exception as e:
                        print(f"Error enhancing item data: {str(e)}")
                        # Add minimal item data if enhancement fails
                        enhanced_items.append({
                            'name': 'Unknown Item',
                            'itemId': item.get("itemId", "unknown"),
                            'slotId': item.get("slotId", 0),
                            'itemCount': item.get("itemCount", 1),
                            'rarity': 'Common',
                            'width': 1,
                            'height': 1
                        })
                        
                stash_data[stash_id] = enhanced_items
                # Add a placeholder path for backward compatibility
                preview_paths[stash_id] = "/static/img/placeholder.png"
            except Exception as e:
                print(f"Error processing stash {stash_id}: {str(e)}")
                import traceback
                traceback.print_exc()
                preview_paths[stash_id] = "/static/img/error.png"  # Fallback image
                stash_data[stash_id] = []  # Empty stash data as fallback
        
        # Return both the placeholder paths (for backward compatibility) and the enhanced data
        response = {
            'previewImages': preview_paths,
            'stashData': stash_data
        }
        return response

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
            stashes = parse_stashes(raw)
            inv_items = stashes.get(StashType.BAG.value, [])
        except:
            inv_items = []
        
        # Create Storage instances
        stash = Storage(StashType.STORAGE.value, stash_items)
        inventory = Storage(StashType.BAG.value, inv_items)
        
        # Check if 'Dark and Darker' window exists before proceeding
        windows = [w for w in gw.getAllWindows() if w.title == "Dark and Darker  "]
        if not windows:
            print("Game window 'Dark and Darker' not found. Sorting cancelled.")
            return False, "Game window not found. Please make sure Dark and Darker is running."
            
        # If window exists, try to focus it
        try:
            windows[0].activate()
            print("Focused window: Dark and Darker")
        except Exception as e:
            print(f"Error focusing window: {e}")
        
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
    
    def _generate_previews(self, character_id):
        # TODO ?
        pass