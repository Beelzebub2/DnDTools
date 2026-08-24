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
import argparse
import concurrent.futures
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, Optional
from urllib.parse import quote

import requests
from PIL import Image

logger = logging.getLogger(__name__)

# API configuration
API_BASE_URL = "https://api.darkerdb.com/v2/items"
API_VERSION = "2026-08-03"
DARKERDB_API_KEY_ENV_NAMES = ("DARKERDB_API_KEY", "DNDTOOLS_DARKERDB_API_KEY")


def get_darkerdb_api_key() -> str:
    """Resolve only credentials explicitly intended for DarkerDB."""
    for name in DARKERDB_API_KEY_ENV_NAMES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


API_KEY = get_darkerdb_api_key()

# Resolve important paths relative to the UI root
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
ITEMS_FILE = BASE_DIR / "assets" / "items.json"
ICON_ENDPOINT_TEMPLATE = "https://api.darkerdb.com/v2/items/{item_id}/icon"
ICON_DOWNLOAD_TIMEOUT = 15


_CLASS_FLAGS = {
    "fighter": 1,
    "barbarian": 2,
    "rogue": 4,
    "ranger": 8,
    "wizard": 16,
    "cleric": 32,
    "bard": 64,
    "warlock": 128,
    "druid": 256,
    "sorcerer": 512,
}

# These fields describe files produced by this updater rather than DarkerDB
# item metadata.  They are the only values carried across catalog refreshes;
# every API-owned field is rebuilt from the current v2 row.
_LOCAL_ITEM_METADATA_FIELDS = (
    "iconPath",
    "iconHash",
    "iconETag",
    "iconLastModified",
)


@dataclass(frozen=True)
class IconProcessResult:
    updated_files: int = 0
    not_modified: int = 0
    hash_matches: int = 0
    metadata_updates: int = 0
    error: Optional[str] = None
    fatal: bool = False
    target_path: Optional[Path] = None
    rollback_path: Optional[Path] = None
    created_new: bool = False


@dataclass(frozen=True)
class IconRefreshSummary:
    updated_files: int = 0
    not_modified: int = 0
    hash_matches: int = 0
    metadata_updates: int = 0
    failures: tuple[str, ...] = ()
    fatal_failures: tuple[str, ...] = ()


@dataclass
class IconPruneTransaction:
    """Quarantined obsolete icons that can be committed or rolled back."""

    quarantine_root: Optional[Path]
    moved_files: list[tuple[Path, Path]]

    @property
    def pruned_files(self) -> int:
        return len(self.moved_files)

    def rollback(self) -> None:
        errors: list[str] = []
        for source_path, quarantine_path in reversed(self.moved_files):
            try:
                source_path.parent.mkdir(parents=True, exist_ok=True)
                if source_path.exists():
                    raise OSError(f"restore target already exists: {source_path}")
                quarantine_path.replace(source_path)
            except OSError as exc:
                errors.append(f"{source_path}: {exc}")
        # Preserve the quarantine for manual recovery if any individual
        # restore failed; deleting it would destroy the last good copy.
        if self.quarantine_root is not None and not errors:
            try:
                shutil.rmtree(self.quarantine_root)
            except OSError as exc:
                errors.append(f"{self.quarantine_root}: {exc}")
        if errors:
            raise OSError("; ".join(errors))

    def commit(self) -> None:
        if self.quarantine_root is not None:
            shutil.rmtree(self.quarantine_root)


def _pascalize(value: object) -> str:
    return "".join(
        part[:1].upper() + part[1:]
        for part in str(value or "").replace("-", "_").split("_")
        if part
    )


def canonical_item_to_game_id(value: object) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("id.item."):
        text = text[len("id.item."):]
    parts = [part for part in text.split("_") if part]
    numeric_suffix = parts.pop() if parts and parts[-1].isdigit() else None
    result = _pascalize("_".join(parts))
    if numeric_suffix:
        result = f"{result}_{numeric_suffix}"
    return result


