#!/usr/bin/env python3
"""Utility to fetch the latest Dark and Darker build id and persist it as JSON."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

STORE_URL_TEMPLATE = (
    "https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=en&filters=depots"
)
USER_AGENT = "DnDTools Build Fetcher/1.0"
_BUILDID_REGEX = re.compile(r'"buildid"\s+"(\d+)"')


class BuildIdError(RuntimeError):
    """Raised when the build id cannot be resolved."""


def _log(message: str) -> None:
    print(message, flush=True)


def fetch_from_store(app_id: int, timeout: int = 30) -> str:
    """Fetch the build id from the Steam Store API."""
    url = STORE_URL_TEMPLATE.format(appid=app_id)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    _log(f"Fetching build id from Steam Store API: {url}")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise BuildIdError(f"Steam Store API returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise BuildIdError(f"Failed to reach Steam Store API: {exc.reason}") from exc

    app_key = str(app_id)
    app_info = payload.get(app_key)
    if not app_info:
        raise BuildIdError(f"App {app_id} missing from Steam Store response")
    if not app_info.get("success"):
        raise BuildIdError(f"Steam Store API indicated success=false for app {app_id}")

    try:
        build_id = app_info["data"]["depots"]["branches"]["public"]["buildid"]
    except KeyError as exc:
        raise BuildIdError("Build id not present in Steam Store response") from exc

    if not build_id:
        raise BuildIdError("Steam Store build id is empty")

    _log(f"Steam Store API build id: {build_id}")
    return build_id


def ensure_steamcmd() -> str:
    """Ensure steamcmd is available and return its path."""
    steamcmd_path = shutil.which("steamcmd")
    if steamcmd_path:
        return steamcmd_path

    _log("SteamCMD not found on PATH, installing via Chocolatey...")
    install = subprocess.run(
        ["choco", "install", "steamcmd", "-y"],
        check=False,
        capture_output=True,
        text=True,
    )
    _log(install.stdout.strip())
    if install.returncode != 0:
        _log(install.stderr.strip())
        raise BuildIdError("Chocolatey failed to install steamcmd")

    steamcmd_path = shutil.which("steamcmd")
    if not steamcmd_path:
        raise BuildIdError("steamcmd installation succeeded but executable not found")
    return steamcmd_path


def fetch_from_steamcmd(app_id: int, username: str, password: str, guard: Optional[str]) -> str:
    """Fetch the build id via steamcmd authenticated login."""
    steamcmd_path = ensure_steamcmd()
    _log(f"Using SteamCMD at {steamcmd_path}")

    if not username or not password:
        raise BuildIdError("Steam credentials must be supplied for SteamCMD fallback")

    cmd = [steamcmd_path, "+login", username, password]
    if guard:
        cmd.append(guard)
    cmd.extend([
        "+app_info_update",
        "1",
        "+app_info_request",
        str(app_id),
        "+app_info_print",
        str(app_id),
        "+quit",
    ])

    _log("Invoking SteamCMD to fetch build id…")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = f"{proc.stdout}\n{proc.stderr}".strip()
    if output:
        _log(output)

    if proc.returncode != 0:
        raise BuildIdError(f"SteamCMD exited with code {proc.returncode}")

    match = _BUILDID_REGEX.search(output)
    if not match:
        raise BuildIdError("SteamCMD output did not contain a build id")

    build_id = match.group(1)
    _log(f"SteamCMD build id: {build_id}")
    return build_id


def write_json(output_path: Path, app_id: int, build_id: str) -> None:
    payload = {
        "app_id": app_id,
        "buildid": build_id,
        "checked": datetime.now(timezone.utc).isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _log(f"Wrote build metadata to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", type=int, required=True, help="Steam application ID")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("DnDversion.json"),
        help="Path to the JSON file to update",
    )
    args = parser.parse_args()

    build_id: Optional[str] = None

    try:
        build_id = fetch_from_store(args.app_id)
    except BuildIdError as exc:
        _log(f"Steam Store lookup failed: {exc}")

    if not build_id:
        username = os.environ.get("STEAM_USERNAME", "").strip()
        password = os.environ.get("STEAM_PASSWORD", "").strip()
        guard = os.environ.get("STEAM_GUARD", "").strip() or None
        try:
            build_id = fetch_from_steamcmd(args.app_id, username, password, guard)
        except BuildIdError as exc:
            raise BuildIdError(
                "Unable to resolve build id using Steam Store API or SteamCMD"
            ) from exc

    if not build_id:
        raise BuildIdError("Build id could not be determined")

    write_json(args.output, args.app_id, build_id)
    _log(f"Final build id: {build_id}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except BuildIdError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
