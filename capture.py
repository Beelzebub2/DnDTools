import pyshark
import socket
import psutil
import struct
from datetime import datetime
import os
import logging
import os
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

class PacketCapture:
    def __init__(self, interface: str = 'Ethernet', port_range: Tuple[int, int] = (20200, 20300)):
        self.interface = interface
        self.port_range = port_range
        self.packet_data = b""
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.data_dir = "data"
        os.makedirs(self.data_dir, exist_ok=True)

    def get_local_ip(self) -> Optional[str]:
        for interface, addrs in psutil.net_if_addrs().items():
            if interface == self.interface:
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        return addr.address
        return None

    def process_packet(self, data: bytes) -> None:
        if len(data) > 8:
            self.packet_data += data
            
            # Only try to parse header if we haven't validated it yet
            if len(self.packet_data) >= 8:
                packet_length, proto_type, random_padding = struct.unpack('<IHH', self.packet_data[:8])
                
                if proto_type == PacketProtocol.S2C_LOBBY_CHARACTER_INFO_RES:
                    print(f"Current packet size: {len(self.packet_data)}/{packet_length}")
                    
                    # Complete packet received
                    if len(self.packet_data) == packet_length:
                        print(f"Complete packet received - Length: {packet_length}, Type: {proto_type}")
                        self.save_packet_data()
                    # Overflow detected - reset buffer
                    elif len(self.packet_data) > packet_length:
                        print(f"Packet overflow detected ({len(self.packet_data)} > {packet_length})")
                        self.packet_data = b""
                    # Still waiting for more fragments
                    else:
                        print(f"Waiting for more data...")
                else:
                    print(f"Incorrect packet type: {proto_type}")
                    self.packet_data = b""

    def save_packet_data(self) -> None:
        data = self.packet_data[8:]
        info = Lobby_pb2.SS2C_LOBBY_CHARACTER_INFO_RES()
        info.ParseFromString(data)
        json_data = MessageToJson(info)
        
        with open("packet_data.json", "w") as json_file:
            json_file.write(json_data)

        self.packet_data = b""

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

