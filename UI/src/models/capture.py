import os
import sys
import subprocess
import asyncio
import tempfile
import glob
import logging

# 1) Grab the original asyncio.spawn function
_orig_create = asyncio.create_subprocess_exec

# 2) Define a wrapper that injects the Windows "no console" flag
async def _create_no_window(*args, **kwargs):
    # send everything to DEVNULL unless you need it
    kwargs.setdefault('stdin',  subprocess.DEVNULL)
    kwargs.setdefault('stdout', subprocess.DEVNULL)
    kwargs.setdefault('stderr', subprocess.DEVNULL)

    # on Windows, suppress the child console
    if sys.platform == 'win32':
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)

    # call the real create_subprocess_exec
    return await _orig_create(*args, **kwargs)

# 3) Monkey-patch asyncio so PyShark’s captures inherit this behavior
asyncio.create_subprocess_exec = _create_no_window


import pyshark

# Make pyshark shutdown safe when event loops are already closed (prevents noisy
# "Event loop is closed"/unawaited coroutine warnings during app exit).
try:
    from pyshark.capture.capture import Capture as _PysharkCapture  # type: ignore

    _orig_ps_close = _PysharkCapture.close

    def _safe_ps_close(self):  # type: ignore
        try:
            running = getattr(self, '_running_processes', None)
            if not running:
                return

            loop = getattr(self, 'eventloop', None)

            # If there is no usable loop (None or closed), run close_async in a fresh loop
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

            # Normal case
            return _orig_ps_close(self)
        except Exception:
            return

    def _safe_ps_del(self):  # type: ignore
        try:
            if getattr(self, '_running_processes', None):
                self.close()
        except Exception:
            # Swallow all exceptions in destructor path
            return

    _PysharkCapture.close = _safe_ps_close  # type: ignore
    _PysharkCapture.__del__ = _safe_ps_del  # type: ignore
except Exception as _patch_err:
    # Best-effort: if patching fails, just continue
    pass
import socket
import psutil
import struct
import json
from datetime import datetime
from typing import Tuple, Optional
import threading
import time
import importlib

from .appdirs import get_capture_state_file, is_frozen
from networking.protos import _PacketCommand_pb2

# Determine paths
current_dir = os.path.dirname(os.path.abspath(__file__))
ui_root     = os.path.abspath(os.path.join(current_dir, "..", ".."))
protos_path = os.path.join(ui_root, "networking", "protos")

# Ensure the protos path is on sys.path
if protos_path not in sys.path:
    sys.path.insert(0, protos_path)

# Dynamically load each _pb2 module under the package name networking.protos.xxx_pb2
for filename in os.listdir(protos_path):
    if not filename.endswith("_pb2.py"):
        continue

    module_name = filename[:-3]  # "Account_pb2"
    full_name   = f"networking.protos.{module_name}"
    file_path   = os.path.join(protos_path, filename)

    # Create a module spec
    spec = importlib.util.spec_from_file_location(full_name, file_path)
    module = importlib.util.module_from_spec(spec)

    # Insert into sys.modules so relative imports inside will resolve
    sys.modules[full_name] = module

    # Execute the module
    spec.loader.exec_module(module)

    # Bring its public names into globals()
    for attr in dir(module):
        if not attr.startswith("_"):
            globals()[attr] = getattr(module, attr)


# Configure subprocess to hide console windows when in executable mode
if is_frozen():
    # Replace subprocess.Popen with a version that hides console windows
    original_popen = subprocess.Popen
    
    def hidden_popen(*args, **kwargs):
        # Add startupinfo to hide console windows on Windows
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs['startupinfo'] = startupinfo
        return original_popen(*args, **kwargs)
    
    # Replace the subprocess.Popen with our modified version
    subprocess.Popen = hidden_popen

