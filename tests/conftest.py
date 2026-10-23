"""Shared test helpers for the claudio (native) test suite.

Provides a fake Claude Code sessions directory + a fake messaging socket server
so the native wire protocol can be exercised with no real Claude session.
"""

import hashlib
import json
import os
import socket
import threading
import time


def wait_for(condition, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def write_session(sessions_dir, pid, sock_path, session_id, name,
                  status="idle", peer_token="deadbeefdeadbeefdeadbeefdeadbeef",
                  **extra):
    """Write a fake <pid>.json + key file into sessions_dir."""
    os.makedirs(sessions_dir, exist_ok=True)
    rec = {
        "pid": pid,
        "sessionId": session_id,
        "cwd": extra.get("cwd", "/tmp/x"),
        "messagingSocketPath": sock_path,
        "name": name,
        "status": status,
        "peerProtocol": 1,
        "peerFeatures": ["notify_idle", "reply_across_default_dirs"],
        "updatedAt": extra.get("updatedAt", int(time.time() * 1000)),
    }
    rec.update({k: v for k, v in extra.items() if k not in ("cwd", "updatedAt")})
    with open(os.path.join(sessions_dir, f"{pid}.json"), "w") as f:
        json.dump(rec, f)
    h = hashlib.sha256(sock_path.encode()).hexdigest()
    with open(os.path.join(sessions_dir, f"{pid}.{h}.key"), "w") as f:
        json.dump({"peerToken": peer_token}, f)
    return rec


class FakeSession:
    """A fake messaging socket server that records received NDJSON lines."""

    def __init__(self, sock_path):
        self.sock_path = sock_path
        self.lines = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(sock_path), exist_ok=True)
        self._srv.bind(sock_path)
        self._srv.listen(5)
        self._stop = False
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        while not self._stop:
            try:
                self._srv.settimeout(0.2)
                conn, _ = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            buf = b""
            conn.settimeout(1.0)
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            except (socket.timeout, OSError):
                pass
            for ln in buf.split(b"\n"):
                if ln.strip():
                    self.lines.append(json.loads(ln))
            conn.close()  # clean close = accepted (no ack payload)

    def close(self):
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.sock_path)
        except FileNotFoundError:
            pass
