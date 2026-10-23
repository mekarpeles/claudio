"""
Tests for claudio.nudges — pure unit tests, no sockets.

Mirrors tests/test_peers.py, since Nudges mirrors Peers.
"""

import json
import os
import shutil
import tempfile
import unittest

from claudio.nudges import NUDGE_PREFIX, Nudges, nudge_message, nudges_path


class TestNudges(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _nudges(self, name='agent'):
        return Nudges(nudges_path(name, self.tmpdir))

    def test_empty_missing_file(self):
        """Missing file returns []."""
        self.assertEqual(self._nudges().load(), [])

    def test_corrupt_file_returns_empty(self):
        """Corrupt JSON returns []."""
        path = nudges_path('agent', self.tmpdir)
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(path, 'w') as f:
            f.write('not valid json {{{{')
        self.assertEqual(Nudges(path).load(), [])

    def test_wrong_type_returns_empty(self):
        """A JSON object where a list is expected returns []."""
        path = nudges_path('agent', self.tmpdir)
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(path, 'w') as f:
            json.dump({'not': 'a list'}, f)
        self.assertEqual(Nudges(path).load(), [])

    def test_non_string_entries_are_dropped(self):
        """A list containing non-strings keeps only the strings."""
        path = nudges_path('agent', self.tmpdir)
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(['ok', 7, None, 'also ok'], f)
        self.assertEqual(Nudges(path).load(), ['ok', 'also ok'])

    def test_add_and_all(self):
        n = self._nudges()
        self.assertTrue(n.add('drink water'))
        self.assertEqual(n.all(), ['drink water'])

    def test_add_is_idempotent(self):
        """Adding the same prompt twice does not duplicate it."""
        n = self._nudges()
        self.assertTrue(n.add('turn off the stove'))
        self.assertFalse(n.add('turn off the stove'), "second add should report no-op")
        self.assertEqual(n.all(), ['turn off the stove'])

    def test_order_is_preserved(self):
        n = self._nudges()
        for prompt in ('eat', 'drink', 'sleep'):
            n.add(prompt)
        self.assertEqual(n.all(), ['eat', 'drink', 'sleep'])

    def test_add_rejects_empty(self):
        """Blank/whitespace-only prompts are rejected rather than stored."""
        n = self._nudges()
        self.assertFalse(n.add(''))
        self.assertFalse(n.add('   '))
        self.assertEqual(n.all(), [])

    def test_remove_by_exact_text(self):
        n = self._nudges()
        n.add('eat')
        n.add('drink')
        self.assertEqual(n.remove('eat'), 'eat')
        self.assertEqual(n.all(), ['drink'])

    def test_remove_nonexistent_returns_none(self):
        """Removing a missing prompt is a no-op returning None."""
        n = self._nudges()
        n.add('eat')
        self.assertIsNone(n.remove('nope'))
        self.assertEqual(n.all(), ['eat'])

    def test_remove_at_index(self):
        n = self._nudges()
        for prompt in ('eat', 'drink', 'sleep'):
            n.add(prompt)
        self.assertEqual(n.remove_at(1), 'drink')
        self.assertEqual(n.all(), ['eat', 'sleep'])

    def test_remove_at_out_of_range_returns_none(self):
        n = self._nudges()
        n.add('eat')
        self.assertIsNone(n.remove_at(5))
        self.assertIsNone(n.remove_at(-1))
        self.assertEqual(n.all(), ['eat'])

    def test_persistence_round_trip(self):
        """A fresh Nudges instance on the same path sees the same data."""
        self._nudges().add('eat')
        self._nudges().add('drink')
        self.assertEqual(Nudges(nudges_path('agent', self.tmpdir)).all(), ['eat', 'drink'])

    def test_nudges_path_format(self):
        self.assertEqual(nudges_path('myagent', '/some/dir'), '/some/dir/myagent.nudges.json')


class TestRemoveTargeting(unittest.TestCase):
    """`remove` targets by exact text first, integer index second — so a prompt
    whose literal text is '3' stays removable by text."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _nudges(self):
        return Nudges(nudges_path('agent', self.tmpdir))

    def test_remove_target_prefers_exact_text_over_index(self):
        n = self._nudges()
        for prompt in ('zero', 'one', '1'):
            n.add(prompt)
        # '1' matches an entry's text exactly, so text wins over index 1 ('one')
        self.assertEqual(n.remove_target('1'), '1')
        self.assertEqual(n.all(), ['zero', 'one'])

    def test_remove_target_falls_back_to_index(self):
        n = self._nudges()
        for prompt in ('zero', 'one', 'two'):
            n.add(prompt)
        self.assertEqual(n.remove_target('1'), 'one')
        self.assertEqual(n.all(), ['zero', 'two'])

    def test_remove_target_unmatched_returns_none(self):
        n = self._nudges()
        n.add('zero')
        self.assertIsNone(n.remove_target('nope'))
        self.assertIsNone(n.remove_target('99'))
        self.assertEqual(n.all(), ['zero'])


class TestNudgeMessage(unittest.TestCase):
    """The synthesized message must be distinguishable from a real peer message."""

    def test_message_is_marked_machine_readable(self):
        self.assertEqual(nudge_message('eat')['_claudio'], 'nudge')

    def test_message_body_carries_prefix_and_prompt(self):
        msg = nudge_message('eat something')
        self.assertTrue(msg['body'].startswith(NUDGE_PREFIX))
        self.assertIn('eat something', msg['body'])

    def test_message_sender_is_noreply(self):
        """There is no peer to reply to, so the sender is the reserved noreply."""
        self.assertEqual(nudge_message('eat')['from'], 'noreply')

    def test_message_is_distinguishable_from_a_real_message(self):
        real = {'from': 'alice', 'body': 'hello'}
        self.assertNotEqual(nudge_message('eat').get('_claudio'), real.get('_claudio'))


if __name__ == '__main__':
    unittest.main()
