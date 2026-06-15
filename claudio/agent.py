"""
claudio — IO wrapper that gives a Claude Code session an inbox.

Listens on a Unix socket for incoming messages, queues them, and delivers
each one via the provided `deliver` callback when `is_idle` returns True.
"""

import json
import os
import socket
import threading
import time
from collections import deque
from typing import Callable

POLL_INTERVAL = 0.5  # seconds between idle checks
DEFAULT_STATE_DIR = os.path.expanduser('~/.claudio')


def socket_path(name: str, state_dir: str = DEFAULT_STATE_DIR) -> str:
    return os.path.join(state_dir, f'{name}.sock')


def send(name: str, message: dict, state_dir: str = DEFAULT_STATE_DIR) -> dict:
    """Send a message to a running claudio agent. Returns the daemon ack."""
    path = socket_path(name, state_dir)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    s.sendall(json.dumps(message).encode())
    ack = s.recv(256)
    s.close()
    return json.loads(ack) if ack else {}


def run(
    name: str,
    deliver: Callable[[dict], None],
    is_idle: Callable[[], bool],
    state_dir: str = DEFAULT_STATE_DIR,
) -> None:
    """
    Start a claudio agent.

    name      — unique agent name; determines socket path
    deliver   — called with a message dict when the session is ready
    is_idle   — returns True when the session can accept a message
    state_dir — directory for the Unix socket (default ~/.claudio)
    """
    os.makedirs(state_dir, exist_ok=True)
    sock = socket_path(name, state_dir)
    try:
        os.unlink(sock)
    except FileNotFoundError:
        pass

    queue: deque = deque()
    lock = threading.Lock()

    def serve() -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock)
        srv.listen(20)
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                continue
            try:
                data = conn.recv(65536)
                if data:
                    with lock:
                        queue.append(json.loads(data))
                    conn.sendall(json.dumps({'ok': True}).encode())
                conn.close()
            except Exception as e:
                try:
                    conn.sendall(json.dumps({'ok': False, 'error': str(e)}).encode())
                    conn.close()
                except Exception:
                    pass

    threading.Thread(target=serve, daemon=True).start()

    while True:
        time.sleep(POLL_INTERVAL)
        with lock:
            if not queue:
                continue
        if not is_idle():
            continue
        with lock:
            msg = queue.popleft()
        deliver(msg)
        time.sleep(1.0)
