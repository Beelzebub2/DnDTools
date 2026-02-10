#!/usr/bin/env python3
"""
CLI script to sync items and icons from the DarkerDB API.

This reuses the same core logic as the runtime ``AssetUpdater`` but is meant to
be run manually (e.g. after a game patch) to update the checked-in assets.

Usage:
    python update_items.py                    # incremental update
    python update_items.py --full             # force full re-fetch
    python update_items.py --icons-only       # only re-sync icons for existing items
    python update_items.py --skip-icons       # only fetch item metadata, skip icons
    python update_items.py --skip-pak         # skip rebuilding icons.pak

Environment:
    API_KEY   – DarkerDB API key (passed as ``?key=…``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Resolve paths relative to the UI root (one level up from scripts/)
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
ASSETS_DIR = BASE_DIR / "assets"

# Make sure we can import from UI/utils
sys.path.insert(0, str(BASE_DIR))

from utils.asset_updater import (
    AssetUpdater,
    _api_params,
    _icon_rel_path,
    _read_json,
    _write_json,
    DARKERDB_ITEMS_URL,
    ITEM_TIMEOUT,
    PAGE_LIMIT,
    MAX_PAGES,
)

logger = logging.getLogger("update_items")


def _render_progress(current: int, total: int, prefix: str = "") -> None:
    if not total:
        return
    bar_length = 30
    filled = int(bar_length * current / total)
    bar = "#" * filled + "-" * (bar_length - filled)
    pct = int(100 * current / total)
    sys.stdout.write(f"\r{prefix}[{bar}] {pct:3d}% ({current}/{total})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update items and icons from DarkerDB API.")
    parser.add_argument("--full", action="store_true", help="Force a full re-fetch (ignore cached state).")
    parser.add_argument("--icons-only", action="store_true", help="Only re-sync icons for existing items.")
    parser.add_argument("--skip-icons", action="store_true", help="Fetch item metadata only, skip icon downloads.")
    parser.add_argument("--skip-pak", action="store_true", help="Skip rebuilding icons.pak after icon sync.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    api_key = os.getenv("API_KEY", "")
    logger.info("DarkerDB Items Updater (v2)")
    logger.info("=" * 40)
    logger.info("Assets dir : %s", ASSETS_DIR)
    logger.info("API key    : %s", (api_key[:8] + "…") if api_key else "<not set>")
    logger.info("Mode       : %s", "FULL" if args.full else "incremental")
    logger.info("")

    updater = AssetUpdater(
        assets_dir=ASSETS_DIR,
        logger=logger,
    )

    # Step 1 — probe patch
    remote_patch, remote_build = updater._probe_patch()
    if remote_patch is None:
        logger.error("Could not reach DarkerDB API — aborting.")
        return 1

    state = _read_json(updater._state_path) or {}
    local_patch = state.get("patch")
    local_build = state.get("build")
    local_max_cursor = state.get("max_cursor", 0)

    patch_changed = (remote_patch != local_patch) or (remote_build != local_build)
    logger.info("Remote: patch=%s  build=%s", remote_patch, remote_build)
    logger.info("Local : patch=%s  build=%s  cursor=%s", local_patch, local_build, local_max_cursor)
    logger.info("Patch changed: %s", patch_changed)
    logger.info("")

    do_full = args.full or patch_changed

    # Step 2 — fetch items (unless --icons-only)
    if args.icons_only:
        logger.info("--icons-only: skipping item fetch")
        items_dict: dict = {}
        new_max_cursor = local_max_cursor
    elif do_full:
        logger.info("Full item fetch…")
        items_dict, new_max_cursor = updater._fetch_all_items()
    else:
        logger.info("Incremental item fetch (cursor > %d)…", local_max_cursor)
        items_dict, new_max_cursor = updater._fetch_new_items(local_max_cursor)

    # Merge
    existing_items: dict = {}
    if updater._items_path.exists():
        try:
            existing_items = json.loads(updater._items_path.read_text("utf-8"))
        except (ValueError, OSError):
            existing_items = {}

    if do_full and not args.icons_only:
        # Sanity check: if API returned far fewer items than we already have,
        # something went wrong (partial fetch).  Merge instead of replacing.
        if len(items_dict) >= len(existing_items) * 0.5 or not existing_items:
            merged = items_dict
            logger.info(
                "Full replace: %d API items replacing %d local items",
                len(items_dict), len(existing_items),
            )
        else:
            merged = {**existing_items, **items_dict}
            logger.warning(
                "Partial fetch detected (%d API vs %d local) — merging instead of replacing",
                len(items_dict), len(existing_items),
            )
    else:
        merged = {**existing_items, **items_dict}

    final_max_cursor = max(new_max_cursor, local_max_cursor)
    logger.info("Items: %d total (%d new)", len(merged), len(items_dict))

    # Step 3 — sync icons
    icon_hashes = _read_json(updater._icon_hashes_path) or {}
    icons_updated = 0
    icons_skipped = 0

    if not args.skip_icons:
        target = merged if (do_full or args.icons_only) else items_dict
        logger.info("Syncing icons for %d items…", len(target))
        icons_updated, icons_skipped = updater._sync_icons(
            target, icon_hashes, force=do_full,
        )
        logger.info("Icons: %d updated, %d skipped (SHA match)", icons_updated, icons_skipped)
    else:
        logger.info("--skip-icons: skipping icon downloads")

    # Step 4 — rebuild icons.pak
    if not args.skip_pak and (icons_updated > 0 or do_full):
        logger.info("Rebuilding icons.pak…")
        updater._run_pre_hooks("icons.pak")
        updater._build_icons_pak()
    elif args.skip_pak:
        logger.info("--skip-pak: skipping icons.pak rebuild")
    else:
        logger.info("No icon changes — icons.pak left as-is")

    # Step 5 — persist
    clean = updater._clean_items_for_disk(merged)
    _write_json(updater._items_path, clean)
    _write_json(updater._icon_hashes_path, icon_hashes)
    _write_json(updater._state_path, {
        "patch": remote_patch,
        "build": remote_build,
        "max_cursor": final_max_cursor,
    })

    logger.info("")
    logger.info("Done!  patch=%s  build=%s  items=%d  icons_updated=%d",
                remote_patch, remote_build, len(merged), icons_updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
