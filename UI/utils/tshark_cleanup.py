import logging
import multiprocessing as mp
import os
import threading
import time
from typing import Iterable, Optional, Sequence

import psutil


def _scan_and_cleanup(parent_pid: int, protected_pids: Optional[Sequence[int]] = None) -> int:
    protected = {pid for pid in (protected_pids or []) if isinstance(pid, int) and pid > 0}
    killed_count = 0
    for proc in psutil.process_iter(['pid', 'name', 'ppid']):
        try:
            name = proc.info.get('name')
            if not name:
                continue
            name_lower = name.lower()
            if 'tshark' not in name_lower and 'dumpcap' not in name_lower:
                continue

            pid = proc.info.get('pid')
            if pid in protected:
                continue

            ppid = proc.info.get('ppid') or 0
            if ppid == parent_pid:
                continue  # Active helper owned by current session

            parent_missing = (ppid <= 1) or (ppid and not psutil.pid_exists(ppid))
            if not parent_missing:
                continue
            proc.kill()
            killed_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return killed_count


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
        queue.put({'killed': result})
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
        killed = int(payload.get('killed', 0))
        if killed:
            logger.info("Cleaned up %s lingering tshark instance(s).", killed)
            _notify_window(window_ref, killed)
        else:
            logger.info("No lingering tshark instances found.")

    threading.Thread(target=listener, name='tshark-cleanup-listener', daemon=True).start()
    return process
