## claudio — native Claude Code messaging

A tiny library + CLI for addressing Claude Code sessions by name and delivering
messages between them over Claude Code's **native** inter-session messaging
socket. No daemon, no tmux, no send-keys.

> **This is claudio v2 (branch `v2`) — the native-messaging rewrite, and the
> baseline the `agents` tool installs from.** Earlier claudio ran a
> Unix-socket inbox server per agent and relied on a host (cmux) to inject
> messages into the session via tmux bracketed-paste. Claude Code now does all of
> that itself, so claudio collapses to session resolution + native delivery. See
> [DESIGN.md](DESIGN.md) for the wire protocol and what was removed.

## How it works

Every Claude Code session publishes `~/.claude/sessions/<pid>.json` with its
`messagingSocketPath` and a sibling `.key` file holding a `peerToken`. claudio
resolves a target (by sessionId / name / pid), then writes the native
newline-delimited JSON frames (`auth` then `user`) to that socket. Claude Code
queues the message and delivers it to the session when it next goes idle.

## Install

```bash
pip install -e /path/to/claudio
```

## CLI

```bash
claudio ls                      # list live Claude sessions
claudio whoami                  # the current session (from CLAUDE_CODE_SESSION_ID)
claudio register pam            # bind logical name "pam" -> current sessionId
claudio register ada --session <sessionId>
claudio resolve pam             # show the live session "pam" points at
claudio aliases                 # all logical-name -> sessionId binds
claudio send pam "hello"        # deliver natively (sender from --from / $CLAUDIO_AGENT_NAME)
claudio send <sessionId|pid|derived-name> "hi" --priority now
```

`register` is the "save socket on startup" step: an agent records its logical
name against its sessionId once at launch, and peers address it by that name
forever after (the binding survives resume because it keys on sessionId, not pid).

## Library

```python
from claudio import native, registry

native.list_sessions()                 # live session records
native.resolve("pam")                  # one record (sessionId/name/pid)
native.send("pam", "hello", sender="ada")   # deliver; raises on failure
registry.register("pam", session_id)   # alias name -> sessionId
```

## Tests

```bash
python3 -m pytest tests/test_native.py -q
```
