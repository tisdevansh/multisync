import json
import os
import threading

APP_NAME = "MultiSync"
APP_VERSION = "1.0.0"


def config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


class Config:
    def __init__(self, path=None):
        self.path = path or os.path.join(config_dir(), "config.json")
        self.lock = threading.Lock()
        self.data = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def output(self, oid):
        return self.data.setdefault("outputs", {}).setdefault(oid, {})

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2)
                os.replace(tmp, self.path)
            except OSError:
                pass
