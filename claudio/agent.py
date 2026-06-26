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
from typing import Callable, Optional

POLL_INTERVAL = 0.5  # seconds between idle checks
_RECV_BUF = 65536
# Default to /tmp/claudio — ephemeral, no cleanup needed, no project dir pollution.
# cmux agents override this by setting CLAUDIO_STATE_DIR to their homedir.
DEFAULT_STATE_DIR = os.path.join(os.path.abspath(os.sep), 'tmp', 'claudio')


def socket_path(name: str, state_dir: str = DEFAULT_STATE_DIR) -> str:
    return os.path.join(state_dir, f'{name}.sock')


def _next_session_name(state_dir: str) -> str:
    """Return the lowest unused integer name (0, 1, 2...), like tmux sessions."""
    try:
        entries = os.listdir(state_dir)
    except FileNotFoundError:
        entries = []
    taken = set()
    for f in entries:
        if f.endswith('.sock'):
            stem = f[:-5]
            try:
                taken.add(int(stem))
            except ValueError:
                pass
    i = 0
    while i in taken:
        i += 1
    return str(i)


def send(name: str, message: dict, state_dir: str = DEFAULT_STATE_DIR) -> dict:
    """Send a message to a running claudio agent by name. Returns the daemon ack."""
    path = socket_path(name, state_dir)
    return send_to(path, message)


def send_to(sock_path: str, message: dict) -> dict:
    """Connect directly to a socket path and send a message. Returns the response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    s.sendall(json.dumps(message).encode())
    ack = s.recv(_RECV_BUF)
    s.close()
    return json.loads(ack) if ack else {}


def start(
    name: Optional[str] = None,
    deliver: Callable[[dict], None] = lambda msg: None,
    is_idle: Callable[[], bool] = lambda: True,
    state_dir: str = DEFAULT_STATE_DIR,
) -> str:
    """
    Start a claudio agent in background threads. Non-blocking. Returns the session name.

    name      — session handle; auto-assigned (0, 1, 2...) if omitted
    deliver   — called with a message dict when the session is ready
    is_idle   — returns True when the session can accept a message
    state_dir — directory for the Unix socket
    """
    from .peers import Peers, peers_path

    os.makedirs(state_dir, exist_ok=True)
    if name is None:
        name = _next_session_name(state_dir)

    sock = socket_path(name, state_dir)
    try:
        os.unlink(sock)
    except FileNotFoundError:
        pass

    queue: deque = deque()
    lock = threading.Lock()
    pending_pairs: dict = {}
    peers = Peers(peers_path(name, state_dir))

    def _handle_pair_request(conn: socket.socket, msg: dict) -> None:
        remote_name = msg.get('name', '')
        remote_socket = msg.get('socket', '')
        with lock:
            existing = pending_pairs.get(remote_name)
            if existing is not None:
                old_conn, _, _ = existing
                try:
                    old_conn.sendall(
                        json.dumps({'ok': False, 'error': 'superseded by newer request'}).encode()
                    )
                    old_conn.close()
                except Exception:
                    pass
            pending_pairs[remote_name] = (conn, remote_socket, time.time())
            queue.append({
                'body': (
                    f'[claudio]: {remote_name} at {remote_socket} wants to pair. '
                    f'Run: claudio pair --approve {remote_name}'
                )
            })
        # conn stays open until approved/rejected/timed-out

    def _handle_pair_approve(conn: socket.socket, msg: dict) -> None:
        approve_name = msg.get('name', '')
        with lock:
            pair_info = pending_pairs.pop(approve_name, None)
        if pair_info is None:
            conn.sendall(
                json.dumps({'ok': False, 'error': f"no pending pair request from '{approve_name}'"}).encode()
            )
            conn.close()
        else:
            held_conn, remote_socket, _ = pair_info
            peers.add(approve_name, remote_socket)
            own_sock = socket_path(name, state_dir)
            held_conn.sendall(
                json.dumps({'ok': True, 'name': name, 'socket': own_sock}).encode()
            )
            held_conn.close()
            conn.sendall(json.dumps({'ok': True}).encode())
            conn.close()

    def _handle_message(conn: socket.socket, msg: dict) -> None:
        # Regular message: enqueue, ack, close
        with lock:
            queue.append(msg)
        conn.sendall(json.dumps({'ok': True}).encode())
        conn.close()

    def handle_connection(conn: socket.socket) -> None:
        try:
            data = conn.recv(_RECV_BUF)
            if not data:
                conn.close()
                return
            msg = json.loads(data)

            claudio_type = msg.get('_claudio')

            if claudio_type == 'pair_request':
                _handle_pair_request(conn, msg)
            elif claudio_type == 'pair_approve':
                _handle_pair_approve(conn, msg)
            else:
                _handle_message(conn, msg)

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

    def cleanup_expired_pairs() -> None:
        PAIR_TIMEOUT = 300
        while True:
            time.sleep(POLL_INTERVAL)
            now = time.time()
            with lock:
                expired = [
                    rname for rname, (_, _, ts) in pending_pairs.items()
                    if now - ts > PAIR_TIMEOUT
                ]
            for rname in expired:
                with lock:
                    pair_info = pending_pairs.pop(rname, None)
                if pair_info is None:
                    continue
                expired_conn, _, _ = pair_info
                try:
                    expired_conn.sendall(
                        json.dumps({'ok': False, 'error': 'pair request timed out'}).encode()
                    )
                    expired_conn.close()
                except Exception:
                    pass

    def delivery_loop() -> None:
        while True:
            time.sleep(POLL_INTERVAL)
            with lock:
                if not queue:
                    continue
            if not is_idle():
                continue
            with lock:
                try:
                    msg = queue.popleft()
                except IndexError:
                    continue
            deliver(msg)
            time.sleep(1.0)

    threading.Thread(target=serve, daemon=True).start()
    threading.Thread(target=cleanup_expired_pairs, daemon=True).start()
    threading.Thread(target=delivery_loop, daemon=True).start()
    return name


def run(
    name: Optional[str] = None,
    deliver: Callable[[dict], None] = lambda msg: None,
    is_idle: Callable[[], bool] = lambda: True,
    state_dir: str = DEFAULT_STATE_DIR,
) -> None:
    """
    Start a claudio agent and block until interrupted.

    name      — session handle; auto-assigned (0, 1, 2...) if omitted
    deliver   — called with a message dict when the session is ready
    is_idle   — returns True when the session can accept a message
    state_dir — directory for the Unix socket
    """
    start(name=name, deliver=deliver, is_idle=is_idle, state_dir=state_dir)
    # Block the calling thread forever (daemon threads keep running)
    while True:
        time.sleep(3600)
