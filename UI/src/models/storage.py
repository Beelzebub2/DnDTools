import argparse
import heapq
import json
import logging
import sys
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.models import macros
from src.models.appdirs import get_characters_dir, resource_path
from src.models.game_data import item_data_manager
from src.models.item import Item
from src.models.point import Point
from src.models.stash_preview import parse_stashes

logger = logging.getLogger(__name__)

class StashType(Enum):
    NONE = 0
    CHEST = 1
    BAG = 2
    EQUIPMENT = 3
    STORAGE = 4
    PURCHASED_STORAGE_0 = 5
    PURCHASED_STORAGE_1 = 6
    PURCHASED_STORAGE_2 = 7
    PURCHASED_STORAGE_3 = 8
    PURCHASED_STORAGE_4 = 9
    SHARED_STASH_0 = 20
    SHARED_STASH_SEASONAL_0 = 30
    GEAR_SET_0 = 100
    GEAR_SET_1 = 101
    GEAR_SET_2 = 102
    MAX = 300

class RarityType(Enum):
    NONE_RARITY_TYPE = 0
    POOR = 1
    COMMON = 2
    UNCOMMON = 3
    RARE = 4
    EPIC = 5
    LEGEND = 6
    UNIQUE = 7
    ARTIFACT = 8

class Storage:
    def __init__(self, stash_type, data):
        self.stash_type = stash_type
        self.data = data

        # stardard or shared stash size 12x20
        if self.stash_type >= 4 and self.stash_type <= 30:
            self.width = 12
            self.height = 20
            self.base_screen_pos = macros.get_screen_positions()['stash']
        elif self.stash_type == StashType.BAG.value:
            self.base_screen_pos = macros.get_screen_positions()['inv']
            self.width = 10
            self.height = 5
        
        self.size = self.height * self.width
        self.grid = [[0 for _ in range(self.height)] for _ in range(self.width)]
        self.pq = []
        self._reserved_slots: set[tuple[int, int]] = set()
        self.load()
    
    def get_items(self):
        """
        Get items from the storage grid.
        Note: This method needs to be implemented to identify unique items in the grid
        and convert position to slotID and other necessary fields.
        For now, recapturing the stash packet is recommended.
        """
        # Implementation needed: identify unique items in grid then convert 
        # position to slotID and other necessary fields
        # Alternative: recapture the stash packet
        return []

    def move(self, item, end_pos, end_stash):
        logger.debug("Moving %s to %s", item, end_pos)

        original_stash = item.stash
        original_position = Point(item.position.x, item.position.y)

        if self is not end_stash:
            for dx in range(item.width):
                for dy in range(item.height):
                    self._reserved_slots.discard((item.position.x + dx, item.position.y + dy))
        if end_stash is not self:
            for dx in range(item.width):
                for dy in range(item.height):
                    end_stash._reserved_slots.discard((end_pos.x + dx, end_pos.y + dy))

        # Clear old location
        for dx in range(item.width):
            for dy in range(item.height):
                self.grid[item.position.x + dx][item.position.y + dy] = 0

        # Place item in new location
        for dx in range(item.width):
            for dy in range(item.height):
                end_stash.grid[end_pos.x + dx][end_pos.y + dy] = item
        
        # Update stash
        item.stash = end_stash
        try:
            macros.move_from_to_reliable(
                self,
                item.position,
                end_stash,
                end_pos,
                item.width,
                item.height,
                item.width,
                item.height,
            )
        except macros.MacroCancelled:
            # Revert grid changes so our internal state matches the game state.
            for dx in range(item.width):
                for dy in range(item.height):
                    end_stash.grid[end_pos.x + dx][end_pos.y + dy] = 0
            for dx in range(item.width):
                for dy in range(item.height):
                    self.grid[original_position.x + dx][original_position.y + dy] = item
            item.stash = original_stash
            item.position = original_position
            raise
        else:
            item.position = end_pos
        
    def find_empty_slot(self, item):
        for y in range(self.height - item.height, -1, -1):  # bottom to top
            for x in range(self.width - item.width, -1, -1):  # right to left
                fits = True
                for dx in range(item.width):
                    for dy in range(item.height):
                        slot = (x + dx, y + dy)
                        if slot in self._reserved_slots or self.grid[slot[0]][slot[1]] != 0:
                            fits = False
                            break
                    if not fits:
                        break
                if fits:
                    return Point(x, y)
        return None  # no valid position found

    def load(self):
        for obj in self.data:
            # Set items with no slotId to 0
            slot_id = obj.get("slotId")
            if slot_id is None:
                obj["slotId"] = 0
            try:
                design_str = obj.get("itemId", "")
                item_id = item_data_manager.get_item_id_from_design_str(design_str)
                width, height = item_data_manager.get_item_dimensions_from_id(item_id)
                rarity = item_data_manager.get_item_rarity_from_id(item_id)
                name = item_data_manager.get_item_name_from_id(item_id)
                item_meta = item_data_manager.get_item_data(item_id)

                slot_id = obj.get("slotId")
                x = slot_id % self.width
                y = slot_id // self.width

                position = Point(x, y)

                rarity_id = item_data_manager.rarity_to_id(rarity)

                if rarity_id is None:
                    # Item not in assets database (e.g. assets not yet updated).
                    # Fall back to Common (2) so the sort can still include it.
                    logger.warning(
                        "Unknown rarity for item %s (rarity=%r) – defaulting to Common",
                        item_id, rarity,
                    )
                    rarity_id = 2

                if not name:
                    name = item_data_manager.format_design_id_as_name(item_id) or item_id

                vendor_price = item_data_manager.get_item_vendor_price(item_id)
                quantity = obj.get("itemCount", 1)
                max_stack = item_data_manager.get_item_max_stack_size(item_id)

                slot_type = item_meta.get("slot_type", "")
                armor_type = item_meta.get("armor_type", "")

                item = Item(
                    item_id,
                    name,
                    rarity_id,
                    position,
                    width,
                    height,
                    self,
                    vendor_price=vendor_price,
                    quantity=quantity,
                    max_stack_size=max_stack,
                    slot_type=slot_type,
                    armor_type=armor_type,
                )

            except Exception as e:
                logger.error("Error creating item from data: %s", e)
                continue

            # Verify placement within bounds
            out_of_bounds = False
            for dx in range(item.width):
                for dy in range(item.height):
                    x = item.position.x + dx
                    y = item.position.y + dy
                    if not (0 <= x < self.width and 0 <= y < self.height):
                        logger.warning("Item %s position out of bounds at (%s, %s), skipping", item, x, y)
                        out_of_bounds = True
                        break
                if out_of_bounds:
                    break
            if out_of_bounds:
                continue
            # Place item and enqueue for sorting
            for dx in range(item.width):
                for dy in range(item.height):
                    self.grid[item.position.x + dx][item.position.y + dy] = item
            heapq.heappush(self.pq, item)
    
    def __repr__(self):
        # import os
        # import sys
        # import json
        # from pathlib import Path

        # # Determine the correct data directory (same logic as StashManager)
        # if globals().get('__compiled__', False):
        #     base_dir = os.getcwd()
        # else:
        #     base_dir = Path(__file__).parent.parent.parent
        # data_dir = os.path.join(base_dir, 'data')
        # characters = []

        # if os.path.exists(data_dir):
        #     for char_file in os.listdir(data_dir):
        #         if char_file.endswith('.json'):
        #             with open(os.path.join(data_dir, char_file), 'r', encoding='utf-8') as f:
        #                 char_data = json.load(f)
        #                 characters.append(char_data)

        # if not characters:
        #     return "No character data found. Please capture character data first."

        # Create the grid representation
        grid = [["." for _ in range(self.width)] for _ in range(self.height)]

        for x in range(self.width):
            for y in range(self.height):
                item = self.grid[x][y]
                if item != 0:
                    grid[y][x] = item.name[0].upper() if item.name else "#"

        # Create the display string
        lines = []
        # Add character information
        # for char in characters:
        #     lines.append(f"\nCharacter: {char.get('characterName', 'Unknown')}")
        #     lines.append(f"Class: {char.get('characterClass', 'Unknown')}")
        #     lines.append(f"Level: {char.get('level', 'Unknown')}")
        #     rank = char.get('rank', {}).get('name', 'Unknown') if isinstance(char.get('rank'), dict) else char.get('rank', 'Unknown')
        #     lines.append(f"Rank: {rank}")
        #     lines.append("-" * 40)

        # Add inventory grid
        lines.append("\nStorage:")
        for row in grid:
            lines.append(" ".join(row))

        return "\n".join(lines)


