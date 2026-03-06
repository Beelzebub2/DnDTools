from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Sequence

import requests

DEFAULT_ASSET_MANIFEST_URL = (
    os.environ.get("DND_ASSET_MANIFEST_URL")
    or os.environ.get("DND_ASSET_RELEASE_URL")
    or "https://dndtools.rrmtools.uk/api/assets/manifest.json"
)
MANIFEST_CACHE_FILENAME = ".asset_manifest.json"


class AssetUpdater:
    """Download and install runtime asset updates via the Update & Release API."""

    def __init__(
        self,
        assets_dir: Path,
        logger: Optional[logging.Logger] = None,
        window_getter: Optional[Callable[[], Optional[object]]] = None,
        on_assets_applied: Optional[Iterable[Callable[[dict], None]]] = None,
        before_asset_replace: Optional[Mapping[str, Iterable[Callable[[], None]]]] = None,
        base_url: Optional[str] = None,
        manifest_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.assets_dir = Path(assets_dir)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)
        self.window_getter = window_getter
        resolved_manifest_url = manifest_url or base_url or DEFAULT_ASSET_MANIFEST_URL
        self.manifest_url = str(resolved_manifest_url).strip()
        if not self.manifest_url:
            raise ValueError("Manifest URL must not be empty")
        if base_url and not manifest_url:
            self.logger.debug("AssetUpdater base_url parameter is deprecated; using it as manifest URL")
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "DnDTools-AssetUpdater/1.0")
        self._hooks: tuple[Callable[[dict], None], ...] = tuple(on_assets_applied or [])
        self._pre_replace_hooks: dict[str, tuple[Callable[[], None], ...]] = {
            str(name): tuple(callbacks)
            for name, callbacks in (before_asset_replace or {}).items()
            if callbacks
        }
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start_async_update(self) -> bool:
        """Start the asset refresh worker if it is not already running."""
        with self._lock:
            if self._worker and self._worker.is_alive():
                self.logger.debug("Asset updater already running; skipping duplicate request")
                return False

            worker = threading.Thread(target=self._run_update, name="AssetUpdater", daemon=True)
            worker.start()
            self._worker = worker
            return True

    # ------------------------------------------------------------------
    # Worker logic
    # ------------------------------------------------------------------
    def _run_update(self) -> None:
        downloads: list[tuple[str, Path]] = []
        try:
            self._notify_ui({
                "status": "checking",
                "message": "Checking for new DarkerDB assets...",
            })

            manifest = self._download_manifest()
            if not manifest:
                self._notify_ui({
                    "status": "error",
                    "message": "Unable to reach the assets feed.",
                    "allowDismiss": True,
                })
                return

            if not self._needs_update(manifest):
                self.logger.info(
                    "Assets already up to date (version=%s release=%s)",
                    manifest.get("version"),
                    manifest.get("release_tag"),
                )
                self._notify_ui({
                    "status": "idle",
                    "message": "Assets are already up to date.",
                    "autoDismiss": True,
                })
                return

            files = self._extract_manifest_files(manifest)
            if not files:
                self.logger.warning("Manifest did not contain any downloadable files")
                self._notify_ui({
                    "status": "error",
                    "message": "Manifest did not include any assets to download.",
                    "allowDismiss": True,
                })
                return

            total = len(files)
            self._notify_ui({
                "status": "downloading",
                "message": f"Downloading {total} asset(s)...",
                "progress": 0,
            })

            max_workers = min(total, 4)
            completed_count = 0
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(self._download_asset, file_info): file_info
                    for file_info in files
                }
                for future in as_completed(futures):
                    file_info = futures[future]
                    display_name = file_info.get("path") or file_info.get("name") or "asset"
                    result = future.result()
                    downloads.append(result)
                    completed_count += 1
                    self.logger.info("Downloaded asset %s (%d/%d)", display_name, completed_count, total)
                    self._notify_ui({
                        "status": "downloading",
                        "message": f"Downloaded {display_name} ({completed_count}/{total})...",
                        "progress": completed_count / total,
                    })

            self._apply_assets(downloads)
            self._cache_manifest(manifest)
            self.logger.info(
                "Asset refresh complete: version=%s release=%s",
                manifest.get("version"),
                manifest.get("release_tag"),
            )

            for hook in self._hooks:
                try:
                    hook(manifest)
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.warning("Post-asset hook failed: %s", exc, exc_info=True)

            self._notify_ui({
                "status": "success",
                "message": "Assets updated. Restart to apply everywhere?",
                "promptRestart": True,
                "metadata": manifest,
            })
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Asset update failed: %s", exc, exc_info=True)
            self._notify_ui({
                "status": "error",
                "message": f"Asset update failed: {exc}",
                "allowDismiss": True,
            })
        finally:
            for _, tmp in downloads:
                try:
                    tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _download_manifest(self) -> Optional[dict]:
        try:
            with self.session.get(self.manifest_url, timeout=15) as response:
                response.raise_for_status()
                payload = response.json()
        except requests.RequestException as exc:
            self.logger.warning("Failed to download manifest from %s: %s", self.manifest_url, exc)
            return None
        except ValueError as exc:
            self.logger.warning("Manifest at %s is not valid JSON: %s", self.manifest_url, exc)
            return None

        if not isinstance(payload, dict):
            self.logger.warning("Manifest at %s is not a JSON object", self.manifest_url)
            return None
        return payload

    def _manifest_cache_path(self) -> Path:
        return self.assets_dir / MANIFEST_CACHE_FILENAME

    def _read_cached_manifest(self) -> Optional[dict]:
        cache_path = self._manifest_cache_path()
        try:
            with cache_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else None
        except FileNotFoundError:
            return None
        except ValueError:
            return None

    def _cache_manifest(self, manifest: dict) -> None:
        cache_path = self._manifest_cache_path()
        try:
            with cache_path.open("w", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2, sort_keys=True)
        except OSError as exc:
            self.logger.debug("Unable to write manifest cache: %s", exc)

    def _manifest_identity(self, manifest: Optional[dict]) -> tuple[str, str, str]:
        if not isinstance(manifest, dict):
            return ("", "", "")

        def _coerce(value: Optional[object]) -> str:
            return str(value or "").strip()

        return (
            _coerce(manifest.get("version")),
            _coerce(manifest.get("release_tag")),
            _coerce(manifest.get("generated_at")),
        )

    def _needs_update(self, remote_manifest: dict) -> bool:
        local_manifest = self._read_cached_manifest()
        return self._manifest_identity(local_manifest) != self._manifest_identity(remote_manifest)

    def _extract_manifest_files(self, manifest: dict) -> list[dict]:
        files = manifest.get("files")
        if not isinstance(files, list):
            return []
        return [file_info for file_info in files if isinstance(file_info, dict) and file_info.get("url")]

    def _download_asset(self, file_info: dict) -> tuple[str, Path]:
        relative_path = str(file_info.get("path") or file_info.get("name") or "").strip()
        url = str(file_info.get("url") or "").strip()
        if not relative_path:
            raise RuntimeError("Manifest entry missing path/name")
        if not url:
            raise RuntimeError(f"Manifest entry for {relative_path} missing download URL")

        expected_sha = str(file_info.get("sha256") or "").strip().lower()
        safe_name = relative_path.replace(os.sep, "-").replace("/", "-")
        suffix = f"-{safe_name}.tmp" if safe_name else ".tmp"

        # Create the temp file inside assets_dir so it lives on the same
        # drive / volume as the final target.  This lets Path.replace() work
        # as an atomic same-filesystem rename and avoids cross-drive and
        # Windows ACL problems (e.g. %TEMP% → "C:\Program Files\…").
        try:
            self.assets_dir.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                prefix="dnd-asset-", suffix=suffix, dir=str(self.assets_dir)
            )
        except OSError:
            # Fall back to system temp if assets_dir is not writable yet
            fd, temp_path = tempfile.mkstemp(prefix="dnd-asset-", suffix=suffix)
        path = Path(temp_path)

        try:
            with self.session.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                digest = hashlib.sha256()
                with os.fdopen(fd, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        if expected_sha:
                            digest.update(chunk)
            if expected_sha and digest.hexdigest() != expected_sha:
                raise RuntimeError(
                    f"Checksum mismatch for {relative_path}: expected {expected_sha}, got {digest.hexdigest()}"
                )
        except requests.RequestException as exc:
            try:
                path.unlink()
            except OSError:
                pass
            raise RuntimeError(f"Failed to download {relative_path}: {exc}") from exc
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return (relative_path, path)

    def _apply_assets(self, downloads: Sequence[tuple[str, Path]]) -> None:
        for relative_path, tmp_path in downloads:
            basename = Path(relative_path).name
            self._run_pre_replace_hooks(relative_path)
            if basename != relative_path:
                self._run_pre_replace_hooks(basename)
            target = self.assets_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Optional[Path] = None
            try:
                if target.exists():
                    backup = target.with_suffix(".bak")
                    try:
                        if backup.exists():
                            backup.unlink()
                        target.rename(backup)
                    except OSError:
                        backup = None
                self._replace_file(tmp_path, target)
                if backup:
                    try:
                        backup.unlink()
                    except OSError:
                        pass
            except Exception as exc:
                raise RuntimeError(f"Failed to replace {relative_path}: {exc}") from exc

    def _notify_ui(self, payload: dict) -> None:
        if not self.window_getter:
            return
        window = None
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
        except Exception as exc:  # pragma: no cover - UI best effort
            self.logger.debug("Unable to push asset update status to UI: %s", exc)

    def _run_pre_replace_hooks(self, name: str) -> None:
        hooks = self._pre_replace_hooks.get(name)
        if not hooks:
            return
        for hook in hooks:
            try:
                hook()
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("Pre-replace hook for %s failed: %s", name, exc, exc_info=True)

    def _replace_file(self, tmp_path: Path, target: Path) -> None:
        attempts = 3
        last_error: Optional[Exception] = None

        # --- Attempt 1-N: atomic rename (fastest, preserves permissions) ---
        for attempt in range(1, attempts + 1):
            try:
                tmp_path.replace(target)
                return
            except PermissionError as exc:
                last_error = exc
                delay = min(0.35 * attempt, 1.0)
                self.logger.warning(
                    "Permission error replacing %s (attempt %s/%s): %s",
                    target.name,
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(delay)
            except (FileNotFoundError, OSError) as exc:
                # OSError covers cross-drive renames and other filesystem
                # errors that an atomic rename cannot handle.
                last_error = exc
                break

        # --- Fallback: copy-then-delete ---
        # Handles cross-drive moves and Windows ACL scenarios where the
        # temp file cannot be *renamed* into the target directory but
        # writing to the target file via a normal copy still succeeds.
        self.logger.info(
            "Atomic rename failed for %s; falling back to copy (%s)",
            target.name,
            last_error,
        )
        copy_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                shutil.copy2(str(tmp_path), str(target))
                # Copy succeeded – remove the temp file
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
                return
            except PermissionError as exc:
                copy_error = exc
                delay = min(0.35 * attempt, 1.0)
                self.logger.warning(
                    "Copy fallback permission error for %s (attempt %s/%s): %s",
                    target.name,
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(delay)
            except OSError as exc:
                copy_error = exc
                break

        # Both strategies exhausted – raise the most relevant error
        raise copy_error or last_error  # type: ignore[misc]