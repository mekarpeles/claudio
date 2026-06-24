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
    """Send a message to a running claudio agent by name. Returns the daemon ack."""
    path = socket_path(name, state_dir)
    return send_to(path, message)


def send_to(sock_path: str, message: dict) -> dict:
    """Connect directly to a socket path and send a message. Returns the response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    s.sendall(json.dumps(message).encode())
    ack = s.recv(65536)
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
    from .peers import Peers, peers_path

    os.makedirs(state_dir, exist_ok=True)
    sock = socket_path(name, state_dir)
    try:
        os.unlink(sock)
    except FileNotFoundError:
        pass

    queue: deque = deque()
    lock = threading.Lock()
    # pending_pairs: name -> (held_conn, remote_socket)
    pending_pairs: dict = {}
    peers = Peers(peers_path(name, state_dir))

    def handle_connection(conn: socket.socket) -> None:
        try:
            data = conn.recv(65536)
            if not data:
                conn.close()
                return
            msg = json.loads(data)

            claudio_type = msg.get('_claudio')

            if claudio_type == 'pair_request':
                remote_name = msg.get('name', '')
                remote_socket = msg.get('socket', '')
                # Hold the connection open; enqueue a notification
                with lock:
                    pending_pairs[remote_name] = (conn, remote_socket)
                    queue.append({
                        'body': (
                            f'[claudio]: {remote_name} at {remote_socket} wants to pair. '
                            f'Run: claudio pair --approve {remote_name}'
                        )
                    })
                # Do NOT close conn here — it stays open until approved/rejected

            elif claudio_type == 'pair_approve':
                approve_name = msg.get('name', '')
                with lock:
                    pair_info = pending_pairs.pop(approve_name, None)
                if pair_info is None:
                    conn.sendall(
                        json.dumps({'ok': False, 'error': f"no pending pair request from '{approve_name}'"}).encode()
                    )
                    conn.close()
                else:
                    held_conn, remote_socket = pair_info
                    # Record the peer
                    peers.add(approve_name, remote_socket)
                    # Respond to alice's held connection
                    own_sock = socket_path(name, state_dir)
                    held_conn.sendall(
                        json.dumps({'ok': True, 'name': name, 'socket': own_sock}).encode()
                    )
                    held_conn.close()
                    # Respond to the approve caller
                    conn.sendall(json.dumps({'ok': True}).encode())
                    conn.close()

            else:
                # Regular message: enqueue, ack, close
                with lock:
                    queue.append(msg)
                conn.sendall(json.dumps({'ok': True}).encode())
                conn.close()

        except Exception as e:
            try:
                conn.sendall(json.dumps({'ok': False, 'error': str(e)}).encode())
                conn.close()
            except Exception:
                pass

    def serve() -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock)
        srv.listen(20)
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                continue
            t = threading.Thread(target=handle_connection, args=(conn,), daemon=True)
            t.start()

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
