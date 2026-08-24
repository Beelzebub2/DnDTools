import os
import sys
import subprocess
import asyncio
import logging
import socket
import psutil
import struct
import json
import threading
import time
import importlib
import uuid
from datetime import datetime
from typing import Tuple, Optional, List, Dict, Any, Set, Iterable
from concurrent.futures import TimeoutError as FutureTimeout
from google.protobuf.json_format import MessageToDict

import pyshark

from .appdirs import get_capture_state_file, is_frozen
from src.models.settings import settings_manager, resolve_tshark_executable
from src.models.capture_utils import patch_asyncio, patch_pyshark, finalize_asyncio_subprocess
from src.models.memory_guard import MemoryGuard
from src.models.packet_buffers import (
    BoundedPacketHistory,
    FramedPacketStreams,
    estimate_json_size,
)
from utils.tshark_cleanup import register_owned_helpers, prune_owned_helper_registry

# Apply patches
patch_asyncio()
patch_pyshark()

logger = logging.getLogger(__name__)

# Determine paths
current_dir = os.path.dirname(os.path.abspath(__file__))
ui_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
protos_path = os.path.join(ui_root, "networking", "protos")

# Ensure the protos path is on sys.path
if protos_path not in sys.path:
    sys.path.insert(0, protos_path)

# Dynamically load protos
# ─── Proto loading & automatic mapping ───────────────────────────────────────
# Loads all *_pb2.py modules, injects their public symbols into globals(),
# then builds PROTO_MAP — a Dict[int, type] that maps every PacketCommand
# enum value to its protobuf message class.  This eliminates per-packet
# name guessing and gives O(1) lookups at capture time.

