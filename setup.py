#!/usr/bin/env python3
import hashlib
import secrets
import json
import getpass
from pathlib import Path

config_file = Path(__file__).parent / "config.json"

password = getpass.getpass("Введи пароль для веб-интерфейса: ")
salt = secrets.token_hex(16)
password_hash = hashlib.sha256((password + salt).encode()).hexdigest()

config = {"password_hash": password_hash, "salt": salt}

with open(config_file, "w") as f:
    json.dump(config, f, indent=2)

print(f"Пароль сохранён в {config_file}")
