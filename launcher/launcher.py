import argparse
import base64
import os
from pathlib import Path
import sys
from pyckup_core.pyckup import Pyckup

HERE = Path(os.path.abspath(__file__)).parent

sys.stdout.reconfigure(line_buffering=True)

def unpack_base64(base64_string, file_path):
    with open(file_path, 'wb') as file:
        file.write(base64.b64decode(base64_string))
    
def launch_pyckup():
    name = os.environ['name']

    # Ensure artifacts directory exists
    if not os.path.exists(HERE / "artifacts"):
        os.makedirs(HERE / "artifacts")
    if not os.path.exists(HERE / f"artifacts/{name}_logs"):
        os.makedirs(HERE / f"artifacts/{name}_logs")
                
    sip_credentials_path = HERE / f'artifacts/{name}_sip_credentials.json'
    conversation_config_path = HERE / f'artifacts/{name}_conversation_config.yaml'
    log_dir = HERE / f'artifacts/{name}_logs'
    
    unpack_base64(os.environ['sip_credentials_base64'], sip_credentials_path)
    unpack_base64(os.environ['conversation_config_base64'], conversation_config_path)
    
    pu = Pyckup(sip_credentials_path=sip_credentials_path, log_dir=log_dir)
    pu.start_listening(conversation_config_path=conversation_config_path, num_devices=os.environ['num_devices'])
    
if __name__ == "__main__":
    launch_pyckup()