import json
import os
import re
import difflib
from PIL import Image, ImageDraw

ITEM_DATA_FILE = "item-data.json"
PACKET_DATA_FILE = "packet_data.json"
MATCHING_DB_FILE = "matchingdb.json"    # new
GRID_WIDTH, GRID_HEIGHT = 12, 20
CELL_SIZE = 45  # Most items use 45x45 px per cell

def load_json(filename):
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)

def normalize_name(name):
    """Lowercase, strip prefix, remove non-alphanumerics, keep all words."""
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r"designdataitem:|id_item_", "", name)
    name = re.sub(r"[^a-z0-9]", "", name)
    return name

def normalize_name_words(name):
    """Lowercase, strip prefix, split on non-chars, remove trivial words, sort remaining."""
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r"designdataitem:|id_item_", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    words = [w for w in name.split() if w not in {"item", "of", "the"}]
    return " " .join(sorted(words))

def get_item_image_path(item_name, item_data):
    # try manual override first
    try:
        matching_db = load_json(MATCHING_DB_FILE)
    except Exception:
        matching_db = {}
    if item_name in matching_db:
        item_name = matching_db[item_name]

    norm_name = normalize_name(item_name)
    norm_words = normalize_name_words(item_name)
    # exact and partial match as before
    for data in item_data.values():
        candidates = [data.get("name",""), data.get("matched_darkerdb_name","")]
        for cand in candidates:
            if normalize_name(cand)==norm_name or normalize_name_words(cand)==norm_words:
                return data["path"].replace("\\",os.sep), data["inventory_width"], data["inventory_height"], data["name"]
    for data in item_data.values():
        candidates = [data.get("name",""), data.get("matched_darkerdb_name","")]
        for cand in candidates:
            if norm_name in normalize_name(cand) or norm_words in normalize_name_words(cand):
                return data["path"].replace("\\",os.sep), data["inventory_width"], data["inventory_height"], data["name"]
    # fuzzy match fallback
    # build map of normalized -> data
    name_map = {}
    for data in item_data.values():
        for cand in (data.get("name",""), data.get("matched_darkerdb_name","")):
            key = normalize_name(cand)
            name_map[key] = data
    keys = list(name_map.keys())
    close = difflib.get_close_matches(norm_name, keys, n=1, cutoff=0.7)
    if close:
        data = name_map[close[0]]
        print(f"Fuzzy matched '{item_name}' → '{data['name']}'")
        return data["path"].replace("\\",os.sep), data["inventory_width"], data["inventory_height"], data["name"]
    return None, None, None, None

def get_item_name_from_id(item_id, item_data):
    # Extract base name from item_id
    if not item_id.startswith("DesignDataItem:Id_Item_"):
        return None
    base = item_id[len("DesignDataItem:Id_Item_"):]
    # Remove trailing _xxxx numbers
    base = re.sub(r'_\d+$', '', base)
    # Replace underscores with spaces
    base = base.replace("_", " ")
    return base

def parse_stashes(packet_data, item_data):
    stashes = {}
    storage_infos = packet_data.get("characterDataBase", {}).get("CharacterStorageInfos", [])
    for storage in storage_infos:
        inventory_id = storage.get("inventoryId")
        items = storage.get("CharacterStorageItemList", [])
        stash_items = []
        for item in items:
            item_id = item.get("itemId", "")
            slot_id = item.get("slotId")
            if slot_id is None:
                continue
            name = get_item_name_from_id(item_id, item_data)
            stash_items.append({
                "name": name,
                "slotId": slot_id,
                "itemId": item_id,
                "itemCount": item.get("itemCount", 1)
            })
        if stash_items:
            stashes[inventory_id] = stash_items
    return stashes

def slotid_to_xy(slot_id):
    return slot_id % GRID_WIDTH, slot_id // GRID_WIDTH

def draw_darkanddarker_grid(img, grid_w, grid_h, cell_size):
    draw = ImageDraw.Draw(img)
    # Fill background with dark color
    draw.rectangle([0, 0, img.width, img.height], fill=(24, 20, 16, 255))
    # Draw gold border
    border_color = (212, 175, 55, 255)  # gold
    border_width = 4
    draw.rectangle(
        [0, 0, img.width - 1, img.height - 1],
        outline=border_color,
        width=border_width
    )
    # Draw grid lines (subtle)
    grid_color = (60, 50, 30, 180)
    for x in range(1, grid_w):
        x_pos = x * cell_size
        draw.line([(x_pos, 0), (x_pos, img.height)], fill=grid_color, width=1)
    for y in range(1, grid_h):
        y_pos = y * cell_size
        draw.line([(0, y_pos), (img.width, y_pos)], fill=grid_color, width=1)
    # Optionally, add a slight vignette or shadow (not implemented for simplicity)

def main():
    item_data = load_json(ITEM_DATA_FILE)
    packet_data = load_json(PACKET_DATA_FILE)
    matching_db = {}  # collect original → matched names

    stashes = parse_stashes(packet_data, item_data)
    if not stashes:
        print("No stashes found in packet data.")
        return

    for stash_id, items in stashes.items():
        print(f"\nProcessing stash inventoryId={stash_id} with {len(items)} items...")
        preview = Image.new("RGBA", (GRID_WIDTH * CELL_SIZE, GRID_HEIGHT * CELL_SIZE), (0, 0, 0, 255))
        draw_darkanddarker_grid(preview, GRID_WIDTH, GRID_HEIGHT, CELL_SIZE)
        found_count = 0
        not_found_count = 0

        for idx, item in enumerate(items):
            img_path, w, h, matched_name = get_item_image_path(item["name"], item_data)
            if not img_path:
                print(f"[{idx+1}/{len(items)}] Item not found in item-data: '{item['name']}' (itemId: {item['itemId']})")
                not_found_count += 1
                continue
            if not os.path.exists(img_path):
                print(f"[{idx+1}/{len(items)}] Image file missing: {img_path} for item '{matched_name}'")
                not_found_count += 1
                continue

            try:
                item_img = Image.open(img_path).convert("RGBA")
            except Exception as e:
                print(f"[{idx+1}/{len(items)}] Failed to open image {img_path}: {e}")
                not_found_count += 1
                continue

            expected_w, expected_h = (w or 1) * CELL_SIZE, (h or 1) * CELL_SIZE
            if item_img.size != (expected_w, expected_h):
                item_img = item_img.resize((expected_w, expected_h), Image.LANCZOS)

            x, y = slotid_to_xy(item["slotId"])
            preview.paste(item_img, (x * CELL_SIZE, y * CELL_SIZE), item_img)
            print(f"[{idx+1}/{len(items)}] Placed '{matched_name}' at ({x},{y}) size {w}x{h}")
            found_count += 1
            # record the mapping
            matching_db[item["name"]] = matched_name

        outname = f"stash_preview_{stash_id}.png"
        preview.save(outname)
        print(f"Done. Items placed: {found_count}, not found/skipped: {not_found_count}")
        print(f"Preview saved as {outname}")

    # save the matching database
    with open("matchingdb.json", "w", encoding="utf-8") as f:
        json.dump(matching_db, f, indent=2)
    print("Matching DB saved as matchingdb.json")

if __name__ == "__main__":
    main()
