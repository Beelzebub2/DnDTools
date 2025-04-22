import pyshark
import socket
import psutil
import struct
from datetime import datetime
import os
import logging
import sys
import shutil  # Add this import
from typing import Tuple, Optional
from google.protobuf.json_format import MessageToJson

# Add the absolute path to the protos directory
current_dir = os.path.dirname(os.path.abspath(__file__))
protos_path = os.path.join(current_dir, "networking", "protos")
sys.path.append(protos_path)

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
    def __init__(self, interface: str = 'Ethernet', port_range: Tuple[int, int] = (20200, 20300)):
        self.interface = interface
        self.port_range = port_range
        self.packet_data = b""
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.data_dir = "data"
        os.makedirs(self.data_dir, exist_ok=True)
        self.MAX_BUFFER_SIZE = 1024 * 1024  # Increase to 1MB
        self.expected_packet_length = None
        self.expected_proto_type = None

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

    def process_packet(self, data: bytes) -> None:
        if len(data) > 0:
            # Add incoming data to buffer
            self.packet_data += data
            current_size = len(self.packet_data)
            
            # Reset if buffer gets too large
            if current_size > self.MAX_BUFFER_SIZE:
                print(f"Buffer exceeded max size ({self.MAX_BUFFER_SIZE} bytes)")
                self.reset_state()
                return

            # Try to parse/validate header
            if self.expected_packet_length is None and current_size >= 8:
                try:
                    packet_length, proto_type, random_padding = struct.unpack('<IHH', self.packet_data[:8])
                    
                    if not self.validate_packet_header(packet_length, proto_type, random_padding):
                        print(f"Invalid header: Length={packet_length}, Type={proto_type}, Padding={random_padding}")
                        self.reset_state()
                        return

                    print(f"New packet header: Length={packet_length}, Type={proto_type}, Padding={random_padding}")
                    self.expected_packet_length = packet_length
                    self.expected_proto_type = proto_type
                except struct.error:
                    self.reset_state()
                    return

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

    def verify_packet(self) -> bool:
        """Verify packet integrity before saving"""
        try:
            if len(self.packet_data) != self.expected_packet_length:
                return False
                
            # Try parsing the protobuf message without saving
            data = self.packet_data[8:]
            info = Lobby_pb2.SS2C_LOBBY_CHARACTER_INFO_RES()
            info.ParseFromString(data)
            return True
        except Exception as e:
            print(f"Packet verification failed: {e}")
            return False

    def reset_state(self) -> None:
        """Reset all packet processing state"""
        self.packet_data = b""
        self.expected_packet_length = None
        self.expected_proto_type = None

    def save_packet_data(self) -> None:
        try:
            if len(self.packet_data) != self.expected_packet_length:
                raise ValueError("Packet length mismatch")
                
            data = self.packet_data[8:]
            info = Lobby_pb2.SS2C_LOBBY_CHARACTER_INFO_RES()
            info.ParseFromString(data)
            json_data = MessageToJson(info)
            
            # Save both to data dir and root for compatibility
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            data_file = os.path.join(self.data_dir, f"{timestamp}.json")
            root_file = "packet_data.json"
            
            # Save to timestamped file
            with open(data_file, "w", encoding='utf-8') as f:
                f.write(json_data)
                
            # Save/update packet_data.json in root
            with open(root_file, "w", encoding='utf-8') as f:
                f.write(json_data)
            
            print(f"Successfully saved packet data to {data_file} and {root_file}")
            
        except Exception as e:
            print(f"Failed to save packet data: {str(e)}")
            raise

    def capture(self) -> None:
        local_ip = self.get_local_ip()
        if not local_ip:
            print(f"Could not find IP address for interface {self.interface}")
            return

        display_filter = (f'ip.dst == {local_ip} and '
                         f'tcp.srcport >= {self.port_range[0]} and '
                         f'tcp.srcport <= {self.port_range[1]}')
        
        capture = pyshark.LiveCapture(interface=self.interface, display_filter=display_filter)

        try:
            for packet in capture.sniff_continuously():
                if 'TCP' in packet and hasattr(packet.tcp, 'payload'):
                    self.process_packet(packet.tcp.payload.binary_value)
        except KeyboardInterrupt:
            print("Capture stopped by user")
        except Exception as e:
            print(f"Capture error: {e}")
        finally:
            try:
                capture.close()
            except:
                pass
            self.packet_data = b""

    def _process_packet_wrapper(self, packet):
        if 'TCP' in packet and hasattr(packet.tcp, 'payload'):
            self.process_packet(packet.tcp.payload.binary_value)

def main():
    capture = PacketCapture()
    capture.capture()

if __name__ == "__main__":
    main()

