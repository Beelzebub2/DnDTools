"""Safely clean up capture helpers previously launched by DnDTools.

The registry is deliberately identity based. A process name alone cannot
establish ownership: users may run Wireshark/tshark independently, and Windows
can reuse a PID after a process exits. DnDTools therefore records the helper
PID and creation time at launch, together with the owning app process identity.
Startup cleanup considers only those persisted records.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional, Sequence

import psutil


_HELPER_NAMES = {"tshark", "tshark.exe", "dumpcap", "dumpcap.exe"}
_ZOMBIE_STATUS = getattr(psutil, "STATUS_ZOMBIE", "zombie")
_IDENTITY_EPSILON = 0.5  # seconds; accommodates platform timestamp precision
_REGISTRY_VERSION = 1
_REGISTRY_LOCK = threading.RLock()


def _default_registry_path() -> Path:
    # Import lazily so the cleanup module remains straightforward to unit test
    # and does not create an appdirs/capture import cycle.
    from src.models.appdirs import get_appdata_dir

    return Path(get_appdata_dir()) / "tshark_helpers.json"


def _registry_path(path: Optional[os.PathLike | str] = None) -> Path:
    return Path(path) if path is not None else _default_registry_path()


def _safe_create_time(proc: psutil.Process) -> Optional[float]:
    try:
        return float(proc.create_time())
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError, TypeError, ValueError):
        return None


def _safe_process_name(proc: psutil.Process) -> Optional[str]:
    try:
        name = str(proc.name() or "").strip().lower()
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
        return None
    return name or None


def _normalize_entry(raw: object) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    try:
        pid = int(raw.get("pid"))
        create_time = float(raw.get("create_time"))
        owner_pid = int(raw.get("owner_pid"))
        owner_create_time = float(raw.get("owner_create_time"))
    except (TypeError, ValueError, OverflowError):
        return None

    name = str(raw.get("name") or "").strip().lower()
    session_id = str(raw.get("session_id") or "").strip()
    if (
        pid <= 0
        or create_time <= 0
        or owner_pid <= 0
        or owner_create_time <= 0
        or name not in _HELPER_NAMES
        or not session_id
    ):
        return None

    try:
        registered_at = float(raw.get("registered_at") or time.time())
    except (TypeError, ValueError, OverflowError):
        registered_at = time.time()

    return {
        "pid": pid,
        "create_time": create_time,
        "name": name,
        "owner_pid": owner_pid,
        "owner_create_time": owner_create_time,
        "session_id": session_id,
        "registered_at": registered_at,
    }


def _load_registry_unlocked(path: Path) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

    entries = payload.get("helpers") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    return [entry for raw in entries if (entry := _normalize_entry(raw)) is not None]


def _write_registry_unlocked(path: Path, entries: Sequence[dict]) -> None:
    normalized = [entry for raw in entries if (entry := _normalize_entry(raw)) is not None]
    if not normalized:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": _REGISTRY_VERSION, "helpers": normalized},
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _identity_key(entry: dict) -> tuple[int, float]:
    return int(entry["pid"]), float(entry["create_time"])


def register_owned_helpers(
    processes: Iterable[psutil.Process],
    *,
    owner_pid: int,
    owner_create_time: Optional[float],
    session_id: str,
    registry_path: Optional[os.PathLike | str] = None,
) -> set[tuple[int, float]]:
    """Persist exact identities for capture helpers owned by this app session."""

    try:
        normalized_owner_pid = int(owner_pid)
        normalized_owner_time = float(owner_create_time)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return set()
    normalized_session = str(session_id or "").strip()
    if normalized_owner_pid <= 0 or normalized_owner_time <= 0 or not normalized_session:
        return set()

    discovered: list[dict] = []
    for proc in processes:
        name = _safe_process_name(proc)
        create_time = _safe_create_time(proc)
        try:
            pid = int(proc.pid)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        if name not in _HELPER_NAMES or create_time is None or pid <= 0:
            continue
        discovered.append({
            "pid": pid,
            "create_time": create_time,
            "name": name,
            "owner_pid": normalized_owner_pid,
            "owner_create_time": normalized_owner_time,
            "session_id": normalized_session,
            "registered_at": time.time(),
        })

    if not discovered:
        return set()

    path = _registry_path(registry_path)
    with _REGISTRY_LOCK:
        existing = _load_registry_unlocked(path)
        by_identity = {_identity_key(entry): entry for entry in existing}
        changed = False
        for entry in discovered:
            key = _identity_key(entry)
            previous = by_identity.get(key)
            if previous is None or any(
                previous.get(field) != entry.get(field)
                for field in ("name", "owner_pid", "owner_create_time", "session_id")
            ):
                by_identity[key] = entry
                changed = True
        if changed:
            _write_registry_unlocked(path, list(by_identity.values()))

    return {_identity_key(entry) for entry in discovered}


def _helper_identity_matches(proc: psutil.Process, entry: dict) -> Optional[bool]:
    name = _safe_process_name(proc)
    create_time = _safe_create_time(proc)
    if name is None or create_time is None:
        return None
    return bool(
        name == entry.get("name")
        and abs(create_time - float(entry["create_time"])) <= _IDENTITY_EPSILON
    )


def _owner_state(entry: dict) -> tuple[bool, str]:
    try:
        owner = psutil.Process(int(entry["owner_pid"]))
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False, "owner_missing"
    except (psutil.AccessDenied, OSError):
        # Ownership cannot be disproved, so fail closed and preserve the helper.
        return True, "owner_inspect_denied"

    owner_time = _safe_create_time(owner)
    if owner_time is None:
        return True, "owner_inspect_denied"
    if abs(owner_time - float(entry["owner_create_time"])) > _IDENTITY_EPSILON:
        return False, "owner_pid_reused"
    try:
        if not owner.is_running() or owner.status() == _ZOMBIE_STATUS:
            return False, "owner_not_running"
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False, "owner_missing"
    except (psutil.AccessDenied, OSError):
        return True, "owner_inspect_denied"
    return True, "active_owner"


def prune_owned_helper_registry(
    *,
    session_id: Optional[str] = None,
    registry_path: Optional[os.PathLike | str] = None,
) -> int:
    """Remove stale registry records without terminating any process."""

    path = _registry_path(registry_path)
    removed = 0
    with _REGISTRY_LOCK:
        survivors: list[dict] = []
        for entry in _load_registry_unlocked(path):
            if session_id is not None and entry.get("session_id") != session_id:
                survivors.append(entry)
                continue
            try:
                proc = psutil.Process(int(entry["pid"]))
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                removed += 1
                continue
            except (psutil.AccessDenied, OSError):
                # Failing to inspect a live identity is not proof that it is
                # stale. Preserve it for a later cleanup attempt.
                survivors.append(entry)
                continue
            identity_matches = _helper_identity_matches(proc, entry)
            if identity_matches is not False:
                survivors.append(entry)
            else:
                removed += 1
        _write_registry_unlocked(path, survivors)
    return removed


def _scan_and_cleanup(
    parent_pid: int,
    protected_pids: Optional[Sequence[int]] = None,
    *,
    registry_path: Optional[os.PathLike | str] = None,
) -> dict:
    """Terminate only stale helpers whose exact identities are registered."""

    del parent_pid  # owner PID/create-time pairs in the registry are authoritative
    protected = {pid for pid in (protected_pids or []) if isinstance(pid, int) and pid > 0}
    stats: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    kill_reasons: Counter[str] = Counter()
    path = _registry_path(registry_path)

    with _REGISTRY_LOCK:
        survivors: list[dict] = []
        for entry in _load_registry_unlocked(path):
            stats["candidates"] += 1
            pid = int(entry["pid"])
            if pid in protected:
                stats["protected"] += 1
                survivors.append(entry)
                continue

            try:
                proc = psutil.Process(pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                stats["stale_registry_entries"] += 1
                skip_reasons["helper_missing"] += 1
                continue
            except (psutil.AccessDenied, OSError):
                stats["inspection_denied"] += 1
                skip_reasons["helper_inspect_denied"] += 1
                survivors.append(entry)
                continue

            identity_matches = _helper_identity_matches(proc, entry)
            if identity_matches is None:
                stats["inspection_denied"] += 1
                skip_reasons["helper_inspect_denied"] += 1
                survivors.append(entry)
                continue
            if not identity_matches:
                # The PID now belongs to a different process (or a different
                # tshark invocation). Never act on it; discard the stale record.
                stats["identity_mismatch"] += 1
                skip_reasons["helper_identity_mismatch"] += 1
                continue

            owner_active, owner_reason = _owner_state(entry)
            if owner_active:
                skip_reasons[owner_reason] += 1
                survivors.append(entry)
                continue

            try:
                proc.kill()
                stats["killed"] += 1
                kill_reasons[owner_reason] += 1
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                stats["stale_during_kill"] += 1
            except (psutil.AccessDenied, OSError):
                stats["kill_denied"] += 1
                skip_reasons["kill_denied"] += 1
                survivors.append(entry)

        _write_registry_unlocked(path, survivors)

    stats["skip_reasons"] = dict(skip_reasons)
    stats["kill_reasons"] = dict(kill_reasons)
    return dict(stats)


def _notify_window(window_ref, killed: int) -> None:
    if not window_ref or killed <= 0:
        return
    message = f"Closed {killed} terminated DnDTools capture helper(s).".replace("'", "\\'")
    try:
        window_ref.evaluate_js(
            f"showNotification('{message}', 'warning');",
            callback=lambda *_: None,
        )
    except Exception:
        pass


def schedule_tshark_cleanup(
    logger: logging.Logger,
    window_ref=None,
    delay_seconds: float = 0.0,
    protected_pids: Optional[Iterable[int]] = None,
    *,
    registry_path: Optional[os.PathLike | str] = None,
):
    """Clean registered stale helpers in a background thread."""

    parent_pid = os.getpid()
    protected_tuple = tuple(
        int(pid) for pid in (protected_pids or []) if isinstance(pid, int) and pid > 0
    )

    def worker() -> None:
        try:
            if delay_seconds and delay_seconds > 0:
                time.sleep(delay_seconds)
            stats = _scan_and_cleanup(
                parent_pid,
                protected_tuple,
                registry_path=registry_path,
            )
        except Exception as exc:
            logger.error("Tshark cleanup failed: %s", exc)
            return

        killed = int(stats.get("killed", 0))
        candidates = int(stats.get("candidates", 0))
        protected = int(stats.get("protected", 0))
        skipped_reasons = stats.get("skip_reasons") or {}
        kill_reasons = stats.get("kill_reasons") or {}
        kill_denied = int(stats.get("kill_denied", 0))
        if killed:
            reason_str = ", ".join(
                f"{reason}:{count}" for reason, count in kill_reasons.items()
            ) or "unknown"
            logger.info(
                "Cleaned up %s registered DnDTools capture helper(s) (%s).",
                killed,
                reason_str,
            )
            _notify_window(window_ref, killed)
        elif candidates:
            skipped = candidates - killed - protected
            detail = ", ".join(
                f"{reason}:{count}" for reason, count in sorted(skipped_reasons.items())
            )
            logger.info(
                "Checked %s registered DnDTools capture helper(s); protected=%s, "
                "denied=%s, skipped=%s (%s).",
                candidates,
                protected,
                kill_denied,
                max(skipped, 0),
                detail or "no details",
            )
        else:
            logger.info("No registered lingering DnDTools capture helpers found.")

    thread = threading.Thread(target=worker, name="tshark-cleanup-worker", daemon=True)
    thread.start()
    return thread
