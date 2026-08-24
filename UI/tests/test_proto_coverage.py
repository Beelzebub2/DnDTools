import importlib
import pkgutil
import sys

import networking.protos
from networking.protos import _PacketCommand_pb2


_PROTOS_PATH = str(next(iter(networking.protos.__path__)))
if _PROTOS_PATH not in sys.path:
    sys.path.insert(0, _PROTOS_PATH)


KNOWN_COMMANDS_WITHOUT_EXTRACTED_MESSAGES = {
    "S2C_PARTY_OUTLAW_CHANGE_NOT",
    "C2S_MERCHANT_SERVICE_REPAIR_REQ",
    "S2C_MERCHANT_SERVICE_REPAIR_RES",
    "S2C_SQUIRE_STATUS_RESTRICTED_CONTENT_NOT",
    "S2D_GATHERING_HALL_PARTY_INFO_NOT",
}


def _is_sentinel(name):
    return (
        name == "PACKET_NONE"
        or name.startswith(("MIN_", "MAX_"))
        or name.endswith(("_BEGIN", "_END"))
    )


def _build_current_map():
    symbols = {}
    for module_info in pkgutil.iter_modules(networking.protos.__path__):
        if not module_info.name.endswith("_pb2"):
            continue
        module = importlib.import_module(f"networking.protos.{module_info.name}")
        for name in dir(module):
            if not name.startswith("_"):
                symbols[name] = getattr(module, name)

    result = {}
    for value in _PacketCommand_pb2.PacketCommand.values():
        command_name = _PacketCommand_pb2.PacketCommand.Name(value)
        for candidate in (f"S{command_name}", command_name):
            message_class = symbols.get(candidate)
            if callable(getattr(message_class, "ParseFromString", None)):
                result[value] = message_class
                break
    return result


def test_every_extracted_packet_message_is_mapped_or_a_known_schema_gap():
    proto_map = _build_current_map()
    missing = set()
    for value in _PacketCommand_pb2.PacketCommand.values():
        name = _PacketCommand_pb2.PacketCommand.Name(value)
        if not _is_sentinel(name) and value not in proto_map:
            missing.add(name)

    assert missing == KNOWN_COMMANDS_WITHOUT_EXTRACTED_MESSAGES


def test_merchant_list_command_maps_to_current_response_message():
    proto_map = _build_current_map()
    command = _PacketCommand_pb2.PacketCommand.S2C_MERCHANT_LIST_RES
    message_class = proto_map[command]

    assert message_class.__name__ == "SS2C_MERCHANT_LIST_RES"
    assert callable(getattr(message_class(), "ParseFromString", None))
