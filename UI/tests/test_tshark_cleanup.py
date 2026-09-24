from __future__ import annotations

import json
import logging
import threading
import types

import psutil

from utils import game_window_watcher, single_instance
from utils import tshark_cleanup
from src.models import hotkeys


class FakeProcess:
    def __init__(self, pid, name, create_time, *, running=True, status="running"):
        self.pid = pid
        self._name = name
        self._create_time = create_time
        self._running = running
        self._status = status
        self.killed = False

    def name(self):
        return self._name

    def create_time(self):
        return self._create_time

    def is_running(self):
        return self._running

    def status(self):
        return self._status

    def kill(self):
        self.killed = True


def _register(path, helper, *, owner_pid=900, owner_create_time=50.0, session="session-a"):
    identities = tshark_cleanup.register_owned_helpers(
        [helper],
        owner_pid=owner_pid,
        owner_create_time=owner_create_time,
        session_id=session,
        registry_path=path,
    )
    assert identities == {(helper.pid, helper.create_time())}


def test_cleanup_only_considers_exact_registered_helpers(tmp_path, monkeypatch):
    registry = tmp_path / "helpers.json"
    helper = FakeProcess(10, "tshark.exe", 25.0)
    unrelated = FakeProcess(20, "tshark.exe", 30.0)
    _register(registry, helper)

    inspected = []

    def get_process(pid):
        inspected.append(pid)
        if pid == helper.pid:
            return helper
        if pid == 900:
            raise psutil.NoSuchProcess(pid)
        raise AssertionError(f"cleanup inspected an unregistered process: {pid}")

    monkeypatch.setattr(tshark_cleanup.psutil, "Process", get_process)
    monkeypatch.setattr(
        tshark_cleanup.psutil,
        "process_iter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("system-wide process scanning is forbidden")
        ),
    )

    stats = tshark_cleanup._scan_and_cleanup(
        123,
        registry_path=registry,
    )

    assert helper.killed is True
    assert unrelated.killed is False
    assert inspected == [10, 900]
    assert stats["killed"] == 1
    assert stats["kill_reasons"] == {"owner_missing": 1}
    assert not registry.exists()


def test_pid_reuse_never_kills_a_different_helper_instance(tmp_path, monkeypatch):
    registry = tmp_path / "helpers.json"
    registered = FakeProcess(10, "dumpcap.exe", 25.0)
    reused_pid = FakeProcess(10, "dumpcap.exe", 40.0)
    _register(registry, registered)

    monkeypatch.setattr(tshark_cleanup.psutil, "Process", lambda _pid: reused_pid)

    stats = tshark_cleanup._scan_and_cleanup(123, registry_path=registry)

    assert reused_pid.killed is False
    assert stats["identity_mismatch"] == 1
    assert stats["skip_reasons"] == {"helper_identity_mismatch": 1}
    assert not registry.exists()


def test_active_registered_owner_protects_current_capture(tmp_path, monkeypatch):
    registry = tmp_path / "helpers.json"
    helper = FakeProcess(10, "tshark.exe", 25.0)
    owner = FakeProcess(900, "dndtools.exe", 50.0)
    _register(registry, helper)

    processes = {helper.pid: helper, owner.pid: owner}
    monkeypatch.setattr(tshark_cleanup.psutil, "Process", processes.__getitem__)

    stats = tshark_cleanup._scan_and_cleanup(123, registry_path=registry)

    assert helper.killed is False
    assert stats.get("killed", 0) == 0
    assert stats["skip_reasons"] == {"active_owner": 1}
    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert payload["helpers"][0]["pid"] == helper.pid
    assert payload["helpers"][0]["create_time"] == helper.create_time()


def test_corrupt_or_missing_registry_never_falls_back_to_name_scanning(tmp_path, monkeypatch):
    registry = tmp_path / "helpers.json"
    registry.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        tshark_cleanup.psutil,
        "process_iter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("system-wide process scanning is forbidden")
        ),
    )
    monkeypatch.setattr(
        tshark_cleanup.psutil,
        "Process",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("no registry identity should be inspected")
        ),
    )

    stats = tshark_cleanup._scan_and_cleanup(123, registry_path=registry)

    assert stats.get("candidates", 0) == 0
    assert stats["skip_reasons"] == {}
    assert stats["kill_reasons"] == {}


