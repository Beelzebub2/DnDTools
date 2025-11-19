from __future__ import annotations

import io
import logging
import threading
import zipfile
from pathlib import Path
from typing import Optional, Union

from .appdirs import resource_path

logger = logging.getLogger(__name__)


def canonical_icon_path(path: Union[str, Path, None]) -> Optional[str]:
    """Normalise icon paths to use forward slashes, icons/ prefix, and .webp extension."""
    if not path:
        return None

    path_obj = Path(str(path))

    parts = list(path_obj.parts)
    if parts and parts[0].lower() in {".", "assets"}:
        path_obj = Path(*parts[1:]) if len(parts) > 1 else Path()
        parts = list(path_obj.parts)

    if path_obj.suffix.lower() != ".webp":
        path_obj = path_obj.with_suffix(".webp")

    if not path_obj.parts:
        return None

    if path_obj.parts[0].lower() != "icons":
        path_obj = Path("icons") / Path(*path_obj.parts)

    return str(path_obj).replace("\\", "/")


class IconPakStore:
    """Lazy loader for icons stored in an LZMA compressed .pak archive."""

    def __init__(self, pak_filename: str = "icons.pak") -> None:
        self._pak_filename = pak_filename
        self._lock = threading.RLock()
        self._zip: Optional[zipfile.ZipFile] = None
        self._manifest: set[str] = set()
        self._cache: dict[str, bytes] = {}
        self._pak_mtime: Optional[float] = None

    @property
    def pak_path(self) -> Path:
        return Path(resource_path(self._pak_filename))

    def _refresh_archive(self) -> Optional[zipfile.ZipFile]:
        pak_path = self.pak_path
        if not pak_path.exists():
            logger.warning("icons.pak not found at %s", pak_path)
            return None

        mtime = pak_path.stat().st_mtime
        if self._zip and self._pak_mtime == mtime:
            return self._zip

        if self._zip:
            try:
                self._zip.close()
            except Exception:  # pragma: no cover - defensive
                pass

        self._zip = zipfile.ZipFile(pak_path, mode="r")
        self._pak_mtime = mtime
        self._manifest = set(self._zip.namelist())
        self._cache.clear()
        logger.info("Loaded icons.pak with %s entries", len(self._manifest))
        return self._zip

    def _normalise(self, filename: str) -> Optional[str]:
        canonical = canonical_icon_path(filename)
        if not canonical:
            return None
        return canonical

    def get_bytes(self, filename: str) -> Optional[bytes]:
        """Retrieve an icon as raw bytes."""
        key = self._normalise(filename)
        if not key:
            return None

        with self._lock:
            archive = self._refresh_archive()
            if not archive:
                return None

            cached = self._cache.get(key)
            if cached is not None:
                return cached

            if key not in self._manifest:
                return None

            data = archive.read(key)
            self._cache[key] = data
            return data

    def exists(self, filename: str) -> bool:
        key = self._normalise(filename)
        if not key:
            return False

        with self._lock:
            archive = self._refresh_archive()
            if not archive:
                return False
            return key in self._manifest

    def stream(self, filename: str):
        """Return a BytesIO stream for Flask's send_file helper."""
        data = self.get_bytes(filename)
        if data is None:
            return None
        return io.BytesIO(data)

    def invalidate_cache(self) -> None:
        """Release any open archive handles so external updaters can replace the pak file."""
        with self._lock:
            if self._zip:
                try:
                    self._zip.close()
                except Exception:  # pragma: no cover - defensive
                    pass
            self._zip = None
            self._manifest.clear()
            self._cache.clear()
            self._pak_mtime = None


icon_store = IconPakStore()
