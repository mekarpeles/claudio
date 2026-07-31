"""
Tests for idle-triggered nudge delivery.

Two layers:
  * pure-function tests for the trigger predicate and the rotation cursor —
    deterministic, fake clock, no sleeping;
  * integration tests that run a real claudio agent in a thread (same pattern
    as tests/test_agent.py) with a short `nudge_after` so they finish quickly.
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
from uuid import uuid4

import claudio
from claudio.agent import socket_path
from claudio.nudges import NUDGE_PREFIX, Nudges, next_prompt, nudges_path, should_fire

from .conftest import wait_for


def _run_agent(name, deliver, is_idle, state_dir, **kwargs):
    """Start claudio.run() in a daemon thread; wait for the socket to appear."""
    t = threading.Thread(
        target=claudio.run,
        kwargs=dict(name=name, deliver=deliver, is_idle=is_idle, state_dir=state_dir, **kwargs),
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


class TestShouldFire(unittest.TestCase):
    """The trigger predicate: has the idle clock run for at least the threshold?"""

    def test_not_idle_never_fires(self):
        """idle_since None means the session is not currently idle."""
        self.assertFalse(should_fire(None, 1000.0, 10.0))

    def test_disabled_threshold_never_fires(self):
        """nudge_after=None is a mute switch — registered nudges stay, nothing fires."""
        self.assertFalse(should_fire(0.0, 1e9, None))

    def test_below_threshold_does_not_fire(self):
        self.assertFalse(should_fire(100.0, 109.9, 10.0))

    def test_at_threshold_fires(self):
        self.assertTrue(should_fire(100.0, 110.0, 10.0))

    def test_above_threshold_fires(self):
        self.assertTrue(should_fire(100.0, 500.0, 10.0))

    def test_zero_threshold_fires_immediately(self):
        """0 is allowed so tests (and impatient consumers) can fire on the next tick."""
        self.assertTrue(should_fire(100.0, 100.0, 0))


class TestNextPrompt(unittest.TestCase):
    """Round-robin rotation keyed on the last-fired text, not an index."""

    def test_empty_list_yields_nothing(self):
        self.assertIsNone(next_prompt([], None))
        self.assertIsNone(next_prompt([], 'eat'))

    def test_first_fire_starts_at_index_zero(self):
        self.assertEqual(next_prompt(['eat', 'drink'], None), 'eat')

    def test_advances_to_the_following_entry(self):
        self.assertEqual(next_prompt(['eat', 'drink', 'sleep'], 'eat'), 'drink')
        self.assertEqual(next_prompt(['eat', 'drink', 'sleep'], 'drink'), 'sleep')

    def test_wraps_around(self):
        self.assertEqual(next_prompt(['eat', 'drink', 'sleep'], 'sleep'), 'eat')

    def test_single_entry_repeats(self):
        self.assertEqual(next_prompt(['eat'], 'eat'), 'eat')

    def test_unknown_last_fired_restarts_at_index_zero(self):
        """If the last-fired prompt was removed from the list, restart cleanly."""
        self.assertEqual(next_prompt(['eat', 'drink'], 'deleted prompt'), 'eat')

    def test_rotation_survives_a_removal_without_skipping(self):
        """An integer cursor would silently skip here; a text cursor does not."""
        prompts = ['a', 'b', 'c']
        self.assertEqual(next_prompt(prompts, 'a'), 'b')
        # 'a' is unregistered between fires; the next one after 'b' is still 'c'
        self.assertEqual(next_prompt(['b', 'c'], 'b'), 'c')


class _AgentTestCase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.name = f'nudge-{uuid4().hex[:8]}'
        self.delivered = []

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _register(self, *prompts):
        n = Nudges(nudges_path(self.name, self.tmpdir))
        for p in prompts:
            n.add(p)
        return n

    def _start(self, is_idle=lambda: True, **kwargs):
        _run_agent(self.name, self.delivered.append, is_idle, self.tmpdir, **kwargs)

    def _nudges(self):
        return [m for m in self.delivered if m.get('_claudio') == 'nudge']

    def _reals(self):
        return [m for m in self.delivered if m.get('_claudio') != 'nudge']


class TestBackwardCompatibility(_AgentTestCase):
    """Zero nudges registered — the default — must behave exactly as before."""

    def test_no_nudges_registered_never_fires(self):
        """Even with an aggressive threshold and a permanently idle session."""
        self._start(nudge_after=0.1)
        time.sleep(3.0)
        self.assertEqual(self.delivered, [], "an empty nudge list must deliver nothing")

    def test_no_nudges_file_at_all_never_fires(self):
        """The nudges file is not even created by starting an agent."""
        self._start(nudge_after=0.1)
        time.sleep(1.5)
        self.assertFalse(os.path.exists(nudges_path(self.name, self.tmpdir)))

    def test_is_idle_not_polled_when_nothing_is_pending(self):
        """With no nudges registered and an empty queue, is_idle() must not be
        called at all — exactly as before nudges existed.

        Not a micro-optimisation: cmux's is_idle() shells out to `tmux
        capture-pane` and `tmux display-message`. Polling it twice a second for
        every idle agent would be a real regression.
        """
        calls = []
        self._start(is_idle=lambda: calls.append(1) or True, nudge_after=0.1)
        time.sleep(2.5)
        self.assertEqual(calls, [], "is_idle() must not be polled on empty-queue ticks")

    def test_is_idle_still_polled_when_a_message_is_queued(self):
        """The gate must not suppress the poll that real delivery depends on."""
        calls = []
        self._start(is_idle=lambda: calls.append(1) or True, nudge_after=0.1)
        claudio.send(self.name, {'body': 'hello'}, state_dir=self.tmpdir)
        self.assertTrue(wait_for(lambda: self.delivered, timeout=3))
        self.assertTrue(calls)

    def test_is_idle_not_polled_after_the_last_nudge_is_unregistered(self):
        """An emptied nudges file still exists on disk; it must not keep the
        agent polling is_idle() forever."""
        n = self._register('temporary')
        calls = []
        self._start(is_idle=lambda: calls.append(1) or True, nudge_after=30.0)
        self.assertTrue(wait_for(lambda: calls, timeout=3))

        n.remove('temporary')
        self.assertEqual(n.all(), [])
        time.sleep(1.0)  # let any in-flight tick land
        calls.clear()
        time.sleep(2.0)
        self.assertEqual(calls, [], "an empty nudge list must stop the polling again")

    def test_is_idle_polled_once_nudges_are_registered(self):
        """Registering a nudge opts the agent into idle polling."""
        calls = []
        self._start(is_idle=lambda: calls.append(1) or True, nudge_after=30.0)
        time.sleep(1.5)
        self.assertEqual(calls, [])
        self._register('a nudge')
        self.assertTrue(wait_for(lambda: calls, timeout=3),
                        "is_idle() must be polled once there is a nudge to fire")

    def test_real_messages_unaffected_by_default_params(self):
        """Default call signature (no nudge_after) still delivers normally."""
        self._start()
        claudio.send(self.name, {'body': 'hello'}, state_dir=self.tmpdir)
        self.assertTrue(wait_for(lambda: len(self.delivered) == 1, timeout=3))
        self.assertEqual(self.delivered[0]['body'], 'hello')

    def test_registered_nudges_do_not_disturb_real_delivery(self):
        """With nudges registered but the threshold not reached, delivery is normal."""
        self._register('a nudge')
        self._start(nudge_after=30.0)
        claudio.send(self.name, {'body': 'hello'}, state_dir=self.tmpdir)
        self.assertTrue(wait_for(lambda: len(self.delivered) == 1, timeout=3))
        self.assertEqual(self.delivered[0]['body'], 'hello')
        self.assertEqual(self._nudges(), [])


class TestNudgeFires(_AgentTestCase):

    def test_nudge_fires_after_threshold(self):
        self._register('check the stove')
        self._start(nudge_after=0.2)
        self.assertTrue(wait_for(lambda: self._nudges(), timeout=4),
                        "a registered nudge should fire on a prolonged-idle session")
        msg = self._nudges()[0]
        self.assertEqual(msg['_claudio'], 'nudge')
        self.assertIn('check the stove', msg['body'])
        self.assertTrue(msg['body'].startswith(NUDGE_PREFIX))

    def test_nudge_does_not_fire_before_threshold(self):
        self._register('check the stove')
        self._start(nudge_after=30.0)
        time.sleep(2.0)
        self.assertEqual(self._nudges(), [])

    def test_nudge_after_none_disables_firing(self):
        """Registered nudges are kept but muted."""
        self._register('check the stove')
        self._start(nudge_after=None)
        time.sleep(2.5)
        self.assertEqual(self.delivered, [])

    def test_nudges_registered_after_start_are_picked_up(self):
        """`claudio nudge add` runs in a separate process; no daemon restart needed."""
        self._start(nudge_after=0.2)
        time.sleep(1.0)
        self.assertEqual(self._nudges(), [])
        self._register('registered late')
        self.assertTrue(wait_for(lambda: self._nudges(), timeout=4))
        self.assertIn('registered late', self._nudges()[0]['body'])


class TestNudgeSuppression(_AgentTestCase):

    def test_no_nudge_while_busy(self):
        """A never-idle session is never nudged, however long it runs."""
        self._register('check the stove')
        self._start(is_idle=lambda: False, nudge_after=0.1)
        time.sleep(3.0)
        self.assertEqual(self.delivered, [])

    def test_flicker_out_of_idle_resets_the_clock(self):
        """Briefly going busy restarts the threshold — nudges need *prolonged* idle."""
        self._register('check the stove')
        busy = threading.Event()
        self._start(is_idle=lambda: not busy.is_set(), nudge_after=1.5)

        # Flip busy for one poll tick, repeatedly, so the clock never accumulates 1.5s.
        deadline = time.time() + 4.0
        while time.time() < deadline:
            busy.set()
            time.sleep(0.6)
            busy.clear()
            time.sleep(0.6)

        self.assertEqual(self._nudges(), [],
                         "clock must reset on every is_idle() False, not just accumulate")

    def test_real_message_resets_the_clock(self):
        """A nudge must not land immediately on the heels of real traffic."""
        self._register('check the stove')
        self._start(nudge_after=2.0)

        # Let the idle clock run to just under the threshold, then send real traffic.
        time.sleep(1.7)
        claudio.send(self.name, {'body': 'real'}, state_dir=self.tmpdir)
        self.assertTrue(wait_for(lambda: self._reals(), timeout=3))

        # Had the clock not reset, the nudge would fire ~immediately after the
        # real message. Check well before the reset clock could legitimately expire.
        time.sleep(1.2)
        self.assertEqual(self._nudges(), [],
                         "real traffic must restart the idle clock")

        # ...and it does still fire once the full threshold elapses afresh.
        self.assertTrue(wait_for(lambda: self._nudges(), timeout=5))

    def test_real_messages_take_priority_over_nudges(self):
        """A queued real message is delivered before any nudge is considered."""
        self._register('check the stove')
        self._start(nudge_after=0.1)
        for i in range(3):
            claudio.send(self.name, {'body': f'real-{i}'}, state_dir=self.tmpdir)

        self.assertTrue(wait_for(lambda: len(self._reals()) == 3, timeout=8))
        # All three real messages precede the first nudge in delivery order.
        kinds = [m.get('_claudio') == 'nudge' for m in self.delivered]
        first_nudge = kinds.index(True) if True in kinds else len(kinds)
        self.assertEqual(kinds[:first_nudge].count(False), 3)


class TestNudgeRotation(_AgentTestCase):

    def test_does_not_spam_the_same_nudge_every_tick(self):
        """POLL_INTERVAL is 0.5s; over ~4s a 1.5s threshold allows at most a few."""
        self._register('only one')
        self._start(nudge_after=1.5)
        time.sleep(4.5)
        fired = len(self._nudges())
        self.assertGreaterEqual(fired, 1)
        self.assertLessEqual(fired, 3, f"threshold must re-elapse between fires, got {fired}")

    def test_rotates_round_robin_through_registered_nudges(self):
        self._register('first', 'second', 'third')
        self._start(nudge_after=0.1)
        self.assertTrue(wait_for(lambda: len(self._nudges()) >= 4, timeout=15))
        bodies = [m['body'] for m in self._nudges()[:4]]
        for body, expected in zip(bodies, ['first', 'second', 'third', 'first']):
            self.assertIn(expected, body)


if __name__ == '__main__':
    unittest.main()
