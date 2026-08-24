"""Generate the distributable quest snapshot from the DarkerDB v2 API.

The API key is used only by this build/update step. Released clients consume
the generated ``assets/quests.json`` file and never receive the credential.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


UI_DIR = Path(__file__).resolve().parent.parent
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

from src.quest_service import (  # noqa: E402
    DARKERDB_API_VERSION,
    DARKERDB_QUESTS_API_URL,
    fetch_darkerdb_quests,
    get_darkerdb_api_key,
)


DEFAULT_OUTPUT = UI_DIR / "assets" / "quests.json"


def update_quests(output_path: Path = DEFAULT_OUTPUT, api_url: str = DARKERDB_QUESTS_API_URL) -> int:
    api_key = get_darkerdb_api_key()
    if not api_key and url_requires_darkerdb_key(api_url):
        raise RuntimeError(
            "DARKERDB_API_KEY (or DNDTOOLS_DARKERDB_API_KEY) is required to update quests"
        )

    quests, metadata = fetch_darkerdb_quests(api_key=api_key, api_url=api_url)
    generated_at = datetime.now(timezone.utc)
    payload = {
        "version": 2,
        "source": "darkerdb-v2",
        "api_version": DARKERDB_API_VERSION,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "timestamp": generated_at.timestamp(),
        "build": metadata.get("build"),
        "patch": metadata.get("patch"),
        "quests": quests,
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(output_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass

    logging.info(
        "Wrote %s normalized quests to %s (build=%s patch=%s)",
        len(quests),
        output_path,
        metadata.get("build"),
        metadata.get("patch"),
    )
    return len(quests)


def url_requires_darkerdb_key(api_url: str) -> bool:
    return str(api_url or "").strip().lower().startswith("https://api.darkerdb.com/v2/")


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the packaged DarkerDB v2 quest snapshot")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--api-url",
        default=os.getenv("DND_QUESTS_API_URL") or DARKERDB_QUESTS_API_URL,
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        update_quests(args.output, args.api_url)
    except Exception as exc:
        logging.error("Quest snapshot update failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
