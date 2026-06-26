## claudio — Claude I/O

A lightweight IO wrapper that gives a Claude Code session an inbox.

claudio listens on a Unix socket for incoming messages, queues them, and delivers each one into the session via a `deliver` callback when `is_idle` returns True. It has no opinion about how messages are delivered or how idleness is detected — those are your callbacks.

Sessions are **ephemeral by design**: there is no persistent registry. When a session stops, its socket is gone. If you need persistent named agents with stop/restart/resume, use [cmux](https://github.com/mekarpeles/cmux) which builds on top of claudio.

## Install

```bash
pip install claudio
```

Or from source:

```bash
pip install -e /path/to/claudio
```

## CLI

Start a session (name is optional; auto-assigns 0, 1, 2... like tmux if omitted):

```bash
claudio alice
claudio          # auto-named
```

List all running sessions:

```bash
claudio ls
```

Pair two agents so they can address each other by name:

```bash
# alice initiates — blocks until bob approves
CLAUDIO_AGENT_NAME=alice claudio pair /tmp/claudio/bob.sock

# bob approves (in another terminal)
CLAUDIO_AGENT_NAME=bob claudio pair --approve alice
```

Send a message:

```bash
CLAUDIO_AGENT_NAME=alice claudio send bob "hello"
CLAUDIO_AGENT_NAME=alice claudio send /tmp/claudio/bob.sock "hello"
```

List peers for the current agent:

```bash
CLAUDIO_AGENT_NAME=alice claudio peers
```

Sockets live at `/tmp/claudio/<name>.sock` by default. Override with `CLAUDIO_STATE_DIR`.

## Python API

```python
import claudio

def deliver(msg: dict) -> None:
    # inject msg["body"] into the session however you like
    print(msg["body"])

def is_idle() -> bool:
    # return True when the session is ready to receive
    return True

claudio.run(name="alice", deliver=deliver, is_idle=is_idle)
```

`run()` blocks forever. Use `start()` for non-blocking:

```python
name = claudio.start(name="alice", deliver=deliver, is_idle=is_idle)
# name is "alice" (or auto-assigned if omitted)
# daemon threads are running; calling thread continues
```

Send from any process:

```python
claudio.send("alice", {"from": "bob", "body": "hello"})
claudio.send_to("/tmp/claudio/alice.sock", {"body": "direct"})
```

## How it works

- `claudio.run()` / `claudio.start()` binds a Unix socket at `{state_dir}/{name}.sock` (default `/tmp/claudio/`) and starts three daemon threads: a socket server, a delivery loop, and a pair-request cleanup timer.
- Incoming messages are queued. When `is_idle()` returns True and the queue is non-empty, the next message is passed to `deliver()`.
- Pairing lets agents address each other by name. A `pair_request` holds the connection open until the remote agent calls `pair_approve`; both sides then record each other in a local peers file.

## Used by

[cmux](https://github.com/mekarpeles/cmux) — a Claude Code multiplexer that uses claudio as the message queue layer and tmux for session management.