def test_single_instance_release_wakes_listener_before_closing_event(monkeypatch):
    calls = []

    class FakeKernel32:
        def SetEvent(self, handle):
            calls.append(("set", handle))
            return 1

        def CloseHandle(self, handle):
            calls.append(("close", handle))
            return 1

    class FakeListener:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            calls.append(("join", timeout))
            self.alive = False

    fake_kernel32 = FakeKernel32()
    monkeypatch.setattr(single_instance.sys, "platform", "win32")
    monkeypatch.setattr(
        single_instance,
        "ctypes",
        types.SimpleNamespace(
            windll=types.SimpleNamespace(kernel32=fake_kernel32),
        ),
    )

    guard = single_instance.SingleInstanceGuard("DnDToolsTest")
    guard._event_handle = 101
    guard._mutex_handle = 202
    guard._listener_thread = FakeListener()

    guard.release()

    assert calls == [
        ("set", 101),
        ("join", 2.0),
        ("close", 101),
        ("close", 202),
    ]
    assert guard._event_handle is None
    assert guard._mutex_handle is None
    assert guard._listener_thread is None


def test_single_instance_shutdown_wakeup_does_not_restore_window(monkeypatch):
    wait_event = threading.Event()
    callback_calls = []

    class FakeKernel32:
        def CreateEventW(self, *_args):
            return 303

        def WaitForSingleObject(self, _handle, _timeout):
            wait_event.wait(timeout=1.0)
            return single_instance._WAIT_OBJECT_0

        def SetEvent(self, _handle):
            wait_event.set()
            return 1

        def ResetEvent(self, _handle):
            wait_event.clear()
            return 1

        def CloseHandle(self, _handle):
            return 1

    monkeypatch.setattr(single_instance.sys, "platform", "win32")
    monkeypatch.setattr(
        single_instance,
        "ctypes",
        types.SimpleNamespace(
            windll=types.SimpleNamespace(kernel32=FakeKernel32()),
        ),
    )

    guard = single_instance.SingleInstanceGuard("DnDToolsShutdownTest")
    guard.start_listener(lambda: callback_calls.append(True))
    guard.release()

    assert callback_calls == []


def test_game_window_state_change_reports_window_geometry_updates():
    previous = {
        "hwnd": 100,
        "pid": 200,
        "title": "Dark and Darker",
        "rect": (0, 0, 1920, 1080),
        "visible": True,
        "focused": True,
        "timestamp": 1.0,
    }
    moved = dict(previous, rect=(100, 50, 1820, 1030), timestamp=2.0)
    replacement = dict(previous, pid=201, timestamp=3.0)
    timestamp_only = dict(previous, timestamp=4.0)

    assert not game_window_watcher.GameWindowWatcherProcess._states_equal(previous, moved)
    assert not game_window_watcher.GameWindowWatcherProcess._states_equal(previous, replacement)
    assert game_window_watcher.GameWindowWatcherProcess._states_equal(previous, timestamp_only)


def test_windows_hotkey_loop_exit_clears_thread_bound_registrations():
    unregistered = []

    class FakeUser32:
        def PeekMessageW(self, *_args):
            return 0

        def GetMessageW(self, *_args):
            return 0

        def UnregisterHotKey(self, _hwnd, native_id):
            unregistered.append(native_id)
            return 1

    backend = hotkeys._WindowsHotkeyBackend.__new__(hotkeys._WindowsHotkeyBackend)
    backend._logger = logging.getLogger("test.hotkey-loop-cleanup")
    backend._user32 = FakeUser32()
    backend._kernel32 = types.SimpleNamespace(GetCurrentThreadId=lambda: 77)
    backend._lock = threading.RLock()
    backend._ready = threading.Event()
    backend._thread_id = None
    backend._pending_commands = {}
    parsed = hotkeys._parse_hotkey("ctrl+f11")
    backend._bindings = {
        "sort": hotkeys._WindowsBinding(42, parsed, lambda: None),
    }
    backend._id_lookup = {42: "sort"}

    backend._message_loop()

    assert unregistered == [42]
    assert backend._bindings == {}
    assert backend._id_lookup == {}
    assert backend._thread_id is None
    assert not backend._ready.is_set()
