"""Native Claude Code inter-session messaging.

Every Claude Code session (v2.1.x, peerProtocol 1) publishes its own messaging
socket. This module resolves sessions and delivers messages over that socket
using Claude Code's native wire protocol — no tmux, no daemon, no send-keys.

Discovery:
    ~/.claude/sessions/<pid>.json   -> record with:
        messagingSocketPath  (e.g. /tmp/cc-socks/<pid>.sock)
        name, formerNames, sessionId, cwd, status, pid, peerProtocol, peerFeatures
    ~/.claude/sessions/<pid>.<sha256(sockpath)>.key -> {"peerToken": "<32 hex>"}

Wire protocol (reverse-engineered from the 2.1.309 bundle):
    - UNIX stream socket, UTF-8, newline-delimited JSON (NDJSON), one object per
      line, write-then-half-close.
    - Line 1 (auth, must be first):  {"type":"auth","token":"<peerToken>"}
    - Line 2 (delivery):
        {"msgV":1,"msg_id":"<uuid4>","type":"user",
         "message":{"role":"user","content":"<text>"},
         "priority":"next","from":"<sender>"}
    - No ack payload. A clean half-close means accepted; ECONNRESET/timeout means
      rejected (bad auth / malformed / peer-credential failure).

This replaces claudio's entire old delivery stack (socket server + tmux
bracketed-paste + Enter-retry submit loop + cursor idle detection): Claude Code
now owns the inbox, queueing, and idle-gated delivery itself.
"""

import glob
import json
import os
import re
import socket
import uuid

SESSIONS_DIR = os.path.expanduser(
    os.environ.get("CLAUDE_SESSIONS_DIR", "~/.claude/sessions")
)

PEER_PROTOCOL = 1
SEND_TIMEOUT = 5.0           # client send timeout (binary uses 5000 ms)
MAX_LINE = 1024 * 1024       # server drops a line over 1 MiB (Ucn)
XML_TAG = "cross-session-message"
_SESSION_FILE_RE = re.compile(r"^\d+\.json$")


class SessionNotFound(Exception):
    """No live session matched the selector."""


class DeliveryError(Exception):
    """The message could not be delivered (socket refused / reset / timed out)."""


# --------------------------------------------------------------------------- #
# Discovery / resolution
# --------------------------------------------------------------------------- #

def list_sessions(include_dead=False):
    """Return every parseable session record under SESSIONS_DIR.

    Each record is the raw dict from <pid>.json plus a synthetic 'alive' bool.
    """
    out = []
    for fp in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
        if not _SESSION_FILE_RE.match(os.path.basename(fp)):
            continue
        try:
            with open(fp) as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        rec["alive"] = _proc_alive(rec.get("pid"))
        if include_dead or rec["alive"]:
            out.append(rec)
    return out


def _proc_alive(pid):
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else — still alive
    return True


def resolve(selector):
    """Resolve a selector to a single live session record.

    selector matches, in priority order: sessionId, current name, a formerName,
    or pid (int or str). Raises SessionNotFound if nothing live matches. When
    several match (e.g. a reused derived name), prefer the most recently updated.
    """
    sel = str(selector)
    by_rank = []  # (rank, -updatedAt, rec); lower rank = better
    for rec in list_sessions():
        rank = None
        if sel == str(rec.get("sessionId")):
            rank = 0
        elif sel == str(rec.get("name")):
            rank = 1
        elif sel in [str(n) for n in (rec.get("formerNames") or [])]:
            rank = 2
        elif sel == str(rec.get("pid")):
            rank = 3
        if rank is not None:
            by_rank.append((rank, -int(rec.get("updatedAt") or 0), rec))
    if not by_rank:
        raise SessionNotFound(
            f"no live Claude session matches {selector!r} "
            f"(looked in {SESSIONS_DIR})"
        )
    by_rank.sort(key=lambda t: (t[0], t[1]))
    return by_rank[0][2]


def peer_token(rec):
    """Read the peerToken for a session record, or None if no key file is found.

    The key file is named <pid>.<sha256(canonical sockpath)>.key; rather than
    recompute the hash (and risk a /tmp vs /private/tmp canonicalization
    mismatch), glob by pid — there is normally exactly one.
    """
    pid = rec.get("pid")
    for kp in glob.glob(os.path.join(SESSIONS_DIR, f"{pid}.*.key")):
        try:
            with open(kp) as f:
                tok = json.load(f).get("peerToken")
            if tok:
                return tok
        except (OSError, json.JSONDecodeError):
            continue
    return None


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #

