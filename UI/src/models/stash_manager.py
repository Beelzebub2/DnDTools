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
from concurrent.futures import ThreadPoolExecutor as ThreadPool
import logging

logger = logging.getLogger(__name__)

class StashManager:
    def __init__(self, resource_dir: str):
        self.data_dir = get_data_dir()
        self.output_dir = get_output_dir()
        # Only ensure data directory exists, not output directory
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.characters_cache = {}
        self._is_loaded = False
        self._load_data()
        self.preview_generator = StashPreviewGenerator(resource_dir=resource_dir)

    def force_reload(self):
        """Force reload of character data, ignoring the loaded flag"""
        self._is_loaded = False
        self.characters_cache.clear()
        self._load_data()

    def _load_data(self):
        """Load character data from packet data files"""
        if self._is_loaded:
            logger.info("Data already loaded, skipping reload")
            return

        self.characters_cache.clear()
        logger.info(f"Loading characters from: {self.data_dir}")
        def load_file(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    packet_data = json.load(f)
                char_data = packet_data.get("characterDataBase", {})
                if not char_data:
                    return None
                char_id = str(char_data.get("characterId"))
                if not char_id:
                    logger.warning(f"No characterId in {file_path}")
                    return None
                raw_stashes = parse_stashes(packet_data)
                stashes = {str(k): v for k, v in raw_stashes.items()}
                raw_class = char_data.get("characterClass", "")
                class_name = raw_class.replace("DesignDataPlayerCharacter:Id_PlayerCharacter_", "")
                nickname = char_data.get("nickName", {}).get("originalNickName", "Unknown")
                streaming_mode_name = char_data.get("nickName", {}).get("streamingModeNickName", "")
                rank_id = char_data.get("nickName", {}).get("rankId", "Unknown")
                fame = char_data.get("nickName", {}).get("fame", 0)
                rank_icon_type = char_data.get("nickName", {}).get("rankIconType", 1)
                return {
                    'id': char_id,
                    'file_path': file_path,
                    'character_data': {
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
                }
            except Exception as e:
                logger.error(f"Error loading packet data file {file_path}: {str(e)}")
                return None        # Load all JSON files from the data directory
        json_files = glob.glob(os.path.join(self.data_dir, "*.json"))
        logger.info(f"Found {len(json_files)} packet data files")

        # Use multiprocessing to load files in parallel
        max_workers = max(1, min(4, len(json_files)))  # Always >= 1
        with ThreadPool(max_workers=max_workers) as pool:
            results = pool.map(load_file, json_files)


        # Process results
        for result in results:
            if result:
                char_id = result['id']
                self.characters_cache[char_id] = result['character_data']

        logger.info(f"Loaded {len(self.characters_cache)} characters")
        # Reduce verbose logging during startup for better performance
        if len(self.characters_cache) <= 3:  # Only show details for small number of characters
            for char_id, char_data in self.characters_cache.items():
                logger.info(f"Character: {char_data['nickname']} ({char_data['class']}, Level {char_data['level']})")
        else:
            logger.info(f"Character details hidden for performance (loaded {len(self.characters_cache)} characters)")

        # Mark data as loaded
        self._is_loaded = True

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
                        search_parts = [
                            name.lower(),
                            rarity.lower(),
                            *[p[0].lower() for p in pp],
                            *[p[0].lower() for p in sp]
                        ]
                        search_str = " ".join(search_parts)
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
                        logger.error(f"Error processing item in search: {str(e)}")
                        continue
        return output

    def get_character_stash_previews(self, character_id):
        """Get detailed item data for all stashes of a character without generating image previews"""
        stashes = self.get_character_stashes(character_id)
        preview_paths = {}  # Keep empty dictionary for backward compatibility
        stash_data = {}
        for stash_id, items in stashes.items():
            try:
                enhanced_items = []
                for item in items:
                    try:
                        design_str = item.get("itemId", "")
                        item_id = item_data_manager.get_item_id_from_design_str(design_str)
                        name = item_data_manager.get_item_name_from_id(item_id)
                        rarity = item_data_manager.get_item_rarity_from_id(item_id)
                        width, height = item_data_manager.get_item_dimensions_from_id(item_id)
                        img_path = item_data_manager.get_item_image_path_from_id(item_id)
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
                        image_url = None
                        if img_path:
                            image_url = f"/assets/{str(img_path)}"
                            image_url = image_url.replace("\\", "/")
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
                        logger.error(f"Error enhancing item data: {str(e)}")
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
                preview_paths[stash_id] = "/static/img/placeholder.png"
            except Exception as e:
                logger.error(f"Error processing stash {stash_id}: {str(e)}")
                import traceback
                traceback.print_exc()
                preview_paths[stash_id] = "/static/img/error.png"
                stash_data[stash_id] = []
        response = {
            'previewImages': preview_paths,
            'stashData': stash_data
        }
        return response

    def sort_stash(self, character_id, stash_id, cancel_event=None):
        logger.info(f"Sorting stash {stash_id} for character {character_id}")
        char = self.characters_cache.get(str(character_id))
        if not char:
            return False, "Character not found"
        stash_items = char.get('stashes', {}).get(str(stash_id))
        if not stash_items:
            return False, "Stash not found"
        file_path = os.path.join(self.data_dir, f"{character_id}.json")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            stashes = parse_stashes(raw)
            inv_items = stashes.get(StashType.BAG.value, [])
        except Exception as e:
            logger.error(f"Error loading inventory items: {str(e)}")
            inv_items = []
        stash = Storage(StashType.STORAGE.value, stash_items)
        inventory = Storage(StashType.BAG.value, inv_items)
        windows = [w for w in gw.getAllWindows() if w.title == "Dark and Darker  "]
        if not windows:
            logger.warning("Game window 'Dark and Darker' not found. Sorting cancelled.")
            return False, "Game window not found. Please make sure Dark and Darker is running."
        try:
            windows[0].activate()
            logger.info("Focused window: Dark and Darker")
        except Exception as e:
            logger.error(f"Error focusing window: {e}")
        sorter = StashSorter(stash, inventory)
        if cancel_event and cancel_event.is_set():
            return False, "Sort cancelled"
        success = sorter.sort(cancel_event)
        if cancel_event and cancel_event.is_set():
            return False, "Sort cancelled"
        if success:
            self._generate_previews(character_id)
        return success, None

    def _get_character(self, character_id):
        try:
            file_path = os.path.join(self.data_dir, f"{character_id}.json")
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    packet_data = json.load(f)
                return packet_data.get("characterDataBase", {})
            return None
        except Exception as e:
            logger.error(f"Error reading character data: {str(e)}")
            return None

    def _save_character(self, character_id, char_data):
        try:
            file_path = os.path.join(self.data_dir, f"{character_id}.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump({"characterDataBase": char_data}, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving character data: {str(e)}")
            return False

    def _generate_previews(self, character_id):
        # TODO ?
        pass