def _humanize_enum(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return " ".join(part.capitalize() for part in text.replace("-", "_").split("_") if part)


def item_has_remote_icon(item: Dict) -> bool:
    """Return whether DarkerDB declares an icon for this item row."""
    return any(
        str(item.get(field) or "").strip()
        for field in ("icon", "icon_url")
    )


def _existing_item_identity(existing_items: Dict[str, Dict]) -> tuple[dict[str, str], dict[tuple[str, str], list[str]]]:
    by_canonical: dict[str, str] = {}
    by_name_rarity: dict[tuple[str, str], list[str]] = {}
    for item_id, record in existing_items.items():
        if not isinstance(record, dict):
            continue
        canonical_id = str(record.get("darkerdb_id") or "").strip().lower()
        if canonical_id:
            by_canonical[canonical_id] = item_id
        identity = (
            str(record.get("name") or "").strip().casefold(),
            str(record.get("rarity") or "").strip().casefold(),
        )
        if identity[0]:
            by_name_rarity.setdefault(identity, []).append(item_id)
    return by_canonical, by_name_rarity


def normalize_v2_item(
    raw: Dict,
    existing_items: Optional[Dict[str, Dict]] = None,
    *,
    by_canonical: Optional[dict[str, str]] = None,
    by_name_rarity: Optional[dict[tuple[str, str], list[str]]] = None,
) -> tuple[str, Dict]:
    """Translate a v2 row and preserve only intentional local metadata."""
    if not isinstance(raw, dict):
        raise ValueError("DarkerDB returned a non-object item row")
    canonical_id = str(raw.get("id") or "").strip()
    if not canonical_id:
        raise ValueError("DarkerDB item row is missing id")

    existing_items = existing_items or {}
    by_canonical = by_canonical or {}
    by_name_rarity = by_name_rarity or {}
    candidate_id = canonical_item_to_game_id(canonical_id)
    item_id = by_canonical.get(canonical_id.lower())
    if not item_id and candidate_id in existing_items:
        item_id = candidate_id
    if not item_id:
        identity = (
            str(raw.get("name") or "").strip().casefold(),
            str(raw.get("rarity") or "").strip().casefold(),
        )
        matches = by_name_rarity.get(identity) or []
        if len(matches) == 1:
            item_id = matches[0]
    item_id = item_id or candidate_id

    previous = existing_items.get(item_id)
    previous = previous if isinstance(previous, dict) else {}

    # Start from the complete current API row instead of merging a small
    # allowlist into the previous record.  The latter left hundreds of removed
    # or renamed DarkerDB fields stale indefinitely.  Null top-level values are
    # omitted to retain the established items.json/UI fallback contract.
    record = {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and value is not None
    }
    for field in _LOCAL_ITEM_METADATA_FIELDS:
        # A remote field with the same name must not override locally generated
        # icon state.  New records receive a derived iconPath below.
        record.pop(field, None)
        if field in previous:
            record[field] = previous[field]

    record.update({
        "id": item_id,
        "darkerdb_id": canonical_id,
        "darkerdb_archetype": raw.get("archetype"),
        "archetype": canonical_item_to_game_id(raw.get("archetype")),
        "name": raw.get("name") or item_id,
        "rarity": _humanize_enum(raw.get("rarity")) or "Unknown",
        "type": _humanize_enum(raw.get("item_type")) or "Misc",
    })

    if raw.get("flavor") is not None:
        record["description"] = raw.get("flavor")

    enum_fields = {
        "slot_type": "slot_type",
        "armor_type": "armor_type",
        "hand_type": "hand_type",
        "weapon_type": "weapon_type",
        "misc_type": "misc_type",
        "utility_type": "utility_type",
    }
    for source, destination in enum_fields.items():
        value = _humanize_enum(raw.get(source))
        if value is not None:
            record[destination] = value
        elif destination in record and raw.get(source) is None:
            record.pop(destination, None)

    if raw.get("wearing_delay_time") is not None:
        record["time_to_equip"] = raw.get("wearing_delay_time")

    required_classes = raw.get("required_class")
    if isinstance(required_classes, list):
        normalized_classes = [str(value).strip().lower() for value in required_classes if str(value).strip()]
        record["required_classes"] = normalized_classes
        record["required_class"] = sum(_CLASS_FLAGS.get(value, 0) for value in set(normalized_classes))

    if item_has_remote_icon(raw):
        if raw.get("icon") is not None:
            record["darkerdbIconHash"] = raw.get("icon")
        if raw.get("icon_url") is not None:
            record["iconUrl"] = raw.get("icon_url")
        record["iconPath"] = normalize_icon_path(
            record.get("iconPath") or derive_icon_path(item_id, record)
        )
    else:
        # Some legitimate catalog rows (currently Bare Hand variants) are
        # explicitly iconless and their icon route returns 404. Do not invent
        # a local path or carry stale conditional/hash metadata for them.
        for field in (*_LOCAL_ITEM_METADATA_FIELDS, "darkerdbIconHash", "iconUrl"):
            record.pop(field, None)
    return item_id, record


def fetch_items_catalog(
    *,
    api_key: str,
    session=None,
    api_url: str = API_BASE_URL,
    page_size: int = 200,
    max_pages: int = 100,
) -> tuple[list[Dict], Dict]:
    if not api_key:
        raise RuntimeError("DARKERDB_API_KEY is required for DarkerDB item updates")
    client = session or requests
    headers = {
        "User-Agent": "DnDTools-Updater/2.0",
        "X-Api-Key": api_key,
        "X-API-Version": API_VERSION,
    }
    cursor = None
    seen_cursors: set[str] = set()
    seen_ids: set[str] = set()
    items: list[Dict] = []
    metadata: Dict = {"api_version": API_VERSION}

    for page in range(1, max_pages + 1):
        logger.info("Fetching item page %s...", page)
        params: Dict[str, object] = {"limit": max(1, min(int(page_size), 200)), "locale": "en"}
        if cursor:
            params["cursor"] = cursor
        response = client.get(api_url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("body"), list):
            raise ValueError("DarkerDB item response body was not a list")
        for row in payload["body"]:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("id") or "").strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            items.append(row)
        for key in ("version", "build", "patch", "request_id"):
            if payload.get(key) is not None:
                metadata[key] = payload.get(key)

        pagination = payload.get("pagination")
        next_cursor = pagination.get("next") if isinstance(pagination, dict) else None
        if not next_cursor:
            break
        cursor_text = str(next_cursor).strip()
        if cursor_text in seen_cursors:
            raise ValueError("DarkerDB returned a repeated item pagination cursor")
        seen_cursors.add(cursor_text)
        cursor = cursor_text
    else:
        raise ValueError("DarkerDB item pagination exceeded the safety limit")

    if not items:
        raise ValueError("DarkerDB returned an empty item catalog")
    metadata["total"] = len(items)
    return items, metadata


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


