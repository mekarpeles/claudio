## claudio — Claude I/O

A lightweight IO wrapper that gives a Claude Code session an inbox.

claudio listens on a Unix socket for incoming messages, queues them, and delivers each one into the session via a `deliver` callback when `is_idle` returns True. It has no opinion about how messages are delivered or how idleness is detected — those are your callbacks.

## Install

```bash
pip install claudio
```

Or from source:

```bash
pip install -e /path/to/claudio
```

## Usage

```python
import claudio

def deliver(msg: dict) -> None:
    # inject msg["body"] into the session however you like
    print(msg["body"])

def is_idle() -> bool:
    # return True when the session is ready to receive
    return True

claudio.run(name="myagent", deliver=deliver, is_idle=is_idle)
```

The agent's inbox is a Unix socket at `~/.claudio/<name>.sock`. Any process can write to it:

```python
import claudio

claudio.send("myagent", {"from": "alice", "body": "hello"})
```

## How it works

- `claudio.run()` starts a socket server in a background thread and enters the delivery loop.
- Incoming messages are queued. When `is_idle()` returns True and the queue is non-empty, the next message is passed to `deliver()`.
- The socket path is `~/.claudio/<name>.sock` by default. Override with `state_dir`.

## Used by

[cmux](https://github.com/mekarpeles/cmux) — a Claude Code multiplexer that uses claudio as the message queue layer and tmux for session management.