def _list_character_files() -> Tuple[Path, List[Path]]:
    data_dir = Path(get_characters_dir())
    if not data_dir.exists():
        raise FileNotFoundError(
            f"Character directory '{data_dir}' does not exist. Capture a character first."
        )
    files = sorted(
        (file for file in data_dir.glob('*.json') if file.is_file()),
        key=lambda file: file.stat().st_mtime,
        reverse=True,
    )
    return data_dir, files


def _select_character_file(selector: Optional[str]) -> Path:
    data_dir, files = _list_character_files()
    if not files:
        raise FileNotFoundError(
            f"No character capture files found in '{data_dir}'. Run the capture process first."
        )

    if not selector:
        return files[0]

    candidate = Path(selector)
    if candidate.is_file():
        return candidate

    normalized = candidate.stem or selector
    direct_match = data_dir / f"{normalized}.json"
    if direct_match.is_file():
        return direct_match

    for file in files:
        if file.stem == normalized or file.name == selector:
            return file

    raise FileNotFoundError(
        f"Unable to locate character capture '{selector}'. Available files: {[file.name for file in files[:5]]}"
    )


def _load_packet(path: Path) -> Dict:
    with path.open('r', encoding='utf-8') as handle:
        return json.load(handle)


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_numeric_stash_map(raw_stashes: Dict) -> Dict[int, List[Dict]]:
    numeric: Dict[int, List[Dict]] = {}
    for key, items in (raw_stashes or {}).items():
        if not isinstance(items, list):
            continue
        key_int = _to_int(key)
        if key_int is None:
            continue
        numeric[key_int] = items
    return numeric


