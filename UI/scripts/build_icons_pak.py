#!/usr/bin/env python3
"""
Build an LZMA-compressed WebP icon pack (``icons.pak``) for the application.

This is the standalone CLI entrypoint.  The same packing logic also lives
inside ``AssetUpdater._build_icons_pak()`` for runtime use.

Usage:
    python build_icons_pak.py                 # default build
    python build_icons_pak.py --check         # verify existing pak matches
    python build_icons_pak.py --skip-convert  # skip PNG→WebP conversion
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

import PIL.Image

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ICONS_DIR = BASE_DIR / "assets" / "icons"
DEFAULT_OUTPUT_PATH = BASE_DIR / "assets" / "icons.pak"
FIXED_ZIP_DATE = (2024, 1, 1, 0, 0, 0)

logger = logging.getLogger("icon_pak_builder")


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

def _render_progress(current: int, total: int) -> None:
    if not total:
        return
    bar_length = 30
    filled = int(bar_length * current / total)
    bar = "#" * filled + "-" * (bar_length - filled)
    pct = int(100 * current / total)
    sys.stdout.write(f"\r[{bar}] {pct:3d}% ({current}/{total})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IconEntry:
    """A single icon destined for the pack."""
    relative_key: str
    source_path: Path
    source_format: str  # "png" or "webp"


def _normalise_key(path: Path) -> str:
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_icons(icons_dir: Path) -> Dict[str, IconEntry]:
    """Find all icons under *icons_dir*.

    PNG sources take precedence so we always regenerate WebP from pristine
    assets.  Existing WebP files are used only when no PNG counterpart exists.
    """
    entries: Dict[str, IconEntry] = {}
    if not icons_dir.exists():
        raise FileNotFoundError(f"Icon source directory not found: {icons_dir}")

    for png in sorted(icons_dir.rglob("*.png")):
        rel_webp = png.relative_to(icons_dir).with_suffix(".webp")
        key = _normalise_key(rel_webp)
        entries[key] = IconEntry(relative_key=key, source_path=png, source_format="png")

    for webp in sorted(icons_dir.rglob("*.webp")):
        rel = webp.relative_to(icons_dir)
        key = _normalise_key(rel)
        if key not in entries:  # prefer PNG when both exist
            entries[key] = IconEntry(relative_key=key, source_path=webp, source_format="webp")

    return entries


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def convert_png_to_webp(path: Path, quality: int) -> bytes:
    """Convert a PNG file to WebP bytes using Pillow."""
    with PIL.Image.open(path) as image:
        if image.mode not in {"RGBA", "RGB", "L"}:
            image = image.convert("RGBA")
        elif image.mode == "L":
            image = image.convert("RGBA")
        buf = io.BytesIO()
        image.save(buf, format="WEBP", method=6, quality=quality, lossless=False)
        return buf.getvalue()


def convert_png_sources(icons_dir: Path, quality: int, *, delete_original: bool = True) -> None:
    """Convert all PNG files to WebP alongside their source directory."""
    png_files = sorted(icons_dir.rglob("*.png"))
    total = len(png_files)
    if total == 0:
        logger.info("No PNG sources found under %s", icons_dir)
        return

    logger.info("Converting %s PNG icons to WebP", total)
    _render_progress(0, total)

    workers = min(32, (os.cpu_count() or 1) + 16)
    executor = ThreadPoolExecutor(max_workers=workers)
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
            except Exception as exc:
                logger.error("Failed to convert %s: %s", png_path, exc)
                _render_progress(index, total)
                continue
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(data)
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
        sys.stdout.write("\n")
        raise
    finally:
        executor.shutdown(wait=not interrupted, cancel_futures=interrupted)

    logger.info("PNG conversion complete.")


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------

def build_zip(entries: Iterable[IconEntry], output_path: Path, quality: int) -> Dict[str, Dict[str, object]]:
    """Create the LZMA compressed ZIP (``.pak``) and return a manifest."""
    entries_list = list(entries)
    total = len(entries_list)
    manifest: Dict[str, Dict[str, object]] = {}

    logger.info("Bundling %s icons into %s", total, output_path)
    if total:
        _render_progress(0, total)

    interval = max(1, total // 10) if total else 1

    with zipfile.ZipFile(output_path, mode="w", compression=zipfile.ZIP_LZMA) as archive:
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

                zi = zipfile.ZipInfo(filename=f"icons/{entry.relative_key}", date_time=FIXED_ZIP_DATE)
                zi.compress_type = zipfile.ZIP_LZMA
                zi.external_attr = 0o644 << 16
                archive.writestr(zi, data)

                if total and (index == total or index % interval == 0):
                    _render_progress(index, total)
        except KeyboardInterrupt:
            logger.warning("Packaging interrupted by user.")
            sys.stdout.write("\n")
            raise

        mi = zipfile.ZipInfo(filename="manifest.json", date_time=FIXED_ZIP_DATE)
        mi.compress_type = zipfile.ZIP_LZMA
        mi.external_attr = 0o644 << 16
        archive.writestr(mi, json.dumps({
            "generated_by": "build_icons_pak.py",
            "quality": quality,
            "total_icons": len(manifest),
            "icons": manifest,
        }, indent=2, sort_keys=True).encode("utf-8"))

    return manifest


def read_existing_manifest(pak_path: Path) -> Dict[str, object] | None:
    """Load the ``manifest.json`` from an existing ``icons.pak``."""
    if not pak_path.exists():
        return None
    try:
        with zipfile.ZipFile(pak_path, mode="r") as archive:
            with archive.open("manifest.json") as f:
                return json.load(f)
    except (KeyError, Exception) as exc:
        logger.error("Failed to read manifest from %s: %s", pak_path, exc)
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert icons to WebP and bundle them into an LZMA .pak file.",
    )
    parser.add_argument("--icons-dir", type=Path, default=DEFAULT_ICONS_DIR,
                        help="Directory containing icon sources (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH,
                        help="Destination .pak file (default: %(default)s)")
    parser.add_argument("--quality", type=int, default=85,
                        help="WebP quality setting 0-100 (default: %(default)s)")
    parser.add_argument("--check", action="store_true",
                        help="Only verify that the existing .pak matches freshly generated output")
    parser.add_argument("--skip-convert", action="store_true",
                        help="Skip converting PNG sources to WebP")
    parser.add_argument("--force-convert", action="store_true",
                        help="Force PNG conversion even when running in --check mode")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)

    if args.check and not args.force_convert:
        args.skip_convert = True

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    logger.info("Starting icon pack build…")

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

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=output_path.parent) as tmpdir:
            temp_output = Path(tmpdir) / "icons.pak"
            manifest = build_zip(entries.values(), temp_output, args.quality)

            if args.check:
                logger.info("Verifying existing icon pack at %s", output_path)
                existing = read_existing_manifest(output_path)
                if existing is None:
                    logger.error("icons.pak is missing or invalid at %s", output_path)
                    return 1
                if existing.get("icons") == manifest:
                    logger.info("icons.pak manifest matches — no update required")
                    return 0
                logger.error("icons.pak manifest differs. Run this script locally and commit the updated icons.pak.")
                return 1

            os.replace(temp_output, output_path)
            logger.info("Generated %s with %s icons", output_path, len(manifest))
    except KeyboardInterrupt:
        logger.warning("Build cancelled by user (Ctrl+C).")
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
