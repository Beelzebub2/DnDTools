import logging
import importlib
import struct
import sys
import threading
import types
from pathlib import Path
from unittest.mock import Mock

from UI.src.models.packet_buffers import (
    BoundedPacketHistory,
    FramedPacketStreams,
    estimate_json_size,
)
from UI.src.models.memory_guard import MemoryGuard


def _frame(proto_type: int, payload: bytes = b"", padding: int = 0) -> bytes:
    return struct.pack("<IHH", len(payload) + 8, proto_type, padding) + payload


def _validator(length: int, proto_type: int, padding: int) -> bool:
    return 8 <= length <= 4096 and proto_type in {1352, 1354, 1401} and padding in {0, 256}


def test_interleaved_tcp_streams_keep_independent_frame_buffers():
    captured = []
    streams = FramedPacketStreams(
        _validator,
        lambda packet, proto: captured.append((proto, packet[8:])),
        max_packet_size=4096,
    )
    merchant_list = _frame(1352, b"merchant-list")
    stock_list = _frame(1354, b"stock-list")

    streams.feed(7, merchant_list[:11], sequence=100)
    streams.feed(8, stock_list, sequence=500)
    streams.feed(7, merchant_list[11:], sequence=111)

    assert captured == [
        (1354, b"stock-list"),
        (1352, b"merchant-list"),
    ]
    assert streams.buffered_bytes == 0


def test_retransmitted_and_overlapping_segments_are_not_decoded_twice():
    captured = []
    streams = FramedPacketStreams(
        _validator,
        lambda packet, proto: captured.append((proto, packet)),
        max_packet_size=4096,
    )
    header_sized_response = _frame(1354, b"done")

    streams.feed(3, header_sized_response[:10], sequence=1000)
    # Starts two bytes before next_sequence. Only the unseen suffix is used.
    streams.feed(3, header_sized_response[8:], sequence=1008)
    # Full TCP retransmission must not create another 12-byte response.
    streams.feed(3, header_sized_response, sequence=1000)

    assert captured == [(1354, header_sized_response)]
    assert streams.buffered_bytes == 0


def test_out_of_order_segment_waits_for_gap_then_drains_in_sequence():
    captured = []
    streams = FramedPacketStreams(
        _validator,
        lambda packet, proto: captured.append((proto, packet[8:])),
        max_packet_size=4096,
    )
    quest_list = _frame(1401, b"quest-payload")

    streams.feed("quests", quest_list[:6], sequence=50)
    streams.feed("quests", quest_list[10:], sequence=60)
    assert captured == []
    streams.feed("quests", quest_list[6:10], sequence=56)

    assert captured == [(1401, b"quest-payload")]
    assert streams.buffered_bytes == 0


def test_first_observed_out_of_order_segment_does_not_become_baseline():
    captured = []
    streams = FramedPacketStreams(
        _validator,
        lambda packet, proto: captured.append((proto, packet[8:])),
        max_packet_size=4096,
    )
    quest_list = _frame(1401, b"quest-payload")

    streams.feed(
        "quests",
        quest_list[10:],
        sequence=60,
        out_of_order=True,
    )
    assert captured == []

    # Gap-filling packets may also be tagged out-of-order by tshark.
    streams.feed("quests", quest_list[:10], sequence=50, out_of_order=True)

    assert captured == [(1401, b"quest-payload")]
    assert streams.buffered_bytes == 0


def test_tcp_sequence_wraparound_keeps_segments_contiguous():
    captured = []
    streams = FramedPacketStreams(
        _validator,
        lambda packet, proto: captured.append((proto, packet[8:])),
        max_packet_size=4096,
    )
    merchant_list = _frame(1352, b"wrapped")
    first_start = (1 << 32) - 4

    streams.feed("merchant", merchant_list[:6], sequence=first_start)
    streams.feed("merchant", merchant_list[6:], sequence=2)

    assert captured == [(1352, b"wrapped")]
    assert streams.buffered_bytes == 0


def test_decoder_resynchronizes_when_capture_starts_mid_packet():
    captured = []
    desyncs = []
    streams = FramedPacketStreams(
        _validator,
        lambda packet, proto: captured.append((proto, packet[8:])),
        max_packet_size=4096,
        on_desync=lambda stream, count, reason: desyncs.append((stream, count, reason)),
    )

    streams.feed("late", b"mid-packet-bytes" + _frame(1352, b"ok"))

    assert captured == [(1352, b"ok")]
    assert desyncs
    assert desyncs[0][1] == len(b"mid-packet-bytes")


def test_packet_history_evicts_by_bytes_and_supports_incremental_reads():
    history = BoundedPacketHistory(max_packets=10, max_bytes=1000)
    for packet_id in range(1, 7):
        history.append(
            {"id": packet_id, "type": "A" if packet_id % 2 else "B"},
            size_bytes=250,
        )

    assert [packet["id"] for packet in history.snapshot()] == [3, 4, 5, 6]
    assert history.total_bytes == 1000
    assert [packet["id"] for packet in history.snapshot(limit=2)] == [5, 6]
    assert [
        packet["id"]
        for packet in history.snapshot(after_id=3, packet_types={"B"}, limit=2)
    ] == [4, 6]


