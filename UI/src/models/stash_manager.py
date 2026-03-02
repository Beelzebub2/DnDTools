from pathlib import Path
import json
import os
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union
import glob
from datetime import datetime
from .stash_preview import parse_stashes, StashPreviewGenerator, ItemInfo
from .storage import Storage, StashType
from .sort import StashSorter, LayoutPlanner, LayoutPlanError
from src.models.game_data import item_data_manager
from src.models import macros
import pygetwindow as gw
from .appdirs import get_output_dir, resource_path, get_characters_dir
from src.models.icon_pak import canonical_icon_path
from concurrent.futures import ThreadPoolExecutor as ThreadPool
import threading
import logging
from src.models.game_overlay import NullOverlaySession, SortOverlaySession
from src.models.loot import format_loot_state_label

logger = logging.getLogger(__name__)

PRIORITY_STASH_IDS: Tuple[str, ...] = ('3', '2')  # equipment first, then bag

class StashManager:
    def __init__(self, resource_dir: str, defer_loading=False):
        self.data_dir = get_characters_dir()
        self.output_dir = get_output_dir()
        # Only ensure data directory exists, not output directory
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.characters_cache = {}
        self._is_loaded = False
        self._cache_lock = threading.Lock()
        self.resource_dir = resource_dir
        
        # Performance tracking
        self.load_stats = {
            'last_load_time': None,
            'characters_loaded': 0,
            'files_processed': 0,
            'average_load_time_per_file': None
        }
        
        # Initialize preview generator (lightweight)
        self.preview_generator = StashPreviewGenerator(resource_dir=resource_dir)
        
        # Load data immediately unless deferred
        if not defer_loading:
            self._load_data()
            
    def force_reload(self):
        """Force reload of character data, ignoring the loaded flag"""
        with self._cache_lock:
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
        
        # Get all JSON files with cached stat results (single syscall per file)
        json_files = []
        for file_path in Path(self.data_dir).glob("*.json"):
            try:
                stat_result = file_path.stat()
                if stat_result.st_size > 10 * 1024 * 1024:  # > 10MB
                    logger.warning(f"Skipping oversized file: {file_path} ({stat_result.st_size/1024/1024:.2f} MB)")
                    continue
                json_files.append((file_path, stat_result))
            except OSError:
                continue
        
        # Sort by modification time (newest first) for better user experience
        json_files.sort(key=lambda entry: entry[1].st_mtime, reverse=True)
        
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
                    
                # Parse stashes efficiently
                raw_stashes = parse_stashes(packet_data)
                stashes = {str(k): v for k, v in raw_stashes.items()}
                
                # Extract character data
                raw_class = char_data.get("characterClass", "")
                class_name = raw_class.replace("DesignDataPlayerCharacter:Id_PlayerCharacter_", "")
                nickname_data = char_data.get("nickName", {})
                
                character_payload = {
                    'id': char_id,
                    'file_path': file_path,
                    'character_data': {
                        'id': char_id,
                        'nickname': nickname_data.get("originalNickName", "Unknown"),
                        'class': class_name,
                        'level': char_data.get("level", 1),
                        'lastUpdate': datetime.now().isoformat(),
                        'stashes': stashes,
                        'streamingModeName': nickname_data.get("streamingModeNickName", ""),
                        'rank': {
                            'name': nickname_data.get("rankId", "Unknown").replace("LeaderboardRankData:Id_LeaderboardRank_", "").replace("_", " "),
                            'fame': nickname_data.get("fame", 0),
                            'iconType': nickname_data.get("rankIconType", 1)
                        }
                    }
                }

                self._precompute_priority_stashes(character_payload['character_data'])
                return character_payload
            except Exception as e:
                logger.error(f"Error loading packet data file {file_path}: {str(e)}")
                return None
                
        logger.info(f"Found {len(json_files)} packet data files")

        # Optimize worker count based on file count and system capabilities
        cpu_count = os.cpu_count() or 4
        max_workers = max(1, min(cpu_count, len(json_files), 8))  # Cap at 8 workers
        
        # Use ThreadPoolExecutor directly — no asyncio overhead for sync file I/O
        loaded_count = 0
        file_paths = [str(fp) for fp, _ in json_files]
        
        with ThreadPool(max_workers=max_workers) as pool:
            for result in pool.map(load_file, file_paths):
                if result:
                    char_id = result['id']
                    self.characters_cache[char_id] = result['character_data']
                    loaded_count += 1
                    
                    # Log progress for large loads
                    if loaded_count % 10 == 0:
                        logger.info(f"Loaded {loaded_count}/{len(json_files)} characters...")
        
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
        with self._cache_lock:
            if loaded_count <= 3:
                for char_id, char_data in self.characters_cache.items():
                    logger.info(f"Character: {char_data['nickname']} ({char_data['class']}, Level {char_data['level']})")
            else:
                logger.info(f"Character details hidden for performance (loaded {loaded_count} characters)")

        # Check for corrections based on newly loaded data
        try:
            from src.models.sort_learning import get_sort_learning_manager
            learning_manager = get_sort_learning_manager()
            if learning_manager:
                all_items = []
                with self._cache_lock:
                    for char_data in self.characters_cache.values():
                        if not char_data:
                            continue
                        stashes = char_data.get("stashes", {})
                        for stash in stashes.values():
                            if hasattr(stash, "pq"):
                                 all_items.extend(stash.pq)
                            elif hasattr(stash, "items"): 
                                 all_items.extend(stash.items)
                
                if all_items:
                    learning_manager.check_corrections(all_items)
        except Exception as e:
            logger.warning("Failed to check for sort feedback corrections: %s", e)

        # Mark data as loaded
        self._is_loaded = True

    @staticmethod
    def _normalize_stash_id(value: Union[str, int, None]) -> Optional[str]:
        """Normalize incoming stash identifiers to their string form."""
        if value is None:
            return None
        try:
            normalized = str(value).strip()
        except Exception:
            return None
        if not normalized:
            return None
        if normalized.lower() == 'character':
            # Character view is a UI abstraction that maps to bag + equipment
            return 'character'
        return normalized

    @staticmethod
    def _compute_stash_signature(items: Iterable[Dict]) -> int:
        """Generate a lightweight hash representing stash contents."""
        signature = 0
        for item in items or []:
            if not isinstance(item, dict):
                continue
            slot_id = item.get('slotId') or 0
            count = item.get('itemCount') or 0
            data = item.get('data') or {}
            unique_id = data.get('itemUniqueId') or item.get('itemId') or slot_id
            signature = ((signature * 1315423911) ^ hash((unique_id, slot_id, count))) & 0xFFFFFFFFFFFFFFFF
        return signature

    @staticmethod
    def _get_stash_cache(char_data: Optional[Dict]) -> Optional[Dict[str, Dict]]:
        if char_data is None:
            return None
        cache = char_data.get('_stash_cache')
        if cache is None:
            cache = {}
            char_data['_stash_cache'] = cache
        return cache

    def _precompute_priority_stashes(
        self,
        char_data: Optional[Dict],
        stash_ids: Optional[Iterable[str]] = None,
    ) -> None:
        """Eagerly build cache entries for stashes the UI opens first."""
        if not char_data:
            return

        stashes = char_data.get('stashes') or {}
        if not isinstance(stashes, dict):
            return

        targets = [str(stash_id) for stash_id in (stash_ids or PRIORITY_STASH_IDS)]
        if not targets:
            return

        stash_cache = self._get_stash_cache(char_data)
        if stash_cache is None:
            return

        for stash_id in targets:
            items = stashes.get(stash_id)
            if not isinstance(items, list):
                continue

            signature = self._compute_stash_signature(items)
            cached_entry = stash_cache.get(stash_id)
            if cached_entry and cached_entry.get('signature') == signature:
                continue

            try:
                _, enhanced_items = self._process_stash_items(stash_id, items)
            except Exception as exc:
                logger.error("Failed to precompute stash %s for character %s: %s", stash_id, char_data.get('id'), exc)
                continue
            stash_cache[stash_id] = {
                'signature': signature,
                'items': enhanced_items,
                'timestamp': time.time(),
            }

    def _get_cache_info(self) -> Dict:
        """Return cache statistics, safe to call from any thread."""
        with self._cache_lock:
            chars = list(self.characters_cache.values())
        return {
            'characters_cached': len(chars),
            'total_stashes': sum(len(c.get('stashes', {})) for c in chars),
            'estimated_items': sum(
                sum(len(s) for s in c.get('stashes', {}).values() if isinstance(s, list))
                for c in chars
            ),
        }

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
            'cache_info': self._get_cache_info(),
            'system_info': {
                'cpu_count': os.cpu_count(),
                'python_version': sys.version
            }
        }

    def get_characters(self) -> List[Dict]:
        """Get list of all characters (full data including stash items)."""
        # Ensure data is loaded before returning characters
        if not self._is_loaded:
            self._load_data()

        with self._cache_lock:
            return list(self.characters_cache.values())

    def get_characters_summary(self) -> List[Dict]:
        """Return a lightweight list of characters for the index page.

        Only includes the fields the character list UI actually needs:
        id, nickname, class, level, lastUpdate, stash counts, rank.
        Excludes all item data so serialisation is near-instant.
        """
        if not self._is_loaded:
            self._load_data()

        with self._cache_lock:
            chars = list(self.characters_cache.values())

        summaries = []
        for char in chars:
            stashes = char.get('stashes', {})
            # Build stash count map (stashId -> item count)
            stash_counts = {}
            for sid, items in stashes.items():
                stash_counts[sid] = len(items) if isinstance(items, list) else 0

            summaries.append({
                'id': char.get('id'),
                'nickname': char.get('nickname'),
                'class': char.get('class'),
                'level': char.get('level'),
                'lastUpdate': char.get('lastUpdate'),
                'stashes': stash_counts,
                'rank': char.get('rank'),
                'streamingModeName': char.get('streamingModeName', ''),
            })
        return summaries

    def update_single_character(self, char_id: str, file_path: str = None) -> bool:
        """Incrementally reload a single character from disk into the cache.

        Much faster than force_reload() which re-reads every file.
        Returns True if the character was successfully loaded/updated.
        """
        if file_path is None:
            file_path = os.path.join(self.data_dir, f"{char_id}.json")

        if not os.path.isfile(file_path):
            logger.warning("update_single_character: file not found: %s", file_path)
            return False

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                packet_data = json.load(f)

            base = packet_data.get('characterDataBase', {})
            if not base:
                return False

            loaded_id = str(base.get('characterId', ''))
            if not loaded_id:
                return False

            raw_stashes = parse_stashes(packet_data)
            stashes = {str(k): v for k, v in raw_stashes.items()}

            raw_class = base.get('characterClass', '')
            class_name = raw_class.replace('DesignDataPlayerCharacter:Id_PlayerCharacter_', '')
            nickname_data = base.get('nickName', {})

            char_data = {
                'id': loaded_id,
                'nickname': nickname_data.get('originalNickName', 'Unknown'),
                'class': class_name,
                'level': base.get('level', 1),
                'lastUpdate': datetime.now().isoformat(),
                'stashes': stashes,
                'streamingModeName': nickname_data.get('streamingModeNickName', ''),
                'rank': {
                    'name': nickname_data.get('rankId', 'Unknown').replace('LeaderboardRankData:Id_LeaderboardRank_', '').replace('_', ' '),
                    'fame': nickname_data.get('fame', 0),
                    'iconType': nickname_data.get('rankIconType', 1),
                },
            }

            self._precompute_priority_stashes(char_data)
            with self._cache_lock:
                self.characters_cache[loaded_id] = char_data
            logger.info("Incrementally updated character %s (%s) in cache", loaded_id, char_data['nickname'])
            return True

        except Exception as exc:
            logger.error("update_single_character failed for %s: %s", char_id, exc, exc_info=True)
            return False

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
        loot_states: Optional[Set[int]] = None,
    ) -> Dict[str, List[Dict]]:
        """Aggregate how many of the specified items exist across all characters.

        Parameters
        ----------
        item_ids : Iterable[str]
            Item identifiers to look up.
        loot_states : set[int] | None
            Optional set of acceptable loot-state values.  When provided only
            stash entries whose loot state is in this set are counted, reducing
            the payload size for callers that would discard them anyway.
        """
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

                    # Skip items that don't match the desired loot state filter
                    if loot_states is not None and loot_state_value not in loot_states:
                        continue

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

                        # Single lookup instead of 3 separate calls
                        try:
                            item_meta = item_data_manager.get_item_data(item_id)
                        except Exception:
                            item_meta = {}

                        name = item_meta.get("name") or item_data_manager.format_design_id_as_name(item_id) or item_id or "Unknown Item"
                        rarity = item_meta.get("rarity") or "Unknown"
                        raw_icon_path = item_meta.get("iconPath")
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

    def _process_stash_items(self, stash_id, items):
        """Process items for a single stash to generate enhanced data"""
        enhanced_items = []
        try:
            for item in items:
                try:
                    design_str = item.get("itemId", "")
                    item_id = item_data_manager.get_item_id_from_design_str(design_str)
                    # Single lookup instead of 5 separate calls
                    item_meta = item_data_manager.get_item_data(item_id)
                    name = item_meta.get("name", "") or item_data_manager.format_design_id_as_name(item_id) or item_id
                    rarity = item_meta.get("rarity", 0)
                    width = item_meta.get("inventory_width", 1)
                    height = item_meta.get("inventory_height", 1)
                    raw_icon_path = item_meta.get("iconPath")
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
                    if raw_icon_path:
                        icon_canonical = canonical_icon_path(raw_icon_path)
                        if icon_canonical:
                            image_url = f"/assets/{icon_canonical}"
                    max_stack = item_meta.get("max_stack_size", 1)
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
                        'vendor_price': item_meta.get("vendor_price", 0),
                        'maxStackSize': max_stack,
                        'max_stack_size': max_stack,
                        'slot_type': item_meta.get("slot_type", ""),
                    }
                    if loot_state_value is not None:
                        enhanced_item['lootState'] = loot_state_value
                    if loot_state_label:
                        enhanced_item['lootStateLabel'] = loot_state_label
                    enhanced_items.append(enhanced_item)
                except Exception as e:
                    logger.error(f"Error enhancing item data: {str(e)}")
                    fallback_design = item.get("itemId", "")
                    fallback_id = item_data_manager.get_item_id_from_design_str(fallback_design) if fallback_design else ""
                    fallback_name = item_data_manager.format_design_id_as_name(fallback_id) or fallback_id or 'Unknown Item'
                    enhanced_items.append({
                        'name': fallback_name,
                        'itemId': fallback_id or item.get("itemId", "unknown"),
                        'slotId': item.get("slotId", 0),
                        'itemCount': item.get("itemCount", 1),
                        'rarity': 'Common',
                        'width': 1,
                        'height': 1
                    })
            return stash_id, enhanced_items
        except Exception as e:
            logger.error(f"Error processing stash {stash_id}: {str(e)}")
            return stash_id, []

    def get_character_stash_previews(
        self,
        character_id,
        stash_ids: Optional[Iterable[Union[str, int]]] = None,
    ):
        """Get detailed item data for character stashes.

        Optionally limit processing to the provided stash IDs to avoid
        regenerating every stash when the UI only needs a subset.
        """
        char_data = self.characters_cache.get(str(character_id))
        stashes = self.get_character_stashes(character_id)
        preview_paths = {str(stash_id): "/static/img/placeholder.png" for stash_id in stashes.keys()}
        stash_stats = {
            str(stash_id): {
                'itemCount': len(items) if isinstance(items, list) else 0,
            }
            for stash_id, items in stashes.items()
        }

        if not stashes:
            return {'previewImages': preview_paths, 'stashData': {}, 'stashStats': stash_stats}

        requested_ids: Optional[Set[str]] = None
        if stash_ids is not None:
            requested_ids = set()
            for raw_id in stash_ids:
                normalized = self._normalize_stash_id(raw_id)
                if not normalized:
                    continue
                if normalized == 'character':
                    requested_ids.update({'2', '3'})
                    continue
                requested_ids.add(normalized)
            if not requested_ids:
                requested_ids = None

        stash_queue: List[str] = []
        if requested_ids is None:
            stash_queue = list(stashes.keys())
        else:
            stash_queue = [stash_id for stash_id in stashes.keys() if stash_id in requested_ids]

        if not stash_queue:
            return {'previewImages': preview_paths, 'stashData': {}, 'stashStats': stash_stats}

        stash_cache = self._get_stash_cache(char_data)
        stash_data: Dict[str, List[Dict]] = {}

        tasks: List[Tuple[str, int, List[Dict]]] = []
        for stash_id in stash_queue:
            items = stashes.get(stash_id)
            if not isinstance(items, list):
                stash_data[stash_id] = []
                continue

            signature = self._compute_stash_signature(items)
            cached_entry = (stash_cache or {}).get(stash_id) if stash_cache else None
            if cached_entry and cached_entry.get('signature') == signature:
                stash_data[stash_id] = cached_entry.get('items', [])
                continue

            tasks.append((stash_id, signature, items))

        if len(tasks) == 1:
            stash_id, signature, items = tasks[0]
            _, enhanced_items = self._process_stash_items(stash_id, items)
            stash_data[stash_id] = enhanced_items
            if stash_cache is not None:
                stash_cache[stash_id] = {
                    'signature': signature,
                    'items': enhanced_items,
                    'timestamp': time.time(),
                }
        elif tasks:
            max_workers = min(len(tasks), os.cpu_count() or 4, 8)
            with ThreadPool(max_workers=max_workers) as pool:
                futures = []
                for stash_id, signature, items in tasks:
                    futures.append((stash_id, signature, pool.submit(self._process_stash_items, stash_id, items)))

                for stash_id, signature, future in futures:
                    try:
                        _, enhanced_items = future.result()
                    except Exception as exc:
                        logger.error(f"Error in stash processing future for stash {stash_id}: {exc}")
                        enhanced_items = []
                    stash_data[stash_id] = enhanced_items
                    if stash_cache is not None:
                        stash_cache[stash_id] = {
                            'signature': signature,
                            'items': enhanced_items,
                            'timestamp': time.time(),
                        }

        response = {
            'previewImages': preview_paths,
            'stashData': stash_data,
            'stashStats': stash_stats,
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
        include_inventory=False,
    ):
        logger.info(f"Sorting stash {stash_id} for character {character_id}")
        session_summary = None
        session: Union[SortOverlaySession, NullOverlaySession]
        session = overlay_session or NullOverlaySession()

        session.update_status("Validating inventory data...", status="info")
        char = self.characters_cache.get(str(character_id))
        if not char:
            session.update_status("Character not found in cache.", status="error")
            session.add_log("No packet data available for selected character.")
            logger.warning("Character %s not found in cache", character_id)
            return False, "Character not found", session_summary
        stash_items = char.get('stashes', {}).get(str(stash_id))
        if not stash_items:
            session.update_status("Selected stash is empty or missing.", status="error")
            session.add_log(f"Stash {stash_id} could not be found for this character.")
            logger.warning("Stash %s not found for character %s", stash_id, character_id)
            return False, "Stash not found", session_summary
        session.update_status("Loading character inventory...", status="info")
        file_path = os.path.join(self.data_dir, f"{character_id}.json")
        stashes = {}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            stashes = parse_stashes(raw)
            inv_items = stashes.get(StashType.BAG.value, [])
        except Exception as e:
            logger.error(f"Error loading inventory items: {str(e)}")
            inv_items = []
            session.add_log("Unable to read latest inventory snapshot; continuing with empty inventory.")

        # Filter out Supplied (loot state 1) items from inventory — the game
        # prevents moving them into the stash.
        if include_inventory and inv_items:
            original_count = len(inv_items)
            inv_items = [
                it for it in inv_items
                if not (isinstance(it, dict) and it.get("data", {}).get("lootState") == 1)
            ]
            supplied_skipped = original_count - len(inv_items)
            if supplied_skipped:
                session.add_log(
                    f"Excluded {supplied_skipped} Supplied item(s) from transfer (cannot be stashed)."
                )

        stash = Storage(int(stash_id), stash_items)
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

        # ── Auto-select the correct stash tab ──
        stash_type_int = int(stash_id)
        session.update_status("Selecting stash tab...", status="info")
        if macros.click_stash_tab(stash_type_int):
            tab_label = macros.STASH_TAB_LABELS[macros.STASH_TYPE_TO_TAB_INDEX[stash_type_int]]
            session.add_log(f"Selected stash tab: {tab_label}")

        # ── Cross-tab overflow: move excess items to another tab ──
        keep, overflow = self._identify_overflow_items(
            stash, pack_mode, stack_mode,
            all_stashes=stashes, source_stash_id=stash_type_int,
        )
        if overflow:
            session.add_log(
                f"Relocating {len(overflow)} item(s) to free workspace for sorting."
            )
            dest_id = self._find_overflow_destination(
                stash_type_int, overflow, stashes,
            )
            if dest_id is None:
                session.update_status(
                    "No destination stash has enough free space for overflow.",
                    status="error",
                )
                return False, "No overflow destination available", session_summary

            dest_label = macros.STASH_TAB_LABELS[macros.STASH_TYPE_TO_TAB_INDEX[dest_id]]
            session.add_log(f"Overflow destination: {dest_label}")

            dest_items = stashes.get(dest_id)
            if dest_items is None:
                dest_items = stashes.get(str(dest_id), [])

            ok = self._execute_overflow_transfer(
                source_stash=stash,
                dest_stash_id=dest_id,
                dest_stash_items=dest_items,
                overflow_items=overflow,
                inventory=inventory,
                cancel_event=cancel_event,
                session=session,
            )
            if not ok:
                session.update_status("Overflow transfer failed.", status="error")
                return False, "Overflow transfer failed", session_summary

            stash.rebuild_pq()
            # Inventory should be empty after a complete transfer.
            inventory = Storage(StashType.BAG.value, inv_items)

        sorter = StashSorter(
            stash,
            inventory,
            pack_mode=pack_mode,
            stack_mode=stack_mode,
            character_id=str(character_id),
            stash_id=int(stash_id) if stash_id is not None else None,
        )
        session.add_log(
            f"Pack mode: {'On' if sorter.pack_mode else 'Off'} · Stack mode: {'On' if sorter.stack_mode else 'Off'}"
        )

        # ── Transfer mode: mark inventory items for placement in stash ──
        if include_inventory and inventory and inventory.pq:
            transfer_count = sorter.mark_items_for_transfer(list(inventory.pq))
            if transfer_count:
                session.add_log(
                    f"Transfer mode: {transfer_count} inventory item(s) will be placed into the stash."
                )
            else:
                session.add_log("Transfer mode enabled but no inventory items to transfer.")

        if cancel_event and cancel_event.is_set():
            return False, "Sort cancelled", session_summary
        success = sorter.sort(cancel_event, overlay_session=session)
        if cancel_event and cancel_event.is_set():
            session_summary = sorter.get_feedback_summary()
            return False, "Sort cancelled", session_summary
        if success:
            session.update_status("Refreshing stash data...", status="success")
            self._generate_previews(character_id)
        session_summary = sorter.get_feedback_summary()
        return success, None, session_summary

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

    # ── Cross-tab overflow helpers ──────────────────────────────────────

    _RELIEF_THRESHOLD = 0.85  # Only relieve if >85% full
    _RELIEF_TARGET = 0.75     # Bring down to ~75% (60 free cells in 240-cell stash)

    _OVERFLOW_CANDIDATE_TYPES = {
        StashType.STORAGE.value,
        StashType.PURCHASED_STORAGE_0.value,
        StashType.PURCHASED_STORAGE_1.value,
        StashType.PURCHASED_STORAGE_2.value,
        StashType.PURCHASED_STORAGE_3.value,
        StashType.PURCHASED_STORAGE_4.value,
        StashType.SHARED_STASH_0.value,
        StashType.SHARED_STASH_SEASONAL_0.value,
    }

    def _identify_overflow_items(self, stash, pack_mode, stack_mode, all_stashes=None, source_stash_id=None):
        """Partition stash items into (keep, overflow).

        Performs trial layouts, removing the lowest-priority items one at a
        time until the layout succeeds.  When the stash is very full (above
        ``_RELIEF_THRESHOLD``) and other stashes have free space, proactively
        evacuates items to create sorting workspace.

        Returns ``(items_that_fit, overflow)``.
        """
        all_items = list(stash.pq)
        if not all_items:
            return [], []

        planner = LayoutPlanner(
            stash.width, stash.height,
            prefer_dense=pack_mode, stash=stash, stack_mode=stack_mode,
        )
        try:
            planner.build(all_items)
        except LayoutPlanError:
            # Items physically don't fit -- fall through to forced removal
            return self._remove_until_fits(stash, all_items, pack_mode, stack_mode)

        # ── Proactive workspace relief ──
        total_cells = stash.width * stash.height
        occupied = sum(itm.width * itm.height for itm in all_items)
        fill_ratio = occupied / total_cells if total_cells else 0

        if fill_ratio <= self._RELIEF_THRESHOLD:
            return all_items, []

        # Check if any destination stash has meaningful free space
        if not self._has_overflow_destination(all_stashes, source_stash_id):
            return all_items, []

        # Target: reduce to _RELIEF_TARGET fill
        target_occupied = int(total_cells * self._RELIEF_TARGET)
        cells_to_free = max(0, occupied - target_occupied)
        if cells_to_free <= 0:
            return all_items, []

        # Remove lowest-priority items first (smallest area, lowest rarity)
        candidates = sorted(all_items, key=lambda itm: (
            itm.width * itm.height,
            getattr(itm, 'rarity', 0) or 0,
            getattr(itm, 'vendor_price', 0) or 0,
        ))

        overflow: list = []
        freed = 0
        remaining = list(all_items)

        for candidate in candidates:
            if freed >= cells_to_free:
                break
            remaining.remove(candidate)
            overflow.append(candidate)
            freed += candidate.width * candidate.height

        if not overflow:
            return all_items, []

        # Verify the reduced set still produces a valid layout
        planner2 = LayoutPlanner(
            stash.width, stash.height,
            prefer_dense=pack_mode, stash=stash, stack_mode=stack_mode,
        )
        try:
            planner2.build(remaining)
            return remaining, overflow
        except LayoutPlanError:
            # Safety: don't overflow if reduced set somehow fails
            return all_items, []

    def _remove_until_fits(self, stash, all_items, pack_mode, stack_mode):
        """Remove lowest-priority items one at a time until layout succeeds."""
        candidates = sorted(all_items, key=lambda itm: (
            itm.width * itm.height,
            getattr(itm, 'rarity', 0) or 0,
            getattr(itm, 'vendor_price', 0) or 0,
        ))

        overflow: list = []
        remaining = list(all_items)

        for candidate in candidates:
            remaining.remove(candidate)
            overflow.append(candidate)
            planner = LayoutPlanner(
                stash.width, stash.height,
                prefer_dense=pack_mode, stash=stash, stack_mode=stack_mode,
            )
            try:
                planner.build(remaining)
                return remaining, overflow
            except LayoutPlanError:
                continue

        return [], all_items

    def _has_overflow_destination(self, all_stashes, source_stash_id):
        """Quick check: does any other stash have significant free space?"""
        if not all_stashes:
            return False
        for stash_type_val in self._OVERFLOW_CANDIDATE_TYPES:
            if stash_type_val == source_stash_id:
                continue
            items = all_stashes.get(stash_type_val)
            if items is None:
                items = all_stashes.get(str(stash_type_val))
            if items is None:
                continue
            try:
                dest = Storage(stash_type_val, items)
                if dest.count_free_cells() >= 20:
                    return True
            except Exception:
                continue
        return False

    def _find_overflow_destination(self, source_stash_id, overflow_items, all_stashes):
        """Pick the stash tab with the most free cells to receive overflow.

        Returns the ``stash_type`` int value, or ``None`` if nothing suitable.
        """
        overflow_cells = sum(itm.width * itm.height for itm in overflow_items)
        best_id = None
        best_free = 0

        for stash_type_val in self._OVERFLOW_CANDIDATE_TYPES:
            if stash_type_val == source_stash_id:
                continue
            items = all_stashes.get(stash_type_val)
            if items is None:
                items = all_stashes.get(str(stash_type_val))
            if items is None:
                items = []
            try:
                dest = Storage(stash_type_val, items)
                free = dest.count_free_cells()
            except Exception:
                continue
            if free >= overflow_cells and free > best_free:
                best_free = free
                best_id = stash_type_val

        return best_id

    def _execute_overflow_transfer(
        self,
        source_stash,
        dest_stash_id,
        dest_stash_items,
        overflow_items,
        inventory,
        cancel_event,
        session,
    ):
        """Move *overflow_items* from *source_stash* to another tab via the
        inventory bridge.

        Returns ``True`` on success.
        """
        source_stash_id = source_stash.stash_type
        dest_stash = Storage(dest_stash_id, dest_stash_items)
        inv_capacity = inventory.width * inventory.height

        # Split items into batches that fit in the inventory.
        batches: list[list] = []
        batch: list = []
        batch_cells = 0
        for item in overflow_items:
            cells = item.width * item.height
            if batch and batch_cells + cells > inv_capacity:
                batches.append(batch)
                batch = [item]
                batch_cells = cells
            else:
                batch.append(item)
                batch_cells += cells
        if batch:
            batches.append(batch)

        session.add_log(
            f"Transferring {len(overflow_items)} overflow item(s) in "
            f"{len(batches)} batch(es)."
        )

        for batch_idx, batch in enumerate(batches):
            if cancel_event and cancel_event.is_set():
                return False

            label = f"Overflow batch {batch_idx + 1}/{len(batches)}"

            # 1. Move items from source stash into inventory.
            session.update_status(f"{label}: moving to inventory...", status="info")
            for item in batch:
                if cancel_event and cancel_event.is_set():
                    return False
                inv_slot = inventory.find_empty_slot(item)
                if inv_slot is None:
                    session.add_log("Inventory full during overflow; aborting.")
                    return False
                source_stash.move(item, inv_slot, inventory)

            # 2. Click destination tab.
            session.update_status(f"{label}: switching to destination tab...", status="info")
            if not macros.click_stash_tab(dest_stash_id):
                session.add_log("Failed to click destination stash tab.")
                return False

            # 3. Move items from inventory into destination stash.
            session.update_status(f"{label}: placing in destination...", status="info")
            for item in batch:
                if cancel_event and cancel_event.is_set():
                    return False
                dest_slot = dest_stash.find_empty_slot(item)
                if dest_slot is None:
                    session.add_log("Destination stash full during overflow; aborting.")
                    macros.click_stash_tab(source_stash_id)
                    return False
                inventory.move(item, dest_slot, dest_stash)

            # 4. Click back to source tab.
            session.update_status(f"{label}: returning to source tab...", status="info")
            if not macros.click_stash_tab(source_stash_id):
                session.add_log("Failed to return to source stash tab.")
                return False

        session.add_log(f"Overflow complete: {len(overflow_items)} item(s) moved.")
        return True

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

    # ── Transfer feasibility & execution ─────────────────────────────

    def check_transfer_feasibility(
        self,
        character_id: str,
        source_stash_id: str,
        target_stash_id: str,
        item_indices: Optional[List[int]] = None,
        pack_mode: bool = False,
        stack_mode: bool = False,
    ) -> Dict:
        """Check whether items from *source_stash_id* can fit in *target_stash_id*.

        Uses the same ML-enhanced LayoutPlanner that powers the sort feature so
        that pack/stack preferences and learned placement scores are respected.

        Parameters
        ----------
        character_id : str
        source_stash_id : str
            Stash id whose items we want to move (e.g. ``"2"`` for bag).
        target_stash_id : str
            Destination stash id (e.g. ``"4"`` for Storage).
        item_indices : list[int] | None
            If provided, only consider items at these indices within the source
            stash list.  ``None`` means *all* items.
        pack_mode, stack_mode : bool
            Forwarded to the LayoutPlanner.

        Returns
        -------
        dict
            ``{"feasible": bool, "placeable": int, "unplaceable": int,
               "total": int, "target_free_cells": int, "target_total_cells": int,
               "items": [...], "message": str}``
        """
        from src.models.sort import LayoutPlanner, LayoutPlanError
        from src.models.sort_learning import get_sort_learning_manager
        from src.models.item import Item
        from src.models.point import Point

        char = self.characters_cache.get(str(character_id))
        if not char:
            return {"feasible": False, "message": "Character not found", "placeable": 0,
                    "unplaceable": 0, "total": 0, "target_free_cells": 0,
                    "target_total_cells": 0, "items": []}

        source_items_raw = char.get("stashes", {}).get(str(source_stash_id))
        target_items_raw = char.get("stashes", {}).get(str(target_stash_id))

        if source_items_raw is None:
            return {"feasible": False, "message": "Source stash empty",
                    "placeable": 0, "unplaceable": 0, "total": 0,
                    "target_free_cells": 0, "target_total_cells": 0, "items": []}
        if target_items_raw is None:
            return {"feasible": False, "message": "Target stash not found",
                    "placeable": 0, "unplaceable": 0, "total": 0,
                    "target_free_cells": 0, "target_total_cells": 0, "items": []}

        # Determine target grid dimensions
        target_sid = int(target_stash_id)
        if target_sid == StashType.BAG.value:
            grid_w, grid_h = 10, 5
        elif target_sid == StashType.EQUIPMENT.value:
            return {"feasible": False, "message": "Cannot transfer to equipment stash",
                    "placeable": 0, "unplaceable": 0, "total": 0,
                    "target_free_cells": 0, "target_total_cells": 0, "items": []}
        else:
            grid_w, grid_h = 12, 20

        # Build Item objects for existing target items so we can pre-mark occupancy
        target_storage = Storage(target_sid, target_items_raw if isinstance(target_items_raw, list) else [])
        total_cells = grid_w * grid_h
        occupied = sum(1 for x in range(grid_w) for y in range(grid_h) if target_storage.grid[x][y] != 0)
        free_cells = total_cells - occupied

        # Select source items to consider
        if not isinstance(source_items_raw, list):
            source_items_raw = []
        if item_indices is not None:
            selected_raw = [source_items_raw[i] for i in item_indices if 0 <= i < len(source_items_raw)]
        else:
            selected_raw = list(source_items_raw)

        # Filter out Supplied (loot state 1) items — the game prevents
        # moving them into the stash so they must be excluded from transfer.
        supplied_count = 0
        filtered_raw = []
        for raw in selected_raw:
            loot_val = raw.get("data", {}).get("lootState")
            try:
                loot_val = int(loot_val) if loot_val is not None else None
            except (TypeError, ValueError):
                loot_val = None
            if loot_val == 1:
                supplied_count += 1
            else:
                filtered_raw.append(raw)
        selected_raw = filtered_raw

        if not selected_raw:
            if supplied_count > 0:
                return {"feasible": True,
                        "message": f"No transferable items ({supplied_count} Supplied item(s) excluded).",
                        "placeable": 0, "unplaceable": 0, "total": 0,
                        "skipped_supplied": supplied_count,
                        "target_free_cells": free_cells, "target_total_cells": total_cells,
                        "items": []}
            return {"feasible": True, "message": "No items to transfer",
                    "placeable": 0, "unplaceable": 0, "total": 0,
                    "target_free_cells": free_cells, "target_total_cells": total_cells,
                    "items": []}

        # Convert raw items to lightweight Item objects for the planner
        source_items: List[Item] = []
        source_item_info: List[Dict] = []
        for raw in selected_raw:
            try:
                design_str = raw.get("itemId", "")
                iid = item_data_manager.get_item_id_from_design_str(design_str)
                meta = item_data_manager.get_item_data(iid)
                w = meta.get("inventory_width", 1) or 1
                h = meta.get("inventory_height", 1) or 1
                rarity = item_data_manager.rarity_to_id(meta.get("rarity", "Common"))
                if rarity is None:
                    rarity = 2  # Default to Common for items not in asset database
                name = meta.get("name") or item_data_manager.format_design_id_as_name(iid) or iid or "Unknown"
                quantity = raw.get("itemCount", 1)
                max_stack = meta.get("max_stack_size", 1) or 1
                itm = Item(iid, name, rarity, Point(0, 0), w, h, None,
                           vendor_price=meta.get("vendor_price", 0),
                           quantity=quantity, max_stack_size=max_stack)
                source_items.append(itm)
                source_item_info.append({"name": name, "width": w, "height": h,
                                         "rarity": meta.get("rarity", "Common"),
                                         "itemId": iid, "quantity": quantity})
            except Exception as exc:
                logger.debug("Skipping item during transfer feasibility: %s", exc)
                continue

        # Merge stackable items to get an accurate item count for the planner
        if source_items:
            source_items, source_item_info = self._merge_stackable_for_transfer(
                source_items, source_item_info, target_storage
            )

        # Combine existing target items + candidate source items and try layout
        combined_items: List[Item] = list(target_storage.pq) + source_items
        learning_mgr = get_sort_learning_manager()

        planner = LayoutPlanner(
            grid_w, grid_h,
            prefer_dense=pack_mode,
            stash=target_storage,
            stack_mode=stack_mode,
            learning_manager=learning_mgr,
        )

        placeable = 0
        unplaceable = 0
        item_results = []

        try:
            plan = planner.build(combined_items)
            # All items placed — the source items that were in the plan are placeable
            placeable = len(source_items)
            for info in source_item_info:
                item_results.append({**info, "placeable": True})
        except LayoutPlanError:
            # Not everything fits — try one-by-one to see which fit
            for idx, itm in enumerate(source_items):
                test_planner = LayoutPlanner(
                    grid_w, grid_h,
                    prefer_dense=pack_mode,
                    stash=target_storage,
                    stack_mode=stack_mode,
                    learning_manager=learning_mgr,
                )
                test_items = list(target_storage.pq) + [itm]
                try:
                    test_planner.build(test_items)
                    placeable += 1
                    item_results.append({**source_item_info[idx], "placeable": True})
                except LayoutPlanError:
                    unplaceable += 1
                    item_results.append({**source_item_info[idx], "placeable": False})

        total = placeable + unplaceable
        feasible = unplaceable == 0 and total > 0
        supplied_note = f" ({supplied_count} Supplied item(s) excluded)" if supplied_count else ""
        if feasible:
            msg = f"All {total} item(s) can be transferred.{supplied_note}"
        elif placeable > 0:
            msg = f"{placeable} of {total} item(s) can be placed. {unplaceable} won't fit.{supplied_note}"
        else:
            msg = f"No items can fit in the target stash.{supplied_note}"

        result = {
            "feasible": feasible,
            "placeable": placeable,
            "unplaceable": unplaceable,
            "total": total,
            "target_free_cells": free_cells,
            "target_total_cells": total_cells,
            "items": item_results,
            "message": msg,
        }
        if supplied_count:
            result["skipped_supplied"] = supplied_count
        return result

    @staticmethod
    def _merge_stackable_for_transfer(
        source_items: List,
        source_info: List[Dict],
        target_storage,
    ):
        """Merge stackable source items with each other and into existing
        target stash stacks so the feasibility check sees a realistic
        item count after stacking."""
        import math

        if not source_items:
            return source_items, source_info

        # ── Phase 1: merge source items with each other ─────────────
        grouped: Dict[tuple, List[int]] = {}  # key → list of indices
        for idx, item in enumerate(source_items):
            max_stack = getattr(item, "max_stack_size", 1) or 1
            if max_stack <= 1:
                continue
            key = (getattr(item, "item_id", None), item.rarity)
            grouped.setdefault(key, []).append(idx)

        keep_indices: set = set(range(len(source_items)))  # start with all

        for key, indices in grouped.items():
            if len(indices) <= 1:
                continue
            items_in_group = [source_items[i] for i in indices]
            max_stack = max(1, getattr(items_in_group[0], "max_stack_size", 1) or 1)
            total_qty = sum(max(1, getattr(itm, "quantity", 1)) for itm in items_in_group)
            required_stacks = min(len(indices), math.ceil(total_qty / max_stack))

            # Sort by descending quantity so the biggest stacks survive
            sorted_indices = sorted(indices, key=lambda i: getattr(source_items[i], "quantity", 1), reverse=True)
            kept = sorted_indices[:required_stacks]
            removed = sorted_indices[required_stacks:]

            remaining_qty = total_qty
            for i in kept:
                qty = min(max_stack, remaining_qty)
                source_items[i].quantity = qty
                remaining_qty -= qty

            for i in removed:
                keep_indices.discard(i)

        # ── Phase 2: merge remaining source items into target stash stacks
        stash_groups: Dict[tuple, list] = {}
        if target_storage:
            for stash_item in list(target_storage.pq):
                ms = getattr(stash_item, "max_stack_size", 1) or 1
                if ms <= 1:
                    continue
                cap = max(0, ms - min(ms, getattr(stash_item, "quantity", 1)))
                if cap <= 0:
                    continue
                key = (getattr(stash_item, "item_id", None), stash_item.rarity)
                stash_groups.setdefault(key, []).append((stash_item, cap))

        for idx in list(keep_indices):
            item = source_items[idx]
            ms = getattr(item, "max_stack_size", 1) or 1
            if ms <= 1:
                continue
            key = (getattr(item, "item_id", None), item.rarity)
            targets = stash_groups.get(key, [])
            inv_qty = max(1, getattr(item, "quantity", 1))
            for ti, (stash_item, cap) in enumerate(targets):
                if cap <= 0:
                    continue
                if inv_qty <= cap:
                    stash_item.quantity = min(ms, getattr(stash_item, "quantity", 1) + inv_qty)
                    targets[ti] = (stash_item, cap - inv_qty)
                    keep_indices.discard(idx)
                    break

        # ── Rebuild filtered lists ──────────────────────────────────
        merged_items = [source_items[i] for i in sorted(keep_indices)]
        merged_info = [source_info[i] for i in sorted(keep_indices)]
        return merged_items, merged_info