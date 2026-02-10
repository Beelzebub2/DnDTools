import os
import sys
import subprocess
import asyncio
import tempfile
import glob
import logging
import socket
import psutil
import struct
import json
import threading
import time
import importlib
from datetime import datetime
from typing import Tuple, Optional, List, Dict, Any, Set
from concurrent.futures import TimeoutError as FutureTimeout
from collections import deque
from google.protobuf.json_format import MessageToDict

import pyshark

from .appdirs import get_capture_state_file, is_frozen
from src.models.settings import settings_manager, resolve_tshark_executable
from src.models.capture_utils import patch_asyncio, patch_pyshark, finalize_asyncio_subprocess
from src.models.memory_guard import MemoryGuard

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

class PacketCapture:
    DEFAULT_TSHARK_MEMORY_LIMIT_MB = 500.0
    DEFAULT_TSHARK_MEMORY_CHECK_SEC = 15.0
    DEFAULT_TSHARK_MEMORY_RESTART_COOLDOWN_SEC = 120.0

    def __init__(self, interface: str = 'Ethernet', port_range: Tuple[int, int] = (20200, 20300), wireshark_path: Optional[str] = None):
        self.interface = interface
        self.port_range = port_range
        self.packet_data = b""
        self.logger = logging.getLogger(__name__)
        self.MAX_BUFFER_SIZE = 1024 * 1024  # 1MB
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
        
        # Packet viewer storage
        self.captured_packets = deque(maxlen=1000)
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
            on_threshold_exceeded=self._on_memory_threshold_exceeded
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
            self.stop_capture_switch(persist_running_state=True)
            time.sleep(1.0)
            self.start_capture_switch()
        except Exception as exc:
            self.logger.error(f"Failed to restart capture after memory guard stop: {exc}", exc_info=True)

    def should_auto_start(self):
        return self.was_running_before

    def parse_proto(self, packet_data, proto_type):
        """Deserialize a packet's payload using the pre-built PROTO_MAP."""
        message_class = PROTO_MAP.get(proto_type)
        if message_class is None:
            return None

        data = packet_data[8:]
        try:
            message = message_class()
            message.ParseFromString(data)
            return message
        except Exception as e:
            try:
                name = _PacketCommand_pb2.PacketCommand.Name(proto_type)
            except (ValueError, KeyError):
                name = str(proto_type)
            self.logger.debug(f"Failed to parse {name} via {message_class.__name__}: {e}")
            return None

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
            proto_type in _PacketCommand_pb2.PacketCommand.values() and 
            padding in [0, 256]
        )

    def process_packet(self, data: bytes) -> Optional[bool]:
        if len(data) == 0:
            return False

        self.packet_data += data

        # Loop to process all complete game packets in the buffer.
        # A single TCP segment may contain multiple back-to-back game packets.
        max_iterations = 50  # safety limit
        iterations = 0
        while iterations < max_iterations:
            iterations += 1
            current_size = len(self.packet_data)

            if current_size == 0:
                break

            if current_size > self.MAX_BUFFER_SIZE:
                self.logger.warning(f"Buffer exceeded max size ({self.MAX_BUFFER_SIZE} bytes)")
                self.reset_state()
                return False

            # Parse header if we haven't yet for this game packet
            if self.expected_packet_length is None and current_size >= 8:
                try:
                    packet_length, proto_type, random_padding = struct.unpack('<IHH', self.packet_data[:8])

                    packet_type_name = "Unknown"
                    if _PacketCommand_pb2 and proto_type in _PacketCommand_pb2._PACKETCOMMAND.values_by_number:
                        packet_type_name = _PacketCommand_pb2._PACKETCOMMAND.values_by_number[proto_type].name

                    if not self.validate_packet_header(packet_length, proto_type, random_padding):
                        self.logger.warning(f"Invalid packet: {packet_type_name} (Type={proto_type}, Length={packet_length}, Padding={random_padding})")
                        self.reset_state()
                        return False

                    self.logger.info(f"New packet: {packet_type_name} (Type={proto_type}, Length={packet_length}, Padding={random_padding})")

                    self.expected_packet_length = packet_length
                    self.expected_proto_type = proto_type
                except struct.error:
                    self.reset_state()
                    return False

            if self.expected_packet_length and self.expected_proto_type:
                if current_size >= self.expected_packet_length:
                    # We have enough data — extract this packet and keep the overflow
                    complete_packet = self.packet_data[:self.expected_packet_length]
                    overflow = self.packet_data[self.expected_packet_length:]

                    if current_size > self.expected_packet_length:
                        self.logger.info(f"Splitting segment: {current_size} bytes = {self.expected_packet_length} (packet) + {len(overflow)} (overflow)")

                    self.handle_packet(complete_packet, self.expected_proto_type)

                    # Reset state and continue with overflow data
                    self.expected_packet_length = None
                    self.expected_proto_type = None
                    self.packet_data = overflow
                    # Continue the loop to process overflow data
                    continue
                else:
                    # Need more data — wait for next TCP segment
                    if current_size % 8192 == 0:
                        self.logger.info(f"Accumulating: {current_size}/{self.expected_packet_length}")
                    break
            else:
                # Need more data to parse the header
                break

        return False

    def reset_state(self) -> None:
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

            if 'tshark' in name or 'dumpcap' in name:
                targets.append(child)
        return targets

    def get_active_helper_pids(self) -> Set[int]:
        """Return the PIDs of any tshark/dumpcap helpers this process spawned."""
        return {proc.pid for proc in self._collect_capture_processes() if proc and proc.pid}

    def _terminate_capture_processes(self, timeout: float = 3.0) -> None:
        targets = self._collect_capture_processes()
        if not targets:
            return

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
            except Exception as capture_error:
                self.logger.error(f"Failed to create LiveCapture: {capture_error}")
                if "tshark" in str(capture_error).lower():
                    self.logger.error("This appears to be a tshark-related issue. Make sure tshark is properly installed and accessible.")
                return

            for packet in self._current_capture.sniff_continuously():
                if self._stop_event.is_set():
                    break
                if 'TCP' in packet and hasattr(packet.tcp, 'payload'):
                    self.process_packet(packet.tcp.payload.binary_value)
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

                temp_dir = tempfile.gettempdir()
                for pcap in glob.glob(os.path.join(temp_dir, '*.pcapng')):
                    try:
                        os.remove(pcap)
                        self.logger.info(f"Deleted temp capture file: {pcap}")
                    except Exception as file_error:
                        self.logger.warning(f"Could not delete {pcap}: {file_error}")
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
            if self.running and self.capture_thread and self.capture_thread.is_alive():
                self.logger.info("Capture already running, ignoring start request")
                return True

            self.running = True
            self._stop_event.clear()
            self._cleanup_capture_on_exit = True
            self._save_state(True)
            self._user_requested_stop = False

            self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
            self._cleanup_complete.clear()
            self.capture_thread.start()

        self._start_memory_guard()

        self.logger.info("Capture thread started")
        return True
        
    def stop_capture_switch(self, persist_running_state: Optional[bool] = None) -> bool:
        with self._state_lock:
            self._force_closing = True
            if not self.running and not (self.capture_thread and self.capture_thread.is_alive()):
                self.logger.info("Capture already stopped, ignoring stop request")
                return True

            self.running = False
            self._stop_event.set()
            persisted_flag = False if persist_running_state is None else bool(persist_running_state)
            self._save_state(persisted_flag)
            thread = self.capture_thread
            self._user_requested_stop = not persisted_flag

        self._stop_memory_guard()

        self._request_capture_shutdown(timeout=6.0)

        if thread and thread.is_alive():
            for timeout in [1.0, 3.0, 6.0]:
                self.logger.info(f"Waiting for capture thread to exit (timeout: {timeout}s)...")
                thread.join(timeout=timeout)
                if not thread.is_alive():
                    self.logger.info("Capture thread exited cleanly")
                    break
            if thread.is_alive():
                self.logger.warning("Capture thread still running after timeouts, forcing cleanup")

        if not self._cleanup_complete.wait(timeout=5.0):
            self.logger.warning("Timed out waiting for capture cleanup to finish")

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
            message = self.parse_proto(packet_data, proto_type)

            # Store for packet viewer
            json_data = None
            parsed = False
            if message:
                parsed = True
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
                except Exception as dict_err:
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
            self.captured_packets.append(packet_info)

            if proto_type in self._quest_packet_types:
                self._persist_quest_packet(packet_data, proto_type, name, message)

            if self.capture_info:
                if proto_type in self.capture_info:
                    self.logger.info(f"Parsing: {name} {proto_type}")
                    if message:
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