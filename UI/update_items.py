#!/usr/bin/env python3
"""
Script to update items.json from DarkerDB API.
Run this script when game updates to fetch the latest item data.

Note: Update the API_URL if the actual endpoint is different.
Check https://darkerdb.com/documentation/items for the correct API endpoint.
"""

import requests
import json
import os
import sys

# API configuration
API_BASE_URL = "https://api.darkerdb.com/v1/items"
API_KEY = os.getenv("API_KEY")  # Use env var, fallback to default

# Path to the items.json file
ITEMS_FILE = os.path.join(os.path.dirname(__file__), "assets", "items.json")

def update_items():
    """Fetch items data from API and update the local items.json file with new items only."""
    try:
        # Load existing items
        existing_items = {}
        if os.path.exists(ITEMS_FILE):
            with open(ITEMS_FILE, 'r', encoding='utf-8') as f:
                existing_items = json.load(f)
        
        print(f"Loaded {len(existing_items)} existing items")
        print("Fetching new items data from DarkerDB API...")
        
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "User-Agent": "DnDTools-Updater/1.0"
        }

        all_new_items = []
        next_url = f"{API_BASE_URL}?limit=50"
        page = 1
        max_pages = 100  # Safety limit

        while page <= max_pages:
            print(f"Fetching page {page}...")
            response = requests.get(next_url, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()
            items = data.get('body', [])
            
            if not items:
                print("No more items found, stopping pagination")
                break
                
            all_new_items.extend(items)
            print(f"  Got {len(items)} items")

            # Check for pagination - stop when 'next' field disappears
            pagination = data.get('pagination', {})
            if 'next' not in pagination:
                print("No more pages available")
                break
                
            next_url = pagination['next']
            page += 1

        print(f"Total items fetched from API: {len(all_new_items)}")

        # Filter to only new items
        new_items_dict = {}
        skipped_existing = 0
        
        for item in all_new_items:
            if not item.get('name'):
                continue

            # Use the actual ID from the API response
            item_id = item.get('id')
            if not item_id:
                continue

            # Skip if already exists
            if item_id in existing_items:
                skipped_existing += 1
                continue

            # Add iconPath based on the API pattern
            item_copy = item.copy()
            item_copy['iconPath'] = f"assets\\icons\\{item.get('type', 'Misc')}\\{item_id}.png"

            new_items_dict[item_id] = item_copy

        print(f"Skipped {skipped_existing} existing items")
        print(f"Adding {len(new_items_dict)} new items")

        if not new_items_dict:
            print("No new items to add")
            return True

        # First update the JSON file with new items
        print(f"Updating {ITEMS_FILE} with {len(existing_items) + len(new_items_dict)} total items...")
        existing_items.update(new_items_dict)
        with open(ITEMS_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing_items, f, indent=2, ensure_ascii=False)
        print("Items data updated successfully!")

        # Then download images for new items
        print("Downloading images for new items...")
        images_downloaded = 0
        for item_id, item_data in new_items_dict.items():
            # Use the item_id (which is now the API ID) for the icon URL
            icon_url = f"https://api.darkerdb.com/v1/items/{item_id}/icon"
            icon_path = item_data['iconPath']
            
            # Create directory if it doesn't exist
            icon_dir = os.path.dirname(icon_path)
            if not os.path.exists(icon_dir):
                os.makedirs(icon_dir, exist_ok=True)
            
            try:
                response = requests.get(icon_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    with open(icon_path, 'wb') as f:
                        f.write(response.content)
                    images_downloaded += 1
                else:
                    print(f"Failed to download image for {item_id}: HTTP {response.status_code}")
            except Exception as e:
                print(f"Error downloading image for {item_id}: {e}")

        print(f"Downloaded {images_downloaded} images")
        return True

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from API: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"Error parsing API response: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

if __name__ == "__main__":
    print("DarkerDB Items Updater")
    print("=" * 30)
    print(f"API URL: {API_BASE_URL}")
    print(f"Using API Key: {API_KEY[:8]}...")  # Show first 8 chars for security
    print(f"Target file: {ITEMS_FILE}")
    print()

    success = update_items()
    if success:
        print("\nUpdate completed successfully!")
        print("You can now restart the application to use the updated items data.")
        sys.exit(0)
    else:
        print("\nUpdate failed!")
        print("Please check the API URL and try again.")
        sys.exit(1)