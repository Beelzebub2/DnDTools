#!/usr/bin/env python3
"""Generate the update manifest for DnDTools releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional, Tuple

ROOT_DEFAULT = Path(__file__).resolve().parents[1]
APP_RELATIVE_PATH = Path("UI") / "app.py"
VERSION_JSON = Path("DnDversion.json")
DIST_DIR_NAME = "dist"
MANIFEST_FILENAME = "update-manifest.json"
ENV_VERSION_KEYS = (
    "DNDTOOLS_RELEASE_VERSION",
    "RELEASE_VERSION",
    "GITHUB_RELEASE_VERSION",
)
ENV_CHANNEL_KEYS = (
    "DNDTOOLS_RELEASE_CHANNEL",
    "RELEASE_CHANNEL",
    "GITHUB_RELEASE_CHANNEL",
)


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


def _read_json_version(root: Path) -> Optional[str]:
    json_path = root / VERSION_JSON
    if not json_path.exists():
        return None

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    version = payload.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


def _normalize_version_tag(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ManifestError("Provided version override is empty")
    normalized = cleaned.lstrip("vV")
    return normalized or cleaned


def _resolve_version(root: Path, cli_version: Optional[str]) -> Tuple[str, str]:
    if cli_version:
        return _normalize_version_tag(cli_version), "cli"

    for key in ENV_VERSION_KEYS:
        env_value = os.environ.get(key)
        if env_value and env_value.strip():
            return _normalize_version_tag(env_value), f"env:{key}"

    json_version = _read_json_version(root)
    if json_version:
        return json_version, "json"

    return _read_app_version(root), "app"


def _sha256(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def _normalize_channel(value: Optional[str]) -> str:
    normalized = (value or "stable").strip().lower()
    if normalized in {"dev", "development", "test", "testing", "preview", "prerelease"}:
        return "dev"
    return "stable"


def _build_manifest(version: str, release_tag: str, exe_path: Path, channel: str) -> dict[str, str]:
    return {
        "version": version,
        "notes": f"Release {release_tag}",
        "url": f"https://github.com/Beelzebub2/DnDTools/releases/download/{release_tag}/DnDTools-Setup-{version}.exe",
        "sha256": _sha256(exe_path),
        "release_tag": release_tag,
        "channel": channel,
    }


def _resolve_release_tag(arg_tag: Optional[str], default_version: str) -> str:
    tag = arg_tag or os.environ.get("RELEASE_TAG")
    if tag:
        return tag
    return f"v{default_version}"


def _resolve_channel(
    channel_override: Optional[str],
    release_tag: Optional[str],
) -> Tuple[str, str]:
    if channel_override:
        return _normalize_channel(channel_override), "cli"

    for key in ENV_CHANNEL_KEYS:
        env_value = os.environ.get(key)
        if env_value and env_value.strip():
            return _normalize_channel(env_value), f"env:{key}"

    if release_tag and release_tag.strip().lower().startswith("test-"):
        return "dev", "tag"

    return "stable", "default"


def generate_manifest(
    root: Path,
    release_tag: Optional[str] = None,
    version_override: Optional[str] = None,
    channel_override: Optional[str] = None,
) -> Path:
    root = root.resolve()
    if not root.exists():
        raise ManifestError(f"Root directory does not exist: {root}")

    version, version_source = _resolve_version(root, version_override)
    tag = _resolve_release_tag(release_tag, version)
    channel, channel_source = _resolve_channel(channel_override, tag)

    dist_dir = root / DIST_DIR_NAME
    exe_path = dist_dir / f"DnDTools-Setup-{version}.exe"
    if not exe_path.exists():
        raise ManifestError(f"Installer not found: {exe_path}")

    manifest = _build_manifest(version, tag, exe_path, channel)

    manifest_path = dist_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        "Manifest generated using version {version} (source: {v_source}) and channel {channel} (source: {c_source}).".format(
            version=version,
            v_source=version_source,
            channel=channel,
            c_source=channel_source,
        )
    )
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
    parser.add_argument(
        "--version",
        dest="version",
        default=None,
        help="Explicit installer version (e.g. v3.6.2). Defaults to env/JSON/app.py",
    )
    parser.add_argument(
        "--channel",
        dest="channel",
        default=None,
        help="Release channel (stable/dev). Defaults to env or release tag prefix.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        manifest_path = generate_manifest(
            args.root,
            release_tag=args.release_tag,
            version_override=args.version,
            channel_override=args.channel,
        )
    except ManifestError as exc:
        raise SystemExit(str(exc))

    print(f"Manifest written to: {manifest_path}")


if __name__ == "__main__":
    main()
