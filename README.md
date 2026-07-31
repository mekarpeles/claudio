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

## Nudges

A **nudge** is a prompt an agent registers for itself — the equivalent of *eat*, *drink*, *sleep*,
*remember to turn off the stove*. When a session has been continuously idle past a threshold **and**
its real inbox is empty, one registered prompt is synthesized into a message and delivered through
the same `deliver()` callback a real message uses. From the session's point of view a nudge just
shows up like any other injected message, except it came from its own list rather than from a peer.

claudio has no opinion about what the prompts are. **The default list is empty**, so nothing fires
until you register something.

```bash
CLAUDIO_AGENT_NAME=alice claudio nudge add "Have you written tests for what you just changed?"
CLAUDIO_AGENT_NAME=alice claudio nudge list
# 0   Have you written tests for what you just changed?

CLAUDIO_AGENT_NAME=alice claudio nudge remove 0                 # by index
CLAUDIO_AGENT_NAME=alice claudio nudge remove "Have you ..."    # or by exact text
```

`add` is idempotent. `remove` matches exact text first and falls back to a 0-based index, so a
prompt whose literal text is `3` stays removable by text. Prompts live in
`{state_dir}/{name}.nudges.json` and are re-read from disk, so registering one takes effect on a
running agent without a restart.

From Python, set the threshold with `nudge_after` (seconds, default 900 — 15 minutes).
`nudge_after=None` mutes nudging without unregistering anything:

```python
import claudio
from claudio import Nudges, nudges_path

Nudges(nudges_path("alice", "/tmp/claudio")).add("Check the stove.")

claudio.run(name="alice", deliver=deliver, is_idle=is_idle, nudge_after=600)
```

A delivered nudge is marked three ways, so it is distinguishable from a real peer message:

```python
{"_claudio": "nudge", "from": "noreply", "body": "[claudio nudge]: Check the stove."}
```

Registered prompts rotate round-robin, and the idle clock resets whenever the session goes busy,
real traffic arrives, or a nudge fires — so the full threshold has to elapse again before the next
one. Real messages always take priority.

> Nudging is only as good as your `is_idle`. The bare `claudio <name>` CLI passes
> `is_idle=lambda: True`, so it treats the session as idle even mid-task; the threshold then just
> measures time since the last delivered message. Callers that can detect real idleness — such as
> [cmux](https://github.com/mekarpeles/cmux) — get the intended behavior.

## How it works

- `claudio.run()` / `claudio.start()` binds a Unix socket at `{state_dir}/{name}.sock` (default `/tmp/claudio/`) and starts three daemon threads: a socket server, a delivery loop, and a pair-request cleanup timer.
- Incoming messages are queued. When `is_idle()` returns True and the queue is non-empty, the next message is passed to `deliver()`.
- Pairing lets agents address each other by name. A `pair_request` holds the connection open until the remote agent calls `pair_approve`; both sides then record each other in a local peers file.
- The delivery loop also tracks how long the session has been *continuously* idle. Once the real queue is empty and that duration passes `nudge_after`, a registered nudge prompt is delivered through the same `deliver()` path. No nudges registered (the default) means this is inert.

## Used by

[cmux](https://github.com/mekarpeles/cmux) — a Claude Code multiplexer that uses claudio as the message queue layer and tmux for session management.
