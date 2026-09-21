import json
import os
from pathlib import Path


def private_write(path, text):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)


def write_json(path, data):
    private_write(path, json.dumps(data, indent=2) + '\n')


def new_directory(path):
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(path, 0o700)
    return path
