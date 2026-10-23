# claudio design — native Claude Code messaging

**Status:** claudio v2 (branch `v2`), 2026-10-23. The native-messaging baseline
the `agents` toolchain installs from (its effective `main`).

## What changed and why

claudio used to be an **inbox primitive**: it ran a Unix-socket server in each
agent, queued messages, and delivered them into the Claude session via a
`deliver` callback. cmux supplied that callback and it was a *hack* — tmux
bracketed-paste into the pane, an Enter-retry "submit" loop to defeat
paste-capture, and idle detection by reading the tmux cursor position. It worked
but was fragile (the "stuck message" bug) and forced tmux on everyone.

Claude Code now ships **native inter-session messaging**. Every session publishes
its own messaging socket and queues/delivers messages itself (idle-gated, via its
`notify_idle` feature). So claudio no longer runs a server, a daemon, or a
delivery loop. It is now just two things:

1. **`native.py`** — resolve a session and write the native wire protocol to its
   socket.
2. **`registry.py`** — a thin `logical-name -> sessionId` alias map ("save socket
   on startup").

`agent.py` (socket server), `peers.py` (pairing), and the whole tmux/send-keys
delivery stack are **deleted**.

**Nudges** (a per-agent list of self-directed prompts, added to v1 in #11) is
*kept*: `nudges.py` is a pure JSON list with no dependency on the deleted stack,
and the `claudio nudge` CLI is retained. What's gone is its old *firing* path (the
delivery loop that injected a prompt after an idle threshold) — v2 has no such
loop, so auto-firing self-prompts on idle needs a native home (e.g. a small
scheduler that calls `native.send`). Tracked as follow-up; the list primitive is
preserved so nothing has to be re-authored.

## The native transport (reverse-engineered from the 2.1.309 bundle)

### Discovery
Each live session writes `~/.claude/sessions/<pid>.json`:

```json
{"pid":96769,"sessionId":"dc33a929-…","cwd":"/Users/mek/Projects",
 "messagingSocketPath":"/tmp/cc-socks/96769.sock","name":"projects-70",
 "status":"busy","peerProtocol":1,
 "peerFeatures":["notify_idle","reply_across_default_dirs","artifact_yield"]}
```

Next to it, `~/.claude/sessions/<pid>.<sha256(sockpath)>.key` holds
`{"peerToken":"<32 hex>"}` — the socket auth. (We glob `<pid>.*.key` rather than
recompute the hash, to dodge `/tmp` vs `/private/tmp` canonicalization.)

A child process of a session also inherits these directly as env vars:
`CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_MESSAGING_SOCKET`,
`CLAUDE_CODE_MESSAGING_TOKEN` — which is how `whoami` / "save on startup" work
with no scanning.

### Wire protocol
UNIX stream socket, UTF-8, **newline-delimited JSON**, write-then-half-close.
The first line must be the auth frame:

```
{"type":"auth","token":"<peerToken>"}\n
{"msgV":1,"msg_id":"<uuid4>","type":"user","message":{"role":"user","content":"<text>"},"priority":"next","from":"<sender>"}\n
```

`content` is wrapped for attribution:
`<cross-session-message from="…" from-name="…" from-session="…">\n<text>\n</cross-session-message>`.

**There is no ack payload.** A clean half-close = accepted; `ECONNRESET`/timeout =
rejected (bad auth, malformed, or peer-credential failure). `priority` is
`"next"` (default) or `"now"`. Max line 1 MiB; client timeout 5 s.

### Auth
`peerToken` is compared constant-time against the `.key` file's value. On
macOS/Linux `authRequired` defaults false and the enforced gate is an
SO_PEERCRED-style same-user peer check (which a same-user tool passes); we still
send the token for portability (Windows requires it) and to be classified as a
real peer.

### Resolution rules
Scan `~/.claude/sessions/*.json`, match a selector against `sessionId` (best),
`name`, `formerNames`, then `pid`, keeping only live pids. Binding by
**sessionId** is deliberate: it survives resume (pid changes, sessionId does
not) and avoids cmux's "newest jsonl in this cwd" misattribution bug.

## Validation
`tests/test_native.py` exercises framing, resolution ranking, token lookup, and
a full send against a fake socket server. The protocol was also confirmed live:
a native send to this author session's own socket was received as a
`cross-session-message`.

## What this unblocks
cmux's reason-for-being shrinks to "keep a `claude` process alive in a pty and
track its session." Messaging, idle-detection, and the per-agent daemon all go
away. See the `agents` tool for the minimal registry/home-dir/up-down layer that
remains, now delivering through `claudio.native`.
