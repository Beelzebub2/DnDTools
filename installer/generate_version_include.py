"""Generate Inno Setup version include based on the UI application's version constant."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_FILE = ROOT_DIR / "UI" / "app.py"
VERSION_INCLUDE = Path(__file__).resolve().parent / "version.iss"
VERSION_JSON = ROOT_DIR / "DnDversion.json"
ENV_VERSION_KEYS = ("DNDTOOLS_RELEASE_VERSION", "RELEASE_VERSION", "GITHUB_RELEASE_VERSION")


def extract_app_version() -> str:
    text = APP_FILE.read_text(encoding="utf-8")
    match = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not match:
        raise RuntimeError("Could not locate APP_VERSION constant in UI/app.py")
    return match.group(1)


def normalize_version_tag(raw_value: str) -> str:
    cleaned = (raw_value or "").strip()
    if not cleaned:
        raise ValueError("Provided version override is empty")
    normalized = cleaned.lstrip("vV")
    return normalized or cleaned


def resolve_requested_version(cli_arg: str | None) -> tuple[str, str]:
    version_source = None
    if cli_arg:
        version_source = "CLI"
        candidate = cli_arg
    else:
        candidate = None
        for key in ENV_VERSION_KEYS:
            if key in os.environ and os.environ[key].strip():
                candidate = os.environ[key]
                version_source = f"env:{key}"
                break

    if candidate:
        normalized = normalize_version_tag(candidate)
        return normalized, version_source or "env"

    return extract_app_version(), "app"


def write_version_include(version: str) -> None:
    VERSION_INCLUDE.write_text(f"#define MyAppVersion \"{version}\"\n", encoding="utf-8")


def write_json_manifest(version: str) -> None:
    VERSION_JSON.write_text(
        json.dumps({"version": version}, indent=2) + "\n",
        encoding="utf-8",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate installer version metadata for DnDTools",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version",
        dest="version_override",
        help="Explicit version (e.g. v3.6.1) to use instead of reading UI/app.py",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    version, source = resolve_requested_version(args.version_override)
    write_version_include(version)
    write_json_manifest(version)
    print(f"Synced installer metadata with version {version} (source: {source})")


if __name__ == "__main__":
    main()
