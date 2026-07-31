"""
claudio.nudges — per-agent list of self-directed prompts ("cravings"), stored as JSON.

A nudge is a prompt string an agent registers for itself. When the session has
been continuously idle past a threshold and its real inbox is empty, one of the
registered prompts is synthesized into a message and delivered through the same
`deliver()` callback a real message uses — so it arrives like any other injected
message, except it came from the agent's own list rather than from a peer.

claudio has no opinion about what the prompts are. The default list is empty.
"""

import json
import os
from typing import List, Optional

# Marks a synthesized nudge in the message body, so the prefix survives any
# deliver() implementation that only looks at msg['body'].
NUDGE_PREFIX = '[claudio nudge]: '

# There is no peer to reply to, so nudges use the reserved noreply sender.
NUDGE_SENDER = 'noreply'


class Nudges:
    """Per-agent list of self-directed prompts, stored as an ordered JSON array."""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> List[str]:
        """Return the prompt list; returns [] if missing, corrupt, or malformed."""
        try:
            with open(self.path, 'r') as f:
                data = json.load(f)
            if isinstance(data, list):
                return [p for p in data if isinstance(p, str)]
            return []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def save(self, data: List[str]) -> None:
        """Persist the prompt list; creates parent dirs if needed."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(data, f)

    def add(self, prompt: str) -> bool:
        """
        Register a prompt. Idempotent — returns False if it was already
        registered or is blank, True if it was appended.
        """
        prompt = prompt.strip()
        if not prompt:
            return False
        data = self.load()
        if prompt in data:
            return False
        data.append(prompt)
        self.save(data)
        return True

    def remove(self, prompt: str) -> Optional[str]:
        """Unregister by exact text; returns the removed prompt, or None if absent."""
        data = self.load()
        if prompt not in data:
            return None
        data.remove(prompt)
        self.save(data)
        return prompt

    def remove_at(self, index: int) -> Optional[str]:
        """Unregister by 0-based index; returns the removed prompt, or None if out of range."""
        data = self.load()
        if not 0 <= index < len(data):
            return None
        prompt = data.pop(index)
        self.save(data)
        return prompt

    def remove_target(self, target: str) -> Optional[str]:
        """
        Unregister by exact text, falling back to 0-based index.

        Text is tried first so that a prompt whose literal text is '3' stays
        removable by text; '3' only means "index 3" when no prompt equals it.
        Exact text is the stable identifier (indices shift as entries are
        removed); the index form exists because prompts are long to retype.
        """
        removed = self.remove(target)
        if removed is not None:
            return removed
        try:
            index = int(target)
        except ValueError:
            return None
        return self.remove_at(index)

    def all(self) -> List[str]:
        return self.load()


def nudges_path(name: str, state_dir: str) -> str:
    return os.path.join(state_dir, f'{name}.nudges.json')


def nudge_message(prompt: str) -> dict:
    """
    Build the message dict for a nudge.

    Marked three ways, each for a different consumer: `_claudio` for anything
    dispatching on message kind, the noreply sender because there is no peer to
    reply to, and the body prefix for a deliver() that only reads the body.
    """
    return {'_claudio': 'nudge', 'from': NUDGE_SENDER, 'body': f'{NUDGE_PREFIX}{prompt}'}


def should_fire(idle_since: Optional[float], now: float, nudge_after: Optional[float]) -> bool:
    """
    True when the idle clock has been running for at least `nudge_after`.

    idle_since is None whenever the session is not currently idle, and
    nudge_after is None when nudging is muted — either disables firing.
    """
    if nudge_after is None or idle_since is None:
        return False
    return (now - idle_since) >= nudge_after


def next_prompt(prompts: List[str], last_fired: Optional[str]) -> Optional[str]:
    """
    Pick the next prompt round-robin, keyed on the last-fired *text*.

    A plain integer cursor goes stale when the list is edited between fires
    (the CLI mutates the file from another process), silently skipping or
    repeating entries after a removal. Resolving the position by text each
    time keeps the rotation order without that failure mode; if the last-fired
    prompt is gone — or nothing has fired yet — the rotation restarts at 0.
    """
    if not prompts:
        return None
    try:
        index = prompts.index(last_fired) + 1
    except ValueError:
        index = 0
    return prompts[index % len(prompts)]
