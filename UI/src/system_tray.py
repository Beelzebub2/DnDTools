import logging
import threading
import webbrowser
import sys
from pathlib import Path
from typing import Optional, Callable, Any, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage
else:
    PILImage = Any

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

logger = logging.getLogger(__name__)

class SystemTray:
    """
    A robust, feature-rich system tray implementation for DnDTools.
    Provides quick access to app controls, status monitoring, and external resources.
    """

    def __init__(
        self,
        app_name: str,
        app_version: str,
        icon_path: Optional[Union[str, Path]],
        on_restore: Callable[[], None],
        on_quit: Callable[[], None],
        capture_controller: Any,
        discord_url: str = "https://discord.gg/X8FuqR2cq6"
    ):
        self.app_name = app_name
        self.app_version = app_version
        self.icon_path = Path(icon_path) if icon_path else None
        self.on_restore = on_restore
        self.on_quit = on_quit
        self.capture_controller = capture_controller
        self.discord_url = discord_url

        self._icon: Optional[Any] = None
        self._icon_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.RLock()
        self._notification_shown = False
        self._notification_timer: Optional[threading.Timer] = None

        if not pystray or not Image:
            logger.warning("System tray dependencies (pystray, Pillow) not found. Tray disabled.")
            self._available = False
        else:
            self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def _load_icon(self) -> Optional[Any]:
        """Load the app icon or generate a high-quality fallback."""
        if not Image:
            return None

        logger.info(f"Loading tray icon from: {self.icon_path}")
        if self.icon_path and self.icon_path.exists():
            logger.info(f"Icon file exists: {self.icon_path}")
            try:
                with Image.open(self.icon_path) as img:
                    # Ensure icon is RGBA for transparency support
                    return img.convert("RGBA")
            except Exception as e:
                logger.warning(f"Failed to load tray icon from {self.icon_path}: {e}")

        logger.info(f"Icon file not found or not set: {self.icon_path}")

        # Fallback: Generate a themed icon (Dark background, Gold 'D')
        try:
            size = (64, 64)
            color_bg = (32, 35, 54, 255)  # Dark blue-grey
            color_fg = (241, 196, 67, 255)  # Gold
            
            image = Image.new('RGBA', size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            
            # Draw a rounded square or circle
            draw.ellipse((4, 4, 60, 60), fill=color_bg)
            
            # Draw a simple diamond shape for DnD feel
            draw.polygon([(32, 16), (48, 32), (32, 48), (16, 32)], fill=color_fg)
            
            return image
        except Exception as e:
            logger.error(f"Failed to generate fallback icon: {e}")
            return None

    def _on_click_restore(self, icon, item):
        logger.info("Tray: Restore requested")
        if self.on_restore:
            self.on_restore()

    def _on_click_quit(self, icon, item):
        logger.info("Tray: Quit requested")
        if self.on_quit:
            self.on_quit()

    def _on_click_capture(self, icon, item):
        if not self.capture_controller:
            return
        
        try:
            state = self.capture_controller.state()
            is_running = state.get('running', False)
            
            if is_running:
                self.capture_controller.stop()
                self.notify("Packet capture stopped.", "Capture Status")
            else:
                self.capture_controller.start()
                self.notify("Packet capture started.", "Capture Status")
            
            # Menu updates automatically on next click, but we can force update if needed
            # pystray doesn't support dynamic menu updates while open easily, 
            # but the next time it opens it will re-evaluate.
        except Exception as e:
            logger.error(f"Tray: Failed to toggle capture: {e}")



    def _on_click_discord(self, icon, item):
        webbrowser.open(self.discord_url)

    def _get_capture_state_label(self, item):
        if not self.capture_controller:
            return "Capture: Unavailable"
        
        try:
            state = self.capture_controller.state()
            running = state.get('running', False)
            return f"Capture: {'Running' if running else 'Stopped'}"
        except Exception:
            return "Capture: Unknown"

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem(f"{self.app_name} v{self.app_version}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Dashboard", self._on_click_restore, default=True),
            pystray.MenuItem(self._get_capture_state_label, self._on_click_capture),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Join Discord", self._on_click_discord),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._on_click_quit),
        )

    def start(self):
        """Start the system tray icon in a background thread."""
        if not self.available:
            return

        with self._lock:
            if self._running:
                return

            icon_image = self._load_icon()
            if not icon_image:
                logger.error("Tray: Could not load or generate icon.")
                return

            self._icon = pystray.Icon(
                self.app_name,
                icon_image,
                f"{self.app_name} v{self.app_version}",
                menu=self._build_menu()
            )
            
            self._running = True
            self._icon_thread = threading.Thread(target=self._run_tray_loop, daemon=True, name="SystemTrayThread")
            self._icon_thread.start()
            logger.info("System tray started.")

    def _run_tray_loop(self):
        try:
            if self._icon:
                self._icon.run()
        except Exception as e:
            logger.error(f"System tray crashed: {e}", exc_info=True)
        finally:
            self._running = False

    def stop(self):
        """Stop the system tray icon."""
        with self._lock:
            if not self._running or not self._icon:
                return
            
            logger.info("Stopping system tray...")
            try:
                self._icon.stop()
            except Exception as e:
                logger.error(f"Error stopping tray: {e}")
            
            self._running = False
            self._icon = None

    def notify(self, message: str, title: str = "DnDTools"):
        """Show a system notification."""
        if self._icon and self.available:
            try:
                self._icon.notify(message, title)
            except Exception as e:
                logger.warning(f"Failed to show notification: {e}")

    def _schedule_notification_clear(self, delay: float = 1.5) -> None:
        if not self._icon or not hasattr(self._icon, 'remove_notification'):
            return

        if self._notification_timer and self._notification_timer.is_alive():
            self._notification_timer.cancel()

        def _clear():
            try:
                self._icon.remove_notification()
            except Exception as exc:
                logger.debug("Tray: remove_notification failed: %s", exc)

        timer = threading.Timer(delay, _clear)
        timer.daemon = True
        timer.start()
        self._notification_timer = timer

    def notify_minimized(self):
        """Notify user that app is minimized to tray (only once)."""
        if not self._notification_shown:
            self.notify("App is running in the background.", "Minimized to Tray")
            self._schedule_notification_clear(delay=1.25)
            self._notification_shown = True

    def update_menu(self):
        """Force menu update (if supported by backend)."""
        if self._icon:
            try:
                self._icon.update_menu()
            except Exception:
                pass
