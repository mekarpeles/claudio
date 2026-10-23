"""claudio agent registry — remember each agent's messaging socket.

Claude Code sessions self-publish under ~/.claude/sessions/<pid>.json with a
*derived* name ("ada-97"). To address an agent by a stable logical name ("pam"),
claudio records, at session startup, that name against the session's:

    sessionId  — stable across resume (pid/socket change, sessionId does not)
    socket     — the messagingSocketPath to forward to (cached; refreshed each
                 register, and re-resolved live from sessionId at send time)
    pid        — the owning process at register time

This is the "save/register the socket on startup" step. sessionId is the durable
key; the socket is remembered so a bare socket forward works even before a live
scan, and so `claudio ls` can show it.

Storage is a single JSON file written with atomic os.replace. Aliases are
written rarely (once per agent start), so JSON suffices; a busier writer set
should move to SQLite (see agents' agents.db).
"""

import json
import os
import re
import tempfile
import time

STATE_DIR = os.path.expanduser(
    os.environ.get("CLAUDIO_STATE_DIR", "~/.claude/claudio")
)
ALIASES_PATH = os.path.join(STATE_DIR, "aliases.json")

_PID_FROM_SOCK = re.compile(r"/(\d+)(?:-[0-9a-f]+)?\.sock$")


def _load():
    try:
        with open(ALIASES_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".aliases-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, ALIASES_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _current_session():
    """Best-effort (sessionId, socket, pid) for the session calling us."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    pid = None
    if sock:
        m = _PID_FROM_SOCK.search(sock)
        if m:
            pid = int(m.group(1))
    return sid, sock, pid


def register(name, session_id=None, socket=None, pid=None):
    """Bind a logical name to a session (upsert). Missing fields default to the
    current session's env (CLAUDE_CODE_SESSION_ID / CLAUDE_CODE_MESSAGING_SOCKET).
    """
    cur_sid, cur_sock, cur_pid = _current_session()
    entry = {
        "sessionId": session_id or cur_sid,
        "socket": socket or cur_sock,
        "pid": pid if pid is not None else cur_pid,
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    data = _load()
    data[name] = entry
    _save(data)
    return entry


def unregister(name):
    data = _load()
    removed = data.pop(name, None)
    if removed is not None:
        _save(data)
    return removed


def get(name):
    """Return the full alias entry (sessionId/socket/pid/registered_at) or None."""
    return _load().get(name)


def session_id_for(name):
    return (_load().get(name) or {}).get("sessionId")


def socket_for(name):
    return (_load().get(name) or {}).get("socket")


def all_aliases():
    return _load()