def _load_protos():
    loaded_protos = {}
    if not os.path.exists(protos_path):
        logger.warning(f"Protos path not found: {protos_path}")
        return loaded_protos

    for filename in os.listdir(protos_path):
        if not filename.endswith("_pb2.py"):
            continue

        module_name = filename[:-3]
        full_name = f"networking.protos.{module_name}"
        file_path = os.path.join(protos_path, filename)

        try:
            spec = importlib.util.spec_from_file_location(full_name, file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[full_name] = module
                spec.loader.exec_module(module)

                # Bring public names into globals() — other code may rely on this
                for attr in dir(module):
                    if not attr.startswith("_"):
                        globals()[attr] = getattr(module, attr)
                        loaded_protos[attr] = getattr(module, attr)
        except Exception as e:
            logger.error(f"Failed to load proto {filename}: {e}")
    return loaded_protos

_loaded_proto_symbols = _load_protos()

# Import PacketCommand after dynamic loading
try:
    from networking.protos import _PacketCommand_pb2
except ImportError:
    logger.error("Could not import _PacketCommand_pb2. Ensure protos are generated and path is correct.")
    _PacketCommand_pb2 = None

_PACKET_COMMAND_VALUES = frozenset(
    _PacketCommand_pb2.PacketCommand.values()
) if _PacketCommand_pb2 else frozenset()


def _build_proto_map() -> Dict[int, Any]:
    """Build a mapping of PacketCommand int → protobuf message class.

    Iterates every value in the PacketCommand enum and attempts to match it
    to a loaded proto class using the standard naming conventions:
      1. "S" + command_name  (e.g. S2C_ALIVE_RES → SS2C_ALIVE_RES)
      2. command_name as-is  (fallback)

    Only classes that have a ``ParseFromString`` method (i.e. real protobuf
    messages) are included.
    """
    proto_map: Dict[int, Any] = {}
    if not _PacketCommand_pb2:
        return proto_map

    g = globals()
    skipped_prefixes = ('MIN_', 'MAX_', 'PACKET_NONE')
    mapped = 0
    unmapped_names: List[str] = []

    for value in _PacketCommand_pb2.PacketCommand.values():
        try:
            name = _PacketCommand_pb2.PacketCommand.Name(value)
        except (ValueError, KeyError):
            continue
        if name.startswith(skipped_prefixes):
            continue

        # Try naming candidates
        for candidate in ("S" + name, name):
            cls = g.get(candidate)
            if cls is not None and callable(getattr(cls, 'ParseFromString', None)):
                proto_map[value] = cls
                mapped += 1
                break
        else:
            unmapped_names.append(name)

    total = mapped + len(unmapped_names)
    logger.info(
        f"Proto map built: {mapped}/{total} packet types have proto classes "
        f"({len(unmapped_names)} unmapped)"
    )
    if unmapped_names:
        logger.debug(f"Unmapped packet types: {', '.join(sorted(unmapped_names))}")

    return proto_map


# Module-level map — built once at import time
PROTO_MAP: Dict[int, Any] = _build_proto_map()

# Configure subprocess to hide console windows when in executable mode
if is_frozen():
    original_popen = subprocess.Popen
    
    def hidden_popen(*args, **kwargs):
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs['startupinfo'] = startupinfo
        return original_popen(*args, **kwargs)
    
    subprocess.Popen = hidden_popen


def _format_hexdump(data: bytes, width: int = 16) -> List[str]:
    lines: List[str] = []
    if width <= 0:
        width = 16

    for offset in range(0, len(data), width):
        chunk = data[offset:offset + width]
        hex_bytes = ' '.join(f"{byte:02X}" for byte in chunk)
        ascii_repr = ''.join(chr(byte) if 32 <= byte <= 126 else '.' for byte in chunk)
        pad = (width - len(chunk)) * 3
        lines.append(f"{offset:04X}  {hex_bytes}{' ' * pad}  {ascii_repr}")

    return lines

def _read_positive_float_env(var_name: str, default: float) -> float:
    raw_value = os.environ.get(var_name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def _read_tcp_int_field(layer: Any, *field_names: str) -> Optional[int]:
    """Read a decimal/hex tshark field without depending on its field class."""
    for field_name in field_names:
        try:
            raw_value = getattr(layer, field_name, None)
        except Exception:
            continue
        if raw_value is None:
            continue
        text = str(raw_value).strip()
        try:
            return int(text, 0)
        except (TypeError, ValueError):
            try:
                return int(text)
            except (TypeError, ValueError):
                continue
    return None


def _tcp_has_field(layer: Any, *field_names: str) -> bool:
    """Return whether tshark exposed a presence/analysis field on a layer."""
    try:
        available = set(getattr(layer, 'field_names', ()) or ())
    except Exception:
        available = set()

    for field_name in field_names:
        if field_name in available:
            return True
        try:
            raw_value = getattr(layer, field_name, None)
        except Exception:
            continue
        if raw_value is None:
            continue
        if str(raw_value).strip().lower() not in ('', '0', 'false', 'none'):
            return True
    return False

class PacketCapture:
    # Includes this application plus owned tshark/dumpcap helpers. A normal
    # capture session is roughly 1 GiB, so keep enough headroom for healthy
    # operation while still restarting far below the reported 20 GiB runaway.
    DEFAULT_TSHARK_MEMORY_LIMIT_MB = 2048.0
    DEFAULT_TSHARK_MEMORY_CHECK_SEC = 15.0
    DEFAULT_TSHARK_MEMORY_RESTART_COOLDOWN_SEC = 120.0
    DEFAULT_PACKET_HISTORY_MAX_COUNT = 1000
    DEFAULT_PACKET_HISTORY_MAX_BYTES = 16 * 1024 * 1024
    DEFAULT_PACKET_JSON_MAX_BYTES = 512 * 1024
    DEFAULT_PACKET_JSON_MAX_WIRE_BYTES = 256 * 1024
    DEFAULT_TCP_REASSEMBLY_MAX_BYTES = 32 * 1024 * 1024
    DEFAULT_UNPARSED_PREVIEW_BYTES = 256

    def __init__(self, interface: str = 'Ethernet', port_range: Tuple[int, int] = (20200, 20300), wireshark_path: Optional[str] = None):
        self.interface = interface
        self.port_range = port_range
        # Backwards-compatible aliases for callers that inspect the default
        # stream. Actual framing state is isolated per tcp.stream below.
        self.packet_data = b""
        self.logger = logging.getLogger(__name__)
        self.MAX_BUFFER_SIZE = 2 * 1024 * 1024
        self.expected_packet_length = None
        self.expected_proto_type = None
        self.running = False
        self.capture_thread: Optional[threading.Thread] = None
        self._cleanup_capture_on_exit = False
        self.capture_info: Dict[int, Any] = {}
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cleanup_lock = threading.Lock()
        self._cleanup_complete = threading.Event()
        self._cleanup_complete.set()
        self._helper_session_id = uuid.uuid4().hex
        self._helper_owner_pid = os.getpid()
        try:
            self._helper_owner_create_time = psutil.Process(
                self._helper_owner_pid
            ).create_time()
        except (psutil.Error, OSError):
            self._helper_owner_create_time = None
        self._helper_registry_path = None
        self._helper_tracker_stop = threading.Event()
        self._helper_tracker_thread: Optional[threading.Thread] = None
        self.STATE_FILE = get_capture_state_file()
        self.tshark_path = resolve_tshark_executable(wireshark_path) or resolve_tshark_executable(settings_manager.get('wiresharkPath'))
        self._apply_tshark_environment()
        self._user_requested_stop = False
        self._force_closing = False
        
        # Memory Guard
        self._memory_guard: Optional[MemoryGuard] = None
        self._memory_guard_last_restart: float = 0.0
        self._memory_guard_restart_cooldown: float = self.DEFAULT_TSHARK_MEMORY_RESTART_COOLDOWN_SEC

        # Quest persistence
        self.quests_dir = None
        self._save_quest_packets = False
        self._quest_packet_types = {}
        if _PacketCommand_pb2:
            for packet_name in (
                'S2C_MERCHANT_LIST_RES',
                'S2C_MERCHANT_QUEST_LIST_INFO_RES',
                'S2C_MERCHANT_QUEST_LOG_LIST_RES',
            ):
                try:
                    value = _PacketCommand_pb2.PacketCommand.Value(packet_name)
                    self._quest_packet_types[value] = packet_name
                except ValueError:
                    self.logger.debug(f"Quest packet {packet_name} not present in PacketCommand enum")
        
        # Packet viewer storage. A count-only deque allowed a thousand expanded
        # multi-megabyte protobuf dictionaries to consume tens of gigabytes.
        history_count = int(_read_positive_float_env(
            "DND_PACKET_HISTORY_MAX_COUNT",
            self.DEFAULT_PACKET_HISTORY_MAX_COUNT,
        ))
        history_bytes = int(_read_positive_float_env(
            "DND_PACKET_HISTORY_MAX_BYTES",
            self.DEFAULT_PACKET_HISTORY_MAX_BYTES,
        ))
        self._packet_json_max_bytes = int(_read_positive_float_env(
            "DND_PACKET_JSON_MAX_BYTES",
            self.DEFAULT_PACKET_JSON_MAX_BYTES,
        ))
        self._packet_json_max_wire_bytes = int(_read_positive_float_env(
            "DND_PACKET_JSON_MAX_WIRE_BYTES",
            self.DEFAULT_PACKET_JSON_MAX_WIRE_BYTES,
        ))
        reassembly_bytes = int(_read_positive_float_env(
            "DND_TCP_REASSEMBLY_MAX_BYTES",
            self.DEFAULT_TCP_REASSEMBLY_MAX_BYTES,
        ))
        self.captured_packets = BoundedPacketHistory(
            max_packets=history_count,
            max_bytes=history_bytes,
        )
        self._packet_streams = FramedPacketStreams(
            self.validate_packet_header,
            self.handle_packet,
            max_packet_size=self.MAX_BUFFER_SIZE,
            max_pending_bytes=self.MAX_BUFFER_SIZE,
            max_total_buffered_bytes=reassembly_bytes,
            on_desync=self._on_stream_desync,
        )
        # Monotonic packet id counter for stable UI keys
        self._packet_id_counter = 0
        
        # Restore state
        self.saved_state = self._restore_state()
        self.was_running_before = self.saved_state.get('running', False)
        
        if self.was_running_before:
            self.logger.info("Previous session had capture running - restoring state")
            threading.Timer(0.1, self._delayed_start).start()
        else:
            self.logger.info("Previous session had capture stopped")

    def _delayed_start(self):
        self.start_capture_switch()

    def _apply_tshark_environment(self):
        if not self.tshark_path:
            return
        try:
            os.environ['PYSHARK_TSHARK_PATH'] = self.tshark_path
            bin_dir = os.path.dirname(self.tshark_path)
            if bin_dir and os.path.isdir(bin_dir):
                current_path = os.environ.get('PATH', '')
                segments = current_path.split(os.pathsep) if current_path else []
                if bin_dir not in segments:
                    os.environ['PATH'] = os.pathsep.join([bin_dir] + segments) if segments else bin_dir
        except Exception as exc:
            self.logger.debug(f"Failed to update environment for tshark: {exc}")

    def set_wireshark_path(self, wireshark_path: Optional[str]) -> bool:
        resolved = resolve_tshark_executable(wireshark_path)
        if resolved == self.tshark_path:
            return False
        self.tshark_path = resolved
        if self.tshark_path:
            self.logger.info(f"Using tshark at: {self.tshark_path}")
        else:
            self.logger.warning("Cleared custom tshark path; relying on system PATH")
        self._apply_tshark_environment()
        return True

    def _init_memory_guard(self):
        threshold_mb = _read_positive_float_env("DND_TSHARK_MEMORY_LIMIT_MB", self.DEFAULT_TSHARK_MEMORY_LIMIT_MB)
        check_interval = max(5.0, _read_positive_float_env("DND_TSHARK_MEMORY_CHECK_SEC", self.DEFAULT_TSHARK_MEMORY_CHECK_SEC))
        self._memory_guard_restart_cooldown = max(60.0, _read_positive_float_env("DND_TSHARK_MEMORY_RESTART_COOLDOWN_SEC", self.DEFAULT_TSHARK_MEMORY_RESTART_COOLDOWN_SEC))
        
        self._memory_guard = MemoryGuard(
            threshold_mb=threshold_mb,
            check_interval=check_interval,
            on_threshold_exceeded=self._on_memory_threshold_exceeded,
            usage_provider=self._capture_memory_usage_mb,
        )

    def _start_memory_guard(self):
        if not self._memory_guard:
            self._init_memory_guard()
        if self._memory_guard:
            self._memory_guard.start()

    def _stop_memory_guard(self):
        if self._memory_guard:
            self._memory_guard.stop()

    def _on_memory_threshold_exceeded(self):
        # This runs in the MemoryGuard thread
        now = time.time()
        if now - self._memory_guard_last_restart < self._memory_guard_restart_cooldown:
            return

        self._memory_guard_last_restart = now
        self.logger.warning("Restarting capture due to memory threshold exceeded.")
        
        threading.Thread(
            target=self._restart_capture_due_to_memory,
            name="TsharkMemoryGuardRestart",
            daemon=True,
        ).start()

    def _restart_capture_due_to_memory(self):
        with self._state_lock:
            should_restart = self.running or (self.capture_thread is not None and self.capture_thread.is_alive())

        if not should_restart:
            return

        if self._user_requested_stop or self._force_closing:
            return

        try:
            if not self.stop_capture_switch(persist_running_state=True):
                self.logger.error(
                    "Capture memory restart aborted because the previous capture did not stop cleanly"
                )
                return
            time.sleep(1.0)
            if not self.start_capture_switch():
                self.logger.error(
                    "Capture memory restart could not start a replacement capture"
                )
        except Exception as exc:
            self.logger.error(f"Failed to restart capture after memory guard stop: {exc}", exc_info=True)

    def should_auto_start(self):
        return self.was_running_before

    def parse_proto(self, packet_data, proto_type):
        """Deserialize a packet's payload using the pre-built PROTO_MAP."""
        message, _error = self._parse_proto_with_error(packet_data, proto_type)
        return message

    def _parse_proto_with_error(self, packet_data, proto_type):
        """Deserialize a payload and retain a concise diagnostic on failure."""
        message_class = PROTO_MAP.get(proto_type)
        if message_class is None:
            return None, "No generated protobuf class is mapped to this packet type"

        data = packet_data[8:]
        try:
            message = message_class()
            message.ParseFromString(data)
            return message, None
        except Exception as e:
            try:
                name = _PacketCommand_pb2.PacketCommand.Name(proto_type)
            except (ValueError, KeyError):
                name = str(proto_type)
            self.logger.debug(f"Failed to parse {name} via {message_class.__name__}: {e}")
            detail = f"{type(e).__name__}: {e}".strip()
            return None, detail[:240]

    def get_local_ip(self) -> Optional[str]:
        for interface, addrs in psutil.net_if_addrs().items():
            if interface == self.interface:
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        return addr.address
        return None

    def validate_packet_header(self, length: int, proto_type: int, padding: int) -> bool:
        if not _PacketCommand_pb2:
            return False
        valid_packet_range = (8, 2 * 1024 * 1024)
        return (
            valid_packet_range[0] <= length <= valid_packet_range[1] and
            proto_type in _PACKET_COMMAND_VALUES and
            padding in [0, 256]
        )

    def process_packet(
        self,
        data: bytes,
        stream_id: Optional[Any] = None,
        tcp_sequence: Optional[int] = None,
        tcp_out_of_order: bool = False,
    ) -> Optional[bool]:
        """Feed a TCP segment into connection-specific framing state.

        TCP metadata comes from tshark and remains optional for backwards
        compatibility with ordered byte-stream callers.
        """
        if not data:
            return False

        self._packet_streams.feed(
            stream_id,
            data,
            tcp_sequence,
            out_of_order=tcp_out_of_order,
        )

        # Preserve the legacy state attributes for the default stream.
        if stream_id is None:
            self.packet_data = self._packet_streams.get_buffer()
            self.expected_packet_length = None
            self.expected_proto_type = None
            if len(self.packet_data) >= 8:
                try:
                    length, proto_type, padding = struct.unpack(
                        '<IHH', self.packet_data[:8]
                    )
                    if self.validate_packet_header(length, proto_type, padding):
                        self.expected_packet_length = length
                        self.expected_proto_type = proto_type
                except struct.error:
                    pass
        return False

    def _on_stream_desync(self, stream_id: str, dropped: int, reason: str) -> None:
        self.logger.debug(
            "TCP stream %s dropped %s byte(s): %s",
            stream_id,
            dropped,
            reason,
        )

    def reset_state(self) -> None:
        self._packet_streams.clear()
        self.packet_data = b""
        self.expected_packet_length = None
        self.expected_proto_type = None

    def _collect_capture_processes(self) -> List[psutil.Process]:
        try:
            parent = psutil.Process(os.getpid())
        except (psutil.Error, OSError) as err:
            self.logger.debug(f"Unable to inspect child processes: {err}")
            return []

        targets: List[psutil.Process] = []
        for child in parent.children(recursive=True):
            try:
                name = child.name().lower()
            except (psutil.Error, OSError):
                continue

            if name in {'tshark', 'tshark.exe', 'dumpcap', 'dumpcap.exe'}:
                targets.append(child)
        return targets

    def _record_owned_capture_processes(
        self,
        processes: Optional[Iterable[psutil.Process]] = None,
    ) -> Set[tuple[int, float]]:
        """Persist exact identities for helpers spawned by this capture."""

        try:
            targets = list(processes) if processes is not None else self._collect_capture_processes()
            return register_owned_helpers(
                targets,
                owner_pid=getattr(self, "_helper_owner_pid", os.getpid()),
                owner_create_time=getattr(self, "_helper_owner_create_time", None),
                session_id=getattr(self, "_helper_session_id", ""),
                registry_path=getattr(self, "_helper_registry_path", None),
            )
        except Exception as exc:
            self.logger.debug("Unable to record owned capture helper identities: %s", exc)
            return set()

    def _start_helper_tracker(self) -> None:
        """Track pyshark helpers even while sniffing is waiting for a packet."""

        existing = getattr(self, "_helper_tracker_thread", None)
        if existing and existing.is_alive():
            return

        stop_event = getattr(self, "_helper_tracker_stop", None)
        if stop_event is None:
            stop_event = threading.Event()
            self._helper_tracker_stop = stop_event
        stop_event.clear()

        def monitor() -> None:
            self._record_owned_capture_processes()
            while not stop_event.wait(0.25):
                self._record_owned_capture_processes()

        self._helper_tracker_thread = threading.Thread(
            target=monitor,
            daemon=True,
            name="DnDToolsCaptureHelperTracker",
        )
        self._helper_tracker_thread.start()

    def _stop_helper_tracker(self) -> None:
        stop_event = getattr(self, "_helper_tracker_stop", None)
        tracker = getattr(self, "_helper_tracker_thread", None)
        if stop_event is not None:
            stop_event.set()
        if (
            tracker
            and tracker.is_alive()
            and tracker is not threading.current_thread()
        ):
            tracker.join(timeout=1.0)
        self._helper_tracker_thread = None

    def _prune_owned_helper_records(self) -> None:
        session_id = getattr(self, "_helper_session_id", "")
        if not session_id:
            return
        try:
            prune_owned_helper_registry(
                session_id=session_id,
                registry_path=getattr(self, "_helper_registry_path", None),
            )
        except Exception as exc:
            self.logger.debug("Unable to prune capture helper registry: %s", exc)

    def _capture_memory_usage_mb(self) -> float:
        """Return RSS for this process and only its owned capture helpers.

        Task Manager can group tshark/dumpcap with the application, so guarding
        only the Python parent misses the failure mode this monitor is intended
        to recover from. Process ids are de-duplicated defensively because a
        recursive child walk may race with helper shutdown.
        """

        try:
            processes: List[psutil.Process] = [psutil.Process(os.getpid())]
        except (psutil.Error, OSError) as err:
            self.logger.debug(f"Unable to inspect application memory: {err}")
            processes = []

        processes.extend(self._collect_capture_processes())
        seen_pids: Set[int] = set()
        total_rss = 0
        for proc in processes:
            try:
                pid = int(proc.pid)
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                total_rss += max(0, int(proc.memory_info().rss))
            except (psutil.Error, OSError, TypeError, ValueError):
                continue
        return total_rss / (1024 * 1024)

    def get_active_helper_pids(self) -> Set[int]:
        """Return the PIDs of any tshark/dumpcap helpers this process spawned."""
        return {proc.pid for proc in self._collect_capture_processes() if proc and proc.pid}

    def _terminate_capture_processes(self, timeout: float = 3.0) -> None:
        targets = self._collect_capture_processes()
        if not targets:
            self._prune_owned_helper_records()
            return

        # Record ownership before termination. If the app exits midway through
        # cleanup, the next launch can safely finish only these exact helpers.
        self._record_owned_capture_processes(targets)

        self.logger.info(f"Terminating {len(targets)} capture helper process(es)")

        for proc in targets:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        deadline = time.time() + timeout
        for proc in targets:
            remaining = max(0.0, deadline - time.time())
            try:
                proc.wait(timeout=remaining if remaining > 0 else 0.1)
            except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        self._prune_owned_helper_records()

    def _save_state(self, running: bool):
        try:
            state = {
                "running": running,
                "timestamp": datetime.now().isoformat(),
                "interface": self.interface,
                "port_range": self.port_range
            }
            with open(self.STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            self.logger.info(f"Saved capture state: running={running}")
        except Exception as e:
            self.logger.error(f"Failed to save capture state: {e}")

    def _restore_state(self) -> dict:
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, "r") as f:
                    state = json.load(f)
                    return state
            return {"running": False}
        except Exception as e:
            self.logger.error(f"Failed to restore capture state: {e}")
            return {"running": False}

    def capture_loop(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            local_ip = self.get_local_ip()
            if not local_ip:
                self.logger.error(f"Could not find IP address for interface {self.interface}")
                return

            display_filter = (
                f'ip.dst == {local_ip} and '
                f'tcp.srcport >= {self.port_range[0]} and '
                f'tcp.srcport <= {self.port_range[1]}'
            )

            self.logger.info(f"Starting capture on interface: {self.interface}, IP: {local_ip}")
            self.logger.info(f"Display filter: {display_filter}")

            self._current_loop = loop
            try:
                self._current_capture = pyshark.LiveCapture(
                    interface=self.interface,
                    display_filter=display_filter,
                    eventloop=loop,
                    tshark_path=self.tshark_path
                )

                if hasattr(self._current_capture, "keep_packets"):
                    try:
                        self._current_capture.keep_packets = False
                        self.logger.debug("LiveCapture configured with keep_packets=False")
                    except Exception as keep_err:
                        self.logger.debug(f"Unable to set keep_packets flag: {keep_err}")
                self._record_owned_capture_processes()
            except Exception as capture_error:
                self.logger.error(f"Failed to create LiveCapture: {capture_error}")
                if "tshark" in str(capture_error).lower():
                    self.logger.error("This appears to be a tshark-related issue. Make sure tshark is properly installed and accessible.")
                return

            for packet in self._current_capture.sniff_continuously():
                if self._stop_event.is_set():
                    break
                self._record_owned_capture_processes()
                if 'TCP' in packet and hasattr(packet.tcp, 'payload'):
                    stream_id = _read_tcp_int_field(packet.tcp, 'stream')
                    tcp_sequence = _read_tcp_int_field(
                        packet.tcp,
                        'seq_raw',
                        'seq',
                    )
                    tcp_out_of_order = _tcp_has_field(
                        packet.tcp,
                        'analysis_out_of_order',
                    )
                    self.process_packet(
                        packet.tcp.payload.binary_value,
                        stream_id=stream_id,
                        tcp_sequence=tcp_sequence,
                        tcp_out_of_order=tcp_out_of_order,
                    )
        except RuntimeError as e:
            if "Event loop" in str(e) and "stopped" in str(e):
                self.logger.info("Event loop stopped during capture, exiting cleanly")
            else:
                self.logger.error(f"Runtime error in capture loop: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Fatal error in capture loop: {e}", exc_info=True)
        finally:
            self._cleanup_capture()
            with self._state_lock:
                self.running = False
            self._stop_event.set()
            
    def _cleanup_capture(self):
        if self._cleanup_complete.is_set():
            return

        if not self._cleanup_lock.acquire(blocking=False):
            return

        try:
            capture = getattr(self, '_current_capture', None)
            loop = getattr(self, '_current_loop', None)
            self._record_owned_capture_processes()
            self._stop_helper_tracker()

            try:
                if capture:
                    try:
                        result = capture.close() if hasattr(capture, 'close') else None
                        if asyncio.iscoroutine(result):
                            cleanup_loop = asyncio.new_event_loop()
                            try:
                                cleanup_loop.run_until_complete(result)
                            finally:
                                cleanup_loop.close()
                    except Exception as sync_error:
                        self.logger.debug(f"capture.close raised {sync_error}; attempting async close")
                        try:
                            if hasattr(capture, 'close_async'):
                                async_result = capture.close_async()
                                if asyncio.iscoroutine(async_result):
                                    cleanup_loop = asyncio.new_event_loop()
                                    try:
                                        cleanup_loop.run_until_complete(async_result)
                                    finally:
                                        cleanup_loop.close()
                        except Exception as async_error:
                            self.logger.warning(f"Could not close capture async: {async_error}")
                    finally:
                        try:
                            processes = getattr(capture, '_running_processes', None)
                            if processes:
                                for proc in list(processes):
                                    try:
                                        finalize_asyncio_subprocess(proc, loop, self.logger)
                                    except Exception as proc_error:
                                        self.logger.debug(f"Unable to finalize subprocess cleanly: {proc_error}")

                                if hasattr(processes, 'clear'):
                                    processes.clear()
                                else:
                                    capture._running_processes = []
                        except Exception as proc_error:
                            self.logger.debug(f"Unable to clear running processes: {proc_error}")

                        try:
                            if hasattr(capture, 'eventloop'):
                                capture.eventloop = None
                        except Exception as loop_attr_error:
                            self.logger.debug(f"Unable to reset capture eventloop: {loop_attr_error}")
            except Exception as e:
                self.logger.error(f"Error during capture cleanup: {e}")
            finally:
                if hasattr(self, '_current_capture'):
                    del self._current_capture

                if loop:
                    try:
                        if not loop.is_closed():
                            try:
                                pending = list(asyncio.all_tasks(loop=loop))
                            except TypeError:
                                pending = list(asyncio.all_tasks())

                            for task in pending:
                                task.cancel()

                            if pending and not loop.is_running():
                                try:
                                    loop.run_until_complete(
                                        asyncio.gather(*pending, return_exceptions=True)
                                    )
                                except Exception as gather_error:
                                    self.logger.debug(f"Error awaiting pending tasks: {gather_error}")

                            try:
                                loop.call_soon_threadsafe(loop.stop)
                            except RuntimeError:
                                pass

                            if not loop.is_running():
                                try:
                                    if hasattr(loop, 'shutdown_asyncgens'):
                                        loop.run_until_complete(loop.shutdown_asyncgens())
                                except Exception as shutdown_error:
                                    self.logger.debug(f"Error during loop shutdown_asyncgens: {shutdown_error}")
                                loop.close()
                    except Exception as loop_error:
                        self.logger.warning(f"Error closing event loop: {loop_error}")
                    finally:
                        self._current_loop = None

                self.reset_state()
                self._terminate_capture_processes()
                self._prune_owned_helper_records()
        finally:
            self._cleanup_complete.set()
            self._cleanup_lock.release()

    def is_active(self) -> bool:
        if self.running:
            return True
        if self.capture_thread and self.capture_thread.is_alive():
            return True
        return False

    def shutdown(self, persist_running_state: Optional[bool] = None):
        try:
            self.stop_capture_switch(persist_running_state=persist_running_state)
        except Exception as e:
            self.logger.error(f"Error during capture shutdown: {e}")

    def start_capture_switch(self) -> bool:
        with self._state_lock:
            if self.capture_thread and self.capture_thread.is_alive():
                if self.running:
                    self.logger.info("Capture already running, ignoring start request")
                    return True
                self.logger.error(
                    "Cannot start capture while the previous capture thread is still exiting"
                )
                return False
            if not self._cleanup_complete.is_set():
                self.logger.error(
                    "Cannot start capture while previous capture cleanup is incomplete"
                )
                return False

            self._force_closing = False
            self.running = True
            self._stop_event.clear()
            self._cleanup_capture_on_exit = True
            self._save_state(True)
            self._user_requested_stop = False

            self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
            self._cleanup_complete.clear()
            self._start_helper_tracker()
            self.capture_thread.start()

        self._start_memory_guard()

        self.logger.info("Capture thread started")
        return True
        
    def stop_capture_switch(self, persist_running_state: Optional[bool] = None) -> bool:
        with self._state_lock:
            self._force_closing = True
            if not self.running and not (self.capture_thread and self.capture_thread.is_alive()):
                self.logger.info("Capture already stopped, ignoring stop request")
                self._force_closing = False
                return True

            self.running = False
            self._stop_event.set()
            persisted_flag = False if persist_running_state is None else bool(persist_running_state)
            self._save_state(persisted_flag)
            thread = self.capture_thread
            self._user_requested_stop = not persisted_flag

        self._stop_memory_guard()

        self._request_capture_shutdown(timeout=6.0)

        thread_still_alive = bool(thread and thread.is_alive())
        if thread_still_alive:
            for timeout in [1.0, 3.0, 6.0]:
                self.logger.info(f"Waiting for capture thread to exit (timeout: {timeout}s)...")
                thread.join(timeout=timeout)
                if not thread.is_alive():
                    self.logger.info("Capture thread exited cleanly")
                    break
            if thread.is_alive():
                self.logger.warning(
                    "Capture thread still running after timeouts; terminating owned capture helpers"
                )
                self._terminate_capture_processes()
                thread.join(timeout=3.0)

        cleanup_complete = self._cleanup_complete.wait(timeout=5.0)
        if not cleanup_complete:
            self.logger.warning("Timed out waiting for capture cleanup to finish")

        thread_still_alive = bool(thread and thread.is_alive())
        if thread_still_alive or not cleanup_complete:
            # Keep the thread reference and force-closing state. A later stop
            # can retry cleanup, while start_capture_switch must refuse to
            # create a second capture over an orphaned first one.
            self.logger.error(
                "Capture did not stop cleanly; refusing to forget the active capture thread"
            )
            return False

        with self._state_lock:
            self.capture_thread = None
            self._cleanup_capture_on_exit = False
            self._force_closing = False

        self.logger.info("Capture switch turned OFF")
        return True

    def _request_capture_shutdown(self, timeout: float = 5.0) -> None:
        capture = getattr(self, '_current_capture', None)
        loop = getattr(self, '_current_loop', None)

        if not capture and not loop:
            return

        closed_via_loop = False

        if loop and not loop.is_closed():
            async def _close_and_stop():
                try:
                    if capture and hasattr(capture, 'close_async'):
                        try:
                            result = capture.close_async()
                        except Exception as close_err:
                            self.logger.debug(f"close_async failed: {close_err}")
                            result = None

                        if asyncio.iscoroutine(result):
                            await result
                    elif capture and hasattr(capture, 'close'):
                        maybe = capture.close()
                        if asyncio.iscoroutine(maybe):
                            await maybe
                finally:
                    loop.call_soon(loop.stop)

            try:
                future = asyncio.run_coroutine_threadsafe(_close_and_stop(), loop)
                future.result(timeout=timeout)
                closed_via_loop = True
            except FutureTimeout:
                self.logger.warning("Timed out waiting for capture loop to exit cleanly")
            except RuntimeError as runtime_err:
                self.logger.debug(f"Capture loop not running during shutdown request: {runtime_err}")
            except Exception as exc:
                self.logger.debug(f"Unexpected error during async capture shutdown: {exc}")

        if capture and not closed_via_loop:
            try:
                result = capture.close() if hasattr(capture, 'close') else None
                if asyncio.iscoroutine(result):
                    cleanup_loop = asyncio.new_event_loop()
                    try:
                        cleanup_loop.run_until_complete(result)
                    finally:
                        cleanup_loop.close()
            except Exception as close_error:
                self.logger.debug(f"Error closing capture synchronously: {close_error}")

        if loop and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
            except Exception as stop_error:
                self.logger.debug(f"Unable to signal event loop stop: {stop_error}")

    def handle_packet(self, packet_data, proto_type):
        if not _PacketCommand_pb2:
            return

        try:
            name = _PacketCommand_pb2.PacketCommand.Name(proto_type)
        except (ValueError, KeyError):
            name = f"Unknown({proto_type})"

        try:
            message, parse_error = self._parse_proto_with_error(
                packet_data,
                proto_type,
            )

            # Store a bounded representation for the packet viewer. Large wire
            # payloads are still parsed and dispatched to handlers, but are not
            # expanded into a much larger Python dictionary merely for history.
            json_data = None
            json_size = 0
            json_omitted_reason = None
            parsed = message is not None
            payload_wire_length = max(0, len(packet_data) - 8)
            if message is not None:
                if payload_wire_length > self._packet_json_max_wire_bytes:
                    json_omitted_reason = "wire_payload_too_large"
                else:
                    try:
                        try:
                            json_data = MessageToDict(
                                message,
                                preserving_proto_field_name=True,
                                including_default_value_fields=True
                            )
                        except TypeError:
                            json_data = MessageToDict(
                                message,
                                preserving_proto_field_name=True
                            )

                        json_size = estimate_json_size(
                            json_data,
                            max_bytes=self._packet_json_max_bytes,
                        )
                        if json_size > self._packet_json_max_bytes:
                            json_data = None
                            json_omitted_reason = "expanded_json_too_large"
                    except Exception as dict_err:
                        json_omitted_reason = "json_conversion_failed"
                        self.logger.debug(f"MessageToDict failed for {name}: {dict_err}")

            # Assign a monotonically increasing id so UI can track items across refreshes
            self._packet_id_counter += 1
            has_handler = bool(self.capture_info and proto_type in self.capture_info)
            packet_info = {
                'id': self._packet_id_counter,
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'type': name,
                'proto_type': proto_type,
                'json': json_data,
                'raw_length': len(packet_data),
                'parsed': parsed,
                'handled': has_handler,
            }
            if parse_error:
                packet_info['parse_error'] = parse_error
                preview = packet_data[8:8 + self.DEFAULT_UNPARSED_PREVIEW_BYTES]
                packet_info['raw_preview_hex'] = preview.hex()
                packet_info['raw_preview_truncated'] = (
                    payload_wire_length > len(preview)
                )
            if json_omitted_reason:
                packet_info['json_omitted'] = True
                packet_info['json_omitted_reason'] = json_omitted_reason
                packet_info['json_limit_bytes'] = self._packet_json_max_bytes

            history_size = 512 + json_size
            if parse_error:
                history_size += len(packet_info.get('raw_preview_hex', ''))
            if not self.captured_packets.append(
                packet_info,
                size_bytes=history_size,
            ):
                self.logger.warning(
                    "Packet history rejected oversized metadata for %s",
                    name,
                )

            if proto_type in self._quest_packet_types:
                self._persist_quest_packet(packet_data, proto_type, name, message)

            if self.capture_info:
                if proto_type in self.capture_info:
                    self.logger.info(f"Parsing: {name} {proto_type}")
                    if message is not None:
                        try:
                            self.capture_info[proto_type](message)
                        except Exception as handler_err:
                            self.logger.error(f"Handler error for {name} ({proto_type}): {handler_err}", exc_info=True)
                    else:
                        self.logger.warning(f"No proto message for handled type: {name} {proto_type}")
                else:
                    # Not an error — most packet types don't have app-level callbacks
                    self.logger.debug(f"Captured (no handler): {name} {proto_type} — {'parsed' if parsed else 'unparsed'}")
            elif not parsed:
                self.logger.debug(f"Captured but could not parse: {name} {proto_type}")

        except Exception as exc:
            self.logger.error(f"Unhandled error in handle_packet for {name} ({proto_type}): {exc}", exc_info=True)

    def _persist_quest_packet(self, packet_data: bytes, proto_type: int, packet_name: str, message) -> None:
        if not getattr(self, '_save_quest_packets', False):
            return

        now = datetime.utcnow()
        timestamp_safe = now.strftime('%Y-%m-%dT%H-%M-%S.%fZ')
        iso_timestamp = now.isoformat() + 'Z'
        base_name = f"{timestamp_safe}_{packet_name}"
        if not self.quests_dir:
            return

        json_path = os.path.join(self.quests_dir, f"{base_name}.json")
        bin_path = os.path.join(self.quests_dir, f"{base_name}.bin")
        hexdump_path = os.path.join(self.quests_dir, f"{base_name}.hexdump.txt")

        payload = None
        if message is not None:
            try:
                payload = MessageToDict(
                    message,
                    preserving_proto_field_name=True,
                    including_default_value_fields=True
                )
            except TypeError:
                payload = MessageToDict(
                    message,
                    preserving_proto_field_name=True
                )
            except Exception as exc:
                self.logger.warning(f"Failed to serialize {packet_name} payload: {exc}")
                payload = None

        header_bytes = packet_data[:8]
        payload_bytes = packet_data[8:]
        header_hex = ' '.join(f"{byte:02X}" for byte in header_bytes)
        hexdump_lines = _format_hexdump(payload_bytes)

        binary_filename = None
        try:
            with open(bin_path, 'wb') as handle:
                handle.write(packet_data)
            binary_filename = os.path.basename(bin_path)
        except Exception as exc:
            self.logger.error(f"Failed to write quest packet binary {packet_name}: {exc}")

        hexdump_filename = None
        try:
            with open(hexdump_path, 'w', encoding='utf-8') as handle:
                handle.write(f"Packet {packet_name} (type={proto_type}, length={len(packet_data)})\n")
                handle.write(f"Captured at {iso_timestamp}\n")
                handle.write(f"Header bytes: {header_hex}\n")
                handle.write(f"Payload length: {len(payload_bytes)} bytes\n\n")
                handle.write("Offset  Hex bytes                                       ASCII\n")
                handle.write("------  ----------------------------------------------  ----------------\n")
                for line in hexdump_lines:
                    handle.write(line + '\n')
            hexdump_filename = os.path.basename(hexdump_path)
        except Exception as exc:
            self.logger.error(f"Failed to write quest packet hexdump {packet_name}: {exc}")

        hexdump_preview = hexdump_lines[:min(len(hexdump_lines), 32)]

        record = {
            "captured_at": iso_timestamp,
            "packet": {
                "name": packet_name,
                "type": proto_type,
                "length": len(packet_data),
            },
            "payload_length": len(payload_bytes),
            "payload": payload,
            "raw": {
                "header_hex": header_hex,
                "payload_hexdump_preview": hexdump_preview,
                "payload_hexdump_line_count": len(hexdump_lines),
                "binary_file": binary_filename,
                "hexdump_file": hexdump_filename,
                "files_relative_to": self.quests_dir,
            },
        }

        try:
            with open(json_path, 'w', encoding='utf-8') as handle:
                json.dump(record, handle, indent=2, ensure_ascii=False)
            self.logger.info(f"Saved quest packet to {json_path}")
        except Exception as exc:
            self.logger.error(f"Failed to persist quest packet {packet_name}: {exc}")

def main():
    from src.models.character import policy
    capture = PacketCapture()
    capture_info = {
        _PacketCommand_pb2.PacketCommand.S2C_SERVICE_POLICY_NOT: policy,
    }
    capture.capture_info = capture_info

    capture.start_capture_switch()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        capture.stop_capture_switch()

if __name__ == "__main__":
    main()
