import asyncio
import subprocess
import sys
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def patch_asyncio():
    """Monkey-patch asyncio to suppress console windows on Windows."""
    if sys.platform != 'win32':
        return

    _orig_create_exec = asyncio.create_subprocess_exec
    _orig_create_shell = asyncio.create_subprocess_shell

    async def _create_no_window_exec(*args, **kwargs):
        kwargs.setdefault('stdin', subprocess.DEVNULL)
        kwargs.setdefault('stdout', subprocess.DEVNULL)
        kwargs.setdefault('stderr', subprocess.DEVNULL)
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
        return await _orig_create_exec(*args, **kwargs)

    async def _create_no_window_shell(*args, **kwargs):
        kwargs.setdefault('stdin', subprocess.DEVNULL)
        kwargs.setdefault('stdout', subprocess.DEVNULL)
        kwargs.setdefault('stderr', subprocess.DEVNULL)
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
        return await _orig_create_shell(*args, **kwargs)

    asyncio.create_subprocess_exec = _create_no_window_exec
    asyncio.create_subprocess_shell = _create_no_window_shell

def patch_pyshark():
    """Monkey-patch PyShark to handle event loop closure gracefully."""
    try:
        import pyshark.capture.capture
        _PysharkCapture = pyshark.capture.capture.Capture

        _orig_ps_close = _PysharkCapture.close

        def _safe_ps_close(self):
            try:
                running = getattr(self, '_running_processes', None)
                if not running:
                    return

                loop = getattr(self, 'eventloop', None)

                def _run_close_async_in_temp_loop():
                    try:
                        coro = self.close_async()
                        if asyncio.iscoroutine(coro):
                            _tmp = asyncio.new_event_loop()
                            try:
                                _tmp.run_until_complete(coro)
                            finally:
                                _tmp.close()
                    except Exception:
                        pass

                if loop is None:
                    _run_close_async_in_temp_loop()
                    return

                try:
                    is_closed = loop.is_closed()
                except Exception:
                    is_closed = True

                if is_closed:
                    _run_close_async_in_temp_loop()
                    return

                return _orig_ps_close(self)
            except Exception:
                return

        def _safe_ps_del(self):
            try:
                if getattr(self, '_running_processes', None):
                    self.close()
            except Exception:
                return

        _PysharkCapture.close = _safe_ps_close
        _PysharkCapture.__del__ = _safe_ps_del
    except Exception:
        pass

async def terminate_asyncio_subprocess(proc: asyncio.subprocess.Process):
    """Gracefully terminate an asyncio subprocess, escalating to kill when needed."""
    if proc is None or proc.returncode is not None:
        return

    try:
        proc.terminate()
    except ProcessLookupError:
        return
    except Exception:
        pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            return
        await proc.wait()

def finalize_asyncio_subprocess(proc: asyncio.subprocess.Process, loop: Optional[asyncio.AbstractEventLoop], logger: Optional[logging.Logger] = None) -> None:
    """Synchronously ensure an asyncio subprocess is terminated, regardless of loop state."""
    if proc is None or proc.returncode is not None:
        return

    async def _runner():
        await terminate_asyncio_subprocess(proc)

    if loop and not loop.is_closed():
        try:
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(_runner(), loop)
                future.result(timeout=6)
            else:
                loop.run_until_complete(_runner())
        except Exception as exc:
            if logger:
                logger.debug(f"Failed to finalize subprocess via existing loop: {exc}")
    else:
        temp_loop = asyncio.new_event_loop()
        try:
            temp_loop.run_until_complete(_runner())
        except Exception as exc:
            if logger:
                logger.debug(f"Failed to finalize subprocess via temp loop: {exc}")
        finally:
            temp_loop.close()
