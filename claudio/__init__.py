"""claudio — native Claude Code inter-session messaging.

Resolve Claude Code sessions by name/id and deliver messages over each session's
own messaging socket (~/.claude/sessions/<pid>.json -> messagingSocketPath),
using the native NDJSON wire protocol. No daemon, no tmux, no send-keys.
"""

from .native import (
    DeliveryError,
    SessionNotFound,
    build_frames,
    list_sessions,
    peer_token,
    resolve,
    send,
    send_to_socket,
    token_for_socket,
    wrap_content,
)
from .registry import (
    all_aliases,
    get,
    register,
    session_id_for,
    socket_for,
    unregister,
)
# The nudges primitive — a per-agent list of self-directed prompts — is retained
# from v1 as a pure data model. Its old firing mechanism (the delivery loop) is
# gone in v2; firing self-prompts on idle needs a native home (see DESIGN.md).
from .nudges import (
    NUDGE_PREFIX,
    NUDGE_SENDER,
    Nudges,
    next_prompt,
    nudge_message,
    nudges_path,
    should_fire,
)

__all__ = [
    "send", "send_to_socket", "token_for_socket", "resolve", "list_sessions",
    "peer_token", "build_frames", "wrap_content", "SessionNotFound",
    "DeliveryError",
    "register", "unregister", "get", "session_id_for", "socket_for",
    "all_aliases",
    "Nudges", "nudges_path", "nudge_message", "next_prompt", "should_fire",
    "NUDGE_PREFIX", "NUDGE_SENDER",
]
