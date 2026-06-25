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
        or os.path.join(os.path.abspath(os.sep), 'tmp', 'claudio')
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


def cmd_start(args: list, state_dir: Optional[str] = None, agent_name: Optional[str] = None) -> int:
    """claudio start [<name>] — start a daemon in the foreground, print incoming messages."""
    import claudio as _claudio

    state_dir = state_dir or _state_dir()
    agent_name = agent_name or (args[0] if args else None) or _agent_name()

    if not agent_name:
        print("usage: claudio start <name>  (or set CLAUDIO_AGENT_NAME)", file=sys.stderr)
        return 1

    sock = socket_path(agent_name, state_dir)
    print(f"claudio: agent '{agent_name}' listening at {sock}")
    print(f"claudio: press Ctrl-C to stop\n")

    def deliver(msg):
        sender = msg.get('from', 'claudio')
        body = msg.get('body', repr(msg))
        print(f"[{sender}]: {body}")

    try:
        _claudio.run(
            name=agent_name,
            deliver=deliver,
            is_idle=lambda: True,
            state_dir=state_dir,
        )
    except KeyboardInterrupt:
        print(f"\nclaudio: stopped '{agent_name}'")
        try:
            os.unlink(sock)
        except FileNotFoundError:
            pass
        return 0


USAGE = """\
claudio — peer-to-peer messaging for Claude Code agents

Usage:
  claudio start <name>               Start a daemon in the foreground (Ctrl-C to stop)
  claudio pair <socket>              Pair with the agent at <socket> (blocks until approved)
  claudio pair --approve <name>      Approve a pending pair request from <name>
  claudio peers                      List current peers
  claudio send <name|socket> <msg>   Send a message to a peer

Environment:
  CLAUDIO_AGENT_NAME   This agent's name (falls back to CMUX_SESSION_NAME)
  CLAUDIO_STATE_DIR    State directory  (falls back to CMUX_STATE_DIR, then ~/.claudio)

Quick test (two terminals):
  term1$ CLAUDIO_STATE_DIR=/tmp/cl CLAUDIO_AGENT_NAME=alice claudio start alice
  term2$ CLAUDIO_STATE_DIR=/tmp/cl CLAUDIO_AGENT_NAME=bob   claudio start bob
  term1$ claudio pair /tmp/cl/bob.sock          # paste in term1 while start is running? No —
  # open a third terminal for client commands:
  term3$ CLAUDIO_STATE_DIR=/tmp/cl CLAUDIO_AGENT_NAME=alice claudio pair /tmp/cl/bob.sock
  term4$ CLAUDIO_STATE_DIR=/tmp/cl CLAUDIO_AGENT_NAME=bob   claudio pair --approve alice
  term3$ CLAUDIO_STATE_DIR=/tmp/cl CLAUDIO_AGENT_NAME=alice claudio send bob "hello"
"""


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(USAGE)
        sys.exit(0)

    cmd = args[0]
    rest = args[1:]

    if cmd == 'start':
        sys.exit(cmd_start(rest))
    elif cmd == 'pair':
        sys.exit(cmd_pair(rest))
    elif cmd == 'peers':
        sys.exit(cmd_peers(rest))
    elif cmd == 'send':
        sys.exit(cmd_send(rest))
    else:
        print(f"claudio: unknown command '{cmd}'", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        sys.exit(1)
