import argparse
import subprocess

from utils import get_service_id


def stop_service(name):
    service_id = get_service_id(name)

    try:
        subprocess.run(f"sudo systemctl stop {service_id}", shell=True, check=True)
        subprocess.run(f"sudo systemctl disable {service_id}", shell=True, check=True)
        subprocess.run(
            f"sudo rm /etc/systemd/system/{service_id}.service", shell=True, check=True
        )
        subprocess.run("sudo systemctl daemon-reload", shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to stop service {service_id}: {e}")
        return

    print(f"Stopped service {service_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Stop systemd service of Pyckup instance."
    )
    parser.add_argument("name", type=str, help="Name of the service")

    args = parser.parse_args()

    stop_service(args.name)
