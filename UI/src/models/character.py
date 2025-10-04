import os
import json
import copy
from datetime import datetime
from pathlib import Path
from google.protobuf.json_format import MessageToJson
from networking.protos import _Defins_pb2
from .appdirs import get_data_dir
import logging
logger = logging.getLogger(__name__)

def policy(message):
    for policy in message.policyList:
        name = _Defins_pb2.Operate.Policy.Name(policy.policyType)
        print(f"{name or 'UnknownPolicy'}: {getattr(policy, 'policyValue', 'N/A')}")

SHARED_STASH_IDS = {20, 30}
SHARED_STASH_ID_STRINGS = {str(stash_id) for stash_id in SHARED_STASH_IDS}

data_dir = get_data_dir()
os.makedirs(data_dir, exist_ok=True)


def _normalize_inventory_id(value):
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return None


def _extract_shared_stash_payload(payload):
    shared_storages = {}
    shared_item_lists = {}

    if not isinstance(payload, dict):
        return shared_storages, shared_item_lists

    base = payload.get("characterDataBase")
    if not isinstance(base, dict):
        return shared_storages, shared_item_lists

    storages = base.get("CharacterStorageInfos") or []
    if isinstance(storages, list):
        for storage in storages:
            if not isinstance(storage, dict):
                continue
            inv_key = _normalize_inventory_id(storage.get("inventoryId"))
            if inv_key in SHARED_STASH_ID_STRINGS:
                shared_storages[inv_key] = copy.deepcopy(storage)

    for list_name in ("CharacterItemList", "CharacterStorageItemList"):
        items = base.get(list_name) or []
        if not isinstance(items, list):
            continue

        grouped = {}
        has_shared_items = False

        for item in items:
            if not isinstance(item, dict):
                continue
            inv_key = _normalize_inventory_id(item.get("inventoryId"))
            if inv_key in SHARED_STASH_ID_STRINGS:
                grouped.setdefault(inv_key, [])
                grouped[inv_key].append(copy.deepcopy(item))
                has_shared_items = True

        if shared_storages:
            for key in shared_storages.keys():
                grouped.setdefault(key, [])

        if has_shared_items or shared_storages:
            shared_item_lists[list_name] = grouped

    return shared_storages, shared_item_lists


def _apply_shared_stash_to_payload(payload, shared_storages, shared_item_lists):
    if not isinstance(payload, dict):
        return False

    base = payload.get("characterDataBase")
    if not isinstance(base, dict):
        return False

    changed = False

    if shared_storages:
        storages = base.get("CharacterStorageInfos")
        storages_list = list(storages) if isinstance(storages, list) else []
        index_map = {}
        for idx, storage in enumerate(storages_list):
            if not isinstance(storage, dict):
                continue
            inv_key = _normalize_inventory_id(storage.get("inventoryId"))
            if inv_key:
                index_map[inv_key] = idx

        for inv_key, storage_data in shared_storages.items():
            if inv_key in index_map:
                if storages_list[index_map[inv_key]] != storage_data:
                    storages_list[index_map[inv_key]] = copy.deepcopy(storage_data)
                    changed = True
            else:
                storages_list.append(copy.deepcopy(storage_data))
                changed = True

        if storages_list != storages:
            base["CharacterStorageInfos"] = storages_list
            changed = True

    for list_name, stash_map in shared_item_lists.items():
        items = base.get(list_name)
        items_list = list(items) if isinstance(items, list) else []

        filtered = []
        for item in items_list:
            inv_key = _normalize_inventory_id(item.get("inventoryId"))
            if inv_key in stash_map:
                continue
            filtered.append(item)

        for stash_id, new_items in stash_map.items():
            filtered.extend(copy.deepcopy(new_items))

        if filtered != items_list:
            base[list_name] = filtered
            changed = True

    return changed


def _sync_shared_stashes(source_char_id, payload):
    shared_storages, shared_item_lists = _extract_shared_stash_payload(payload)
    if not shared_storages and not shared_item_lists:
        return

    data_path = Path(data_dir)
    for candidate in data_path.glob("*.json"):
        if candidate.stem == str(source_char_id):
            continue

        try:
            with candidate.open("r", encoding="utf-8") as handle:
                other_payload = json.load(handle)
        except Exception as exc:
            logger.warning(f"Skipping shared stash sync for {candidate}: {exc}")
            continue

        if _apply_shared_stash_to_payload(other_payload, shared_storages, shared_item_lists):
            try:
                with candidate.open("w", encoding="utf-8") as handle:
                    json.dump(other_payload, handle, ensure_ascii=False)
            except Exception as exc:
                logger.error(f"Failed to write shared stash sync for {candidate}: {exc}")


def save_packet_data(message) -> bool:
    try:

        json_data = MessageToJson(message)
        # Overwrite file if characterId matches (no date in filename)
        if '"result": 1' in json_data and '"characterDataBase": {' in json_data:
            char_data = message.characterDataBase
            char_id = str(char_data.characterId)
            data_file = os.path.join(data_dir, f"{char_id}.json")
            with open(data_file, "w", encoding='utf-8') as f:
                f.write(json_data)
            try:
                payload = json.loads(json_data)
            except json.JSONDecodeError as exc:
                logger.warning(f"Failed to parse character JSON for shared stash sync (characterId={char_id}): {exc}")
            else:
                _sync_shared_stashes(char_id, payload)
            logger.info(f"Saved/updated target packet data to {data_file} (characterId={char_id})")
            return True

        # Save other packets to timestamped files as before
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        data_file = os.path.join(data_dir, f"{timestamp}.json")
        with open(data_file, "w", encoding='utf-8') as f:
            f.write(json_data)
        logger.info(f"Successfully saved packet data to {data_file}")
        return False

    except Exception as e:
        logger.error(f"Failed to save packet data: {str(e)}")
        raise