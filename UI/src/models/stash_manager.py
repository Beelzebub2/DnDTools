from pathlib import Path
import json
import os
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union
import glob
from datetime import datetime
from .stash_preview import parse_stashes, StashPreviewGenerator, ItemInfo
from .storage import Storage, StashType
from .sort import StashSorter
from src.models.game_data import item_data_manager
from src.models import macros
import pygetwindow as gw
from .appdirs import get_data_dir, get_output_dir, resource_path
from src.models.icon_pak import canonical_icon_path
import asyncio
from concurrent.futures import ThreadPoolExecutor as ThreadPool
import logging
from src.models.game_overlay import NullOverlaySession, SortOverlaySession
from src.models.loot import format_loot_state_label

logger = logging.getLogger(__name__)

class StashManager:
    def __init__(self, resource_dir: str, defer_loading=False):
        self.data_dir = get_data_dir()
        self.output_dir = get_output_dir()
        # Only ensure data directory exists, not output directory
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.characters_cache = {}
        self._is_loaded = False
        self.resource_dir = resource_dir
        
        # Performance tracking
        self.load_stats = {
            'last_load_time': None,
            'characters_loaded': 0,
            'files_processed': 0,
            'average_load_time_per_file': None
        }
        
        # Initialize preview generator (lightweight)        self.preview_generator = StashPreviewGenerator(resource_dir=resource_dir)
        
        # Load data immediately unless deferred
        if not defer_loading:
            self._load_data()
            
    def force_reload(self):
        """Force reload of character data, ignoring the loaded flag"""
        self._is_loaded = False
        self.characters_cache.clear()
        self._load_data()
        
    def _load_data(self, force=False):
        """
        Load character data from packet data files
        
        Args:
            force: If True, forces a reload even if data is already loaded
        """
        if self._is_loaded and not force:
            logger.info("Data already loaded, skipping reload")
            return

        start_time = time.time()
        self.characters_cache.clear()
        logger.info(f"Loading characters from: {self.data_dir}")
        
        # Get all JSON files first and sort by modification time (newest first)
        json_files = []
        for file_path in Path(self.data_dir).glob("*.json"):
            try:
                # Quick size check before adding to list
                file_size = file_path.stat().st_size
                if file_size > 10 * 1024 * 1024:  # > 10MB
                    logger.warning(f"Skipping oversized file: {file_path} ({file_size/1024/1024:.2f} MB)")
                    continue
                json_files.append(file_path)
            except OSError:
                continue
        
        # Sort by modification time (newest first) for better user experience
        json_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        
        def load_file(file_path):
            try:
                # Skip excessively large files (likely corrupted)
                file_size = os.path.getsize(file_path)
                if file_size > 10 * 1024 * 1024:  # > 10MB
                    logger.warning(f"Skipping oversized file: {file_path} ({file_size/1024/1024:.2f} MB)")
                    return None
                    
                with open(file_path, 'r', encoding='utf-8') as f:
                    packet_data = json.load(f)
                char_data = packet_data.get("characterDataBase", {})
                if not char_data:
                    return None
                char_id = str(char_data.get("characterId"))
                if not char_id:
                    logger.warning(f"No characterId in {file_path}")
                    return None
                    
                # Parse stashes efficiently
                raw_stashes = parse_stashes(packet_data)
                stashes = {str(k): v for k, v in raw_stashes.items()}
                
                # Extract character data
                raw_class = char_data.get("characterClass", "")
                class_name = raw_class.replace("DesignDataPlayerCharacter:Id_PlayerCharacter_", "")
                nickname_data = char_data.get("nickName", {})
                
                return {
                    'id': char_id,
                    'file_path': file_path,
                    'character_data': {
                        'id': char_id,
                        'nickname': nickname_data.get("originalNickName", "Unknown"),
                        'class': class_name,
                        'level': char_data.get("level", 1),
                        'lastUpdate': datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat(),
                        'stashes': stashes,
                        'streamingModeName': nickname_data.get("streamingModeNickName", ""),
                        'rank': {
                            'name': nickname_data.get("rankId", "Unknown").replace("LeaderboardRankData:Id_LeaderboardRank_", "").replace("_", " "),
                            'fame': nickname_data.get("fame", 0),
                            'iconType': nickname_data.get("rankIconType", 1)
                        }
                    }
                }
            except Exception as e:
                logger.error(f"Error loading packet data file {file_path}: {str(e)}")
                return None
                
        logger.info(f"Found {len(json_files)} packet data files")

        # Optimize worker count based on file count and system capabilities
        cpu_count = os.cpu_count() or 4
        max_workers = max(1, min(cpu_count, len(json_files), 8))  # Cap at 8 workers
        
        # Use asyncio to make data loading truly asynchronous
        async def load_data_async():
            loop = asyncio.get_event_loop()
            loaded_count = 0
            
            with ThreadPool(max_workers=max_workers) as pool:
                # Submit all tasks using the pre-sorted file list
                futures = [loop.run_in_executor(pool, load_file, str(file_path)) for file_path in json_files]
                
                # Process results as they complete (asynchronous processing)
                for future in asyncio.as_completed(futures):
                    result = await future
                    if result:
                        char_id = result['id']
                        self.characters_cache[char_id] = result['character_data']
                        loaded_count += 1
                        
                        # Log progress for large loads
                        if loaded_count % 10 == 0:
                            logger.info(f"Loaded {loaded_count}/{len(json_files)} characters...")
            
            return loaded_count

        # Run the async loading
        loaded_count = asyncio.run(load_data_async())
        
        load_time = time.time() - start_time
        self.load_stats.update({
            'last_load_time': load_time,
            'characters_loaded': loaded_count,
            'files_processed': len(json_files),
            'average_load_time_per_file': load_time / len(json_files) if json_files else 0
        })
        
        logger.info(f"Loaded {loaded_count} characters in {load_time:.2f} seconds")
        logger.info(f"Average load time per file: {self.load_stats['average_load_time_per_file']:.4f} seconds")
        
        # Only show character details for small number of characters
        if loaded_count <= 3:
            for char_id, char_data in self.characters_cache.items():
                logger.info(f"Character: {char_data['nickname']} ({char_data['class']}, Level {char_data['level']})")
        else:
            logger.info(f"Character details hidden for performance (loaded {loaded_count} characters)")

        # Mark data as loaded
        self._is_loaded = True

    def get_performance_stats(self) -> Dict:
        """Get performance statistics for data loading and memory usage"""
        import psutil
        import sys
        
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return {
            'load_stats': self.load_stats,
            'memory_usage': {
                'rss': memory_info.rss / 1024 / 1024,  # MB
                'vms': memory_info.vms / 1024 / 1024,  # MB
            },
            'cache_info': {
                'characters_cached': len(self.characters_cache),
                'total_stashes': sum(len(char.get('stashes', {})) for char in self.characters_cache.values()),
                'estimated_items': sum(
                    sum(len(stash) for stash in char.get('stashes', {}).values() if isinstance(stash, list))
                    for char in self.characters_cache.values()
                )
            },
            'system_info': {
                'cpu_count': os.cpu_count(),
                'python_version': sys.version
            }
        }

    def get_characters(self) -> List[Dict]:
        """Get list of all characters"""
        # Ensure data is loaded before returning characters
        if not self._is_loaded:
            self._load_data()
            
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

    def get_item_holdings(
        self,
        item_ids: Iterable[str],
    ) -> Dict[str, List[Dict]]:
        """Aggregate how many of the specified items exist across all characters."""
        if not item_ids:
            return {}

        targets = [str(item_id).strip() for item_id in item_ids if item_id]
        if not targets:
            return {}

        if not self._is_loaded:
            self._load_data()

        target_set = set(targets)
        aggregated: Dict[str, List[Dict]] = {item_id: [] for item_id in target_set}

        for char in self.get_characters():
            if not isinstance(char, dict):
                continue

            stashes = char.get('stashes') or {}
            if not isinstance(stashes, dict):
                continue

            character_holdings: Dict[str, Dict] = {}

            for stash_id, items in stashes.items():
                if not isinstance(items, list):
                    continue

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    design_str = item.get("itemId") or ""
                    try:
                        canonical_id = item_data_manager.get_item_id_from_design_str(design_str)
                    except Exception:
                        continue

                    if canonical_id not in target_set:
                        continue

                    loot_state_raw = item.get("data", {}).get("lootState")
                    try:
                        loot_state_value = int(loot_state_raw)
                    except (TypeError, ValueError):
                        loot_state_value = None

                    count_raw = item.get("itemCount", 1)
                    try:
                        count = int(count_raw)
                    except (TypeError, ValueError):
                        count = 1
                    if count <= 0:
                        count = 1

                    stash_entry = {
                        'stash_id': str(stash_id),
                        'count': count,
                        'slot_id': item.get("slotId"),
                    }
                    if loot_state_value is not None:
                        stash_entry['loot_state'] = loot_state_value

                    holding = character_holdings.setdefault(canonical_id, {
                        'character_id': str(char.get('id')),
                        'character_name': char.get('nickname') or 'Unknown',
                        'character_class': char.get('class'),
                        'character_level': char.get('level'),
                        'last_update': char.get('lastUpdate'),
                        'total': 0,
                        'stashes': []
                    })
                    holding['total'] += count
                    holding['stashes'].append(stash_entry)

            for item_id, info in character_holdings.items():
                info['stashes'].sort(
                    key=lambda payload: (-payload.get('count', 0), str(payload.get('stash_id')))
                )
                aggregated.setdefault(item_id, []).append(info)

        for item_id, entries in aggregated.items():
            entries.sort(
                key=lambda payload: (
                    -payload.get('total', 0),
                    (payload.get('character_name') or '').lower()
                )
            )

        return {item_id: aggregated.get(item_id, []) for item_id in targets}

    def search_items(self, query: str) -> List[Dict]:
        """Search for items across all character stashes"""
        query = (query or '').strip()
        if not query:
            return []

        if not self._is_loaded:
            self._load_data()

        keywords = [segment.strip().lower() for segment in query.split(',') if segment.strip()]
        if not keywords:
            return []

        output: List[Dict] = []
        effect_prefix = "DesignDataItemPropertyType:Id_ItemPropertyType_Effect_"

        for char in self.get_characters():
            stashes = char.get('stashes', {})
            if not isinstance(stashes, dict):
                continue

            char_nickname = char.get('nickname') or 'Unknown'
            char_id = char.get('id')
            char_class = char.get('class') or 'Unknown'
            char_level = char.get('level')

            for stash_id, stash in stashes.items():
                if not isinstance(stash, list):
                    continue

                for item in stash:
                    try:
                        design_str = item.get("itemId") or ""

                        try:
                            item_id = item_data_manager.get_item_id_from_design_str(design_str)
                        except Exception:
                            item_id = design_str or item.get("data", {}).get("itemUniqueId") or "unknown"

                        try:
                            name = item_data_manager.get_item_name_from_id(item_id)
                        except Exception:
                            name = design_str or "Unknown Item"

                        try:
                            rarity = item_data_manager.get_item_rarity_from_id(item_id)
                        except Exception:
                            rarity = "Unknown"

                        try:
                            raw_icon_path = item_data_manager.get_item_image_path_from_id(item_id)
                        except Exception:
                            raw_icon_path = None
                        icon_path = canonical_icon_path(raw_icon_path) if raw_icon_path else None

                        data = item.get("data") or {}

                        pp: List[Tuple[str, object]] = []
                        for prop in data.get("primaryPropertyArray", []):
                            if not isinstance(prop, dict):
                                continue
                            prop_id = prop.get("propertyTypeId")
                            if not prop_id:
                                continue
                            prop_name = str(prop_id).replace(effect_prefix, "")
                            pp.append((prop_name, prop.get("propertyValue")))

                        sp: List[Tuple[str, object]] = []
                        for prop in data.get("secondaryPropertyArray", []):
                            if not isinstance(prop, dict):
                                continue
                            prop_id = prop.get("propertyTypeId")
                            if not prop_id:
                                continue
                            prop_name = str(prop_id).replace(effect_prefix, "")
                            sp.append((prop_name, prop.get("propertyValue")))

                        search_parts = [
                            str(name).lower(),
                            str(rarity).lower(),
                            *[str(prop_name).lower() for prop_name, _ in pp],
                            *[str(prop_name).lower() for prop_name, _ in sp],
                        ]
                        search_str = " ".join(filter(None, search_parts))
                        if not search_str or not all(keyword in search_str for keyword in keywords):
                            continue

                        item_count_raw = item.get("itemCount", 1)
                        try:
                            item_count = int(item_count_raw)
                        except (TypeError, ValueError):
                            item_count = 1
                        if item_count < 0:
                            item_count = 0

                        slot_id_raw = item.get("slotId")
                        try:
                            slot_id = int(slot_id_raw)
                        except (TypeError, ValueError):
                            slot_id = slot_id_raw

                        output.append({
                            'nickname': char_nickname,
                            'id': char_id,
                            'class': char_class,
                            'level': char_level,
                            'itemCount': item_count,
                            'slotId': slot_id,
                            'item': {
                                'name': name or "Unknown Item",
                                'rarity': rarity or "Unknown",
                                'pp': pp,
                                'sp': sp,
                                'iconPath': icon_path,
                            },
                            'stash_id': stash_id,
                        })
                    except Exception as exc:
                        logger.error("Error processing item in search: %s", exc)
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
                            image_url_path = canonical_icon_path(img_path)
                            if image_url_path:
                                image_url = f"/assets/{image_url_path}"
                        max_stack = item_data_manager.get_item_max_stack_size(item_id)
                        loot_state_raw = item.get("data", {}).get("lootState")
                        loot_state_value = None
                        loot_state_label = None
                        if loot_state_raw is not None:
                            try:
                                loot_state_value = int(loot_state_raw)
                            except (TypeError, ValueError):
                                loot_state_value = None
                            if loot_state_value is not None:
                                loot_state_label = format_loot_state_label(loot_state_value)
                            else:
                                loot_state_label = str(loot_state_raw)
                        enhanced_item = {
                            'name': name,
                            'itemId': item_id,
                            'itemUniqueId': str(data.get("itemUniqueId", "")),
                            'originalData': data,
                            'slotId': item.get("slotId", 0),
                            'itemCount': item.get("itemCount", 1),
                            'rarity': rarity,
                            'width': width or 1,
                            'height': height or 1,
                            'pp': pp,
                            'sp': sp,
                            'imagePath': image_url,
                            'vendor_price': item_data_manager.get_item_vendor_price(item_id),
                            'maxStackSize': max_stack,
                            'max_stack_size': max_stack
                        }
                        if loot_state_value is not None:
                            enhanced_item['lootState'] = loot_state_value
                        if loot_state_label:
                            enhanced_item['lootStateLabel'] = loot_state_label
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

    def sort_stash(
        self,
        character_id,
        stash_id,
        cancel_event=None,
        pack_mode=False,
        stack_mode=False,
        overlay_session: Union[SortOverlaySession, NullOverlaySession, None] = None,
    ):
        logger.info(f"Sorting stash {stash_id} for character {character_id}")
        session: Union[SortOverlaySession, NullOverlaySession]
        session = overlay_session or NullOverlaySession()

        session.update_status("Validating inventory data...", status="info")
        char = self.characters_cache.get(str(character_id))
        if not char:
            session.update_status("Character not found in cache.", status="error")
            session.add_log("No packet data available for selected character.")
            logger.warning("Character %s not found in cache", character_id)
            return False, "Character not found"
        stash_items = char.get('stashes', {}).get(str(stash_id))
        if not stash_items:
            session.update_status("Selected stash is empty or missing.", status="error")
            session.add_log(f"Stash {stash_id} could not be found for this character.")
            logger.warning("Stash %s not found for character %s", stash_id, character_id)
            return False, "Stash not found"
        session.update_status("Loading character inventory...", status="info")
        file_path = os.path.join(self.data_dir, f"{character_id}.json")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            stashes = parse_stashes(raw)
            inv_items = stashes.get(StashType.BAG.value, [])
        except Exception as e:
            logger.error(f"Error loading inventory items: {str(e)}")
            inv_items = []
            session.add_log("Unable to read latest inventory snapshot; continuing with empty inventory.")
        stash = Storage(StashType.STORAGE.value, stash_items)
        inventory = Storage(StashType.BAG.value, inv_items)
        session.update_status("Locating Dark and Darker window...", status="info")
        windows = [w for w in gw.getAllWindows() if w.title == "Dark and Darker  "]
        if not windows:
            logger.warning("Game window 'Dark and Darker' not found. Sorting cancelled.")
            session.update_status("Game window not found. Please bring Dark and Darker to the foreground.", status="error")
            session.add_log("Window titled 'Dark and Darker  ' was not detected.")
            return False, "Game window not found. Please make sure Dark and Darker is running."
        try:
            windows[0].activate()
            logger.info("Focused window: Dark and Darker")
            session.update_status("Game window focused. Resetting modifiers...", status="info")
            self._reset_modifier_state(session)
            session.update_status("Game window focused. Executing sort...", status="info")
        except Exception as e:
            logger.error(f"Error focusing window: {e}")
            session.add_log("Unable to focus the game window automatically – please ensure it is active.")

        sorter = StashSorter(stash, inventory, pack_mode=pack_mode, stack_mode=stack_mode)
        session.add_log(
            f"Pack mode: {'On' if sorter.pack_mode else 'Off'} · Stack mode: {'On' if sorter.stack_mode else 'Off'}"
        )
        if cancel_event and cancel_event.is_set():
            return False, "Sort cancelled"
        success = sorter.sort(cancel_event, overlay_session=session)
        if cancel_event and cancel_event.is_set():
            return False, "Sort cancelled"
        if success:
            session.update_status("Refreshing stash data...", status="success")
            self._generate_previews(character_id)
        return success, None

    def _reset_modifier_state(self, session: Union[SortOverlaySession, NullOverlaySession]) -> None:
        if not hasattr(macros, "tap_alt"):
            logger.debug("tap_alt helper unavailable; skipping modifier reset")
            return
        try:
            macros.tap_alt()
        except Exception as exc:
            logger.debug("Failed to reset modifier state via Alt tap: %s", exc)
        else:
            session.add_log("Tapped Alt to clear any stuck modifier state.")

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
        """
        Generate visual previews for character stashes.
        This functionality is currently disabled but may be implemented in the future
        to provide visual representations of stash contents.
        """
        # Preview generation is currently not implemented
        # This could be extended to use the StashPreviewGenerator class
        pass