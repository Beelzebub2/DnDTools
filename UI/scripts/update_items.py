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
import hashlib
from pathlib import Path
from typing import Dict, Tuple

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
ICON_ENDPOINT_TEMPLATE = "https://api.darkerdb.com/v1/items/{item_id}/icon"
ICON_DOWNLOAD_TIMEOUT = 15


def normalize_icon_path(path_value: str) -> str:
    if not path_value:
        return path_value
    return path_value.replace('\\', '/')


def derive_icon_path(item_id: str, item_data: Dict) -> str:
    item_type = item_data.get('type') or 'Misc'
    safe_type = str(item_type).replace(' ', '_')
    return f"icons/{safe_type}/{item_id}.webp"


def compute_file_hash(path: Path) -> str:
    if not path.exists():
        return ''
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def compute_bytes_hash(payload: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def convert_icon_to_webp_bytes(raw_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(raw_bytes))
    if image.mode not in {"RGBA", "RGB"}:
        image = image.convert("RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=90, method=6, lossless=False)
    return buffer.getvalue()


def update_icon_metadata(record: Dict, content_hash: str, response_headers: Dict) -> bool:
    updated = False
    if record.get('iconHash') != content_hash:
        record['iconHash'] = content_hash
        updated = True
    etag = response_headers.get('ETag') or response_headers.get('etag')
    if etag:
        etag = etag.strip('"')
    if etag and record.get('iconETag') != etag:
        record['iconETag'] = etag
        updated = True
    last_modified = response_headers.get('Last-Modified')
    if last_modified and record.get('iconLastModified') != last_modified:
        record['iconLastModified'] = last_modified
        updated = True
    return updated


def refresh_icons(existing_items: Dict, fetched_items: Dict[str, Dict], headers: Dict) -> Tuple[int, int, int, int]:
    """Ensure local icon files match the latest remote content.

    Returns a tuple with counts: (updated_files, not_modified, hash_matches, metadata_updates).
    """

    updated_files = 0
    not_modified = 0
    hash_matches = 0
    metadata_updates = 0

    for item_id, api_item in fetched_items.items():
        record = existing_items.get(item_id)
        if not record:
            continue

        icon_path = normalize_icon_path(record.get('iconPath') or derive_icon_path(item_id, api_item))
        record['iconPath'] = icon_path
        target_path = BASE_DIR / icon_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        file_missing = not target_path.exists()

        if not record.get('iconHash') and target_path.exists():
            record['iconHash'] = compute_file_hash(target_path)
            if record['iconHash']:
                metadata_updates += 1

        request_headers = headers.copy()
        if record.get('iconETag'):
            request_headers['If-None-Match'] = record['iconETag']
        if record.get('iconLastModified'):
            request_headers.setdefault('If-Modified-Since', record['iconLastModified'])
        if file_missing:
            request_headers.pop('If-None-Match', None)
            request_headers.pop('If-Modified-Since', None)

        icon_url = ICON_ENDPOINT_TEMPLATE.format(item_id=item_id)
        try:
            response = requests.get(icon_url, headers=request_headers, timeout=ICON_DOWNLOAD_TIMEOUT)
        except requests.RequestException as exc:
            logger.error("Failed to fetch icon for %s: %s", item_id, exc)
            continue

        if response.status_code == 304:
            not_modified += 1
            continue

        if response.status_code != 200:
            logger.warning("Unexpected status %s downloading icon for %s", response.status_code, item_id)
            continue

        try:
            processed_bytes = convert_icon_to_webp_bytes(response.content)
        except Exception as exc:
            logger.error("Failed to convert icon for %s: %s", item_id, exc)
            continue

        new_hash = compute_bytes_hash(processed_bytes)
        existing_hash = compute_file_hash(target_path)
        if existing_hash and existing_hash == new_hash:
            if update_icon_metadata(record, new_hash, response.headers):
                metadata_updates += 1
            hash_matches += 1
            continue

        try:
            with target_path.open('wb') as icon_file:
                icon_file.write(processed_bytes)
        except OSError as exc:
            logger.error("Failed to write icon for %s: %s", item_id, exc)
            continue

        if update_icon_metadata(record, new_hash, response.headers):
            metadata_updates += 1
        updated_files += 1

    return updated_files, not_modified, hash_matches, metadata_updates

def update_items():
    """Fetch item metadata and ensure icon assets stay in sync using hash checks."""
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

        fetched_items_map = {}
        new_items_dict = {}
        skipped_existing = 0

        for item in all_new_items:
            if not item.get('name'):
                continue

            # Use the actual ID from the API response
            item_id = item.get('id')
            if not item_id:
                continue

            fetched_items_map[item_id] = item

            # Skip if already exists
            if item_id in existing_items:
                skipped_existing += 1
                continue

            # Add iconPath based on the API pattern
            item_copy = item.copy()
            icon_rel = derive_icon_path(item_id, item)
            item_copy['iconPath'] = icon_rel

            new_items_dict[item_id] = item_copy

        logger.info("Skipped %s existing items", skipped_existing)
        if new_items_dict:
            logger.info("Adding %s new items", len(new_items_dict))
        else:
            logger.info("No new item metadata detected; verifying existing icon assets")

        existing_items.update(new_items_dict)

        icon_stats = refresh_icons(existing_items, fetched_items_map, headers)
        icons_updated, icons_not_modified, hash_matches, metadata_updates = icon_stats
        logger.info(
            "Icon sync summary: %s updated, %s not-modified (server), %s skipped via hash match, %s metadata refreshes",
            icons_updated,
            icons_not_modified,
            hash_matches,
            metadata_updates,
        )

        should_write = bool(new_items_dict) or icons_updated > 0 or metadata_updates > 0
        if should_write:
            logger.info("Writing %s items to %s", len(existing_items), ITEMS_FILE)
            with ITEMS_FILE.open('w', encoding='utf-8') as f:
                json.dump(existing_items, f, indent=2, ensure_ascii=False)
            logger.info("Items data saved successfully")
        else:
            logger.info("No changes detected in item catalog; skipping write")

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