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
# How long a session must be *continuously* idle before a registered nudge
# fires.  15 minutes: long enough never to trigger during normal work (a
# session routinely sits idle a minute or two between turns while a human
# reads output), short enough to still land inside a working session.  Inert
# for every existing caller regardless, since the default nudge list is empty.
DEFAULT_NUDGE_AFTER = 900.0
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
    nudge_after: Optional[float] = DEFAULT_NUDGE_AFTER,
) -> str:
    """
    Start a claudio agent in background threads. Non-blocking. Returns the session name.

    name        — session handle; auto-assigned (0, 1, 2...) if omitted
    deliver     — called with a message dict when the session is ready
    is_idle     — returns True when the session can accept a message
    state_dir   — directory for the Unix socket
    nudge_after — seconds of continuous idleness before a registered nudge
                  fires; None mutes nudging without unregistering anything.
                  No effect unless prompts are registered (default: none).
    """
    from .nudges import Nudges, next_prompt, nudge_message, nudges_path, should_fire
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
    nudges = Nudges(nudges_path(name, state_dir))

    # Monotonic timestamp of when the session last became continuously idle,
    # or None whenever it is not idle.  Shared with the socket threads, so it
    # lives under the same `lock` that guards `queue` — no second lock.
    idle_since: Optional[float] = None

    def _reset_idle_clock() -> None:
        """Restart the idle clock. Caller must hold `lock`.

        Called wherever something happens that makes the session non-idle or
        gives it work: is_idle() observed False, a real message enqueued, a
        real message delivered, or a nudge fired.  A nudge is for genuinely
        prolonged idleness, so any of those has to make the threshold elapse
        again from scratch.
        """
        nonlocal idle_since
        idle_since = None

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
            _reset_idle_clock()
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
            _reset_idle_clock()
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
        nonlocal idle_since
        last_nudge: Optional[str] = None  # delivery-loop-local; needs no lock
        nudge_cache: tuple = (None, [])   # (stat key, prompts)

        def current_nudges() -> list:
            """Registered prompts, re-read only when the file actually changes.

            One stat() per tick, and a JSON parse only when another process
            (`claudio nudge add`) has touched the file — so registrations are
            picked up live without a restart, and without re-reading on every
            tick forever.
            """
            nonlocal nudge_cache
            try:
                st = os.stat(nudges.path)
            except OSError:
                return []
            key = (st.st_mtime_ns, st.st_size)
            if nudge_cache[0] != key:
                nudge_cache = (key, nudges.load())
            return nudge_cache[1]

        while True:
            time.sleep(POLL_INTERVAL)

            with lock:
                queued = bool(queue)

            # Cheap per-tick gate: a stat(), and no is_idle() call. With no
            # nudges registered — the default — the loop must behave exactly as
            # it did before nudges existed, and that includes *not* polling
            # is_idle() on empty-queue ticks: cmux's is_idle() shells out to
            # tmux twice per call, so polling it 2x/sec for every idle agent
            # would be a real regression.
            nudging = nudge_after is not None and bool(current_nudges())
            if not queued and not nudging:
                continue

            idle = is_idle()
            now = time.monotonic()  # monotonic: a duration threshold must not
                                    # be perturbed by wall-clock/NTP steps
            with lock:
                if not idle:
                    _reset_idle_clock()
                elif idle_since is None:
                    idle_since = now
                due = should_fire(idle_since, now, nudge_after)

            if not idle:
                continue

            # Real messages keep strict priority; nudges are only considered
            # once the real inbox is empty.
            if queued:
                with lock:
                    try:
                        msg = queue.popleft()
                    except IndexError:
                        continue
                    _reset_idle_clock()
                deliver(msg)
                time.sleep(1.0)
                continue

            if not due:
                continue

            prompt = next_prompt(current_nudges(), last_nudge)
            if prompt is None:
                continue
            last_nudge = prompt
            with lock:
                _reset_idle_clock()
            deliver(nudge_message(prompt))
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
    nudge_after: Optional[float] = DEFAULT_NUDGE_AFTER,
) -> None:
    """
    Start a claudio agent and block until interrupted.

    name        — session handle; auto-assigned (0, 1, 2...) if omitted
    deliver     — called with a message dict when the session is ready
    is_idle     — returns True when the session can accept a message
    state_dir   — directory for the Unix socket
    nudge_after — seconds of continuous idleness before a registered nudge
                  fires; None mutes nudging without unregistering anything.
                  No effect unless prompts are registered (default: none).
    """
    start(name=name, deliver=deliver, is_idle=is_idle, state_dir=state_dir,
          nudge_after=nudge_after)
    # Block the calling thread forever (daemon threads keep running)
    while True:
        time.sleep(3600)
