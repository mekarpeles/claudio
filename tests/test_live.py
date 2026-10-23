"""Live integration test — a REAL native send over the actual cc-socks socket.

Unlike test_native.py (which uses a fake socket server), this exercises Claude
Code's real receiver. It only runs inside a Claude Code session (where
CLAUDE_CODE_MESSAGING_SOCKET / CLAUDE_CODE_SESSION_ID are set); elsewhere it
skips. It sends to the current session itself — safe, disturbs no other agent —
and asserts the socket accepted the frames (no DeliveryError / clean close).

Run inside a session with:  python3 -m pytest tests/test_live.py -v
"""

import os

import pytest

from claudio import native

SESSION_ID = os.environ.get("CLAUDE_CODE_SESSION_ID")
SOCKET = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")


@pytest.mark.skipif(not (SESSION_ID and SOCKET),
                    reason="not inside a Claude Code session")
def test_live_self_send_resolves():
    rec = native.resolve(SESSION_ID)
    assert rec["messagingSocketPath"] == SOCKET
    assert native.peer_token(rec)  # a key file exists for this session


@pytest.mark.skipif(not (SESSION_ID and SOCKET),
                    reason="not inside a Claude Code session")
def test_live_self_send_delivers():
    # A real send to our own socket. Success = no exception (clean half-close).
    rec = native.send(
        SESSION_ID,
        "[claudio test_live] self-send over the real cc-socks socket.",
        sender="claudio-test-live",
    )
    assert rec["messagingSocketPath"] == SOCKET
