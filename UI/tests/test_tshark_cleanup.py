from __future__ import annotations

import json

import psutil

from utils import tshark_cleanup


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
