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
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable, Mapping, Optional, Sequence

import requests

DEFAULT_ASSET_MANIFEST_URL = (
    os.environ.get("DND_ASSET_MANIFEST_URL")
    or os.environ.get("DND_ASSET_RELEASE_URL")
    or "https://dndtools.rrmtools.uk/api/assets/manifest.json"
)
MANIFEST_CACHE_FILENAME = ".asset_manifest.json"
PUBLISHED_ASSET_FILENAMES = frozenset({
    "changelog.json",
    "checksums.json",
    "darkerdb_health.json",
    "icons.pak",
    "items.json",
    "quests.json",
})
PUBLISHED_ASSET_MAX_BYTES = {
    "changelog.json": 4 * 1024 * 1024,
    "checksums.json": 4 * 1024 * 1024,
    "darkerdb_health.json": 4 * 1024 * 1024,
    "icons.pak": 512 * 1024 * 1024,
    "items.json": 256 * 1024 * 1024,
    "quests.json": 128 * 1024 * 1024,
}


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

    def _validate_asset_path(self, value: object) -> str:
        """Return a safe published asset basename or reject the manifest entry.

        Asset manifests are remote input.  Keeping the update surface to the
        release's known root-level data files prevents a compromised or
        malformed manifest from writing elsewhere in the application bundle.
        Both separator styles are checked so validation behaves identically on
        Windows and in the Linux release workflow.
        """
        relative_path = str(value or "").strip()
        if not relative_path or "\x00" in relative_path:
            raise RuntimeError("Manifest entry missing a valid path/name")

        portable_path = relative_path.replace("\\", "/")
        path = PurePosixPath(portable_path)
        raw_parts = portable_path.split("/")
        if (
            path.is_absolute()
            or PureWindowsPath(relative_path).is_absolute()
            or any(part in {"", ".", ".."} for part in raw_parts)
            or len(path.parts) != 1
        ):
            raise RuntimeError(f"Unsafe asset path in manifest: {relative_path!r}")

        basename = path.name
        if basename not in PUBLISHED_ASSET_FILENAMES:
            raise RuntimeError(f"Manifest requested unsupported asset: {basename!r}")

        assets_root = self.assets_dir.resolve()
        target = (assets_root / basename).resolve()
        try:
            target.relative_to(assets_root)
        except ValueError as exc:  # pragma: no cover - allowlist is also a guard
            raise RuntimeError(f"Asset target escapes assets directory: {relative_path!r}") from exc
        return basename

    def _extract_manifest_files(self, manifest: dict) -> list[dict]:
        files = manifest.get("files")
        if not isinstance(files, list):
            return []
        validated: list[dict] = []
        seen_paths: set[str] = set()
        for file_info in files:
            if not isinstance(file_info, dict) or not file_info.get("url"):
                continue
            relative_path = self._validate_asset_path(
                file_info.get("path") or file_info.get("name")
            )
            if relative_path in seen_paths:
                raise RuntimeError(f"Manifest contains duplicate asset path: {relative_path}")
            seen_paths.add(relative_path)
            normalized = dict(file_info)
            normalized["path"] = relative_path
            expected_sha, expected_size = self._validate_asset_metadata(
                relative_path,
                normalized,
            )
            normalized["sha256"] = expected_sha
            normalized["size"] = expected_size
            validated.append(normalized)
        return validated

    def _validate_asset_metadata(self, relative_path: str, file_info: Mapping) -> tuple[str, int]:
        expected_sha = str(file_info.get("sha256") or "").strip().lower()
        if len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
            raise RuntimeError(f"Manifest entry for {relative_path} has an invalid SHA-256")

        raw_size = file_info.get("size")
        if isinstance(raw_size, bool):
            raise RuntimeError(f"Manifest entry for {relative_path} has an invalid size")
        try:
            expected_size = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Manifest entry for {relative_path} has an invalid size") from exc
        max_size = PUBLISHED_ASSET_MAX_BYTES[relative_path]
        if expected_size <= 0 or expected_size > max_size:
            raise RuntimeError(
                f"Manifest entry for {relative_path} has size {expected_size}; "
                f"allowed range is 1..{max_size} bytes"
            )
        return expected_sha, expected_size

    def _download_asset(self, file_info: dict) -> tuple[str, Path]:
        relative_path = self._validate_asset_path(
            file_info.get("path") or file_info.get("name")
        )
        url = str(file_info.get("url") or "").strip()
        if not url:
            raise RuntimeError(f"Manifest entry for {relative_path} missing download URL")

        expected_sha, expected_size = self._validate_asset_metadata(relative_path, file_info)
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
                downloaded_size = 0
                with os.fdopen(fd, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        downloaded_size += len(chunk)
                        if downloaded_size > expected_size:
                            raise RuntimeError(
                                f"Size mismatch for {relative_path}: expected {expected_size}, "
                                f"download exceeded {downloaded_size} bytes"
                            )
                        handle.write(chunk)
                        digest.update(chunk)
            if downloaded_size != expected_size:
                raise RuntimeError(
                    f"Size mismatch for {relative_path}: expected {expected_size}, got {downloaded_size}"
                )
            if digest.hexdigest() != expected_sha:
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
        assets_root = self.assets_dir.resolve()
        prepared: list[tuple[str, Path, Path]] = []
        seen_paths: set[str] = set()

        # Validate the complete batch before running hooks or touching a file.
        # _download_asset() already validates remote input, but this boundary
        # is intentionally defensive because tests and future callers can pass
        # download tuples directly.
        for relative_path, tmp_path in downloads:
            relative_path = self._validate_asset_path(relative_path)
            if relative_path in seen_paths:
                raise RuntimeError(f"Asset batch contains duplicate path: {relative_path}")
            seen_paths.add(relative_path)
            prepared.append((relative_path, Path(tmp_path), assets_root / relative_path))

        # (name, target, existed_before, backup). Backups are retained until
        # every replacement succeeds, making a manifest update transactional
        # rather than a sequence of independently committed file updates.
        applied: list[tuple[str, Path, bool, Optional[Path]]] = []
        try:
            for relative_path, tmp_path, target in prepared:
                self._run_pre_replace_hooks(relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target_existed = target.exists()
                backup: Optional[Path] = None

                if target_existed:
                    # Reserve a unique same-directory name. A fixed `.bak`
                    # path could overwrite a recoverable backup left by an
                    # interrupted previous update.
                    fd, backup_name = tempfile.mkstemp(
                        prefix=f".{target.name}.dndtools-",
                        suffix=".bak",
                        dir=str(target.parent),
                    )
                    os.close(fd)
                    backup = Path(backup_name)
                    try:
                        # Keep the canonical asset readable until the new
                        # file is atomically promoted. Renaming it to the
                        # rollback path first created a crash window where
                        # the application could observe no canonical asset.
                        shutil.copy2(target, backup)
                    except Exception:
                        try:
                            backup.unlink(missing_ok=True)  # type: ignore[arg-type]
                        except OSError:
                            pass
                        raise

                # Register this target before replacement because a copy
                # fallback can leave a partial destination and then fail.
                applied.append((relative_path, target, target_existed, backup))
                self._replace_file(tmp_path, target)
        except Exception as exc:
            rollback_errors: list[str] = []
            for relative_path, target, target_existed, backup in reversed(applied):
                try:
                    if target_existed:
                        if backup is None or not backup.exists():
                            raise OSError("rollback backup is missing")
                        # Path.replace uses atomic overwrite semantics on the
                        # same volume, removing any partial/new target.
                        backup.replace(target)
                    else:
                        target.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError as rollback_exc:
                    rollback_errors.append(f"{relative_path}: {rollback_exc}")
                    self.logger.error(
                        "Failed to roll back asset %s: %s",
                        relative_path,
                        rollback_exc,
                        exc_info=True,
                    )

            detail = f"Failed to apply asset batch: {exc}"
            if rollback_errors:
                detail += "; rollback also failed for " + "; ".join(rollback_errors)
            raise RuntimeError(detail) from exc

        # Commit the batch only after every target has been replaced. Backup
        # cleanup is best effort: a stale hidden backup is preferable to
        # reporting a failed update after all canonical files are valid.
        for _, _, _, backup in applied:
            if backup is None:
                continue
            try:
                backup.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError as exc:
                self.logger.warning("Unable to remove asset rollback file %s: %s", backup, exc)

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

        # --- Attempt 1-N: atomic same-volume promotion ---
        for attempt in range(1, attempts + 1):
            try:
                os.replace(tmp_path, target)
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

        # --- Fallback: copy to a same-directory stage, then atomically promote ---
        # A direct copy into target can expose a truncated/partial canonical
        # asset. This fallback handles cross-volume temp files without ever
        # writing non-atomically to the published path.
        self.logger.info(
            "Atomic promotion failed for %s; staging a local copy (%s)",
            target.name,
            last_error,
        )
        fd, local_name = tempfile.mkstemp(
            prefix=f".{target.name}.dndtools-",
            suffix=".tmp",
            dir=str(target.parent),
        )
        os.close(fd)
        local_stage = Path(local_name)
        try:
            shutil.copy2(tmp_path, local_stage)
            promotion_error: Optional[Exception] = None
            for attempt in range(1, attempts + 1):
                try:
                    os.replace(local_stage, target)
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    return
                except PermissionError as exc:
                    promotion_error = exc
                    delay = min(0.35 * attempt, 1.0)
                    self.logger.warning(
                        "Local promotion permission error for %s (attempt %s/%s): %s",
                        target.name,
                        attempt,
                        attempts,
                        exc,
                    )
                    time.sleep(delay)
                except OSError as exc:
                    promotion_error = exc
                    break
            raise promotion_error or last_error  # type: ignore[misc]
        finally:
            try:
                local_stage.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass
