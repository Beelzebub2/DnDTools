#!/usr/bin/env python3
"""
Script to update items.json from DarkerDB API.
Run this script when game updates to fetch the latest item data.

Note: Update the API_URL if the actual endpoint is different.
Check https://darkerdb.com/documentation/items for the correct API endpoint.
"""

import io
import json
import logging
import os
import sys
from pathlib import Path

import requests
from PIL import Image

logger = logging.getLogger(__name__)

# API configuration
API_BASE_URL = "https://api.darkerdb.com/v1/items"
API_KEY = os.getenv("API_KEY")  # Use env var

# Resolve important paths relative to the UI root
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
ITEMS_FILE = BASE_DIR / "assets" / "items.json"

def update_items():
    """Fetch items data from API and update the local items.json file with new items only."""
    try:
        # Load existing items
        existing_items = {}
        if ITEMS_FILE.exists():
            with ITEMS_FILE.open('r', encoding='utf-8') as f:
                existing_items = json.load(f)

        logger.info("Loaded %s existing items", len(existing_items))
        logger.info("Fetching new items data from DarkerDB API...")

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "User-Agent": "DnDTools-Updater/1.0"
        }

        all_new_items = []
        next_url = f"{API_BASE_URL}?limit=50"
        page = 1
        max_pages = 100  # Safety limit

        while page <= max_pages:
            logger.info("Fetching page %s...", page)
            response = requests.get(next_url, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()
            items = data.get('body', [])
            
            if not items:
                logger.info("No more items found, stopping pagination")
                break
                
            all_new_items.extend(items)
            logger.info("  Got %s items", len(items))

            # Check for pagination - stop when 'next' field disappears
            pagination = data.get('pagination', {})
            if 'next' not in pagination:
                logger.info("No more pages available")
                break
                
            next_url = pagination['next']
            page += 1

        logger.info("Total items fetched from API: %s", len(all_new_items))

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
            icon_rel = f"icons/{item.get('type', 'Misc')}/{item_id}.webp"
            item_copy['iconPath'] = icon_rel

            new_items_dict[item_id] = item_copy

        logger.info("Skipped %s existing items", skipped_existing)
        logger.info("Adding %s new items", len(new_items_dict))

        if not new_items_dict:
            logger.info("No new items to add")
            return True

        # First update the JSON file with new items
        logger.info("Updating %s with %s total items...", ITEMS_FILE, len(existing_items) + len(new_items_dict))
        existing_items.update(new_items_dict)
        with ITEMS_FILE.open('w', encoding='utf-8') as f:
            json.dump(existing_items, f, indent=2, ensure_ascii=False)
        logger.info("Items data updated successfully!")

        # Then download images for new items
        logger.info("Downloading images for new items...")
        images_downloaded = 0
        for item_id, item_data in new_items_dict.items():
            # Use the item_id (which is now the API ID) for the icon URL
            icon_url = f"https://api.darkerdb.com/v1/items/{item_id}/icon"
            icon_path = Path(item_data['iconPath'])
            target_path = BASE_DIR / icon_path

            # Create directory if it doesn't exist
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            try:
                response = requests.get(icon_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    image = Image.open(io.BytesIO(response.content))
                    if image.mode not in {"RGBA", "RGB"}:
                        image = image.convert("RGBA")
                    image.save(target_path, format="WEBP", quality=90, method=6, lossless=False)
                    images_downloaded += 1
                else:
                    logger.warning("Failed to download image for %s: HTTP %s", item_id, response.status_code)
            except Exception as e:
                logger.error("Error downloading image for %s: %s", item_id, e)

        logger.info("Downloaded %s images", images_downloaded)
        return True

    except requests.exceptions.RequestException as e:
        logger.error("Error fetching data from API: %s", e)
        return False
    except json.JSONDecodeError as e:
        logger.error("Error parsing API response: %s", e)
        return False
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    masked_key = (API_KEY[:8] + "...") if API_KEY else "<missing>"
    logger.info("DarkerDB Items Updater")
    logger.info("%s", "=" * 30)
    logger.info("API URL: %s", API_BASE_URL)
    logger.info("Using API Key: %s", masked_key)
    logger.info("Target file: %s", ITEMS_FILE)
    logger.info("")

    success = update_items()
    if success:
        logger.info("\nUpdate completed successfully!")
        logger.info("You can now restart the application to use the updated items data.")
        sys.exit(0)
    else:
        logger.error("\nUpdate failed!")
        logger.error("Please check the API URL and try again.")
        sys.exit(1)