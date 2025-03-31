
import argparse
import os
from utils import get_service_id

def start_service(name, conversation_config_base64, sip_credentials_base64, num_devices, openai_key):
    service_id = get_service_id(name)
    
    # Write service config
    service_config = f"""
    [Unit]
    Description=Runs Pyckup instance {name}   
    After=network.target

    [Service]
    ExecStart=python /home/ubuntu/pyckup/launcher/launcher.py
    WorkingDirectory=/home/ubuntu/pyckup/
    Environment="name={name}"
    Environment="sip_credentials_base64={sip_credentials_base64}"
    Environment="conversation_config_base64={conversation_config_base64}"
    Environment="num_devices={num_devices}"
    Environment="PYTHONPATH=${{PYTHONPATH}}:/home/ubuntu/pyckup"
    Environment="OPENAI_API_KEY={openai_key}"
    Restart=always
    User=ubuntu
    Group=ubuntu
    StandardOutput=append:/home/ubuntu/pyckup/launcher/artifacts/{name}.log

    [Install]
    WantedBy=multi-user.target
    """
    
    service_file_path = f"/etc/systemd/system/{service_id}.service"
    with open(service_file_path, 'w') as service_file:
        service_file.write(service_config)
        
    # Execute service
    os.system(f"sudo systemctl daemon-reload")
    os.system(f"sudo systemctl enable {service_id}")
    os.system(f"sudo systemctl start {service_id}")
    
    print(f"Started service {service_id}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Start Pyckup instance as a systemd service.')
    parser.add_argument('name', type=str, help='Name of the service')
    parser.add_argument('conversation_config_base64', type=str, help='Base64 encoded conversation configuration')
    parser.add_argument('sip_credentials_base64', type=str, help='Base64 encoded SIP credentials')
    parser.add_argument('num_devices', type=int, help='Number of possible simultaneous devices')
    parser.add_argument('openai_key', type=str, help='OpenAI API key')


    args = parser.parse_args()
    
    start_service(args.name, args.conversation_config_base64, args.sip_credentials_base64, args.num_devices, args.openai_key)