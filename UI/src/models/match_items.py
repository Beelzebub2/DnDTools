import json
import os
from datetime import datetime
import glob

def load_json(filepath: str) -> dict:
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_latest_unmatched_file(data_dir: str) -> str:
    files = glob.glob(os.path.join(data_dir, "unmatched_items_*.txt"))
    if not files:
        raise FileNotFoundError("No unmatched items files found")
    return max(files)

def main():
    # Setup paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(base_dir, "assets")
    
    # Load databases
    item_data = load_json(os.path.join(assets_dir, "item-data.json"))
    matching_db_path = os.path.join(assets_dir, "matchingdb.json")
    matching_db = load_json(matching_db_path) if os.path.exists(matching_db_path) else {}

    # Get available items
    available_items = sorted({data.get("name") for data in item_data.values() if "name" in data})
    print("\nAvailable items:")
    for i, name in enumerate(available_items, 1):
        print(f"{i:3d}. {name}")

    try:
        # Load latest unmatched items
        unmatched_file = get_latest_unmatched_file(assets_dir)
        with open(unmatched_file, 'r', encoding='utf-8') as f:
            unmatched = [line.strip() for line in f if line.strip()]
            
        print(f"\nFound {len(unmatched)} unmatched items in {os.path.basename(unmatched_file)}")
        
        # Process each unmatched item
        for item in unmatched:
            if item in matching_db:
                print(f"\nItem '{item}' already matched to '{matching_db[item]}'")
                continue
                
            print(f"\nMatching item: {item}")
            while True:
                match = input("Enter number or name from available items (or 's' to skip): ").strip()
                
                if match.lower() == 's':
                    break
                    
                if match.isdigit() and 1 <= int(match) <= len(available_items):
                    match = available_items[int(match) - 1]
                    
                if match in available_items:
                    matching_db[item] = match
                    print(f"Added: {item} → {match}")
                    break
                else:
                    print("Invalid selection. Try again or enter 's' to skip.")

        # Save updated matching database
        with open(matching_db_path, 'w', encoding='utf-8') as f:
            json.dump(matching_db, f, indent=2, sort_keys=True)
        print(f"\nSaved {len(matching_db)} entries to matchingdb.json")

    except FileNotFoundError as e:
        print(f"Error: {e}")
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        if matching_db:
            with open(matching_db_path, 'w', encoding='utf-8') as f:
                json.dump(matching_db, f, indent=2, sort_keys=True)
            print("Saved current progress to matchingdb.json")

if __name__ == "__main__":
    main()
