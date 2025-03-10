import os
import subprocess


def get_services():
    service_names = []
    for service_name in os.listdir("/etc/systemd/system/"):
        if service_name.startswith("calle_"):
            service_names.append(service_name)
    
    services = []
    for service_name in service_names:
        # get environment
        result = subprocess.run(
            ["sudo", "systemctl", "show", service_name, "--property=Environment"],
            capture_output=True,
            text=True
        )
        service_environment = result.stdout.split("Environment=")[1]
        service_environment = dict(item.split("=", 1) for item in service_environment.split())
        
        services.append({
            "name": service_environment["name"],
            "num_devices": service_environment["num_devices"],
        })
        
    print(services)
    

if __name__ == "__main__":
    get_services()