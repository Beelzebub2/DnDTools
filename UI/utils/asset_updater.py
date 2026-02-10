"""
DarkerDB Asset Updater — complete redesign.

Flow:
    1.  Probe the DarkerDB API to read the current ``patch`` and ``build``.
    2.  Compare them against the locally-cached values in ``.asset_state.json``.
    3a. **Patch changed** → full refresh: re-fetch every item, re-download every
        icon whose SHA differs, rebuild ``icons.pak``.
    3b. **Same patch** → incremental: use the highest known ``cursor`` to page
        only *new* items, download *their* icons, rebuild ``icons.pak`` only
        when something actually changed.
    4.  Persist the new state so the next launch skips redundant work.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse, parse_qs

import requests

try:
    from PIL import Image
except ImportError:  # PIL may be missing at import-time in frozen builds
    Image = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DARKERDB_API_BASE = "https://api.darkerdb.com/v1"
DARKERDB_ITEMS_URL = f"{DARKERDB_API_BASE}/items"
DARKERDB_ICON_URL = f"{DARKERDB_API_BASE}/items/{{item_id}}/icon"
API_KEY = os.getenv("API_KEY", "")

STATE_FILENAME = ".asset_state.json"
ICON_HASHES_FILENAME = ".icon_hashes.json"
ITEMS_FILENAME = "items.json"
ICONS_PAK_FILENAME = "icons.pak"

# Pagination
PAGE_LIMIT = 50  # DarkerDB max
MAX_PAGES = 200  # safety
ICON_TIMEOUT = 15  # seconds per icon download
ITEM_TIMEOUT = 30  # seconds per items page

# Icon packing
WEBP_QUALITY = 85
FIXED_ZIP_DATE = (2024, 1, 1, 0, 0, 0)
MAX_ICON_WORKERS = 24


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _api_params() -> Dict[str, str]:
    """Return query params that should go on every DarkerDB request."""
    params: Dict[str, str] = {"condense": "true"}
    if API_KEY:
        params["key"] = API_KEY
    return params


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _convert_to_webp(raw: bytes) -> bytes:
    """Convert any image bytes to WebP."""
    if Image is None:
        raise RuntimeError("Pillow is required for icon conversion")
    img = Image.open(io.BytesIO(raw))
    if img.mode not in {"RGBA", "RGB"}:
        img = img.convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=WEBP_QUALITY, method=6, lossless=False)
    return buf.getvalue()


def _icon_rel_path(item_id: str, item_type: str) -> str:
    """Derive the relative icon path inside ``icons/``."""
    safe_type = (item_type or "Misc").replace(" ", "_")
    return f"icons/{safe_type}/{item_id}.webp"


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text("utf-8")) if path.exists() else None
    except (ValueError, OSError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")


# ---------------------------------------------------------------------------
# Core updater
# ---------------------------------------------------------------------------

class AssetUpdater:
    """Download items + icons from DarkerDB and pack icons into ``icons.pak``.

    Lifecycle:
        1. ``start_async_update()`` kicks off a background thread.
        2. The thread probes DarkerDB for the current game patch.
        3. Depending on whether the patch changed it does a full or incremental
           item + icon sync.
        4. ``icons.pak`` is rebuilt when any icon file changed.
        5. ``on_assets_applied`` hooks are called so the UI can reload.
    """

    def __init__(
        self,
        assets_dir: Path,
        logger: Optional[logging.Logger] = None,
        window_getter: Optional[Callable[[], Optional[object]]] = None,
        on_assets_applied: Optional[Iterable[Callable[[dict], None]]] = None,
        before_asset_replace: Optional[Mapping[str, Iterable[Callable[[], None]]]] = None,
        session: Optional[requests.Session] = None,
        # kept for backwards compat — ignored
        base_url: Optional[str] = None,
        manifest_url: Optional[str] = None,
    ) -> None:
        self.assets_dir = Path(assets_dir)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)
        self.window_getter = window_getter
        self.session = session or requests.Session()
        # Size the connection pool to match our max worker count so
        # urllib3 doesn't discard and re-create connections constantly.
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=MAX_ICON_WORKERS,
            pool_maxsize=MAX_ICON_WORKERS,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.setdefault("User-Agent", "DnDTools-AssetUpdater/2.0")
        self._hooks: Tuple[Callable[[dict], None], ...] = tuple(on_assets_applied or [])
        self._pre_replace_hooks: Dict[str, Tuple[Callable[[], None], ...]] = {
            str(name): tuple(cbs) for name, cbs in (before_asset_replace or {}).items() if cbs
        }
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    # -- paths -------------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return self.assets_dir / STATE_FILENAME

    @property
    def _icon_hashes_path(self) -> Path:
        return self.assets_dir / ICON_HASHES_FILENAME

    @property
    def _items_path(self) -> Path:
        return self.assets_dir / ITEMS_FILENAME

    @property
    def _icons_dir(self) -> Path:
        return self.assets_dir / "icons"

    @property
    def _icons_pak_path(self) -> Path:
        return self.assets_dir / ICONS_PAK_FILENAME

    # -- public API --------------------------------------------------------
    def start_async_update(self) -> bool:
        """Start the asset refresh worker if not already running."""
        with self._lock:
            if self._worker and self._worker.is_alive():
                self.logger.debug("Asset updater already running")
                return False
            t = threading.Thread(target=self._run, name="AssetUpdater", daemon=True)
            t.start()
            self._worker = t
            return True

    # -- worker ------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._notify_ui({"status": "checking", "message": "Checking DarkerDB for updates…"})

            # ── Step 1: probe current patch/build ─────────────────────────
            remote_patch, remote_build = self._probe_patch()
            if remote_patch is None:
                self._notify_ui({
                    "status": "error",
                    "message": "Unable to reach DarkerDB API.",
                    "allowDismiss": True,
                })
                return

            state = _read_json(self._state_path) or {}
            local_patch = state.get("patch")
            local_build = state.get("build")
            local_max_cursor = state.get("max_cursor", 0)

            patch_changed = (remote_patch != local_patch) or (remote_build != local_build)
            self.logger.info(
                "DarkerDB probe: patch=%s build=%s (local patch=%s build=%s) → %s",
                remote_patch, remote_build, local_patch, local_build,
                "FULL refresh" if patch_changed else "incremental",
            )

            # ── Step 2: fetch items ───────────────────────────────────────
            self._notify_ui({"status": "downloading", "message": "Fetching items from DarkerDB…"})

            if patch_changed:
                items_dict, new_max_cursor = self._fetch_all_items()
            else:
                items_dict, new_max_cursor = self._fetch_new_items(local_max_cursor)

            # Merge with existing items
            existing_items: Dict[str, dict] = {}
            if self._items_path.exists():
                try:
                    existing_items = json.loads(self._items_path.read_text("utf-8"))
                except (ValueError, OSError):
                    existing_items = {}

            if patch_changed:
                # Full replace — but only if we fetched a reasonable amount.
                # If the API returned far fewer items than we already have,
                # something went wrong (partial fetch / timeout).  In that
                # case merge instead of replacing so we don't lose data.
                if len(items_dict) >= len(existing_items) * 0.5 or not existing_items:
                    merged = items_dict
                    self.logger.info(
                        "Full replace: %d API items replacing %d local items",
                        len(items_dict), len(existing_items),
                    )
                else:
                    merged = {**existing_items, **items_dict}
                    self.logger.warning(
                        "Partial fetch detected (%d API vs %d local) — merging instead of replacing",
                        len(items_dict), len(existing_items),
                    )
            else:
                merged = {**existing_items, **items_dict}

            final_max_cursor = max(new_max_cursor, local_max_cursor)

            # ── Step 3: download icons ────────────────────────────────────
            icon_hashes = _read_json(self._icon_hashes_path) or {}

            if patch_changed:
                # Full icon refresh — check every item
                target_items = merged
            else:
                # Only new / changed items
                target_items = items_dict

            icons_updated, icons_skipped = self._sync_icons(
                target_items, icon_hashes, force=patch_changed,
            )
            self.logger.info("Icons: %d updated, %d skipped", icons_updated, icons_skipped)

            # ── Step 4: rebuild icons.pak if anything changed ─────────────
            if icons_updated > 0 or patch_changed:
                self._notify_ui({"status": "downloading", "message": "Packing icons…"})
                self._run_pre_hooks(ICONS_PAK_FILENAME)
                self._build_icons_pak()

            # ── Step 5: persist ───────────────────────────────────────────
            clean_items = self._clean_items_for_disk(merged)
            _write_json(self._items_path, clean_items)
            _write_json(self._icon_hashes_path, icon_hashes)
            _write_json(self._state_path, {
                "patch": remote_patch,
                "build": remote_build,
                "max_cursor": final_max_cursor,
            })

            # ── Step 6: notify ────────────────────────────────────────────
            metadata = {
                "patch": remote_patch,
                "build": remote_build,
                "items_total": len(merged),
                "items_new": len(items_dict),
                "icons_updated": icons_updated,
            }
            for hook in self._hooks:
                try:
                    hook(metadata)
                except Exception as exc:
                    self.logger.warning("Post-asset hook failed: %s", exc, exc_info=True)

            if icons_updated or items_dict:
                self._notify_ui({
                    "status": "success",
                    "message": f"Assets updated — {len(items_dict)} new items, {icons_updated} icons refreshed.",
                    "metadata": metadata,
                    "autoDismiss": True,
                })
            else:
                self._notify_ui({
                    "status": "idle",
                    "message": "Assets are already up to date.",
                    "autoDismiss": True,
                })

        except Exception as exc:
            self.logger.error("Asset update failed: %s", exc, exc_info=True)
            self._notify_ui({
                "status": "error",
                "message": f"Asset update failed: {exc}",
                "allowDismiss": True,
            })

    # ------------------------------------------------------------------
    # DarkerDB API interaction
    # ------------------------------------------------------------------

    def _probe_patch(self) -> Tuple[Optional[int], Optional[str]]:
        """Fetch a single item page just to read the envelope ``patch`` + ``build``."""
        try:
            params = {**_api_params(), "limit": "1"}
            r = self.session.get(DARKERDB_ITEMS_URL, params=params, timeout=ITEM_TIMEOUT)
            r.raise_for_status()
            envelope = r.json()
            return envelope.get("patch"), envelope.get("build")
        except Exception as exc:
            self.logger.warning("Patch probe failed: %s", exc)
            return None, None

    def _fetch_all_items(self) -> Tuple[Dict[str, dict], int]:
        """Page through every item using cursor pagination."""
        return self._paginate_items(cursor=0)

    def _fetch_new_items(self, after_cursor: int) -> Tuple[Dict[str, dict], int]:
        """Fetch only items with cursor > ``after_cursor``."""
        return self._paginate_items(cursor=after_cursor)

    def _paginate_items(self, cursor: int = 0) -> Tuple[Dict[str, dict], int]:
        """Walk the DarkerDB items endpoint using cursor pagination.

        Returns ``(items_dict, max_cursor)`` where *items_dict* maps
        ``item_id → item_data``.
        """
        items: Dict[str, dict] = {}
        max_cursor = cursor
        page = 0

        while page < MAX_PAGES:
            page += 1
            params: Dict[str, Any] = {**_api_params(), "limit": str(PAGE_LIMIT)}
            # Always pass cursor to force cursor-based pagination.
            # Without it the API defaults to page-based and the 'next'
            # URL won't contain a cursor value, breaking our loop.
            params["cursor"] = str(cursor)

            try:
                r = self.session.get(DARKERDB_ITEMS_URL, params=params, timeout=ITEM_TIMEOUT)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                self.logger.error("Items page %d failed: %s", page, exc)
                break

            body: List[dict] = data.get("body") or []
            if not body:
                break

            for item in body:
                item_id = item.get("id")
                if not item_id:
                    continue
                c = item.get("cursor", 0)
                if c > max_cursor:
                    max_cursor = c
                # Derive and store iconPath
                item_type = item.get("type") or "Misc"
                item["iconPath"] = _icon_rel_path(item_id, item_type)
                items[item_id] = item

            self.logger.info(
                "Items page %d: got %d items (total so far: %d)",
                page, len(body), len(items),
            )
            self._notify_ui({
                "status": "downloading",
                "message": f"Fetching items… page {page} ({len(items)} items)",
            })

            pagination = data.get("pagination") or {}
            if "next" not in pagination:
                break
            # Extract cursor from the next URL
            next_url = pagination["next"]
            qs = parse_qs(urlparse(next_url).query)
            next_cursor_values = qs.get("cursor")
            if next_cursor_values:
                cursor = int(next_cursor_values[0])
            else:
                break

        self.logger.info("Total items fetched: %d (max_cursor=%d)", len(items), max_cursor)
        return items, max_cursor

    # ------------------------------------------------------------------
    # Icon synchronisation
    # ------------------------------------------------------------------

    def _sync_icons(
        self,
        items: Dict[str, dict],
        icon_hashes: Dict[str, str],
        force: bool = False,
    ) -> Tuple[int, int]:
        """Download icons that are new or whose SHA has changed.

        *icon_hashes* is **mutated in place** with updated SHA values.
        Returns ``(updated_count, skipped_count)``.
        """
        updated = 0
        skipped = 0
        total = len(items)
        if not total:
            return 0, 0

        self.logger.info("Syncing icons for %d items (force=%s)…", total, force)
        completed = 0
        lock = threading.Lock()

        def _process(item_id: str, item: dict) -> Tuple[bool, bool]:
            """Returns ``(was_updated, was_skipped)``."""
            icon_rel = item.get("iconPath") or _icon_rel_path(item_id, item.get("type", "Misc"))
            target = self.assets_dir / icon_rel
            target.parent.mkdir(parents=True, exist_ok=True)

            # Download icon from DarkerDB
            url = DARKERDB_ICON_URL.format(item_id=item_id)
            params = _api_params()
            try:
                resp = self.session.get(url, params=params, timeout=ICON_TIMEOUT)
            except requests.RequestException as exc:
                self.logger.debug("Icon download failed for %s: %s", item_id, exc)
                return False, False

            if resp.status_code != 200:
                self.logger.debug("Icon %s returned status %s", item_id, resp.status_code)
                return False, False

            # Convert to WebP
            try:
                webp_bytes = _convert_to_webp(resp.content)
            except Exception as exc:
                self.logger.debug("Icon conversion failed for %s: %s", item_id, exc)
                return False, False

            new_sha = _sha256_bytes(webp_bytes)
            old_sha = icon_hashes.get(item_id, "")

            # Skip if SHA matches and the file already exists on disk
            if not force and old_sha == new_sha and target.exists():
                return False, True  # skipped

            # Write icon file
            try:
                target.write_bytes(webp_bytes)
            except OSError as exc:
                self.logger.error("Failed to write icon %s: %s", target, exc)
                return False, False

            with lock:
                icon_hashes[item_id] = new_sha
            return True, False

        workers = min(MAX_ICON_WORKERS, (os.cpu_count() or 1) * 4)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process, iid, idata): iid for iid, idata in items.items()}
            for future in as_completed(futures):
                completed += 1
                if completed % 50 == 0 or completed == total:
                    self._notify_ui({
                        "status": "downloading",
                        "message": f"Downloading icons… {completed}/{total}",
                        "progress": completed / total,
                    })
                try:
                    was_updated, was_skipped = future.result()
                    if was_updated:
                        updated += 1
                    elif was_skipped:
                        skipped += 1
                except Exception as exc:
                    self.logger.debug("Icon worker error: %s", exc)

        return updated, skipped

    # ------------------------------------------------------------------
    # icons.pak builder (inline — no subprocess needed)
    # ------------------------------------------------------------------

    def _build_icons_pak(self) -> None:
        """Rebuild ``icons.pak`` from all ``.webp`` files under ``assets/icons/``."""
        icons_dir = self._icons_dir
        if not icons_dir.exists():
            self.logger.warning("Icons directory does not exist: %s", icons_dir)
            return

        webp_files = sorted(icons_dir.rglob("*.webp"))
        if not webp_files:
            self.logger.warning("No WebP icons found under %s", icons_dir)
            return

        self.logger.info("Building icons.pak with %d icons…", len(webp_files))

        pak_path = self._icons_pak_path
        tmp_pak = pak_path.with_suffix(".pak.tmp")
        manifest: Dict[str, dict] = {}

        try:
            with zipfile.ZipFile(tmp_pak, mode="w", compression=zipfile.ZIP_LZMA) as archive:
                for webp in webp_files:
                    rel = webp.relative_to(icons_dir)
                    key = str(rel).replace("\\", "/")
                    data = webp.read_bytes()
                    sha = _sha256_bytes(data)
                    manifest[key] = {"sha256": sha, "size": len(data)}

                    zi = zipfile.ZipInfo(filename=f"icons/{key}", date_time=FIXED_ZIP_DATE)
                    zi.compress_type = zipfile.ZIP_LZMA
                    zi.external_attr = 0o644 << 16
                    archive.writestr(zi, data)

                # Embed manifest inside the pak
                mi = zipfile.ZipInfo(filename="manifest.json", date_time=FIXED_ZIP_DATE)
                mi.compress_type = zipfile.ZIP_LZMA
                mi.external_attr = 0o644 << 16
                archive.writestr(mi, json.dumps({
                    "generated_by": "asset_updater",
                    "quality": WEBP_QUALITY,
                    "total_icons": len(manifest),
                    "icons": manifest,
                }, indent=2, sort_keys=True).encode())

            # Atomic replace
            if pak_path.exists():
                pak_path.unlink()
            tmp_pak.rename(pak_path)
            self.logger.info("icons.pak rebuilt with %d icons", len(manifest))
        except Exception as exc:
            self.logger.error("Failed to build icons.pak: %s", exc, exc_info=True)
            try:
                tmp_pak.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_items_for_disk(items: Dict[str, dict]) -> Dict[str, dict]:
        """Strip transient icon-caching keys before writing items.json."""
        drop_keys = {"iconHash", "iconETag", "iconLastModified"}
        cleaned: Dict[str, dict] = {}
        for item_id, item in items.items():
            cleaned[item_id] = {k: v for k, v in item.items() if k not in drop_keys}
        return cleaned

    def _run_pre_hooks(self, name: str) -> None:
        for hook in self._pre_replace_hooks.get(name, ()):
            try:
                hook()
            except Exception as exc:
                self.logger.warning("Pre-replace hook for %s failed: %s", name, exc, exc_info=True)

    def _notify_ui(self, payload: dict) -> None:
        if not self.window_getter:
            return
        try:
            window = self.window_getter()
        except Exception:
            window = None
        if not window:
            return
        try:
            script = (
                "window.handleAssetUpdateStatus && "
                f"window.handleAssetUpdateStatus({json.dumps(payload)});"
            )
            window.evaluate_js(script)
        except Exception as exc:
            self.logger.debug("UI notify failed: %s", exc)