def test_stream_reassembly_has_a_global_byte_cap():
    desyncs = []
    streams = FramedPacketStreams(
        _validator,
        lambda packet, proto: None,
        max_packet_size=4096,
        max_pending_bytes=4096,
        max_total_buffered_bytes=10,
        on_desync=lambda stream, count, reason: desyncs.append(
            (stream, count, reason)
        ),
    )

    streams.feed("oldest", b"12345")
    streams.feed("middle", b"12345")
    streams.feed("newest", b"12345")

    assert streams.buffered_bytes <= 10
    assert streams.stream_count == 2
    assert desyncs == [
        ("oldest", 5, "global TCP reassembly memory limit exceeded")
    ]


def _load_capture_module(tmp_path, monkeypatch):
    # Import through the same top-level package layout used by UI/app.py.
    ui_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(ui_root)
    # Cleanup itself is independent of packet decoding; avoid initializing a
    # real WinPcap/tshark stack in the unit-test process.
    monkeypatch.setitem(sys.modules, "pyshark", types.ModuleType("pyshark"))
    appdirs_module = sys.modules["src.models.appdirs"]
    monkeypatch.setattr(
        appdirs_module,
        "get_capture_state_file",
        lambda: str(tmp_path / "capture_state.json"),
        raising=False,
    )
    monkeypatch.setattr(appdirs_module, "is_frozen", lambda: False, raising=False)
    settings_module = types.ModuleType("src.models.settings")
    settings_module.settings_manager = types.SimpleNamespace(
        get=lambda *args, **kwargs: None
    )
    settings_module.resolve_tshark_executable = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "src.models.settings", settings_module)
    import src.models.capture_utils as capture_utils
    monkeypatch.setattr(capture_utils, "patch_asyncio", lambda: None)
    monkeypatch.setattr(capture_utils, "patch_pyshark", lambda: None)
    sys.modules.pop("src.models.capture", None)
    return importlib.import_module("src.models.capture")


def test_capture_cleanup_does_not_delete_unrelated_temp_pcaps(tmp_path, monkeypatch):
    unrelated = tmp_path / "unrelated-wireshark.pcapng"
    unrelated.write_bytes(b"unrelated")

    try:
        capture_module = _load_capture_module(tmp_path, monkeypatch)
        PacketCapture = capture_module.PacketCapture

        capture = PacketCapture.__new__(PacketCapture)
        capture.logger = logging.getLogger("test.capture.cleanup")
        capture._cleanup_complete = threading.Event()
        capture._cleanup_lock = threading.Lock()
        capture._current_capture = None
        capture._current_loop = None
        capture._packet_streams = Mock()
        capture.packet_data = b"buffered"
        capture.expected_packet_length = 12
        capture.expected_proto_type = 1352
        capture._terminate_capture_processes = Mock()

        capture._cleanup_capture()
    finally:
        sys.modules.pop("src.models.capture", None)

    assert capture._cleanup_complete.is_set()
    capture._packet_streams.clear.assert_called_once_with()
    capture._terminate_capture_processes.assert_called_once_with()
    assert unrelated.read_bytes() == b"unrelated"


def test_capture_memory_usage_includes_owned_helpers_once(tmp_path, monkeypatch):
    capture_module = _load_capture_module(tmp_path, monkeypatch)

    class FakeProcess:
        def __init__(self, pid, rss):
            self.pid = pid
            self._rss = rss

        def memory_info(self):
            return types.SimpleNamespace(rss=self._rss)

    parent = FakeProcess(10, 3 * 1024 * 1024)
    helper = FakeProcess(20, 2 * 1024 * 1024)
    duplicate_helper = FakeProcess(20, 99 * 1024 * 1024)
    monkeypatch.setattr(capture_module.psutil, "Process", lambda *_args: parent)

    capture = capture_module.PacketCapture.__new__(capture_module.PacketCapture)
    capture.logger = logging.getLogger("test.capture.memory")
    capture._collect_capture_processes = Mock(
        return_value=[helper, duplicate_helper]
    )

    assert capture._capture_memory_usage_mb() == 5.0


def test_default_capture_memory_guard_allows_observed_healthy_baseline(tmp_path, monkeypatch):
    capture_module = _load_capture_module(tmp_path, monkeypatch)
    try:
        # Live validation sits just above 1 GiB. The default must leave useful
        # headroom while still intervening long before the reported 20 GiB hang.
        assert 1536 <= capture_module.PacketCapture.DEFAULT_TSHARK_MEMORY_LIMIT_MB < 20 * 1024
    finally:
        sys.modules.pop("src.models.capture", None)


