"""Generate Inno Setup version include based on the UI application's version constant."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_FILE = ROOT_DIR / "UI" / "app.py"
VERSION_INCLUDE = Path(__file__).resolve().parent / "version.iss"
VERSION_JSON = ROOT_DIR / "DnDversion.json"


def extract_app_version() -> str:
    text = APP_FILE.read_text(encoding="utf-8")
    match = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not match:
        raise RuntimeError("Could not locate APP_VERSION constant in UI/app.py")
    return match.group(1)


def write_version_include(version: str) -> None:
    VERSION_INCLUDE.write_text(f"#define MyAppVersion \"{version}\"\n", encoding="utf-8")


def write_json_manifest(version: str) -> None:
    VERSION_JSON.write_text(
        json.dumps({"version": version}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    version = extract_app_version()
    write_version_include(version)
    write_json_manifest(version)
    print(f"Synced installer metadata with version {version}")


if __name__ == "__main__":
    main()
