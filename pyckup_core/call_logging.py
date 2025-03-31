import hashlib
import os
from pathlib import Path
import time
from typing import Union


def setup_log(log_dir: Union[str, Path], phone_number: str) -> Path:
    """
    Creates a log file unique to the call.

    Args:
        log_dir (str | Path): The directory where the log file will be created.
        phone_number (str): The phone number of the caller.

    Returns:
        Path: The path to the log file.
    """
    timestamp = time.strftime("%Y-%m-%d, %H:%M:%S")

    hash = hashlib.sha256(f"{phone_number}_{timestamp}".encode())
    call_id = hash.hexdigest()
    log_path = Path(log_dir) / f"{call_id}.log"

    os.makedirs(log_dir, exist_ok=True)

    with open(log_path, "w") as log_file:
        log_file.write(f"Caller Phone Number: {phone_number}\n")
        log_file.write(f"Call Start Time: {timestamp}\n")
        log_file.write("\n")

    return log_path


def log_message(log_path: Path, message: str, role: str = "User") -> None:
    """
    Appends a message to a call's log.

    Args:
        log_path (Path): The path to the log file.
        message (str): The message to append.
        role (str): Who sent the message. Defaults to "User".

    Returns:
        None
    """
    timestamp = time.strftime("%H:%M:%S")

    with open(log_path, "a") as log_file:
        log_file.write(f"[{timestamp}] {role}: {message}\n")
