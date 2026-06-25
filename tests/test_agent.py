"""
Tests for claudio.agent — no tmux required.

Each test starts claudio.run() in a thread with mock deliver/is_idle callbacks,
sends messages via claudio.send(), and asserts delivery behavior.
"""

import threading
import time
import unittest

import claudio
from claudio.agent import DEFAULT_STATE_DIR


STATE_DIR = '/tmp/claudio-test'


def _run_agent(name, deliver, is_idle, ready_event):
    """Start claudio.run() in a background thread; set ready_event when socket is up."""
    def _start():
        ready_event.set()
        claudio.run(name=name, deliver=deliver, is_idle=is_idle, state_dir=STATE_DIR)
    t = threading.Thread(target=_start, daemon=True)
    t.start()
    ready_event.wait(timeout=2)
    # Give the socket a moment to bind
    time.sleep(0.05)


class TestDelivery(unittest.TestCase):

    def test_message_delivered_when_idle(self):
        """A message sent to an idle agent is delivered."""
        delivered = []
        ready = threading.Event()
        _run_agent('t-idle', lambda msg: delivered.append(msg), lambda: True, ready)

        claudio.send('t-idle', {'body': 'hello'}, state_dir=STATE_DIR)
        time.sleep(1.5)

        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]['body'], 'hello')

    def test_message_held_while_busy(self):
        """Messages queue up while is_idle returns False, then deliver when idle."""
        delivered = []
        idle = threading.Event()
        ready = threading.Event()

        _run_agent('t-busy', lambda msg: delivered.append(msg), lambda: idle.is_set(), ready)

        claudio.send('t-busy', {'body': 'first'}, state_dir=STATE_DIR)
        claudio.send('t-busy', {'body': 'second'}, state_dir=STATE_DIR)
        time.sleep(1.0)
        self.assertEqual(len(delivered), 0, "should not deliver while busy")

        idle.set()
        time.sleep(2.5)  # two messages × ~1s delivery gap
        self.assertEqual(len(delivered), 2)

    def test_delivery_order(self):
        """Messages are delivered in the order they were sent."""
        delivered = []
        ready = threading.Event()
        _run_agent('t-order', lambda msg: delivered.append(msg['body']), lambda: True, ready)

        for i in range(5):
            claudio.send('t-order', {'body': str(i)}, state_dir=STATE_DIR)
        time.sleep(7)  # 5 messages × ~1s each + buffer

        self.assertEqual(delivered, ['0', '1', '2', '3', '4'])

    def test_send_returns_ack(self):
        """claudio.send() returns {'ok': True} on success."""
        ready = threading.Event()
        _run_agent('t-ack', lambda msg: None, lambda: True, ready)

        ack = claudio.send('t-ack', {'body': 'ping'}, state_dir=STATE_DIR)
        self.assertEqual(ack, {'ok': True})

    def test_multiple_senders(self):
        """Messages from different senders all arrive and are attributed correctly."""
        delivered = []
        ready = threading.Event()
        _run_agent('t-multi', lambda msg: delivered.append(msg), lambda: True, ready)

        claudio.send('t-multi', {'from': 'alice', 'body': 'hi'}, state_dir=STATE_DIR)
        claudio.send('t-multi', {'from': 'bob',   'body': 'hey'}, state_dir=STATE_DIR)
        time.sleep(3.5)

        senders = [m['from'] for m in delivered]
        self.assertIn('alice', senders)
        self.assertIn('bob', senders)


class TestTOCTOURace(unittest.TestCase):
    """Regression tests for the TOCTOU race in the delivery loop.

    The race: the loop checks `if not queue` under the lock, releases the lock,
    checks is_idle(), then re-acquires the lock and calls popleft().  If another
    thread drains the queue in the window between the two lock acquisitions,
    popleft() raises IndexError.

    This test fires many concurrent senders so that the window is hit
    repeatedly, and asserts:
      1. No IndexError crash (the agent stays alive throughout), and
      2. Every sent message is eventually delivered (no silent drops).
    """

    def test_no_crash_under_concurrent_senders(self):
        """Concurrent senders must not cause IndexError / crash in delivery loop."""
        delivered = []
        errors = []
        ready = threading.Event()

        def deliver(msg):
            delivered.append(msg)

        # is_idle toggles rapidly to maximise the chance of hitting the race window
        toggle = threading.Event()

        def is_idle():
            return toggle.is_set()

        _run_agent('t-toctou-crash', deliver, is_idle, ready)

        N = 10
        # Send all messages while the agent is busy so they stack up in the queue
        for i in range(N):
            try:
                claudio.send('t-toctou-crash', {'body': str(i)}, state_dir=STATE_DIR)
            except Exception as e:
                errors.append(e)

        self.assertEqual(errors, [], f"send raised: {errors}")

        # Now open the idle gate — the delivery loop will race to drain the queue
        toggle.set()
        time.sleep(N * 1.5 + 3)  # generous budget: N messages × ~1s each + overhead

        self.assertEqual(
            len(delivered), N,
            f"Expected {N} deliveries, got {len(delivered)}: {[m['body'] for m in delivered]}",
        )

    def test_no_message_drop_under_concurrent_senders(self):
        """All messages sent concurrently are delivered exactly once, in any order."""
        delivered = []
        ready = threading.Event()

        _run_agent('t-toctou-drop', lambda msg: delivered.append(msg['body']), lambda: True, ready)

        N = 10
        threads = []
        barrier = threading.Barrier(N)

        def sender(i):
            barrier.wait()  # all N threads send simultaneously
            claudio.send('t-toctou-drop', {'body': str(i)}, state_dir=STATE_DIR)

        for i in range(N):
            t = threading.Thread(target=sender, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        time.sleep(N * 1.5 + 3)

        self.assertEqual(sorted(delivered), [str(i) for i in range(N)])


if __name__ == '__main__':
    unittest.main()
