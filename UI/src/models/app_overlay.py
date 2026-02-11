"""
In-Game Overlay Manager
=======================

Manages a secondary pywebview window that acts as a transparent, always-on-top
overlay panel.  The overlay provides the full application UI (characters, search,
quests, sorting) rendered in a compact overlay-friendly layout.

The window is created lazily on first toggle and reused thereafter.  Visibility
is controlled via a global hotkey registered through the existing
GlobalHotkeyManager infrastructure.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import webview as _webview_types

try:
    import webview
except ImportError:
    webview = None  # type: ignore[assignment]

try:
    import ctypes
except ImportError:
    ctypes = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

__all__ = ["AppOverlayManager"]


class AppOverlayManager:
    """Manages the in-game overlay window lifecycle.

    Key design principles
    ---------------------
    * The overlay loads the same Flask server at ``/overlay`` — no second server.
    * The window is frameless, always-on-top, and semi-transparent.
    * Toggling is controlled via a configurable global hotkey.
    * The manager stores minimal state and delegates rendering to the web layer.
    """

    # Default window geometry (centered, 80 % of primary monitor)
    DEFAULT_WIDTH = 1100
    DEFAULT_HEIGHT = 700
    MIN_WIDTH = 800
    MIN_HEIGHT = 520

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._window: Optional[_webview_types.Window] = None
        self._visible = False
        self._enabled = False
        self._opacity: float = 0.92
        self._server = None  # Flask app reference — set via configure()
        self._main_window = None  # Main pywebview window — used to derive base URL
        self._creating = False
        self._ready_event = threading.Event()
        self._hwnd: Optional[int] = None

    # ------------------------------------------------------------------ config

    def configure(
        self,
        *,
        server,
        main_window=None,
        enabled: bool = False,
        opacity: float = 0.92,
    ) -> None:
        """Apply settings (called from Api.set_window)."""
        with self._lock:
            self._server = server
            if main_window is not None:
                self._main_window = main_window
            self._enabled = bool(enabled)
            self._opacity = max(0.3, min(1.0, float(opacity)))

        # If an overlay window already exists, apply opacity immediately
        if self._window and sys.platform.startswith("win") and ctypes:
            self._apply_opacity()

    def update_settings(self, *, enabled: bool, opacity: float) -> None:
        """Hot-update overlay preferences from settings save."""
        with self._lock:
            was_enabled = self._enabled
            self._enabled = bool(enabled)
            self._opacity = max(0.3, min(1.0, float(opacity)))

        # If overlay was disabled and we have a window — hide it
        if was_enabled and not self._enabled and self._visible:
            self.hide()

        # Apply new opacity if window exists
        if self._window:
            self._apply_opacity()

    # ------------------------------------------------------------------ public

    def toggle(self) -> None:
        """Toggle overlay visibility.  Called from the global hotkey callback."""
        if not self._enabled:
            logger.debug("Overlay toggle ignored — feature is disabled")
            return

        if self._visible:
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        """Show the overlay window (create it if necessary)."""
        if not self._enabled:
            return

        with self._lock:
            if self._visible and self._window:
                # Already visible — just bring to front
                self._bring_to_front()
                return

        if not self._window:
            self._create_window()
        else:
            try:
                self._window.show()
            except Exception as exc:
                logger.warning("Failed to show overlay window: %s", exc)
                # Window might have been destroyed — recreate
                self._window = None
                self._create_window()

        self._visible = True
        self._apply_opacity()
        self._bring_to_front()
        logger.info("Overlay shown")

    def hide(self) -> None:
        """Hide the overlay window without destroying it."""
        with self._lock:
            self._visible = False

        if self._window:
            try:
                self._window.hide()
            except Exception as exc:
                logger.debug("Failed to hide overlay window: %s", exc)

        logger.info("Overlay hidden")

    def destroy(self) -> None:
        """Permanently destroy the overlay window (called on app shutdown)."""
        with self._lock:
            self._visible = False
            window = self._window
            self._window = None
            self._hwnd = None

        if window:
            try:
                window.destroy()
            except Exception as exc:
                logger.debug("Failed to destroy overlay window: %s", exc)

    @property
    def is_visible(self) -> bool:
        return self._visible

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ----------------------------------------------------------------- internal

    def _create_window(self) -> None:
        """Create the overlay webview window."""
        if webview is None:
            logger.error("pywebview is not available — cannot create overlay")
            return

        if self._server is None:
            logger.error("Flask server not configured — cannot create overlay")
            return

        with self._lock:
            if self._creating:
                return
            self._creating = True

        try:
            # Derive overlay URL from the already-running server
            overlay_url = self._resolve_overlay_url()
            if not overlay_url:
                logger.error("Cannot determine overlay URL — main window not ready")
                return

            logger.info("Creating overlay window at %s", overlay_url)
            window = webview.create_window(
                "DnDTools Overlay",
                overlay_url,
                width=self.DEFAULT_WIDTH,
                height=self.DEFAULT_HEIGHT,
                min_size=(self.MIN_WIDTH, self.MIN_HEIGHT),
                frameless=True,
                easy_drag=False,
                on_top=True,
                transparent=True,
                background_color="#000000",
            )

            self._window = window

            # Cache HWND on Windows for SetLayeredWindowAttributes
            if sys.platform.startswith("win"):
                threading.Thread(
                    target=self._cache_hwnd_when_ready,
                    daemon=True,
                    name="OverlayHWNDCache",
                ).start()

        except Exception as exc:
            logger.error("Failed to create overlay window: %s", exc)
            self._window = None
        finally:
            with self._lock:
                self._creating = False

    def _cache_hwnd_when_ready(self) -> None:
        """Wait for the native handle to become available and cache it."""
        import time

        for _ in range(50):  # up to 5 seconds
            time.sleep(0.1)
            if not self._window:
                return
            try:
                native = self._window.native
                if native is None:
                    continue
                raw = native.Handle if hasattr(native, "Handle") else native
                hwnd = self._to_int(raw)
                if hwnd:
                    self._hwnd = hwnd
                    self._apply_opacity()
                    logger.debug("Overlay HWND cached: %s", hwnd)
                    return
            except Exception:
                continue
        logger.debug("Overlay HWND caching timed out")

    def _resolve_overlay_url(self) -> Optional[str]:
        """Derive the overlay URL from the main window's running server.

        pywebview serves the Flask app on a random local port.  We read
        the main window's current URL to discover that port, then build
        the ``/overlay`` route URL from it.
        """
        # Try getting the URL from the main window
        if self._main_window:
            try:
                current = self._main_window.get_current_url()
                if current:
                    # current is e.g. "http://127.0.0.1:54321/" or "http://localhost:54321/settings"
                    from urllib.parse import urlparse
                    parsed = urlparse(current)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    return f"{base}/overlay"
            except Exception as exc:
                logger.debug("Could not get URL from main window: %s", exc)

        # Fallback: check pywebview's global server
        try:
            from webview.http import global_server
            if global_server and hasattr(global_server, 'address'):
                host, port = global_server.address
                return f"http://{host}:{port}/overlay"
        except Exception:
            pass

        # Last resort: inspect all pywebview windows
        try:
            for win in webview.windows:
                if win is not self._window:
                    url = win.get_current_url()
                    if url:
                        from urllib.parse import urlparse
                        parsed = urlparse(url)
                        return f"{parsed.scheme}://{parsed.netloc}/overlay"
        except Exception:
            pass

        return None

    def _apply_opacity(self) -> None:
        """Set the window opacity using Win32 API."""
        if not sys.platform.startswith("win") or ctypes is None:
            return

        hwnd = self._hwnd
        if not hwnd:
            return

        try:
            user32 = ctypes.windll.user32
            # Ensure WS_EX_LAYERED style
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if not (style & WS_EX_LAYERED):
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)

            LWA_ALPHA = 0x02
            alpha = max(76, min(255, int(self._opacity * 255)))  # min ~30%
            user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
        except Exception as exc:
            logger.debug("Failed to set overlay opacity: %s", exc)

    def _bring_to_front(self) -> None:
        """Bring the overlay window to the foreground."""
        if self._window:
            try:
                if hasattr(self._window, "restore"):
                    self._window.restore()
                if hasattr(self._window, "show"):
                    self._window.show()
            except Exception:
                pass

        # Win32: Force to topmost
        if sys.platform.startswith("win") and ctypes and self._hwnd:
            try:
                user32 = ctypes.windll.user32
                HWND_TOPMOST = -1
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_SHOWWINDOW = 0x0040
                SWP_NOACTIVATE = 0x0010
                user32.SetWindowPos(
                    self._hwnd,
                    HWND_TOPMOST,
                    0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
                )
            except Exception:
                pass

    @staticmethod
    def _to_int(raw) -> Optional[int]:
        """Convert a .NET IntPtr / plain int to a Python int."""
        if isinstance(raw, int):
            return raw
        for attr in ("ToInt64", "ToInt32"):
            fn = getattr(raw, attr, None)
            if callable(fn):
                try:
                    return int(fn())
                except Exception:
                    continue
        try:
            return int(raw)
        except Exception:
            return None