def _render_progress(current: int, total: int, prefix: str = "") -> None:
    if not total:
        return
    bar_length = 30
    filled_length = int(bar_length * current / total)
    bar = "#" * filled_length + "-" * (bar_length - filled_length)
    percent = int((current / total) * 100)
    sys.stdout.write(f"\r{prefix}[{bar}] {percent:3d}% ({current}/{total})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


def _icon_path_and_target(item_id: str, api_item: Dict, record: Dict) -> tuple[str, Path]:
    raw_path = record.get('iconPath') or derive_icon_path(item_id, api_item)
    icon_path = normalize_icon_path(str(raw_path))
    if icon_path.lower().endswith('.png'):
        icon_path = icon_path[:-4] + '.webp'

    portable_path = PurePosixPath(icon_path)
    if (
        not icon_path
        or "\x00" in icon_path
        or portable_path.is_absolute()
        or PureWindowsPath(str(raw_path)).is_absolute()
        or any(part in {"", ".", ".."} for part in icon_path.split("/"))
    ):
        raise ValueError(f"Unsafe icon path for {item_id}: {icon_path!r}")

    assets_root_path = BASE_DIR / "assets"
    # Stabilize path resolution before icon workers start creating nested
    # directories concurrently (important on Windows for an initially missing
    # temporary/application asset directory).
    assets_root_path.mkdir(parents=True, exist_ok=True)
    assets_root = assets_root_path.resolve()
    # The path is assembled only from the lexically validated relative parts.
    # Avoid resolving the child again: while workers create its parent tree,
    # Windows can transiently return a differently canonicalized path and
    # produce a false containment rejection.
    target_path = assets_root.joinpath(*portable_path.parts)
    return icon_path, target_path


def quarantine_unreferenced_icons(catalog: Dict[str, Dict]) -> IconPruneTransaction:
    """Move WebP files absent from *catalog* out of the published icon tree.

    The caller must commit only after the catalog has been published. Until
    then every move is recoverable via ``rollback()``.
    """

    assets_root = (BASE_DIR / "assets").resolve()
    icons_root = assets_root / "icons"
    referenced: set[str] = set()

    for item_id, record in catalog.items():
        if not isinstance(record, dict) or not record.get("iconPath"):
            continue
        icon_path, _ = _icon_path_and_target(item_id, record, record)
        portable_path = PurePosixPath(icon_path)
        if (
            not portable_path.parts
            or portable_path.parts[0].casefold() != "icons"
            or portable_path.suffix.casefold() != ".webp"
        ):
            raise ValueError(f"Catalog icon path is not a WebP under icons/: {icon_path!r}")
        referenced.add(portable_path.as_posix().casefold())

    if not icons_root.exists():
        return IconPruneTransaction(None, [])

    obsolete: list[Path] = []
    for path in icons_root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() != ".webp":
            continue
        relative_key = PurePosixPath("icons", *path.relative_to(icons_root).parts)
        if relative_key.as_posix().casefold() not in referenced:
            obsolete.append(path)

    if not obsolete:
        return IconPruneTransaction(None, [])

    quarantine_root = Path(tempfile.mkdtemp(prefix=".icons-prune-", dir=str(assets_root)))
    moved_files: list[tuple[Path, Path]] = []
    transaction = IconPruneTransaction(quarantine_root, moved_files)
    try:
        for source_path in sorted(obsolete):
            relative_path = source_path.relative_to(icons_root)
            quarantine_path = quarantine_root / relative_path
            quarantine_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.replace(quarantine_path)
            moved_files.append((source_path, quarantine_path))
    except Exception as exc:
        try:
            transaction.rollback()
        except OSError as rollback_exc:
            raise RuntimeError(
                f"Failed to quarantine obsolete icon {source_path}: {exc}; "
                f"rollback also failed: {rollback_exc}"
            ) from exc
        raise RuntimeError(f"Failed to quarantine obsolete icon {source_path}: {exc}") from exc

    return transaction


def _write_icon_bytes(target_path: Path, payload: bytes) -> None:
    """Atomically replace an icon without exposing a partial WebP file."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=str(target_path.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_bytes(payload)
        temp_path.replace(target_path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass


def _backup_icon_target(target_path: Path) -> Optional[Path]:
    """Create a same-directory rollback copy without mutating the target."""
    if not target_path.exists():
        return None
    fd, backup_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.dndtools-",
        suffix=".bak",
        dir=str(target_path.parent),
    )
    os.close(fd)
    backup_path = Path(backup_name)
    try:
        shutil.copy2(target_path, backup_path)
    except Exception:
        try:
            backup_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass
        raise
    return backup_path


def _restore_icon_target(
    target_path: Path,
    rollback_path: Optional[Path],
    created_new: bool,
) -> None:
    if rollback_path is not None:
        if not rollback_path.exists():
            raise OSError(f"Icon rollback file is missing: {rollback_path}")
        rollback_path.replace(target_path)
    elif created_new:
        target_path.unlink(missing_ok=True)  # type: ignore[arg-type]


def process_icon(
    item_id: str,
    api_item: Dict,
    record: Dict,
    headers: Dict,
    force_refresh: bool,
    failure_is_fatal: bool = False,
    transactional: bool = False,
) -> IconProcessResult:
    """Process one icon and surface failures that invalidate the catalog."""
    updated_files = 0
    not_modified = 0
    hash_matches = 0
    metadata_updates = 0

    icon_path, target_path = _icon_path_and_target(item_id, api_item, record)
    record['iconPath'] = icon_path
    target_path.parent.mkdir(parents=True, exist_ok=True)

    def _cleanup_png():
        try:
            png_path = target_path.with_suffix('.png')
            if png_path.exists():
                png_path.unlink()
        except OSError:
            pass

    if target_path.exists():
        _cleanup_png()

    file_missing = not target_path.exists()
    failure_is_fatal = failure_is_fatal or file_missing

    def _result(
        *,
        error: Optional[str] = None,
        rollback_path: Optional[Path] = None,
        created_new: bool = False,
    ) -> IconProcessResult:
        return IconProcessResult(
            updated_files=updated_files,
            not_modified=not_modified,
            hash_matches=hash_matches,
            metadata_updates=metadata_updates,
            error=error,
            fatal=bool(error and failure_is_fatal),
            target_path=target_path if rollback_path is not None or created_new else None,
            rollback_path=rollback_path,
            created_new=created_new,
        )

    if not record.get('iconHash') and target_path.exists():
        record['iconHash'] = compute_file_hash(target_path)
        if record['iconHash']:
            metadata_updates += 1

    # Icon/image routes are intentionally keyless. Avoid consuming the
    # darkerdb.data request budget across thousands of conditional checks.
    request_headers = {
        key: value for key, value in headers.items() if key.lower() != 'x-api-key'
    }
    if record.get('iconETag'):
        request_headers['If-None-Match'] = record['iconETag']
    if record.get('iconLastModified'):
        request_headers.setdefault('If-Modified-Since', record['iconLastModified'])
    if file_missing or force_refresh:
        request_headers.pop('If-None-Match', None)
        request_headers.pop('If-Modified-Since', None)

    source_item_id = api_item.get('id') or item_id
    icon_url = ICON_ENDPOINT_TEMPLATE.format(item_id=quote(str(source_item_id), safe=''))
    try:
        response = requests.get(
            icon_url,
            headers=request_headers,
            timeout=ICON_DOWNLOAD_TIMEOUT,
        )
    except requests.RequestException as exc:
        message = f"Failed to fetch icon for {item_id}: {exc}"
        logger.error(message)
        return _result(error=message)

    if response.status_code == 304:
        if file_missing:
            message = f"Icon endpoint returned 304 for missing icon {item_id}"
            logger.error(message)
            return _result(error=message)
        not_modified += 1
        return _result()

    if response.status_code != 200:
        message = f"Icon endpoint returned HTTP {response.status_code} for {item_id}"
        logger.warning(message)
        return _result(error=message)

    try:
        processed_bytes = convert_icon_to_webp_bytes(response.content)
    except Exception as exc:
        message = f"Failed to decode/convert icon for {item_id}: {exc}"
        logger.error(message)
        return _result(error=message)

    new_hash = compute_bytes_hash(processed_bytes)
    existing_hash = compute_file_hash(target_path)
    if existing_hash and existing_hash == new_hash:
        if update_icon_metadata(record, new_hash, response.headers):
            metadata_updates += 1
        hash_matches += 1
        return _result()

    rollback_path: Optional[Path] = None
    created_new = not target_path.exists()
    try:
        if transactional:
            rollback_path = _backup_icon_target(target_path)
        _write_icon_bytes(target_path, processed_bytes)
        _cleanup_png()
    except Exception as exc:
        if rollback_path is not None or created_new:
            try:
                _restore_icon_target(target_path, rollback_path, created_new)
            except OSError as rollback_exc:
                exc = RuntimeError(f"{exc}; icon rollback also failed: {rollback_exc}")
        message = f"Failed to write icon for {item_id}: {exc}"
        logger.error(message)
        return _result(error=message)

    if update_icon_metadata(record, new_hash, response.headers):
        metadata_updates += 1
    updated_files += 1

    return _result(rollback_path=rollback_path, created_new=created_new and transactional)


def refresh_icons(
    existing_items: Dict,
    fetched_items: Dict[str, Dict],
    headers: Dict,
    force_refresh: bool = False,
    required_item_ids: Optional[set[str]] = None,
) -> IconRefreshSummary:
    """Ensure icons exist and report failures that make a catalog unsafe."""

    updated_files = 0
    not_modified = 0
    hash_matches = 0
    metadata_updates = 0
    failures: list[str] = []
    fatal_failures: list[str] = []
    required_item_ids = set(required_item_ids or ())
    changed_results: list[IconProcessResult] = []
    seen_targets: dict[Path, str] = {}

    tasks: dict[concurrent.futures.Future, tuple[str, bool]] = {}
    # Use a reasonable number of workers
    max_workers = min(32, (os.cpu_count() or 1) * 4)
    
    logger.info("Starting icon refresh with %d workers...", max_workers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for item_id, api_item in fetched_items.items():
            record = existing_items.get(item_id)
            if not record:
                message = f"No normalized item record available for icon {item_id}"
                failures.append(message)
                fatal_failures.append(message)
                continue

            if not item_has_remote_icon(api_item):
                for field in (*_LOCAL_ITEM_METADATA_FIELDS, "darkerdbIconHash", "iconUrl"):
                    record.pop(field, None)
                logger.debug("Skipping explicitly iconless item %s", item_id)
                continue

            try:
                _, target_path = _icon_path_and_target(item_id, api_item, record)
                failure_is_fatal = item_id in required_item_ids or not target_path.exists()
                previous_owner = seen_targets.get(target_path)
                if previous_owner is not None:
                    message = (
                        f"Icon target collision: {previous_owner} and {item_id} both map to "
                        f"{target_path}"
                    )
                    logger.error(message)
                    failures.append(message)
                    fatal_failures.append(message)
                    continue
                seen_targets[target_path] = item_id
            except Exception as exc:
                message = f"Unable to resolve icon target for {item_id}: {exc}"
                logger.error(message)
                failures.append(message)
                fatal_failures.append(message)
                continue

            future = executor.submit(
                process_icon,
                item_id,
                api_item,
                record,
                headers,
                force_refresh,
                failure_is_fatal,
                True,
            )
            tasks[future] = (item_id, failure_is_fatal)

        total = len(tasks)
        completed = 0
        
        if total > 0:
            _render_progress(0, total, prefix="Icons: ")
            
            for future in concurrent.futures.as_completed(tasks):
                item_id, failure_is_fatal = tasks[future]
                try:
                    result = future.result()
                    updated_files += result.updated_files
                    not_modified += result.not_modified
                    hash_matches += result.hash_matches
                    metadata_updates += result.metadata_updates
                    if result.error:
                        failures.append(result.error)
                        if result.fatal:
                            fatal_failures.append(result.error)
                    if result.target_path is not None:
                        changed_results.append(result)
                except Exception as exc:
                    message = f"Icon worker failed for {item_id}: {exc}"
                    logger.error(message)
                    failures.append(message)
                    if failure_is_fatal:
                        fatal_failures.append(message)
                
                completed += 1
                _render_progress(completed, total, prefix="Icons: ")

    if fatal_failures:
        # Do not leave a half-refreshed icon tree when the catalog itself will
        # be rejected. This also makes direct/local updater runs as safe as the
        # CI workflow's later git rollback.
        for result in reversed(changed_results):
            try:
                _restore_icon_target(
                    result.target_path,  # type: ignore[arg-type]
                    result.rollback_path,
                    result.created_new,
                )
            except OSError as exc:
                message = f"Failed to roll back icon {result.target_path}: {exc}"
                logger.error(message)
                failures.append(message)
                fatal_failures.append(message)
    else:
        for result in changed_results:
            if result.rollback_path is None:
                continue
            try:
                result.rollback_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError as exc:
                logger.warning("Unable to remove icon rollback file %s: %s", result.rollback_path, exc)

    return IconRefreshSummary(
        updated_files=updated_files,
        not_modified=not_modified,
        hash_matches=hash_matches,
        metadata_updates=metadata_updates,
        failures=tuple(failures),
        fatal_failures=tuple(fatal_failures),
    )

def update_items(force_refresh: bool = False):
    """Fetch item metadata and ensure icon assets stay in sync using hash checks."""
    prune_transaction: Optional[IconPruneTransaction] = None
    try:
        if not API_KEY:
            logger.error("DARKERDB_API_KEY is required for DarkerDB item updates")
            return False

        # Load existing items
        existing_items = {}
        if ITEMS_FILE.exists():
            with ITEMS_FILE.open('r', encoding='utf-8') as f:
                existing_items = json.load(f)

        logger.info("Loaded %s existing items", len(existing_items))
        logger.info("Fetching new items data from DarkerDB API...")

        headers = {
            "User-Agent": "DnDTools-Updater/2.0",
            "X-API-Version": API_VERSION,
        }

        all_new_items, metadata = fetch_items_catalog(api_key=API_KEY)

        logger.info("Total items fetched from API: %s", len(all_new_items))

        by_canonical, by_name_rarity = _existing_item_identity(existing_items)
        fetched_items_map: Dict[str, Dict] = {}
        updated_items: Dict[str, Dict] = {}
        for item in all_new_items:
            item_id, record = normalize_v2_item(
                item,
                existing_items,
                by_canonical=by_canonical,
                by_name_rarity=by_name_rarity,
            )
            if not item_id:
                continue
            fetched_items_map[item_id] = item
            updated_items[item_id] = record

        added_ids = set(updated_items) - set(existing_items)
        removed_ids = set(existing_items) - set(updated_items)
        changed_metadata = updated_items != existing_items
        logger.info(
            "Normalized %s items (%s added, %s removed; build=%s patch=%s)",
            len(updated_items),
            len(added_ids),
            len(removed_ids),
            metadata.get("build"),
            metadata.get("patch"),
        )

        icon_stats = refresh_icons(
            updated_items,
            fetched_items_map,
            headers,
            force_refresh=force_refresh,
            required_item_ids=added_ids,
        )
        logger.info(
            "Icon sync summary: %s updated, %s not-modified (server), %s skipped via hash match, %s metadata refreshes, %s failure(s)",
            icon_stats.updated_files,
            icon_stats.not_modified,
            icon_stats.hash_matches,
            icon_stats.metadata_updates,
            len(icon_stats.failures),
        )

        if icon_stats.fatal_failures:
            preview = "; ".join(icon_stats.fatal_failures[:10])
            if len(icon_stats.fatal_failures) > 10:
                preview += f"; ... (+{len(icon_stats.fatal_failures) - 10} more)"
            logger.error(
                "Refusing to publish an item catalog with missing/new icon failures: %s",
                preview,
            )
            return False

        prune_transaction = quarantine_unreferenced_icons(updated_items)
        if prune_transaction.pruned_files:
            logger.info(
                "Quarantined %s unreferenced WebP icon(s) pending catalog publication",
                prune_transaction.pruned_files,
            )

        should_write = (
            changed_metadata
            or icon_stats.updated_files > 0
            or icon_stats.metadata_updates > 0
            or prune_transaction.pruned_files > 0
        )
        if should_write:
            logger.info("Writing %s items to %s", len(updated_items), ITEMS_FILE)
            temp_file = ITEMS_FILE.with_name(f"{ITEMS_FILE.name}.{os.getpid()}.tmp")
            try:
                with temp_file.open('w', encoding='utf-8') as f:
                    json.dump(updated_items, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                temp_file.replace(ITEMS_FILE)
            finally:
                temp_file.unlink(missing_ok=True)  # type: ignore[arg-type]
            logger.info("Items data saved successfully")
        else:
            logger.info("No changes detected in item catalog; skipping write")

        prune_transaction.commit()
        prune_transaction = None

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
    finally:
        if prune_transaction is not None:
            try:
                prune_transaction.rollback()
            except OSError as rollback_error:
                logger.error("Failed to restore quarantined icons: %s", rollback_error)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update items and icons from DarkerDB API.")
    parser.add_argument("--force-icons", action="store_true", help="Force re-download and hash check of all icons, ignoring cache headers.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logger.info("DarkerDB Items Updater")
    logger.info("%s", "=" * 30)
    logger.info("API URL: %s", API_BASE_URL)
    logger.info("Target file: %s", ITEMS_FILE)
    if args.force_icons:
        logger.info("Mode: FORCE REFRESH (ignoring cache headers)")
    logger.info("")

    success = update_items(force_refresh=args.force_icons)
    if success:
        logger.info("\nUpdate completed successfully!")
        logger.info("You can now restart the application to use the updated items data.")
        sys.exit(0)
    else:
        logger.error("\nUpdate failed!")
        logger.error("Please check the API URL and try again.")
        sys.exit(1)
