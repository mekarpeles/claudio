"""
claudio CLI — peer management and message sending commands.

Commands:
    claudio [<name>]                   Start a daemon (name optional, auto-assigned if omitted)
    claudio discover                   List all running claudio agents
    claudio pair <socket>              Initiate pairing (blocks until approved, 5min timeout)
    claudio pair --approve <name>      Approve a pending pair request from <name>
    claudio peers                      List current peers
    claudio send <name|socket> <msg>   Send a message to a peer
"""

import os
import sys
import time
from typing import Optional

from .agent import DEFAULT_STATE_DIR, _RECV_BUF, _next_session_name, send_to, socket_path, start as _start
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


def cmd_start(args: list, state_dir: Optional[str] = None, agent_name: Optional[str] = None) -> int:
    """claudio start [<name>] — start a daemon in the foreground."""
    state_dir = state_dir or _state_dir()
    agent_name = agent_name or (args[0] if args else None) or _agent_name()
    if agent_name is None:
        agent_name = _next_session_name(state_dir)

    delivered = []

    def deliver(msg):
        sender = msg.get('from', 'claudio')
        body = msg.get('body', repr(msg))
        print(f"[{sender}]: {body}")
        delivered.append(msg)

    name = _start(
        name=agent_name,
        deliver=deliver,
        is_idle=lambda: True,
        state_dir=state_dir,
    )

    sock = socket_path(name, state_dir)
    print(f"claudio: session '{name}' listening at {sock}")
    print(f"claudio: press Ctrl-C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\nclaudio: stopped '{name}'")
        try:
            os.unlink(sock)
        except FileNotFoundError:
            pass
        return 0


def cmd_discover(args: list, state_dir: Optional[str] = None) -> int:
    """claudio discover — list all running claudio agents."""
    import socket as _socket

    state_dir = state_dir or _state_dir()
    try:
        entries = os.listdir(state_dir)
    except FileNotFoundError:
        print("(no agents running)")
        return 0

    socks = sorted(f for f in entries if f.endswith('.sock'))
    if not socks:
        print("(no agents running)")
        return 0

    live = []
    for fname in socks:
        name = fname[:-5]
        path = os.path.join(state_dir, fname)
        alive = False
        try:
            c = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            c.settimeout(0.5)
            c.connect(path)
            c.close()
            alive = True
        except Exception:
            pass
        if alive:
            live.append((name, path))

    if not live:
        print("(no agents running)")
        return 0

    name_w = max(len(n) for n, _ in live)
    for name, path in live:
        print(f"{name:<{name_w}}   {path}")
    return 0


def cmd_pair_approve(args: list, state_dir: Optional[str] = None, agent_name: Optional[str] = None) -> int:
    """
    claudio pair --approve <name>  — approve a pending pair request from <name>.

    Sends a pair_approve message to the caller's own running daemon and returns
    immediately.  Does NOT block.
    """
    state_dir = state_dir or _state_dir()
    agent_name = agent_name or _agent_name()

    # args may be ['--approve', '<name>'] or just ['<name>'] depending on caller;
    # normalise: strip a leading '--approve' flag if present.
    if args and args[0] == '--approve':
        args = args[1:]

    if not args:
        print("usage: claudio pair --approve <name>", file=sys.stderr)
        return 1
    remote_name = args[0]

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


def cmd_pair_initiate(args: list, state_dir: Optional[str] = None, agent_name: Optional[str] = None) -> int:
    """
    claudio pair <socket>  — initiate pairing with a remote agent.

    Opens a direct socket connection to <socket>, sends a pair_request, then
    blocks for up to 5 minutes waiting for the remote agent to approve.  On
    success, records the remote agent in the local peers file.
    """
    import json
    import socket as _socket

    state_dir = state_dir or _state_dir()
    agent_name = agent_name or _agent_name()

    if not args:
        print("usage: claudio pair <socket>", file=sys.stderr)
        return 1

    target_socket = os.path.expanduser(args[0])

    if not agent_name:
        print("claudio: CLAUDIO_AGENT_NAME or CMUX_SESSION_NAME must be set", file=sys.stderr)
        return 1

    own_sock = socket_path(agent_name, state_dir)

    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        s.connect(target_socket)
    except Exception as e:
        print(f"claudio: cannot connect to {target_socket}: {e}", file=sys.stderr)
        return 1

    msg = {'_claudio': 'pair_request', 'name': agent_name, 'socket': own_sock}
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
        peers = Peers(peers_path(agent_name, state_dir))
        peers.add(remote_name, remote_sock)
        print(f"claudio: paired with {remote_name}")
        return 0
    else:
        print(f"claudio: pair failed: {resp.get('error', 'unknown error')}", file=sys.stderr)
        return 1


def cmd_pair(args: list, state_dir: Optional[str] = None, agent_name: Optional[str] = None) -> int:
    """
    claudio pair <socket>              — initiate pairing
    claudio pair --approve <name>      — approve a pending pair request

    Thin dispatcher: routes to cmd_pair_approve or cmd_pair_initiate.
    """
    if args and args[0] == '--approve':
        return cmd_pair_approve(args, state_dir=state_dir, agent_name=agent_name)
    return cmd_pair_initiate(args, state_dir=state_dir, agent_name=agent_name)


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


USAGE = """\
claudio — peer-to-peer messaging for Claude Code agents

Usage:
  claudio [<name>]                   Start a daemon (auto-names 0, 1, 2... if omitted)
  claudio discover                   List all running claudio agents
  claudio pair <socket>              Pair with the agent at <socket> (blocks until approved)
  claudio pair --approve <name>      Approve a pending pair request from <name>
  claudio peers                      List current peers
  claudio send <name|socket> <msg>   Send a message to a peer

Environment:
  CLAUDIO_AGENT_NAME   Override this agent's name
  CLAUDIO_STATE_DIR    State directory (default: /tmp/claudio)

Quick start (two terminals):
  term1$ claudio alice
  term2$ claudio bob
  term3$ CLAUDIO_AGENT_NAME=alice claudio pair /tmp/claudio/bob.sock
  term4$ CLAUDIO_AGENT_NAME=bob   claudio pair --approve alice
  term3$ CLAUDIO_AGENT_NAME=alice claudio send bob "hello"
  term5$ claudio discover
"""


_SUBCOMMANDS = {'discover', 'pair', 'peers', 'send'}


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(USAGE)
        sys.exit(0)

    cmd = args[0]
    rest = args[1:]

    # 'start' kept as a silent alias
    if cmd in ('start',) or cmd not in _SUBCOMMANDS:
        # treat cmd as optional name: `claudio [name]` or `claudio start [name]`
        name_args = rest if cmd == 'start' else args
        sys.exit(cmd_start(name_args))
    elif cmd == 'discover':
        sys.exit(cmd_discover(rest))
    elif cmd == 'pair':
        sys.exit(cmd_pair(rest))
    elif cmd == 'peers':
        sys.exit(cmd_peers(rest))
    elif cmd == 'send':
        sys.exit(cmd_send(rest))