class PacketCapture:
    def __init__(self, interface: str = 'Ethernet', port_range: Tuple[int, int] = (20200, 20300)):
        self.interface = interface
        self.port_range = port_range
        self.packet_data = b""
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.MAX_BUFFER_SIZE = 1024 * 1024  # 1MB
        self.expected_packet_length = None
        self.expected_proto_type = None
        self.running = False  # Initialize as False first
        self.capture_thread = None
        self._cleanup_capture_on_exit = False
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self.STATE_FILE = get_capture_state_file()
        
        # Restore state - keep track of what the previous state was
        self.saved_state = self._restore_state()
        self.was_running_before = self.saved_state.get('running', False)
        
        # Automatically restore previous capture state
        if self.was_running_before:
            self.logger.info("Previous session had capture running - restoring state")
            # Use a timer to start capture after initialization completes
            threading.Timer(0.1, self._delayed_start).start()
        else:
            self.logger.info("Previous session had capture stopped")

    def _delayed_start(self):
        """Start capture after a brief delay to ensure full initialization"""
        self.start_capture_switch()

    def background_init(self):
        """Initialize capture in background, restoring previous state if needed"""
        # This method is now optional since auto-restore happens in __init__
        if not self.running and self.was_running_before:
            self.logger.info("Manual restore requested - starting capture")
            self.start_capture_switch()
        else:
            self.logger.info("Background init called - capture state already correct")
            
    def should_auto_start(self):
        """Return whether capture should auto-start based on previous state"""
        return self.was_running_before

    def parse_proto(self, packet_data, proto_type):
        data = packet_data[8:]

        command_name = _PacketCommand_pb2.PacketCommand.Name(proto_type)

        try:
            # For server packets
            message_class = globals().get("S" + command_name)
            if message_class:
                message = message_class()
                message.ParseFromString(data)
                if message:
                    return message
        except Exception as e:
            self.logger.warning(f"Error parsing proto: {e}")

        return None

    def get_local_ip(self) -> Optional[str]:
        for interface, addrs in psutil.net_if_addrs().items():
            if interface == self.interface:
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        return addr.address
        return None

    def validate_packet_header(self, length: int, proto_type: int, padding: int) -> bool:
        """Validate packet header values"""
        valid_packet_range = (8, 2 * 1024 * 1024)  # Between 100 bytes and 2MB
        return (
            valid_packet_range[0] <= length <= valid_packet_range[1] and
            proto_type in _PacketCommand_pb2.PacketCommand.values() and 
            padding in [0, 256]  # Common padding values
        )

    def process_packet(self, data: bytes) -> Optional[bool]:
        if len(data) > 0:
            # Add incoming data to buffer
            self.packet_data += data
            current_size = len(self.packet_data)
            
            # Reset if buffer gets too large
            if current_size > self.MAX_BUFFER_SIZE:
                self.logger.warning(f"Buffer exceeded max size ({self.MAX_BUFFER_SIZE} bytes)")
                self.reset_state()
                return False

            # Try to parse/validate header
            if self.expected_packet_length is None and current_size >= 8:
                try:
                    packet_length, proto_type, random_padding = struct.unpack('<IHH', self.packet_data[:8])
                    
                    # Get packet type name from _PacketCommand_pb2 before validation
                    packet_type_name = _PacketCommand_pb2._PACKETCOMMAND.values_by_number[proto_type].name if proto_type in _PacketCommand_pb2._PACKETCOMMAND.values_by_number else "Unknown"
                    
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

            # Process packet data
            if self.expected_packet_length and self.expected_proto_type:
                # Handle overflow by trimming
                if current_size > self.expected_packet_length:
                    self.logger.info(f"Trimming overflow {current_size} -> {self.expected_packet_length}")
                    self.packet_data = self.packet_data[:self.expected_packet_length]
                    current_size = self.expected_packet_length

                # Complete packet
                if current_size == self.expected_packet_length:
                    self.handle_packet(self.packet_data, self.expected_proto_type)
                    self.reset_state()
                # Progress update
                elif current_size % 8192 == 0:
                    self.logger.info(f"Accumulating: {current_size}/{self.expected_packet_length}")
        return False

    def reset_state(self) -> None:
        """Reset all packet processing state"""
        self.packet_data = b""
        self.expected_packet_length = None
        self.expected_proto_type = None

    def _terminate_capture_processes(self, timeout: float = 3.0) -> None:
        """Ensure helper capture processes like tshark/dumpcap are terminated."""
        try:
            parent = psutil.Process(os.getpid())
        except (psutil.Error, OSError) as err:
            self.logger.debug(f"Unable to inspect child processes: {err}")
            return

        targets = []
        for child in parent.children(recursive=True):
            try:
                name = child.name().lower()
            except (psutil.Error, OSError):
                continue

            if 'tshark' in name or 'dumpcap' in name:
                targets.append(child)

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
        """Save capture state to persistent storage"""
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
        """Restore capture state from persistent storage"""
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
        """Main capture loop that runs in a separate thread."""
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
                    eventloop=loop
                )
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
        """Clean up capture resources and terminate helper processes."""
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
                        if processes is not None:
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

    def is_active(self) -> bool:
        """Return True if capture thread is alive or running flag is set."""
        if self.running:
            return True
        if self.capture_thread and self.capture_thread.is_alive():
            return True
        return False

    def shutdown(self, persist_running_state: Optional[bool] = None):
        """Properly shutdown capture and persist the desired running state."""
        try:
            self.stop_capture_switch(persist_running_state=persist_running_state)
        except Exception as e:
            self.logger.error(f"Error during capture shutdown: {e}")

    def start_capture_switch(self) -> bool:
        """Start packet capture in a background thread if not already running."""
        with self._state_lock:
            if self.running and self.capture_thread and self.capture_thread.is_alive():
                self.logger.info("Capture already running, ignoring start request")
                return True

            self.running = True
            self._stop_event.clear()
            self._cleanup_capture_on_exit = True
            self._save_state(True)

            self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
            self.capture_thread.start()

        self.logger.info("Capture thread started")
        return True
        
    def stop_capture_switch(self, persist_running_state: Optional[bool] = None) -> bool:
        """Stop packet capture gracefully.

        Args:
            persist_running_state: When provided, overrides the persisted running flag
                saved to disk. This allows the caller to remember user intent across
                app restarts even though the capture has to stop for cleanup.
        """
        with self._state_lock:
            if not self.running and not (self.capture_thread and self.capture_thread.is_alive()):
                self.logger.info("Capture already stopped, ignoring stop request")
                return True

            self.running = False
            self._stop_event.set()
            persisted_flag = False if persist_running_state is None else bool(persist_running_state)
            self._save_state(persisted_flag)
            thread = self.capture_thread

        capture = getattr(self, '_current_capture', None)
        if capture:
            try:
                capture.close()
            except Exception as close_error:
                self.logger.debug(f"Error closing capture during stop: {close_error}")

        if thread and thread.is_alive():
            for timeout in [1.0, 3.0, 6.0]:
                self.logger.info(f"Waiting for capture thread to exit (timeout: {timeout}s)...")
                thread.join(timeout=timeout)
                if not thread.is_alive():
                    self.logger.info("Capture thread exited cleanly")
                    break
            if thread.is_alive():
                self.logger.warning("Capture thread still running after timeouts, forcing cleanup")

        self._cleanup_capture()

        with self._state_lock:
            self.capture_thread = None
            self._cleanup_capture_on_exit = False

        self.logger.info("Capture switch turned OFF")
        return True

    def _process_packet_wrapper(self, packet):
        if 'TCP' in packet and hasattr(packet.tcp, 'payload'):
            self.process_packet(packet.tcp.payload.binary_value)
    
    def handle_packet(self, packet_data, proto_type):
        name = _PacketCommand_pb2.PacketCommand.Name(proto_type)
        if self.capture_info:
            message = self.parse_proto(packet_data, proto_type)
            if proto_type in self.capture_info:
                self.logger.info(f"Parsing: {name} {proto_type}")
                if message:
                    self.capture_info[proto_type](message)
                else:
                    self.logger.warning("Invalid Packet")
            else:
                self.logger.info(f"No handle for: {name} {proto_type}")
                if message:
                    self.logger.info("Valid Packet")
                else:
                    self.logger.warning("Invalid Packet")

def main():
    from src.models.character import policy
    capture = PacketCapture()
    capture_info = {
        # S2C_LOBBY_CHARACTER_INFO_RES: print,
        # S2C_ACCOUNT_CHARACTER_LIST_RES: print,
        # S2C_INVENTORY_MOVE_RES: print,
        # S2C_INVENTORY_SWAP_RES: print,
        # S2C_INVENTORY_MERGE_RES: print,
        # S2C_STORAGE_INFO_RES: print,
        # S2C_PING_INFO_RES: print,
        _PacketCommand_pb2.PacketCommand.S2C_SERVICE_POLICY_NOT: policy,
    }
    capture.capture_info = capture_info

    # Simulate switch: start background capture
    capture.start_capture_switch()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        capture.stop_capture_switch()

if __name__ == "__main__":
    main()