import socket
import psutil
import struct
import json
from datetime import datetime
import os
import logging
import sys
import subprocess
from typing import Tuple, Optional
from google.protobuf.json_format import MessageToJson
import threading
import time
from .appdirs import get_data_dir, get_capture_state_file, is_frozen
from scapy.all import sniff, TCP, IP
from collections import defaultdict

# Add the absolute path to the protos directory
current_dir = os.path.dirname(os.path.abspath(__file__))
ui_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
protos_path = os.path.join(ui_root, "networking", "protos")
sys.path.insert(0, protos_path)

from networking.protos import Lobby_pb2
from networking.protos import _PacketCommand_pb2

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

class PacketProtocol:
    S2C_LOBBY_CHARACTER_INFO_RES = 44
    S2C_ACCOUNT_CHARACTER_LIST_RES = 18
    
    @staticmethod
    def is_valid_type(proto_type: int) -> bool:
        return proto_type in [
            PacketProtocol.S2C_LOBBY_CHARACTER_INFO_RES,
            PacketProtocol.S2C_ACCOUNT_CHARACTER_LIST_RES
        ]

class PacketCapture:

    def __init__(self, interface: str = 'Ethernet', port_range: Tuple[int, int] = (20200, 20300), on_new_character=None):
        self.interface = interface
        self.port_range = port_range
        self.packet_data = b""
        # Use the centralized logging system instead of configuring logging here
        self.logger = logging.getLogger(__name__)
        self.data_dir = get_data_dir()
        self.STATE_FILE = get_capture_state_file()
        os.makedirs(self.data_dir, exist_ok=True)
        self.MAX_BUFFER_SIZE = 1024 * 1024  # 1MB
        self.expected_packet_length = None
        self.expected_proto_type = None
        self.running = False  # Initialize as False first
        self.capture_thread = None
        self.on_new_character = on_new_character
        self.tcp_stream_buffers = defaultdict(bytes)  # key: (src_ip, src_port, dst_ip, dst_port)
        
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
        valid_packet_range = (100, 2 * 1024 * 1024)  # Between 100 bytes and 2MB
        return (
            valid_packet_range[0] <= length <= valid_packet_range[1] and
            PacketProtocol.is_valid_type(proto_type) and
            padding in [0, 256]  # Common padding values
        )

    def process_packet(self, data: bytes) -> Optional[bool]:
        if len(data) > 0:
            self.packet_data += data
            current_size = len(self.packet_data)
            self.logger.debug(f"Received {len(data)} bytes, buffer size now {current_size}")
            if current_size > self.MAX_BUFFER_SIZE:
                self.logger.warning(f"Buffer exceeded max size ({self.MAX_BUFFER_SIZE} bytes), resetting state.")
                self.reset_state()
                return
            if self.expected_packet_length is None and current_size >= 8:
                try:
                    packet_length, proto_type, random_padding = struct.unpack('<IHH', self.packet_data[:8])
                    packet_type_name = _PacketCommand_pb2._PACKETCOMMAND.values_by_number[proto_type].name if proto_type in _PacketCommand_pb2._PACKETCOMMAND.values_by_number else "Unknown"
                    self.logger.info(f"Header: Type={proto_type} ({packet_type_name}), Length={packet_length}, Padding={random_padding}")
                    if not self.validate_packet_header(packet_length, proto_type, random_padding):
                        self.logger.warning(f"Invalid packet header: Type={proto_type} ({packet_type_name}), Length={packet_length}, Padding={random_padding}")
                        self.reset_state()
                        return
                    self.logger.info(f"Started new packet: {packet_type_name} (Type={proto_type}, Length={packet_length})")
                    self.expected_packet_length = packet_length
                    self.expected_proto_type = proto_type
                except struct.error as e:
                    self.logger.error(f"Header unpack error: {e}")
                    self.reset_state()
                    return
            if self.expected_packet_length and self.expected_proto_type:
                if current_size > self.expected_packet_length:
                    self.logger.warning(f"Trimming overflow {current_size} -> {self.expected_packet_length}")
                    self.packet_data = self.packet_data[:self.expected_packet_length]
                    current_size = self.expected_packet_length
                if current_size == self.expected_packet_length:
                    self.logger.info(f"Full packet received: {current_size} bytes (Type={self.expected_proto_type})")
                    if self.verify_packet():
                        self.logger.info(f"Packet verified successfully (Type={self.expected_proto_type})")
                        self.save_packet_data()
                        self.reset_state()
                        return True
                    else:
                        self.logger.warning(f"Packet verification failed (Type={self.expected_proto_type})")
                        self.reset_state()
                elif current_size % 8192 == 0 or current_size == self.expected_packet_length - 1:
                    self.logger.debug(f"Accumulating: {current_size}/{self.expected_packet_length}")
        # No spammy return
        return

    def verify_packet(self) -> bool:
        """Verify packet integrity before saving"""
        if len(self.packet_data) != self.expected_packet_length:
            self.logger.error(f"verify_packet: Length mismatch: got {len(self.packet_data)}, expected {self.expected_packet_length}")
            return False
        data = self.packet_data[8:]
        try:
            if self.expected_proto_type == PacketProtocol.S2C_LOBBY_CHARACTER_INFO_RES:
                info = Lobby_pb2.SS2C_LOBBY_CHARACTER_INFO_RES()
            elif self.expected_proto_type == PacketProtocol.S2C_ACCOUNT_CHARACTER_LIST_RES:
                info = Lobby_pb2.SS2C_ACCOUNT_CHARACTER_LIST_RES()
            else:
                self.logger.error(f"verify_packet: Unsupported proto type: {self.expected_proto_type}")
                return False
            info.ParseFromString(data)
            return True
        except Exception as e:
            self.logger.error(f"verify_packet: Exception parsing proto type {self.expected_proto_type}: {e}\nFirst 32 bytes: {data[:32].hex()}")
            return False

    def reset_state(self) -> None:
        """Reset all packet processing state"""
        self.packet_data = b""
        self.expected_packet_length = None
        self.expected_proto_type = None

    def save_packet_data(self) -> bool:
        try:
            if len(self.packet_data) != self.expected_packet_length:
                raise ValueError("Packet length mismatch")
            data = self.packet_data[8:]
            try:
                if self.expected_proto_type == PacketProtocol.S2C_LOBBY_CHARACTER_INFO_RES:
                    info = Lobby_pb2.SS2C_LOBBY_CHARACTER_INFO_RES()
                elif self.expected_proto_type == PacketProtocol.S2C_ACCOUNT_CHARACTER_LIST_RES:
                    info = Lobby_pb2.SS2C_ACCOUNT_CHARACTER_LIST_RES()
                else:
                    self.logger.info(f"Skipping unsupported proto type: {self.expected_proto_type}")
                    return False
                info.ParseFromString(data)
                json_data = MessageToJson(info)
            except Exception as e:
                self.logger.error(f"Failed to parse packet data for proto type {self.expected_proto_type}: {e}")
                return False

            # Overwrite file if characterId matches (no date in filename)
            if '"result": 1' in json_data and '"characterDataBase": {' in json_data:
                char_data = info.characterDataBase
                char_id = str(char_data.characterId)
                data_file = os.path.join(self.data_dir, f"{char_id}.json")
                with open(data_file, "w", encoding='utf-8') as f:
                    f.write(json_data)
                self.logger.info(f"Saved/updated target packet data to {data_file} (characterId={char_id})")
                if self.on_new_character:
                    self.on_new_character(char_id)
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

    def process_tcp_stream(self, conn_key, data: bytes) -> Optional[bool]:
        """
        Buffer TCP data for a connection, parse packets as they become available.
        """
        self.tcp_stream_buffers[conn_key] += data
        buf = self.tcp_stream_buffers[conn_key]
        processed_any = False
        while True:
            if len(buf) < 8:
                break  # Not enough for header
            try:
                packet_length, proto_type, random_padding = struct.unpack('<IHH', buf[:8])
            except struct.error:
                break
            if not self.validate_packet_header(packet_length, proto_type, random_padding):
                # Desync, drop one byte and try again
                buf = buf[1:]
                continue
            if len(buf) < packet_length:
                break  # Wait for more data
            # We have a full packet
            self.packet_data = buf[:packet_length]
            self.expected_packet_length = packet_length
            self.expected_proto_type = proto_type
            self.logger.info(f"Full packet received: {packet_length} bytes (Type={proto_type})")
            if self.verify_packet():
                self.logger.info(f"Packet verified successfully (Type={proto_type})")
                if self.save_packet_data():
                    processed_any = True
            else:
                self.logger.warning(f"Packet verification failed (Type={proto_type})")
            # Remove processed packet from buffer
            buf = buf[packet_length:]
            self.reset_state()
        self.tcp_stream_buffers[conn_key] = buf
        if processed_any:
            return True
        # No spammy return
        return

    def capture(self) -> bool:
        found_flag = False
        local_ip = self.get_local_ip()
        if not local_ip:
            print(f"Could not find IP address for interface {self.interface}")
            return False

        # BPF filter for scapy
        display_filter = (
            f"tcp and dst host {local_ip} and src portrange {self.port_range[0]}-{self.port_range[1]}"
        )

        def scapy_packet_callback(packet):
            if packet.haslayer(TCP) and packet.haslayer(IP):
                ip = packet[IP]
                tcp = packet[TCP]
                conn_key = (ip.src, tcp.sport, ip.dst, tcp.dport)
                payload = bytes(tcp.payload)
                self.process_tcp_stream(conn_key, payload)  # Do not print or log return value
            # Do not return False or anything

        try:
            sniff(
                iface=self.interface,
                filter=display_filter,
                prn=scapy_packet_callback,
                store=0,
                stop_filter=lambda x: found_flag or not self.running
            )
        except KeyboardInterrupt:
            print("Capture stopped by user")
        except Exception as e:
            print(f"Capture error: {e}")
        finally:
            self.packet_data = b""
        return found_flag

    def capture_loop(self) -> None:
        local_ip = self.get_local_ip()
        if not local_ip:
            self.logger.error(f"Could not find IP address for interface {self.interface}")
            return

        display_filter = (
            f"tcp and dst host {local_ip} and src portrange {self.port_range[0]}-{self.port_range[1]}"
        )

        def scapy_packet_callback(packet):
            if not self.running:
                return True  # Stop sniffing
            if packet.haslayer(TCP) and packet.haslayer(IP):
                ip = packet[IP]
                tcp = packet[TCP]
                conn_key = (ip.src, tcp.sport, ip.dst, tcp.dport)
                payload = bytes(tcp.payload)
                self.process_tcp_stream(conn_key, payload)
            # Do not return False or anything

        try:
            sniff(
                iface=self.interface,
                filter=display_filter,
                prn=scapy_packet_callback,
                store=0,
                stop_filter=lambda x: not self.running
            )
        except Exception as e:
            self.logger.error(f"Capture loop error: {e}")
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

def main():
    capture = PacketCapture()
    # Simulate switch: start background capture
    capture.start_capture_switch()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        capture.stop_capture_switch()

if __name__ == "__main__":
    main()