import pyshark
import socket
import psutil
import struct
from datetime import datetime
import os
import logging
from typing import Tuple, Optional
from google.protobuf.json_format import MessageToJson
import Lobby_pb2

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
            packet_length, proto_type, random_padding = struct.unpack('<IHH', self.packet_data[:8])

            if proto_type == 44:  # S2C_LOBBY_CHARACTER_INFO_RES
                if len(self.packet_data) == packet_length:
                    self.logger.info(f"Processing packet: Length={packet_length}, Type={proto_type}")
                    self.save_packet_data()
                elif len(self.packet_data) > packet_length:
                    self.logger.error("Packet data overflow")
                    self.packet_data = b""
            else:
                self.packet_data = b""

    def save_packet_data(self) -> None:
        try:
            info = Lobby_pb2.SS2C_LOBBY_CHARACTER_INFO_RES()
            info.ParseFromString(self.packet_data[8:])
            json_data = MessageToJson(info)

            filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            path = os.path.join(self.data_dir, f"{filename}.json")
            with open(path, "w") as json_file:
                json_file.write(json_data)

            self.logger.info(f"Successfully saved packet data to: {path}")
            
        except Exception as e:
            self.logger.error(f"Failed to save packet data: {e}")
        finally:
            self.packet_data = b""

    def capture(self) -> None:
        local_ip = self.get_local_ip()
        if not local_ip:
            self.logger.error(f"Could not find IP address for interface {self.interface}")
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
            self.logger.info("Capture stopped by user")
        except Exception as e:
            self.logger.error(f"Capture error: {e}")

def main():
    capture = PacketCapture()
    capture.capture()

if __name__ == "__main__":
    main()

