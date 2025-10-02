#!/usr/bin/env python3
"""Generate the update manifest for DnDTools releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

ROOT_DEFAULT = Path(__file__).resolve().parents[1]
APP_RELATIVE_PATH = Path("UI") / "app.py"
DIST_DIR_NAME = "dist"
MANIFEST_FILENAME = "update-manifest.json"


class ManifestError(RuntimeError):
    """Raised when the manifest cannot be generated."""


def _read_app_version(root: Path) -> str:
    app_path = root / APP_RELATIVE_PATH
    if not app_path.exists():
        raise ManifestError(f"app.py not found at expected path: {app_path}")

    text = app_path.read_text(encoding="utf-8")
    match = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not match:
        raise ManifestError("Unable to determine APP_VERSION from app.py")

    return match.group(1)


def _sha256(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def _build_manifest(version: str, release_tag: str, exe_path: Path) -> dict[str, str]:
    return {
        "version": version,
        "notes": f"Release {release_tag}",
        "url": f"https://github.com/Beelzebub2/DnDTools/releases/download/{release_tag}/DnDTools-Setup-{version}.exe",
        "sha256": _sha256(exe_path),
    }


def _resolve_release_tag(arg_tag: Optional[str], default_version: str) -> str:
    tag = arg_tag or os.environ.get("RELEASE_TAG")
    if tag:
        return tag
    return f"v{default_version}"


def generate_manifest(root: Path, release_tag: Optional[str] = None) -> Path:
    root = root.resolve()
    if not root.exists():
        raise ManifestError(f"Root directory does not exist: {root}")

    version = _read_app_version(root)
    tag = _resolve_release_tag(release_tag, version)

    dist_dir = root / DIST_DIR_NAME
    exe_path = dist_dir / f"DnDTools-Setup-{version}.exe"
    if not exe_path.exists():
        raise ManifestError(f"Installer not found: {exe_path}")

    manifest = _build_manifest(version, tag, exe_path)

    manifest_path = dist_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT_DEFAULT,
        help="Project root directory (defaults to repository root)",
    )
    parser.add_argument(
        "--release-tag",
        dest="release_tag",
        default=None,
        help="Release tag to embed in the manifest (defaults to RELEASE_TAG env var or v<version>)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        manifest_path = generate_manifest(args.root, args.release_tag)
    except ManifestError as exc:
        raise SystemExit(str(exc))

    print(f"Manifest written to: {manifest_path}")


if __name__ == "__main__":
    main()
