"""
Shared test helpers for claudio test suite.

Provides:
- run_agent: start a claudio agent in a daemon thread, wait for socket to appear
- wait_for: poll a condition up to a timeout instead of using fixed sleeps
"""

import os
import threading
import time

import claudio
from claudio.agent import socket_path


def run_agent(name, deliver, is_idle, state_dir):
    """Start claudio.run() in a daemon thread; wait for socket to appear."""
    t = threading.Thread(
        target=claudio.run,
        kwargs=dict(name=name, deliver=deliver, is_idle=is_idle, state_dir=state_dir),
        daemon=True,
    )
    t.start()
    sock = socket_path(name, state_dir)
    deadline = time.time() + 5
    while time.time() < deadline:
        if os.path.exists(sock):
            return
        time.sleep(0.02)
    raise RuntimeError(f"socket for agent '{name}' never appeared: {sock}")


def wait_for(condition, timeout=10.0, interval=0.1):
    """Poll condition() up to timeout seconds, returning True if it became True."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False
