from __future__ import annotations

import shutil
import subprocess
import types
from pathlib import Path

import pytest

from src.models import app_overlay, calibration_overlay, grid_debug_overlay
from src.system_tray import SystemTray


NODE = shutil.which("node")
PACKET_VIEWER_SCRIPT = (
    Path(__file__).resolve().parents[1] / "static" / "js" / "packet_viewer.js"
)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser-script regression tests")
def test_corrupt_expanded_packet_storage_cannot_brick_route_initialization():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

for (const storedValue of ['{broken-json', '{"not":"an-array"}']) {
    let removed = 0;
    let domReadyHandler = null;
    const storage = {
        getItem(key) {
            if (key !== 'packetViewerExpanded') throw new Error('unexpected key');
            return storedValue;
        },
        removeItem(key) {
            if (key !== 'packetViewerExpanded') throw new Error('unexpected key');
            removed += 1;
        },
        setItem() {}
    };
    const windowObject = {};
    const documentObject = {
        readyState: 'loading',
        addEventListener(type, handler) {
            if (type === 'DOMContentLoaded') domReadyHandler = handler;
        }
    };
    const context = vm.createContext({
        window: windowObject,
        document: documentObject,
        localStorage: storage,
        console,
        Set,
        Map,
        JSON,
        Number,
        Array
    });

    new vm.Script(source, { filename: 'packet_viewer.js' }).runInContext(context);
    if (removed !== 1) {
        throw new Error(`invalid storage was not cleared: ${storedValue}`);
    }
    if (typeof domReadyHandler !== 'function') {
        throw new Error('packet viewer initialization was not registered');
    }
}
"""

    result = subprocess.run(
        [NODE, "-e", harness, str(PACKET_VIEWER_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_app_overlay_creation_failure_does_not_publish_visible_state():
    manager = app_overlay.AppOverlayManager()
    manager._enabled = True
    manager._server = object()
    manager._create_window = lambda: None
    manager._ready_event = types.SimpleNamespace(
        clear=lambda: None,
        wait=lambda timeout=None: False,
    )

    manager.show()

    assert manager._window is None
    assert manager.is_visible is False


def test_app_overlay_create_cannot_resurrect_after_destroy(monkeypatch):
    manager = app_overlay.AppOverlayManager()
    manager._enabled = True
    manager._server = object()
    manager._resolve_overlay_url = lambda: "http://127.0.0.1:1234/overlay"
    manager._get_display_resolution = lambda: (1920, 1080)

    destroyed = []
    fake_window = types.SimpleNamespace(destroy=lambda: destroyed.append(True))

    def create_window(*_args, **_kwargs):
        manager.destroy()
        return fake_window

    monkeypatch.setattr(
        app_overlay,
        "webview",
        types.SimpleNamespace(create_window=create_window),
    )
    monkeypatch.setattr(app_overlay.sys, "platform", "linux")

    manager._create_window()

    assert destroyed == [True]
    assert manager._window is None
    assert manager.is_enabled is False


def test_calibration_timeout_requests_window_close(monkeypatch):
    def point(x, y):
        return types.SimpleNamespace(x=x, y=y)

    positions = {
        "jump": 40.0,
        "stash": point(100, 200),
        "inv": point(300, 400),
        "stash_tab_origin": point(80, 210),
        "stash_tab_spacing": 47.0,
    }
    monkeypatch.setattr(calibration_overlay.macros, "get_screen_positions", lambda: positions)
    monkeypatch.setattr(
        calibration_overlay.macros,
        "get_current_resolution",
        lambda: (1920, 1080),
        raising=False,
    )
    monkeypatch.setattr(
        calibration_overlay.macros,
        "get_base_screen_positions",
        lambda: positions,
        raising=False,
    )
    monkeypatch.setattr(calibration_overlay.macros, "STASH_TAB_COUNT", 8, raising=False)
    monkeypatch.setattr(
        calibration_overlay.macros,
        "STASH_TAB_LABELS",
        [str(index) for index in range(8)],
        raising=False,
    )

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

    class FakeEvent:
        def clear(self):
            pass

        def wait(self, timeout=None):
            return False

    monkeypatch.setattr(calibration_overlay.threading, "Thread", FakeThread)
    overlay = calibration_overlay.CalibrationOverlay()
    overlay._enabled = True
    overlay._ready = FakeEvent()
    overlay._done = FakeEvent()
    overlay.OPEN_TIMEOUT_SECONDS = 0.0
    closed = []
    overlay._request_close = lambda: closed.append(True)

    result = overlay.open()

    assert result == {"saved": False}
    assert closed == [True]


def test_stale_grid_overlay_timer_cannot_close_newer_window(monkeypatch):
    posted = []
    overlay = grid_debug_overlay.GridDebugOverlay()
    overlay._generation = 2
    overlay._hwnd = 202
    monkeypatch.setattr(grid_debug_overlay.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        grid_debug_overlay,
        "win32gui",
        types.SimpleNamespace(PostMessage=lambda *args: posted.append(args)),
    )
    monkeypatch.setattr(
        grid_debug_overlay,
        "win32con",
        types.SimpleNamespace(WM_CLOSE=0x0010),
    )

    overlay._auto_close_after(10.0, generation=1, hwnd=101)
    assert posted == []

    overlay._auto_close_after(10.0, generation=2, hwnd=202)
    assert posted == [(202, 0x0010, 0, 0)]


def test_system_tray_stop_cancels_timer_and_joins_run_thread():
    events = []

    class FakeIcon:
        def stop(self):
            events.append("icon-stop")

    class FakeTimer:
        def is_alive(self):
            return True

        def cancel(self):
            events.append("timer-cancel")

    class FakeThread:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            events.append(("thread-join", timeout))
            self.alive = False

    tray = SystemTray(
        "DnDTools",
        "test",
        None,
        lambda: None,
        lambda: None,
        None,
    )
    tray._running = True
    tray._icon = FakeIcon()
    tray._icon_thread = FakeThread()
    tray._notification_timer = FakeTimer()

    tray.stop()

    assert events == ["timer-cancel", "icon-stop", ("thread-join", 2.0)]
    assert tray._notification_timer is None
    assert tray._icon is None
    assert tray._icon_thread is None
    assert tray._running is False
