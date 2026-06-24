"""
Tests for claudio.peers — pure unit tests, no sockets.
"""

import os
import tempfile
import shutil
import unittest

from claudio.peers import Peers, peers_path


class TestPeers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _peers(self, name='agent'):
        path = peers_path(name, self.tmpdir)
        return Peers(path)

    def test_empty_missing_file(self):
        """Missing file returns {}."""
        p = self._peers()
        self.assertEqual(p.load(), {})

    def test_corrupt_file_returns_empty(self):
        """Corrupt JSON returns {}."""
        path = peers_path('agent', self.tmpdir)
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(path, 'w') as f:
            f.write('not valid json {{{{')
        p = Peers(path)
        self.assertEqual(p.load(), {})

    def test_add_and_get(self):
        p = self._peers()
        p.add('alice', '/tmp/alice.sock')
        self.assertEqual(p.get('alice'), '/tmp/alice.sock')

    def test_add_multiple_all(self):
        p = self._peers()
        p.add('alice', '/tmp/alice.sock')
        p.add('bob', '/tmp/bob.sock')
        all_peers = p.all()
        self.assertEqual(all_peers.get('alice'), '/tmp/alice.sock')
        self.assertEqual(all_peers.get('bob'), '/tmp/bob.sock')
        self.assertEqual(len(all_peers), 2)

    def test_remove_existing(self):
        p = self._peers()
        p.add('alice', '/tmp/alice.sock')
        p.remove('alice')
        self.assertIsNone(p.get('alice'))

    def test_remove_nonexistent_noop(self):
        """Removing a missing peer is a no-op."""
        p = self._peers()
        p.remove('nonexistent')  # should not raise
        self.assertEqual(p.all(), {})

    def test_persistence_round_trip(self):
        """save/load round-trip preserves data."""
        p = self._peers()
        p.add('alice', '/tmp/alice.sock')
        p.add('bob', '/tmp/bob.sock')
        # Create a fresh Peers instance pointing at the same path
        p2 = Peers(peers_path('agent', self.tmpdir))
        data = p2.load()
        self.assertEqual(data['alice'], '/tmp/alice.sock')
        self.assertEqual(data['bob'], '/tmp/bob.sock')

    def test_get_missing_returns_none(self):
        p = self._peers()
        self.assertIsNone(p.get('nobody'))

    def test_peers_path_format(self):
        path = peers_path('myagent', '/some/dir')
        self.assertEqual(path, '/some/dir/myagent.peers.json')


if __name__ == '__main__':
    unittest.main()
