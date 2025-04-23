from scapy.all import sniff, AsyncSniffer
from scapy.layers.inet import IP, TCP
import socket
import psutil
import struct
import json
from datetime import datetime
import os
import logging
import sys
import shutil
from typing import Tuple, Optional
from google.protobuf.json_format import MessageToJson
import threading
import time
import asyncio

# Add the absolute path to the protos directory
current_dir = os.path.dirname(os.path.abspath(__file__))
ui_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
protos_path = os.path.join(ui_root, "networking", "protos")
sys.path.insert(0, protos_path)

# Direct imports from that folder
from networking.protos import Lobby_pb2
from networking.protos import _PacketCommand_pb2

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
    STATE_FILE = "capture_state.json"

    def __init__(self, interface: str = 'Ethernet', port_range: Tuple[int, int] = (20200, 20300), on_new_character=None):
        self.interface = interface
        self.port_range = port_range
        self.packet_data = b""
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.data_dir = "data"
        os.makedirs(self.data_dir, exist_ok=True)
        self.MAX_BUFFER_SIZE = 1024 * 1024  # 1MB
        self.expected_packet_length = None
        self.expected_proto_type = None
        self.running = False  # Initialize as False first
        self.capture_thread = None
        self.on_new_character = on_new_character
        
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
                    if self.verify_packet():
                        self.save_packet_data()
                    self.reset_state()
                # Progress update
                elif current_size % 8192 == 0:
                    print(f"Accumulating: {current_size}/{self.expected_packet_length}")
        return False

    def verify_packet(self) -> bool:
        """Verify packet integrity before saving"""
        if len(self.packet_data) != self.expected_packet_length:
            return False
        data = self.packet_data[8:]
        # Only verify supported packet types
        try:
            if self.expected_proto_type == PacketProtocol.S2C_LOBBY_CHARACTER_INFO_RES:
                info = Lobby_pb2.SS2C_LOBBY_CHARACTER_INFO_RES()
            elif self.expected_proto_type == PacketProtocol.S2C_ACCOUNT_CHARACTER_LIST_RES:
                info = Lobby_pb2.SS2C_ACCOUNT_CHARACTER_LIST_RES()
            else:
                return False
            info.ParseFromString(data)
            return True
        except Exception:
            # Suppress parse errors during verification
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
                    return False
                info.ParseFromString(data)
                json_data = MessageToJson(info)
            except Exception as e:
                print(f"Failed to parse packet data for proto type {self.expected_proto_type}: {e}")
                return False

            # Overwrite file if characterId matches (no date in filename)
            if '"result": 1' in json_data and '"characterDataBase": {' in json_data:
                char_data = info.characterDataBase
                char_id = str(char_data.characterId)
                data_file = os.path.join(self.data_dir, f"{char_id}.json")
                with open(data_file, "w", encoding='utf-8') as f:
                    f.write(json_data)
                print(f"Saved/updated target packet data to {data_file}")

                # Notify app/UI if callback is set
                if self.on_new_character:
                    self.on_new_character(char_id)
                return True

            # Save other packets to timestamped files as before
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            data_file = os.path.join(self.data_dir, f"{timestamp}.json")
            with open(data_file, "w", encoding='utf-8') as f:
                f.write(json_data)
            print(f"Successfully saved packet data to {data_file}")
            return False

        except Exception as e:
            print(f"Failed to save packet data: {str(e)}")
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

    def capture(self) -> bool:
        found_flag = False
        local_ip = self.get_local_ip()
        if not local_ip:
            print(f"Could not find IP address for interface {self.interface}")
            return False

        bpf_filter = (
            f"tcp and dst host {local_ip} and "
            f"src portrange {self.port_range[0]}-{self.port_range[1]}"
        )
        # Stop filter when target packet is found
        def stop_filter(pkt):
            nonlocal found_flag
            if pkt.haslayer(TCP) and pkt[TCP].payload:
                found_flag = self.process_packet(bytes(pkt[TCP].payload))
            return found_flag

        # Try BPF filter first, fallback to manual filtering on failure
        try:
            sniff(
                iface=self.interface,
                filter=bpf_filter,
                prn=lambda pkt: None,
                stop_filter=stop_filter,
                store=False
            )
        except Exception as e:
            print(f"Warning: BPF filter failed ({e}), falling back to manual filtering.")
            def stop_filter_manual(pkt):
                nonlocal found_flag
                if pkt.haslayer(IP) and pkt.haslayer(TCP):
                    if pkt[IP].dst == local_ip and self.port_range[0] <= pkt[TCP].sport <= self.port_range[1]:
                        found_flag = self.process_packet(bytes(pkt[TCP].payload))
                return found_flag
            sniff(
                iface=self.interface,
                prn=lambda pkt: None,
                stop_filter=stop_filter_manual,
                store=False
            )
        return found_flag

    def capture_loop(self) -> None:
        local_ip = self.get_local_ip()
        if not local_ip:
            self.logger.error(f"Could not find IP address for interface {self.interface}")
            return
        bpf_filter = (
            f"tcp and dst host {local_ip} and "
            f"src portrange {self.port_range[0]}-{self.port_range[1]}"
        )
        # Use AsyncSniffer with BPF, fallback to manual filtering on failure
        try:
            sniffer = AsyncSniffer(
                iface=self.interface,
                filter=bpf_filter,
                prn=lambda pkt: self._process_packet_wrapper(pkt),
                store=False
            )
            self._current_sniffer = sniffer
            sniffer.start()
        except Exception as e:
            self.logger.warning(f"BPF filter failed ({e}), falling back to manual filtering.")
            sniffer = AsyncSniffer(
                iface=self.interface,
                prn=lambda pkt: self._process_packet_wrapper(pkt) if pkt.haslayer(IP) and pkt.haslayer(TCP) and pkt[IP].dst == local_ip and self.port_range[0] <= pkt[TCP].sport <= self.port_range[1] else None,
                store=False
            )
            self._current_sniffer = sniffer
            sniffer.start()
        try:
            while self.running:
                time.sleep(0.1)
        except Exception as e:
            self.logger.error(f"Capture loop error: {e}")
        finally:
            sniffer.stop()
            self.reset_state()

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
        """Process a scapy packet wrapper callback."""
        # Only process TCP packets with payload
        if packet.haslayer(TCP) and packet[TCP].payload:
            self.process_packet(bytes(packet[TCP].payload))

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