def _ensure_supported_stash(stash_id: int) -> None:
    if stash_id == StashType.BAG.value:
        return
    if StashType.STORAGE.value <= stash_id <= StashType.SHARED_STASH_SEASONAL_0.value:
        return
    if StashType.PURCHASED_STORAGE_0.value <= stash_id <= StashType.PURCHASED_STORAGE_4.value:
        return
    raise ValueError(
        f"Stash id {stash_id} is not supported by the Storage grid renderer."
    )


def _resolve_stash_id(requested: Optional[str], stash_map: Dict[int, List[Dict]]) -> int:
    if not stash_map:
        raise ValueError("No stashes found in the selected capture file.")

    if requested is None:
        preferred = (
            StashType.STORAGE.value,
            StashType.SHARED_STASH_0.value,
            StashType.SHARED_STASH_SEASONAL_0.value,
            StashType.BAG.value,
        )
        for candidate in preferred:
            if candidate in stash_map:
                return candidate
        return sorted(stash_map.keys())[0]

    candidate = _to_int(requested)
    if candidate is None or candidate not in stash_map:
        raise ValueError(f"Stash id '{requested}' not found in capture data.")
    return candidate


def _stash_label(stash_id: int) -> str:
    try:
        return StashType(stash_id).name
    except ValueError:
        return f"ID_{stash_id}"


def _print_stash_listing(stash_map: Dict[int, List[Dict]]) -> None:
    print("Available stashes:")
    for stash_id in sorted(stash_map.keys()):
        label = _stash_label(stash_id)
        count = len(stash_map[stash_id])
        print(f"  {stash_id:<3} {label:<24} {count} items")


def _describe_character(packet_data: Dict) -> str:
    char_data = packet_data.get('characterDataBase') or {}
    nickname = (char_data.get('nickName') or {}).get('originalNickName') or 'Unknown'
    raw_class = char_data.get('characterClass') or ''
    class_name = raw_class.replace('DesignDataPlayerCharacter:Id_PlayerCharacter_', '') or 'Unknown'
    level = char_data.get('level') or '?'
    return f"{nickname} (Level {level} {class_name})"


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect captured stash data and render the Storage grid."
    )
    parser.add_argument(
        '-c', '--character',
        help="Character capture to load (path or characterId). Defaults to the newest capture.",
    )
    parser.add_argument(
        '-s', '--stash',
        help="Numeric stash id to render (e.g., 4 for STORAGE, 2 for BAG).",
    )
    parser.add_argument(
        '-l', '--list',
        action='store_true',
        help="List available stash ids in the capture and exit.",
    )

    args = parser.parse_args(argv)

    try:
        character_file = _select_character_file(args.character)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        packet_data = _load_packet(character_file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error reading capture '{character_file}': {exc}", file=sys.stderr)
        return 1

    raw_stashes = parse_stashes(packet_data)
    stash_map = _build_numeric_stash_map(raw_stashes)

    if not stash_map:
        print("No stash information found in the capture file.", file=sys.stderr)
        return 1

    if args.list:
        print(f"Character file: {character_file}")
        print(_describe_character(packet_data))
        _print_stash_listing(stash_map)
        return 0

    try:
        stash_id = _resolve_stash_id(args.stash, stash_map)
        _ensure_supported_stash(stash_id)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    storage = Storage(stash_id, stash_map[stash_id])
    print(f"Character file: {character_file}")
    print(_describe_character(packet_data))
    print(f"Rendering stash {stash_id} ({_stash_label(stash_id)}).\n")
    print(storage)
    return 0


if __name__ == '__main__':
    sys.exit(main())
