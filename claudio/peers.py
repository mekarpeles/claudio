"""
claudio.peers — per-agent address book: name → socket path, stored as JSON.
"""

import json
import os
from typing import Optional


class Peers:
    """Per-agent address book: name → socket path, stored as JSON."""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> dict:
        """Return the peers dict; returns {} if missing or corrupt."""
        try:
            with open(self.path, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def save(self, data: dict) -> None:
        """Persist the peers dict; creates parent dirs if needed."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(data, f)

    def add(self, name: str, sock: str) -> None:
        data = self.load()
        data[name] = sock
        self.save(data)

    def remove(self, name: str) -> None:
        """Remove a peer by name; no-op if missing."""
        data = self.load()
        if name in data:
            del data[name]
            self.save(data)

    def get(self, name: str) -> Optional[str]:
        return self.load().get(name)

    def all(self) -> dict:
        return self.load()


def peers_path(name: str, state_dir: str) -> str:
    return os.path.join(state_dir, f'{name}.peers.json')
