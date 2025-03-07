import argparse
import base64
import os
from pathlib import Path
from calle_core.call_e import call_e

HERE = Path(os.path.abspath(__file__)).parent

def unpack_base64(base64_string, file_path):
    with open(file_path, 'wb') as file:
        file.write(base64.b64decode(base64_string))
    
def launch_calle(sip_credentials_base64, conversation_config_base64, num_devices):
    sip_credentials_path = HERE / 'artifacts/sip_credentials.json'
    conversation_config_path = HERE / 'artifacts/conversation_config.yaml'
    log_dir = HERE / 'artifacts/logs'
    
    unpack_base64(sip_credentials_base64, sip_credentials_path)
    unpack_base64(conversation_config_base64, conversation_config_path)
    
    calle = call_e(sip_credentials_path=sip_credentials_path, log_dir=log_dir)
    calle.start_listening(conversation_config_path=conversation_config_path, num_devices=num_devices)
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Launch a new Call-E instance.')
    parser.add_argument('conversation_config_base64', type=str, help='Base64 encoded conversation configuration')
    parser.add_argument('sip_credentials_base64', type=str, help='Base64 encoded SIP credentials')
    parser.add_argument('num_devices', type=int, help='Number of possible simultaneous devices')

    args = parser.parse_args()
    launch_calle(args.sip_credentials_base64, args.conversation_config_base64, args.num_devices)