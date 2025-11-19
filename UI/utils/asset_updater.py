from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Sequence

import requests

DEFAULT_ASSET_RELEASE_URL = os.environ.get(
    "DND_ASSET_RELEASE_URL",
    "https://github.com/Beelzebub2/DnDTools/releases/download/assets-latest",
)


class AssetUpdater:
    """Download and install runtime asset updates from the assets-latest release."""

    def __init__(
        self,
        assets_dir: Path,
        logger: Optional[logging.Logger] = None,
        window_getter: Optional[Callable[[], Optional[object]]] = None,
        on_assets_applied: Optional[Iterable[Callable[[dict], None]]] = None,
        before_asset_replace: Optional[Mapping[str, Iterable[Callable[[], None]]]] = None,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.assets_dir = Path(assets_dir)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)
        self.window_getter = window_getter
        self.base_url = (base_url or DEFAULT_ASSET_RELEASE_URL).rstrip("/")
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

            remote_meta = self._download_metadata()
            if not remote_meta:
                self._notify_ui({
                    "status": "error",
                    "message": "Unable to reach the assets feed.",
                    "allowDismiss": True,
                })
                return

            if not self._needs_update(remote_meta):
                self.logger.info("Assets already up to date (build %s)", remote_meta.get("build"))
                self._notify_ui({
                    "status": "idle",
                    "message": "Assets are already up to date.",
                    "autoDismiss": True,
                })
                return

            asset_names = (
                "items.json",
                "icons.pak",
                "changelog.json",
                "darkerdb_health.json",
            )
            total = len(asset_names)

            for index, name in enumerate(asset_names, start=1):
                url = f"{self.base_url}/{name}"
                self.logger.info("Downloading asset %s from %s", name, url)
                self._notify_ui({
                    "status": "downloading",
                    "message": f"Downloading {name} ({index}/{total})...",
                    "progress": index / total,
                })
                tmp_path = self._download_asset(url, name)
                downloads.append((name, tmp_path))

            self._apply_assets(downloads)
            self.logger.info("Asset refresh complete: build %s patch %s", remote_meta.get("build"), remote_meta.get("patch"))

            for hook in self._hooks:
                try:
                    hook(remote_meta)
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.warning("Post-asset hook failed: %s", exc, exc_info=True)

            self._notify_ui({
                "status": "success",
                "message": "Assets updated. Restart to apply everywhere?",
                "promptRestart": True,
                "metadata": remote_meta,
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
    def _download_metadata(self) -> Optional[dict]:
        meta_url = f"{self.base_url}/darkerdb_health.json"
        try:
            with self.session.get(meta_url, timeout=15) as response:
                response.raise_for_status()
                payload = response.json()
            if isinstance(payload, dict):
                return payload
            self.logger.warning("Metadata at %s is not a JSON object", meta_url)
        except requests.RequestException as exc:
            self.logger.warning("Failed to download metadata from %s: %s", meta_url, exc)
        except ValueError as exc:
            self.logger.warning("Metadata at %s is not valid JSON: %s", meta_url, exc)
        return None

    def _read_local_metadata(self) -> Optional[dict]:
        local_path = self.assets_dir / "darkerdb_health.json"
        try:
            with local_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else None
        except FileNotFoundError:
            return None
        except ValueError:
            return None

    def _needs_update(self, remote_meta: dict) -> bool:
        local_meta = self._read_local_metadata()
        if not local_meta:
            return True

        def _coerce(value):
            return (str(value) if value is not None else None)

        remote_version = (_coerce(remote_meta.get("build")), _coerce(remote_meta.get("patch")))
        local_version = (_coerce(local_meta.get("build")), _coerce(local_meta.get("patch")))
        return remote_version != local_version

    def _download_asset(self, url: str, name: str) -> Path:
        fd, temp_path = tempfile.mkstemp(prefix="dnd-asset-", suffix=f"-{name}.tmp")
        path = Path(temp_path)
        try:
            with self.session.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                with os.fdopen(fd, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)
        except requests.RequestException as exc:
            try:
                path.unlink()
            except OSError:
                pass
            raise RuntimeError(f"Failed to download {name}: {exc}") from exc
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return path

    def _apply_assets(self, downloads: Sequence[tuple[str, Path]]) -> None:
        for name, tmp_path in downloads:
            self._run_pre_replace_hooks(name)
            target = self.assets_dir / name
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
                raise RuntimeError(f"Failed to replace {name}: {exc}") from exc

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
            except FileNotFoundError as exc:
                last_error = exc
                break
        if last_error:
            raise last_error