def test_capture_records_helper_pid_and_creation_time(tmp_path, monkeypatch):
    capture_module = _load_capture_module(tmp_path, monkeypatch)

    class FakeHelper:
        pid = 77

        @staticmethod
        def name():
            return "tshark.exe"

        @staticmethod
        def create_time():
            return 123.5

    calls = []

    def register(processes, **kwargs):
        calls.append((list(processes), kwargs))
        return {(77, 123.5)}

    monkeypatch.setattr(capture_module, "register_owned_helpers", register)
    capture = capture_module.PacketCapture.__new__(capture_module.PacketCapture)
    capture.logger = logging.getLogger("test.capture.registry")
    capture._helper_owner_pid = 42
    capture._helper_owner_create_time = 100.25
    capture._helper_session_id = "capture-session"
    capture._helper_registry_path = tmp_path / "helpers.json"
    capture._collect_capture_processes = Mock(return_value=[FakeHelper()])

    assert capture._record_owned_capture_processes() == {(77, 123.5)}
    assert len(calls) == 1
    processes, kwargs = calls[0]
    assert len(processes) == 1
    assert processes[0].pid == 77
    assert kwargs == {
        "owner_pid": 42,
        "owner_create_time": 100.25,
        "session_id": "capture-session",
        "registry_path": tmp_path / "helpers.json",
    }


def test_memory_guard_uses_injected_aggregate_usage_provider():
    callbacks = []
    guard = None

    def on_threshold():
        callbacks.append(True)
        guard._stop_event.set()

    guard = MemoryGuard(
        threshold_mb=100,
        check_interval=0.01,
        on_threshold_exceeded=on_threshold,
        usage_provider=lambda: 125.0,
    )

    guard._loop()

    assert callbacks == [True]


def test_stuck_capture_thread_is_preserved_and_blocks_restart(tmp_path, monkeypatch):
    capture_module = _load_capture_module(tmp_path, monkeypatch)

    class StuckThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    capture = capture_module.PacketCapture.__new__(capture_module.PacketCapture)
    capture.logger = logging.getLogger("test.capture.stuck")
    capture._state_lock = threading.Lock()
    capture._force_closing = False
    capture.running = True
    capture.capture_thread = StuckThread()
    capture._stop_event = threading.Event()
    capture._cleanup_complete = Mock()
    capture._cleanup_complete.wait.return_value = False
    capture._save_state = Mock()
    capture._stop_memory_guard = Mock()
    capture._request_capture_shutdown = Mock()
    capture._terminate_capture_processes = Mock()

    assert capture.stop_capture_switch() is False
    assert isinstance(capture.capture_thread, StuckThread)
    assert capture._force_closing is True
    capture._terminate_capture_processes.assert_called_once_with()
    assert capture.start_capture_switch() is False


def test_already_stopped_capture_resets_force_closing(tmp_path, monkeypatch):
    capture_module = _load_capture_module(tmp_path, monkeypatch)
    capture = capture_module.PacketCapture.__new__(capture_module.PacketCapture)
    capture.logger = logging.getLogger("test.capture.already-stopped")
    capture._state_lock = threading.Lock()
    capture._force_closing = False
    capture.running = False
    capture.capture_thread = None

    assert capture.stop_capture_switch() is True
    assert capture._force_closing is False


def test_start_capture_resets_force_closing_after_clean_stop(tmp_path, monkeypatch):
    capture_module = _load_capture_module(tmp_path, monkeypatch)

    class DeferredThread:
        def __init__(self, target=None, daemon=None, name=None):
            self.target = target
            self.daemon = daemon
            self.name = name
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return False

    monkeypatch.setattr(capture_module.threading, "Thread", DeferredThread)
    capture = capture_module.PacketCapture.__new__(capture_module.PacketCapture)
    capture.logger = logging.getLogger("test.capture.clean-restart")
    capture._state_lock = threading.Lock()
    capture._force_closing = True
    capture.running = False
    capture.capture_thread = None
    capture._cleanup_complete = threading.Event()
    capture._cleanup_complete.set()
    capture._stop_event = threading.Event()
    capture._cleanup_capture_on_exit = False
    capture._user_requested_stop = True
    capture._save_state = Mock()
    capture._start_memory_guard = Mock()

    assert capture.start_capture_switch() is True
    assert capture._force_closing is False
    assert capture.running is True
    assert capture.capture_thread.started is True


def test_memory_restart_does_not_start_after_failed_stop(tmp_path, monkeypatch):
    capture_module = _load_capture_module(tmp_path, monkeypatch)
    capture = capture_module.PacketCapture.__new__(capture_module.PacketCapture)
    capture.logger = logging.getLogger("test.capture.restart")
    capture._state_lock = threading.Lock()
    capture.running = True
    capture.capture_thread = Mock()
    capture.capture_thread.is_alive.return_value = True
    capture._user_requested_stop = False
    capture._force_closing = False
    capture.stop_capture_switch = Mock(return_value=False)
    capture.start_capture_switch = Mock(return_value=True)

    capture._restart_capture_due_to_memory()

    capture.stop_capture_switch.assert_called_once_with(persist_running_state=True)
    capture.start_capture_switch.assert_not_called()


def test_json_size_estimation_stops_after_limit_is_exceeded():
    value = {"items": ["x" * 200 for _ in range(100)]}

    limited = estimate_json_size(value, max_bytes=512)
    exact = estimate_json_size(value)

    assert 512 < limited <= exact
    assert exact > 20_000
