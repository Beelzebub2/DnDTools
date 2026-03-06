import threading
import time
import psutil
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

class MemoryGuard:
    """
    Monitors system memory usage and triggers a callback if usage exceeds a threshold.
    """
    def __init__(self, threshold_mb: int = 500, check_interval: float = 2.0, on_threshold_exceeded: Optional[Callable[[], None]] = None):
        self.threshold_mb = threshold_mb
        self.check_interval = check_interval
        self.on_threshold_exceeded = on_threshold_exceeded
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

    def start(self):
        if self._is_running:
            return
        self._stop_event.clear()
        self._is_running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="MemoryGuardThread")
        self._thread.start()
        logger.info("MemoryGuard started.")

    def stop(self):
        if not self._is_running:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._is_running = False
        logger.info("MemoryGuard stopped.")

    def _loop(self):
        process = psutil.Process()
        while not self._stop_event.is_set():
            try:
                mem_info = process.memory_info()
                rss_mb = mem_info.rss / (1024 * 1024)
                if rss_mb > self.threshold_mb:
                    logger.warning(f"Memory usage {rss_mb:.2f} MB exceeded threshold {self.threshold_mb} MB.")
                    if self.on_threshold_exceeded:
                        try:
                            self.on_threshold_exceeded()
                        except Exception as e:
                            logger.error(f"Error in MemoryGuard callback: {e}")
                    # Longer cooldown after triggering; check stop event for responsive shutdown
                    if self._stop_event.wait(10):
                        break
                    continue
            except Exception as e:
                logger.error(f"Error in MemoryGuard loop: {e}")
            
            if self._stop_event.wait(self.check_interval):
                break
