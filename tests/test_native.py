"""Tests for claudio.native — the native Claude Code messaging wire protocol."""

import json
import os

import pytest

from claudio import native, registry
from tests.conftest import FakeSession, write_session, wait_for


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setattr(native, "SESSIONS_DIR", str(d))
    return str(d)


@pytest.fixture
def socks_dir(tmp_path):
    d = tmp_path / "cc-socks"
    d.mkdir()
    return str(d)


def test_wrap_content_envelope():
    out = native.wrap_content("hello", sender="pam", from_session="abc")
    assert out.startswith('<cross-session-message ')
    assert 'from="pam"' in out and 'from-name="pam"' in out
    assert 'from-session="abc"' in out
    assert out.rstrip().endswith("</cross-session-message>")
    assert "hello" in out


def test_wrap_content_escapes_quotes():
    out = native.wrap_content("x", sender='a"b')
    assert 'a"b' not in out and "&quot;" in out


def test_build_frames_auth_then_user():
    blob = native.build_frames("hi", sender="pam", token="TOK").decode()
    first, second = blob.strip().split("\n")
    assert json.loads(first) == {"type": "auth", "token": "TOK"}
    msg = json.loads(second)
    assert msg["msgV"] == 1 and msg["type"] == "user"
    assert msg["message"]["role"] == "user"
    assert msg["priority"] == "next" and msg["from"] == "pam"
    # msg_id is a uuid4
    assert len(msg["msg_id"].split("-")) == 5


def test_build_frames_no_token_omits_auth():
    blob = native.build_frames("hi", token=None).decode()
    assert "auth" not in blob
    assert len(blob.strip().split("\n")) == 1


def test_build_frames_too_large_raises():
    with pytest.raises(native.DeliveryError):
        native.build_frames("x" * (native.MAX_LINE + 10), token=None)


def test_resolve_by_session_id_name_pid(sessions_dir):
    write_session(sessions_dir, os.getpid(), "/tmp/x.sock", "sid-1", "ada-97")
    assert native.resolve("sid-1")["name"] == "ada-97"
    assert native.resolve("ada-97")["sessionId"] == "sid-1"
    assert native.resolve(os.getpid())["sessionId"] == "sid-1"


def test_resolve_dead_pid_excluded(sessions_dir):
    # pid 999999 almost certainly not alive
    write_session(sessions_dir, 999999, "/tmp/x.sock", "sid-dead", "ghost")
    with pytest.raises(native.SessionNotFound):
        native.resolve("ghost")


def test_resolve_prefers_session_id_over_name(sessions_dir):
    # Two live records; selector equals one's sessionId and the other's name.
    write_session(sessions_dir, os.getpid(), "/tmp/a.sock", "collide", "other")
    write_session(sessions_dir, os.getppid(), "/tmp/b.sock", "sid-b", "collide")
    # exact sessionId match (rank 0) beats name match (rank 1)
    assert native.resolve("collide")["sessionId"] == "collide"


def test_peer_token_read(sessions_dir):
    write_session(sessions_dir, os.getpid(), "/tmp/x.sock", "sid", "n",
                  peer_token="cafe" * 8)
    rec = native.resolve("sid")
    assert native.peer_token(rec) == "cafe" * 8


def test_send_delivers_auth_and_user_frames(sessions_dir, socks_dir):
    sock_path = os.path.join(socks_dir, f"{os.getpid()}.sock")
    srv = FakeSession(sock_path)
    try:
        write_session(sessions_dir, os.getpid(), sock_path, "sid-send", "target",
                      peer_token="feed" * 8)
        rec = native.send("sid-send", "payload-text", sender="pam")
        assert rec["name"] == "target"
        assert wait_for(lambda: len(srv.lines) >= 2)
        auth, msg = srv.lines[0], srv.lines[1]
        assert auth == {"type": "auth", "token": "feed" * 8}
        assert msg["from"] == "pam"
        assert "payload-text" in msg["message"]["content"]
    finally:
        srv.close()


def test_send_unknown_target_raises(sessions_dir):
    with pytest.raises(native.SessionNotFound):
        native.send("nope", "x")


# --- registry ------------------------------------------------------------- #

@pytest.fixture
def alias_dir(tmp_path, monkeypatch):
    d = tmp_path / "claudio"
    monkeypatch.setattr(registry, "STATE_DIR", str(d))
    monkeypatch.setattr(registry, "ALIASES_PATH", str(d / "aliases.json"))
    # Don't let the real session's env leak into registration during tests.
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
    return str(d)


def test_registry_remembers_socket(alias_dir):
    registry.register("pam", session_id="sid-123",
                      socket="/tmp/cc-socks/5.sock", pid=5)
    assert registry.session_id_for("pam") == "sid-123"
    assert registry.socket_for("pam") == "/tmp/cc-socks/5.sock"
    assert registry.get("pam")["pid"] == 5
    registry.unregister("pam")
    assert registry.get("pam") is None


def test_registry_defaults_to_current_session_env(alias_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-sid")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/tmp/cc-socks/42.sock")
    info = registry.register("ada")
    assert info["sessionId"] == "env-sid"
    assert info["socket"] == "/tmp/cc-socks/42.sock"
    assert info["pid"] == 42  # parsed from the socket filename


def test_registry_upsert(alias_dir):
    registry.register("pam", session_id="sid-1")
    registry.register("pam", session_id="sid-2")
    assert registry.session_id_for("pam") == "sid-2"


def test_token_for_socket(sessions_dir):
    write_session(sessions_dir, os.getpid(), "/tmp/cc-socks/x.sock", "sid", "n",
                  peer_token="beef" * 8)
    assert native.token_for_socket("/tmp/cc-socks/x.sock") == "beef" * 8


def test_send_to_socket_direct(sessions_dir, socks_dir):
    sock_path = os.path.join(socks_dir, f"{os.getpid()}.sock")
    srv = FakeSession(sock_path)
    try:
        write_session(sessions_dir, os.getpid(), sock_path, "sid-d", "target",
                      peer_token="feed" * 8)
        native.send_to_socket(sock_path, "direct-payload", sender="pam")
        assert wait_for(lambda: len(srv.lines) >= 2)
        assert srv.lines[0] == {"type": "auth", "token": "feed" * 8}
        assert "direct-payload" in srv.lines[1]["message"]["content"]
    finally:
        srv.close()
