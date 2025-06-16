import os
import sys
import subprocess
import asyncio

# Patch subprocess.Popen to always hide console windows on Windows (before importing pyshark)
if (
    os.name == 'nt' and (
        globals().get("__compiled__", False) or hasattr(sys, 'frozen') or hasattr(sys, '_MEIPASS')
    )
):
    # Store the original Popen class before any modification
    _original_popen_class = subprocess.Popen
    
    def hidden_popen(*args, **kwargs):
        # Hide console window
        if os.name == 'nt':
            startupinfo = kwargs.get('startupinfo') or subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            kwargs['startupinfo'] = startupinfo
            # Add CREATE_NO_WINDOW for extra reliability
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x08000000
        # Call the original class, not the patched version
        return _original_popen_class(*args, **kwargs)
    
    # Replace subprocess.Popen with our wrapper
    subprocess.Popen = hidden_popen

import pyshark
import socket
import psutil
import struct
import json
from datetime import datetime
import logging
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
        """Main capture loop that runs in a separate thread"""
        try:
            # Set up event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            local_ip = self.get_local_ip()
            if not local_ip:
                self.logger.error(f"Could not find IP address for interface {self.interface}")
                return
                
            display_filter = (f'ip.dst == {local_ip} and '
                              f'tcp.srcport >= {self.port_range[0]} and '
                              f'tcp.srcport <= {self.port_range[1]}')
            
            self.logger.info(f"Starting capture on interface: {self.interface}, IP: {local_ip}")
            self.logger.info(f"Display filter: {display_filter}")
            
            # Store capture object as instance variable for cleanup
            try:
                self._current_capture = pyshark.LiveCapture(
                    interface=self.interface,
                    display_filter=display_filter
                )
                self._current_loop = loop
            except Exception as e:
                self.logger.error(f"Failed to create LiveCapture: {e}")
                # Check if this is a tshark/executable issue
                if "tshark" in str(e).lower():
                    self.logger.error("This appears to be a tshark-related issue. Make sure tshark is properly installed and accessible.")
                raise
            
            try:
                for packet in self._current_capture.sniff_continuously():
                    if not self.running:
                        break
                    if 'TCP' in packet and hasattr(packet.tcp, 'payload'):
                        self.process_packet(packet.tcp.payload.binary_value)
            except Exception as e:
                self.logger.error(f"Capture loop error: {e}")
                # Log additional details for debugging
                import traceback
                self.logger.error(f"Full traceback: {traceback.format_exc()}")
            finally:
                self._cleanup_capture()
        except Exception as e:
            self.logger.error(f"Fatal error in capture_loop: {e}")
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            # Make sure we cleanup even if there's a fatal error
            try:
                self._cleanup_capture()
            except:
                pass
            
    def _cleanup_capture(self):
        """Clean up capture resources properly"""
        try:
            if hasattr(self, '_current_capture'):
                # Try to close synchronously first
                try:
                    if hasattr(self._current_capture, 'close'):
                        self._current_capture.close()
                except Exception:
                    # If sync close fails, try async approach
                    try:
                        # Check if we have an event loop and it's not running
                        if hasattr(self, '_current_loop') and not self._current_loop.is_closed():
                            if not self._current_loop.is_running():
                                if hasattr(self._current_capture, 'close_async'):
                                    # Always create a new task and ensure it's awaited
                                    future = asyncio.ensure_future(
                                        self._current_capture.close_async(), 
                                        loop=self._current_loop
                                    )
                                    self._current_loop.run_until_complete(future)
                        else:
                            # Create new loop for cleanup
                            cleanup_loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(cleanup_loop)
                            if hasattr(self._current_capture, 'close_async'):
                                # Wrap in a future and await it properly
                                future = asyncio.ensure_future(
                                    self._current_capture.close_async(), 
                                    loop=cleanup_loop
                                )
                                cleanup_loop.run_until_complete(future)
                            cleanup_loop.close()
                    except Exception as e:
                        self.logger.warning(f"Could not close capture async: {e}")
                
                # Make sure the reference is deleted
                del self._current_capture
                
                # Clean up the event loop
                if hasattr(self, '_current_loop'):
                    try:
                        if not self._current_loop.is_closed():
                            # Cancel all running tasks
                            pending = asyncio.all_tasks(self._current_loop) if hasattr(asyncio, 'all_tasks') else []
                            for task in pending:
                                task.cancel()
                            if pending:
                                self._current_loop.run_until_complete(
                                    asyncio.gather(*pending, return_exceptions=True)
                                )
                            self._current_loop.close()
                    except Exception as e:
                        self.logger.warning(f"Error closing event loop: {e}")
                    del self._current_loop
        except Exception as e:
            self.logger.error(f"Error during capture cleanup: {e}")
        finally:
            self.reset_state()

    def shutdown(self):
        """Properly shutdown capture and save state"""
        try:
            current_state = self.running
            if current_state:
                self.logger.info(f"Shutting down capture (was running: {current_state})...")
                self._save_state(True)  # Save as running=True before stopping
                self.running = False
                if self.capture_thread is not None:
                    self.capture_thread.join(timeout=5.0)
                    if self.capture_thread.is_alive():
                        self.logger.warning("Capture thread still running after timeout, forcing cleanup")
                        self._cleanup_capture()
                self.capture_thread = None
                self.logger.info("Capture shutdown complete")
            else:
                self.logger.info("Capture was already stopped, no action needed for shutdown")
        except Exception as e:
            self.logger.error(f"Error during capture shutdown: {e}")

    def start_capture_switch(self) -> None:
        """Start packet capture in background thread if not already running."""
        if self.capture_thread is not None and self.capture_thread.is_alive():
            self.logger.info("Capture already running, ignoring start request")
            return
        self.running = True
        self._save_state(True)
        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()
        self.logger.info("Capture thread started")
        
    def stop_capture_switch(self) -> None:
        """Stop packet capture gracefully."""
        if not self.running:
            self.logger.info("Capture already stopped, ignoring stop request")
            return
            
        # Set running to False to signal the capture loop to exit
        self.running = False
        self._save_state(False)
        
        if self.capture_thread is not None and self.capture_thread.is_alive():
            # Try to join with increasing timeouts to prevent hanging
            for timeout in [1.0, 3.0, 6.0]:
                self.logger.info(f"Waiting for capture thread to exit (timeout: {timeout}s)...")
                self.capture_thread.join(timeout=timeout)
                if not self.capture_thread.is_alive():
                    self.logger.info("Capture thread exited cleanly")
                    break
            
            # If thread is still alive after all timeouts, force cleanup
            if self.capture_thread.is_alive():
                self.logger.warning("Capture thread still running after timeouts, forcing cleanup")
                self._cleanup_capture()
                
        self.capture_thread = None
        self.logger.info("Capture switch turned OFF")

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