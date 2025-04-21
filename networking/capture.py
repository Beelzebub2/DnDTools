import pyshark
import socket
import psutil
import struct
import sys
from google.protobuf.json_format import MessageToJson
import Lobby_pb2

protos_dir = "protos"

if protos_dir not in sys.path:
    sys.path.append(protos_dir)

def get_local_ip(interface_name):
    for interface, addrs in psutil.net_if_addrs().items():
        if interface == interface_name:
            for addr in addrs:
                if addr.family == socket.AF_INET:  # IPv4 address
                    return addr.address
    return None

def capture_packets(interface='Ethernet', port_range=(20200, 20300)):
    local_ip = get_local_ip(interface)
    if not local_ip:
        print(f"Could not find IP address for interface {interface}")
        return

    capture = pyshark.LiveCapture(interface=interface, display_filter=f'ip.dst == {local_ip} and tcp.srcport >= {port_range[0]} and tcp.srcport <= {port_range[1]}')

    packet_data = b""
    for packet in capture.sniff_continuously():
        if 'TCP' in packet:
            if hasattr(packet.tcp, 'payload'):
                payload = packet.tcp.payload.binary_value
                if len(payload) > 8:
                    packet_data += payload
                    packet_length, proto_type, random_padding = struct.unpack('<IHH', packet_data[:8])

                    if proto_type == 44: # S2C_LOBBY_CHARACTER_INFO_RES
                        print(f"{len(packet_data)}/{packet_length}")
                        
                        if len(packet_data) == packet_length:
                            print(f"Packet Length: {packet_length}, Proto Type: {proto_type}, Random Padding: {random_padding}")

                            data = packet_data[8:]

                            info = Lobby_pb2.SS2C_LOBBY_CHARACTER_INFO_RES()
                            info.ParseFromString(data)

                            json_data = MessageToJson(info)

                            with open("packet_data.json", "w") as json_file:
                                json_file.write(json_data)

                            packet_data = b""

                        if len(packet_data) > packet_length:
                            print("ERROR")
                            packet_data = b""
                    else:
                        packet_data = b""

if __name__ == "__main__":
    capture_packets()

