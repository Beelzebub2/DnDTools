#!/usr/bin/env python3
"""Build an LZMA-compressed WebP icon pack for the application."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable
import zipfile

import PIL.Image

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ICONS_DIR = BASE_DIR / "assets" / "icons"
DEFAULT_OUTPUT_PATH = BASE_DIR / "assets" / "icons.pak"
FIXED_ZIP_DATE = (2024, 1, 1, 0, 0, 0)

logger = logging.getLogger("icon_pak_builder")


def _render_progress(current: int, total: int) -> None:
    if not total:
        return
    bar_length = 30
    filled_length = int(bar_length * current / total)
    bar = "#" * filled_length + "-" * (bar_length - filled_length)
    percent = int((current / total) * 100)
    sys.stdout.write(f"\r[{bar}] {percent:3d}% ({current}/{total})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


def _progress_newline() -> None:
    sys.stdout.write("\n")
    sys.stdout.flush()


@dataclass(frozen=True)
class IconEntry:
    """A single icon resource destined for the pack."""

    relative_key: str
    source_path: Path
    source_format: str  # "png" or "webp"


def _normalise_key(path: Path) -> str:
    return str(path).replace("\\", "/")


def discover_icons(icons_dir: Path) -> Dict[str, IconEntry]:
    """Discover icons to include in the pack.

    PNG sources take precedence so we always regenerate WebP from pristine assets.
    Existing WebP files are used only when no PNG counterpart exists.
    """

    entries: Dict[str, IconEntry] = {}

    if not icons_dir.exists():
        raise FileNotFoundError(f"Icon source directory not found: {icons_dir}")

    png_files = sorted(icons_dir.rglob("*.png"))
    webp_files = sorted(icons_dir.rglob("*.webp"))

    for png in png_files:
        rel_webp = png.relative_to(icons_dir).with_suffix(".webp")
        key = _normalise_key(rel_webp)
        entries[key] = IconEntry(relative_key=key, source_path=png, source_format="png")

    for webp in webp_files:
        rel_webp = webp.relative_to(icons_dir)
        key = _normalise_key(rel_webp)
        # Prefer PNG sources when they exist so we regenerate deterministically.
        if key not in entries:
            entries[key] = IconEntry(relative_key=key, source_path=webp, source_format="webp")

    return entries


def convert_png_to_webp(path: Path, quality: int) -> bytes:
    """Convert a PNG file to WebP bytes using Pillow."""

    with PIL.Image.open(path) as image:
        if image.mode not in {"RGBA", "RGB", "L"}:
            image = image.convert("RGBA")
        elif image.mode == "L":
            image = image.convert("RGBA")

        buffer = io.BytesIO()
        image.save(
            buffer,
            format="WEBP",
            method=6,
            quality=quality,
            lossless=False,
        )
        return buffer.getvalue()


def convert_png_sources(icons_dir: Path, quality: int, *, delete_original: bool = True) -> None:
    """Convert all PNG files to WebP alongside their source directory."""

    png_files = sorted(icons_dir.rglob("*.png"))
    total = len(png_files)
    if total == 0:
        logger.info("No PNG sources found under %s", icons_dir)
        return

    logger.info("Converting %s PNG icons to WebP", total)
    _render_progress(0, total)

    max_workers = min(32, (os.cpu_count() or 1) + 16)

    executor = ThreadPoolExecutor(max_workers=max_workers)
    future_map = {}
    interrupted = False
    try:
        for png_path in png_files:
            future_map[executor.submit(convert_png_to_webp, png_path, quality)] = png_path

        for index, future in enumerate(as_completed(future_map), start=1):
            png_path = future_map[future]
            target_path = png_path.with_suffix(".webp")

            try:
                data = future.result()
            except Exception as exc:  # pragma: no cover - unexpected conversion failure
                logger.error("Failed to convert %s: %s", png_path, exc)
                _render_progress(index, total)
                continue

            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with target_path.open("wb") as handle:
                    handle.write(data)
            except OSError as exc:
                logger.error("Failed to write WebP %s: %s", target_path, exc)
            else:
                if delete_original:
                    try:
                        png_path.unlink()
                    except OSError as exc:
                        logger.warning("Failed to delete original PNG %s: %s", png_path, exc)

            _render_progress(index, total)
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("PNG conversion interrupted by user.")
        for future in future_map:
            future.cancel()
        _progress_newline()
        raise
    finally:
        executor.shutdown(wait=not interrupted, cancel_futures=interrupted)

    logger.info("PNG conversion complete.")


def build_zip(entries: Iterable[IconEntry], output_path: Path, quality: int) -> Dict[str, Dict[str, object]]:
    """Create the LZMA compressed ZIP (.pak) and return a manifest."""

    entries_list = list(entries)
    total = len(entries_list)
    manifest: Dict[str, Dict[str, object]] = {}

    logger.info("Bundling %s icons into %s", total, output_path)

    if total:
        _render_progress(0, total)

    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_LZMA) as archive:

        progress_interval = max(1, total // 10) if total else 1

        try:
            for index, entry in enumerate(sorted(entries_list, key=lambda e: e.relative_key), start=1):
                if entry.source_format == "png":
                    data = convert_png_to_webp(entry.source_path, quality)
                else:
                    data = entry.source_path.read_bytes()

                sha256 = hashlib.sha256(data).hexdigest()
                manifest[entry.relative_key] = {
                    "source": entry.source_format,
                    "sha256": sha256,
                    "size": len(data),
                }

                zip_info = zipfile.ZipInfo(filename=f"icons/{entry.relative_key}", date_time=FIXED_ZIP_DATE)
                zip_info.compress_type = zipfile.ZIP_LZMA
                zip_info.external_attr = 0o644 << 16
                archive.writestr(zip_info, data)

                if total:
                    if index == total or index % progress_interval == 0:
                        _render_progress(index, total)
        except KeyboardInterrupt:
            logger.warning("Packaging interrupted by user.")
            _progress_newline()
            raise

        manifest_info = zipfile.ZipInfo(filename="manifest.json", date_time=FIXED_ZIP_DATE)
        manifest_info.compress_type = zipfile.ZIP_LZMA
        manifest_info.external_attr = 0o644 << 16
        archive.writestr(
            manifest_info,
            json.dumps(
                {
                    "generated_by": "build_icons_pak.py",
                    "quality": quality,
                    "total_icons": len(manifest),
                    "icons": manifest,
                },
                indent=2,
                sort_keys=True,
            ).encode("utf-8"),
        )

    return manifest


def read_existing_manifest(pak_path: Path) -> Dict[str, object] | None:
    """Load the manifest.json from an existing icons.pak archive."""

    if not pak_path.exists():
        return None

    try:
        with zipfile.ZipFile(pak_path, mode="r") as archive:
            with archive.open("manifest.json") as manifest_file:
                return json.load(manifest_file)
    except KeyError:
        logger.error("manifest.json not found inside %s", pak_path)
        return None
    except Exception as exc:
        logger.error("Failed to read manifest from %s: %s", pak_path, exc)
        return None


def files_identical(path_a: Path, path_b: Path) -> bool:
    if not path_a.exists() or not path_b.exists():
        return False

    def _hash(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    return _hash(path_a) == _hash(path_b)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert icons to WebP and bundle them into an LZMA .pak file.")
    parser.add_argument("--icons-dir", type=Path, default=DEFAULT_ICONS_DIR, help="Directory containing icon sources (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Destination .pak file (default: %(default)s)")
    parser.add_argument("--quality", type=int, default=85, help="WebP quality setting (0-100, default: %(default)s)")
    parser.add_argument("--check", action="store_true", help="Only verify that the existing .pak matches freshly generated output")
    parser.add_argument("--skip-convert", action="store_true", help="Skip converting PNG sources to WebP")
    parser.add_argument("--force-convert", action="store_true", help="Force PNG conversion even when running in --check mode")
    return parser.parse_args(argv)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)

    if args.check and not args.force_convert:
        args.skip_convert = True

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger.info("Starting icon pack build...")

    icons_dir = args.icons_dir.resolve()
    output_path = args.output.resolve()

    try:
        if args.skip_convert:
            logger.info("Skipping PNG conversion step")
        else:
            convert_png_sources(icons_dir, args.quality)

        entries = discover_icons(icons_dir)
        logger.info("Discovered %s icons under %s", len(entries), icons_dir)
        if not entries:
            logger.warning("No icons were discovered in %s", icons_dir)

        ensure_parent(output_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_output = Path(tmpdir) / "icons.pak"
            manifest = build_zip(entries.values(), temp_output, args.quality)

            if args.check:
                logger.info("Verifying existing icon pack at %s", output_path)
                existing_manifest = read_existing_manifest(output_path)
                if existing_manifest is None:
                    logger.error("icons.pak is missing or invalid at %s", output_path)
                    return 1

                existing_icons = existing_manifest.get("icons")
                if existing_icons == manifest:
                    logger.info("icons.pak manifest matches existing archive; no update required")
                    return 0

                logger.error("icons.pak manifest differs from the newly generated pack. Run this script locally and commit the updated icons.pak.")
                return 1

            os.replace(temp_output, output_path)
            logger.info("Generated %s with %s icons", output_path, len(manifest))
    except KeyboardInterrupt:
        logger.warning("Icon pack build cancelled by user (Ctrl+C).")
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
