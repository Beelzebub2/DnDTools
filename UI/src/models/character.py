import os
from datetime import datetime
from google.protobuf.json_format import MessageToJson
from networking.protos import _Defins_pb2
from .appdirs import get_data_dir
import logging
logger = logging.getLogger(__name__)

def policy(message):
    for policy in message.policyList:
        name = _Defins_pb2.Operate.Policy.Name(policy.policyType)
        print(f"{name or 'UnknownPolicy'}: {getattr(policy, 'policyValue', 'N/A')}")

data_dir = get_data_dir()
os.makedirs(data_dir, exist_ok=True)

def save_packet_data(message) -> bool:
    try:

        json_data = MessageToJson(message)
        # Overwrite file if characterId matches (no date in filename)
        if '"result": 1' in json_data and '"characterDataBase": {' in json_data:
            char_data = message.characterDataBase
            char_id = str(char_data.characterId)
            data_file = os.path.join(data_dir, f"{char_id}.json")
            with open(data_file, "w", encoding='utf-8') as f:
                f.write(json_data)
            logger.info(f"Saved/updated target packet data to {data_file} (characterId={char_id})")
            return True

        # Save other packets to timestamped files as before
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        data_file = os.path.join(data_dir, f"{timestamp}.json")
        with open(data_file, "w", encoding='utf-8') as f:
            f.write(json_data)
        logger.info(f"Successfully saved packet data to {data_file}")
        return False

    except Exception as e:
        logger.error(f"Failed to save packet data: {str(e)}")
        raise