def _esc_attr(s):
    return (str(s).replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def wrap_content(text, sender, from_session=None, hop_chain=None):
    """Wrap raw text in the native cross-session-message envelope (attribution)."""
    attrs = [f'from="{_esc_attr(sender)}"', f'from-name="{_esc_attr(sender)}"']
    if from_session:
        attrs.append(f'from-session="{_esc_attr(from_session)}"')
    if hop_chain:
        attrs.append(f'hop-chain="{_esc_attr(hop_chain)}"')
    return f'<{XML_TAG} {" ".join(attrs)}>\n{text}\n</{XML_TAG}>'


def build_frames(text, sender="claudio", priority="next", token=None,
                 from_session=None, wrap=True):
    """Build the exact bytes to write to a session's messaging socket."""
    content = wrap_content(text, sender, from_session) if wrap else text
    msg = {
        "msgV": 1,
        "msg_id": str(uuid.uuid4()),
        "type": "user",
        "message": {"role": "user", "content": content},
        "priority": priority,
        "from": sender,
    }
    blob = b""
    if token:
        blob += (json.dumps({"type": "auth", "token": token}) + "\n").encode()
    line = json.dumps(msg) + "\n"
    if len(line.encode()) > MAX_LINE:
        raise DeliveryError(f"message too large ({len(line)} bytes > {MAX_LINE})")
    blob += line.encode()
    return blob


def token_for_socket(sock_path):
    """Find the peerToken for a socket path by matching a session record."""
    for rec in list_sessions(include_dead=True):
        if rec.get("messagingSocketPath") == sock_path:
            tok = peer_token(rec)
            if tok:
                return tok
    # Fall back to the pid embedded in the socket filename.
    m = re.search(r"/(\d+)(?:-[0-9a-f]+)?\.sock$", sock_path)
    if m:
        for kp in glob.glob(os.path.join(SESSIONS_DIR, f"{m.group(1)}.*.key")):
            try:
                with open(kp) as f:
                    tok = json.load(f).get("peerToken")
                if tok:
                    return tok
            except (OSError, json.JSONDecodeError):
                continue
    return None


def send(selector, text, sender="claudio", priority="next",
         from_session=None, wrap=True):
    """Deliver text to a live session. Returns the resolved record on success.

    Raises SessionNotFound / DeliveryError. Delivery is fire-and-forget: Claude
    Code queues the message and surfaces it to the session when it next goes idle.
    """
    rec = resolve(selector)
    sock_path = rec.get("messagingSocketPath")
    if not sock_path:
        raise DeliveryError(f"session {selector!r} has no messagingSocketPath")
    send_to_socket(
        sock_path, text, sender=sender, priority=priority,
        token=peer_token(rec), from_session=from_session, wrap=wrap,
    )
    return rec


def send_to_socket(sock_path, text, sender="claudio", priority="next",
                   token=None, from_session=None, wrap=True):
    """Forward a message directly to a messaging socket path (the thin utility).

    If token is None, it is looked up from the session record owning sock_path.
    Raises DeliveryError on refusal/timeout.
    """
    if token is None:
        token = token_for_socket(sock_path)
    blob = build_frames(
        text, sender=sender, priority=priority,
        token=token, from_session=from_session, wrap=wrap,
    )
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(SEND_TIMEOUT)
    try:
        s.connect(sock_path)
        s.sendall(blob)
        s.shutdown(socket.SHUT_WR)
        # No ack payload. recv() returns b'' on the server's clean close
        # (accepted); a reset raises ConnectionResetError (rejected).
        try:
            s.recv(64)
        except socket.timeout:
            pass
    except (ConnectionResetError, BrokenPipeError) as e:
        raise DeliveryError(
            f"{sock_path} rejected the message (auth/peer check failed?): {e}"
        )
    except (OSError, socket.timeout) as e:
        raise DeliveryError(f"could not deliver to {sock_path}: {e}")
    finally:
        s.close()
