import pyshark
import socket
import psutil
import struct
import json
from datetime import datetime
import os
import logging
import sys
from typing import Tuple, Optional
from google.protobuf.json_format import MessageToJson
import threading
import time
import asyncio
import importlib
import subprocess

from .appdirs import get_data_dir, get_capture_state_file, is_frozen
from src.models.protos import *
from networking.protos import _PacketCommand_pb2
from networking.protos import _Defins_pb2


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

def parse_proto(packet_data, proto_type):
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
        print(e)

    return None

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
        self.data_dir = get_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.MAX_BUFFER_SIZE = 1024 * 1024  # 1MB
        self.expected_packet_length = None
        self.expected_proto_type = None
        self.running = False  # Initialize as False first
        self.capture_thread = None
        self.STATE_FILE = get_capture_state_file()
        
        # Restore state but don't start capture automatically
        saved_state = self._restore_state()
        if saved_state.get('running', False):
            self.running = True  # Only set the flag, don't start capture
            self.logger.info("Restored previous running state (capture will start when explicitly requested)")

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
            padding == 256  # Common padding values
        )

    def process_packet(self, data: bytes) -> Optional[bool]:
        if len(data) > 0:
            # Add incoming data to buffer
            self.packet_data += data
            current_size = len(self.packet_data)
            
            # Reset if buffer gets too large
            if current_size > self.MAX_BUFFER_SIZE:
                print(f"Buffer exceeded max size ({self.MAX_BUFFER_SIZE} bytes)")
                self.reset_state()
                return False

            # Try to parse/validate header
            if self.expected_packet_length is None and current_size >= 8:
                try:
                    packet_length, proto_type, random_padding = struct.unpack('<IHH', self.packet_data[:8])
                    
                    # Get packet type name from _PacketCommand_pb2 before validation
                    packet_type_name = _PacketCommand_pb2._PACKETCOMMAND.values_by_number[proto_type].name if proto_type in _PacketCommand_pb2._PACKETCOMMAND.values_by_number else "Unknown"
                    
                    if not self.validate_packet_header(packet_length, proto_type, random_padding):
                        print(f"Invalid packet: {packet_type_name} (Type={proto_type}, Length={packet_length}, Padding={random_padding})")
                        self.reset_state()
                        return False

                    print(f"New packet: {packet_type_name} (Type={proto_type}, Length={packet_length}, Padding={random_padding})")
                    
                    self.expected_packet_length = packet_length
                    self.expected_proto_type = proto_type
                except struct.error:
                    self.reset_state()
                    return False

            # Process packet data
            if self.expected_packet_length and self.expected_proto_type:
                # Handle overflow by trimming
                if current_size > self.expected_packet_length:
                    print(f"Trimming overflow {current_size} -> {self.expected_packet_length}")
                    self.packet_data = self.packet_data[:self.expected_packet_length]
                    current_size = self.expected_packet_length

                # Complete packet
                if current_size == self.expected_packet_length:
                    self.handle_packet(self.packet_data, self.expected_proto_type)
                    self.reset_state()
                # Progress update
                elif current_size % 8192 == 0:
                    print(f"Accumulating: {current_size}/{self.expected_packet_length}")
        return False

    def reset_state(self) -> None:
        """Reset all packet processing state"""
        self.packet_data = b""
        self.expected_packet_length = None
        self.expected_proto_type = None


    def save_packet_data(self, message) -> bool:
        try:

            json_data = MessageToJson(message)
            # Overwrite file if characterId matches (no date in filename)
            if '"result": 1' in json_data and '"characterDataBase": {' in json_data:
                char_data = message.characterDataBase
                char_id = str(char_data.characterId)
                data_file = os.path.join(self.data_dir, f"{char_id}.json")
                with open(data_file, "w", encoding='utf-8') as f:
                    f.write(json_data)
                self.logger.info(f"Saved/updated target packet data to {data_file} (characterId={char_id})")
                return True

            # Save other packets to timestamped files as before
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            data_file = os.path.join(self.data_dir, f"{timestamp}.json")
            with open(data_file, "w", encoding='utf-8') as f:
                f.write(json_data)
            self.logger.info(f"Successfully saved packet data to {data_file}")
            return False

        except Exception as e:
            self.logger.error(f"Failed to save packet data: {str(e)}")
            raise

    def _save_state(self):
        """Save capture state to persistent storage"""
        try:
            state = {
                "running": self.running,
                "timestamp": datetime.now().isoformat(),
                "interface": self.interface,
                "port_range": self.port_range
            }
            with open(self.STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            self.logger.info(f"Saved capture state: running={self.running}")
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
        # Set up event loop for this thread
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        local_ip = self.get_local_ip()
        if not local_ip:
            self.logger.error(f"Could not find IP address for interface {self.interface}")
            return
            
        display_filter = (f'ip.dst == {local_ip} and '
                          f'tcp.srcport >= {self.port_range[0]} and '
                          f'tcp.srcport <= {self.port_range[1]}')
        
        # Store capture object as instance variable for cleanup
        self._current_capture = pyshark.LiveCapture(interface=self.interface, display_filter=display_filter)
        self._current_loop = loop
        
        try:
            for packet in self._current_capture.sniff_continuously():
                if not self.running:
                    break
                if 'TCP' in packet and hasattr(packet.tcp, 'payload'):
                    self.process_packet(packet.tcp.payload.binary_value)
        except Exception as e:
            self.logger.error(f"Capture loop error: {e}")
        finally:
            self._cleanup_capture()

    def _cleanup_capture(self):
        """Clean up capture resources properly"""
        try:
            if hasattr(self, '_current_capture'):
                # Create a new event loop for cleanup if needed
                if not hasattr(self, '_current_loop') or self._current_loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                else:
                    loop = self._current_loop

                # Run close_async in the event loop
                if hasattr(self._current_capture, 'close_async'):
                    loop.run_until_complete(self._current_capture.close_async())
                else:
                    self._current_capture.close()
                
                del self._current_capture
                
                # Clean up the event loop
                if hasattr(self, '_current_loop'):
                    if not self._current_loop.is_closed():
                        self._current_loop.close()
                    del self._current_loop
        except Exception as e:
            self.logger.error(f"Error during capture cleanup: {e}")
        finally:
            self.reset_state()

    def start_capture_switch(self) -> None:
        """Start packet capture in background thread if not already running."""
        if self.capture_thread is not None and self.capture_thread.is_alive():
            self.logger.info("Capture already running, ignoring start request")
            return
        self.running = True
        self._save_state()
        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()
        self.logger.info("Capture thread started")

    def stop_capture_switch(self) -> None:
        """Stop packet capture gracefully."""
        if not self.running:
            self.logger.info("Capture already stopped, ignoring stop request")
            return
        self.running = False
        self._save_state()
        if self.capture_thread is not None:
            self.capture_thread.join(timeout=10.0)
            if self.capture_thread.is_alive():
                self.logger.warning("Capture thread still running after timeout, forcing cleanup")
                self._cleanup_capture()
        self.capture_thread = None
        self.logger.info("Capture switch turned OFF")

    def _process_packet_wrapper(self, packet):
        if 'TCP' in packet and hasattr(packet.tcp, 'payload'):
            self.process_packet(packet.tcp.payload.binary_value)

    def handle_account_info(self, message):
        self.save_packet_data(message)
    
    def handle_packet(self, packet_data, proto_type):
        name = _PacketCommand_pb2.PacketCommand.Name(proto_type)
        if self.capture_info:
            if proto_type in self.capture_info:
                self.logger.info(f"Parsing: {name} {proto_type}")
                message = parse_proto(packet_data, proto_type)
                if message:
                    self.capture_info[proto_type](message)
            else:
                self.logger.info(f"No handle for: {name} {proto_type}")
                message = parse_proto(packet_data, proto_type)
                if message:
                    self.logger.info("Valid Packet")
                else:
                    self.logger.info("Invalid Packet")

def policy(message):
    for policy in message.policyList:
        name = _Defins_pb2.Operate.Policy.Name(policy.policyType)
        print(f"{name or 'UnknownPolicy'}: {getattr(policy, 'policyValue', 'N/A')}")

def main():
    capture = PacketCapture()
    capture_info = {
        # S2C_LOBBY_CHARACTER_INFO_RES: print,
        # S2C_ACCOUNT_CHARACTER_LIST_RES: print,
        # S2C_INVENTORY_MOVE_RES: print,
        # S2C_INVENTORY_SWAP_RES: print,
        # S2C_INVENTORY_MERGE_RES: print,
        # S2C_STORAGE_INFO_RES: print,
        # S2C_PING_INFO_RES: print,
        S2C_SERVICE_POLICY_NOT: policy,
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