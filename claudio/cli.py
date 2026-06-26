"""
claudio CLI — peer management and message sending commands.

Commands:
    claudio pair <socket>              # initiate pairing (blocks until approved, 5min timeout)
    claudio pair --approve <name>      # approve a pending pair request from <name>
    claudio peers                      # list current peers
    claudio send <name|socket> <msg>   # send a message to a peer
"""

import os
import sys
from typing import Optional

from .agent import DEFAULT_STATE_DIR, send_to, socket_path
from .peers import Peers, peers_path


def _agent_name() -> Optional[str]:
    return os.environ.get('CLAUDIO_AGENT_NAME') or os.environ.get('CMUX_SESSION_NAME') or None


def _state_dir() -> str:
    return (
        os.environ.get('CLAUDIO_STATE_DIR')
        or os.environ.get('CMUX_STATE_DIR')
        or DEFAULT_STATE_DIR
    )


def _resolve_target(target: str, state_dir: str, agent_name: Optional[str]) -> Optional[str]:
    """
    Resolve a send target to a socket path.

    1. If target starts with / or ~: use as socket path directly.
    2. Look up in peers file (requires agent_name).
    3. Return None — caller must surface a clear error rather than silently
       falling back to a convention-based path that may not exist.
    """
    if target.startswith('/') or target.startswith('~'):
        return os.path.expanduser(target)
    if agent_name:
        peers = Peers(peers_path(agent_name, state_dir))
        sock = peers.get(target)
        if sock:
            return sock
    return None


def cmd_pair(args: list, state_dir: Optional[str] = None, agent_name: Optional[str] = None) -> int:
    """
    claudio pair <socket>              — initiate pairing
    claudio pair --approve <name>      — approve a pending pair request
    """
    state_dir = state_dir or _state_dir()
    agent_name = agent_name or _agent_name()

    if args and args[0] == '--approve':
        if len(args) < 2:
            print("usage: claudio pair --approve <name>", file=sys.stderr)
            return 1
        remote_name = args[1]
        if not agent_name:
            print("claudio: CLAUDIO_AGENT_NAME or CMUX_SESSION_NAME must be set", file=sys.stderr)
            return 1
        own_sock = socket_path(agent_name, state_dir)
        try:
            resp = send_to(own_sock, {'_claudio': 'pair_approve', 'name': remote_name})
        except Exception as e:
            print(f"claudio: failed to contact own daemon: {e}", file=sys.stderr)
            return 1
        if resp.get('ok'):
            print(f"claudio: paired with {remote_name}")
            return 0
        else:
            print(f"claudio: pair approve failed: {resp.get('error', 'unknown error')}", file=sys.stderr)
            return 1

    # Initiate pairing
    if not args:
        print("usage: claudio pair <socket>", file=sys.stderr)
        return 1
    target_socket = os.path.expanduser(args[0])
    if not agent_name:
        print("claudio: CLAUDIO_AGENT_NAME or CMUX_SESSION_NAME must be set", file=sys.stderr)
        return 1
    own_sock = socket_path(agent_name, state_dir)

    import socket as _socket
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        s.connect(target_socket)
    except Exception as e:
        print(f"claudio: cannot connect to {target_socket}: {e}", file=sys.stderr)
        return 1

    msg = {'_claudio': 'pair_request', 'name': agent_name, 'socket': own_sock}
    import json
    s.sendall(json.dumps(msg).encode())
    s.settimeout(300)  # 5-minute timeout
    try:
        data = s.recv(65536)
    except _socket.timeout:
        print("claudio: pair request timed out (5 minutes)", file=sys.stderr)
        s.close()
        return 1
    finally:
        s.close()

    if not data:
        print("claudio: no response from remote agent", file=sys.stderr)
        return 1

    resp = json.loads(data)
    if resp.get('ok'):
        remote_name = resp.get('name', target_socket)
        remote_sock = resp.get('socket', target_socket)
        # Record the peer locally
        peers = Peers(peers_path(agent_name, state_dir))
        peers.add(remote_name, remote_sock)
        print(f"claudio: paired with {remote_name}")
        return 0
    else:
        print(f"claudio: pair failed: {resp.get('error', 'unknown error')}", file=sys.stderr)
        return 1


def cmd_peers(args: list, state_dir: Optional[str] = None, agent_name: Optional[str] = None) -> int:
    """claudio peers — list current peers."""
    state_dir = state_dir or _state_dir()
    agent_name = agent_name or _agent_name()

    if not agent_name:
        print("claudio: CLAUDIO_AGENT_NAME or CMUX_SESSION_NAME must be set", file=sys.stderr)
        return 1

    peers = Peers(peers_path(agent_name, state_dir))
    all_peers = peers.all()
    if not all_peers:
        print("(no peers)")
        return 0
    for pname, psock in all_peers.items():
        print(f"{pname}   {psock}")
    return 0


def cmd_send(args: list, state_dir: Optional[str] = None, agent_name: Optional[str] = None) -> int:
    """claudio send <name|socket> <msg> — send a message to a peer."""
    state_dir = state_dir or _state_dir()
    agent_name = agent_name or _agent_name()

    if len(args) < 2:
        print("usage: claudio send <name|socket> <message>", file=sys.stderr)
        return 1

    target, message = args[0], args[1]
    sock_path = _resolve_target(target, state_dir, agent_name)
    if sock_path is None:
        print(f"claudio: no peer named '{target}' — run 'claudio pair' first", file=sys.stderr)
        return 1
    try:
        resp = send_to(sock_path, {'body': message})
    except Exception as e:
        print(f"claudio: send failed: {e}", file=sys.stderr)
        return 1
    if resp.get('ok'):
        return 0
    else:
        print(f"claudio: send error: {resp.get('error', 'unknown error')}", file=sys.stderr)
        return 1


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("usage: claudio <command> [args...]")
        print("commands: pair, peers, send")
        sys.exit(0)

    cmd = args[0]
    rest = args[1:]

    if cmd == 'pair':
        sys.exit(cmd_pair(rest))
    elif cmd == 'peers':
        sys.exit(cmd_peers(rest))
    elif cmd == 'send':
        sys.exit(cmd_send(rest))
    else:
        print(f"claudio: unknown command '{cmd}'", file=sys.stderr)
        sys.exit(1)
