import logging
import multiprocessing as mp
import os
import threading
import time
from collections import Counter
from typing import Iterable, Optional, Sequence, Tuple

import psutil

_SYSTEM_PARENT_PIDS = {0, 4}
_SYSTEM_PARENT_NAMES = {"system", "system idle process"}
_ZOMBIE_STATUS = getattr(psutil, "STATUS_ZOMBIE", "zombie")
_REUSE_EPSILON = 0.5  # seconds


def _safe_create_time(proc: psutil.Process) -> Optional[float]:
    try:
        return proc.create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
        return None


def _is_orphaned_helper(proc: psutil.Process, parent_pid: int) -> Tuple[bool, str]:
    """Return (True, reason) when the helper's parent is stale or missing."""

    ppid = int(proc.info.get('ppid') or 0)
    if ppid == parent_pid:
        return False, 'current_session'
    if ppid <= 0:
        return True, 'missing_parent'
    if ppid in _SYSTEM_PARENT_PIDS:
        return True, 'system_parent'

    helper_ct = _safe_create_time(proc)

    try:
        parent_proc = psutil.Process(ppid)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return True, 'missing_parent'
    except psutil.Error:
        return False, 'parent_inspect_failed'

    parent_name = (parent_proc.name() or "").lower()
    if parent_name in _SYSTEM_PARENT_NAMES:
        return True, 'system_parent'

    if not parent_proc.is_running():
        return True, 'parent_not_running'

    try:
        if parent_proc.status() == _ZOMBIE_STATUS:
            return True, 'parent_zombie'
    except psutil.Error:
        pass

    parent_ct = _safe_create_time(parent_proc)
    if helper_ct and parent_ct and parent_ct > helper_ct + _REUSE_EPSILON:
        return True, 'parent_pid_reused'

    return False, 'active_parent'


def _scan_and_cleanup(parent_pid: int, protected_pids: Optional[Sequence[int]] = None) -> dict:
    protected = {pid for pid in (protected_pids or []) if isinstance(pid, int) and pid > 0}
    stats: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    kill_reasons: Counter[str] = Counter()

    for proc in psutil.process_iter(['pid', 'name', 'ppid']):
        try:
            name = proc.info.get('name')
            if not name:
                continue
            name_lower = name.lower()
            if 'tshark' not in name_lower and 'dumpcap' not in name_lower:
                continue

            stats['candidates'] += 1

            pid = proc.info.get('pid')
            if pid in protected:
                stats['protected'] += 1
                continue

            orphaned, reason = _is_orphaned_helper(proc, parent_pid)
            if not orphaned:
                skip_reasons[reason] += 1
                continue

            try:
                proc.kill()
                stats['killed'] += 1
                kill_reasons[reason] += 1
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                stats['stale_during_kill'] += 1
            except psutil.AccessDenied:
                stats['kill_denied'] += 1
                skip_reasons['access_denied'] += 1
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            stats['enumeration_denied'] += 1

    stats['skip_reasons'] = dict(skip_reasons)
    stats['kill_reasons'] = dict(kill_reasons)
    return dict(stats)


def _cleanup_worker(
    delay: float,
    parent_pid: int,
    queue: 'mp.Queue',
    protected_pids: Optional[Sequence[int]],
) -> None:
    try:
        if delay and delay > 0:
            time.sleep(delay)
        result = _scan_and_cleanup(parent_pid, protected_pids)
        queue.put({'stats': result})
    except Exception as exc:
        queue.put({'error': str(exc)})


def _notify_window(window_ref, killed: int) -> None:
    if not window_ref or killed <= 0:
        return
    message = f"Closed {killed} terminated tshark instance(s).".replace("'", "\\'")
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
):
    """Offload tshark cleanup to a separate process and report results asynchronously."""

    result_queue: 'mp.Queue' = mp.Queue()
    parent_pid = os.getpid()
    protected_tuple = tuple(int(pid) for pid in (protected_pids or []) if isinstance(pid, int) and pid > 0)
    process = mp.Process(
        target=_cleanup_worker,
        args=(delay_seconds, parent_pid, result_queue, protected_tuple),
        daemon=True,
        name='tshark-cleanup-worker',
    )
    process.start()

    def listener():
        payload = result_queue.get()
        if payload.get('error'):
            logger.error("Tshark cleanup failed: %s", payload['error'])
            return
        stats = payload.get('stats') or {}
        killed = int(stats.get('killed', 0))
        candidates = int(stats.get('candidates', 0))
        protected = int(stats.get('protected', 0))
        skipped_reasons = stats.get('skip_reasons') or {}
        kill_reasons = stats.get('kill_reasons') or {}
        kill_denied = int(stats.get('kill_denied', 0))
        if killed:
            reason_str = ', '.join(f"{reason}:{count}" for reason, count in kill_reasons.items()) or 'unknown'
            logger.info("Cleaned up %s lingering tshark instance(s) (%s).", killed, reason_str)
            _notify_window(window_ref, killed)
        elif candidates:
            skipped = candidates - killed - protected
            detail = ', '.join(f"{reason}:{count}" for reason, count in sorted(skipped_reasons.items()))
            logger.info(
                "Detected %s tshark helper(s); protected=%s, denied=%s, skipped=%s (%s).",
                candidates,
                protected,
                kill_denied,
                max(skipped, 0),
                detail or 'no details',
            )
        else:
            logger.info("No lingering tshark instances found.")

    threading.Thread(target=listener, name='tshark-cleanup-listener', daemon=True).start()
    return process
