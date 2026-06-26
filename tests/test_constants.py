"""
Tests for DRY constants — issue #6.

Verifies:
- DEFAULT_STATE_DIR is importable from claudio.agent and equals '/tmp/claudio'
- DEFAULT_STATE_DIR re-exported from claudio.cli matches the one in claudio.agent
- _RECV_BUF is defined in claudio.agent and equals 65536
- _RECV_BUF is importable from claudio.cli and matches claudio.agent's value
"""

import unittest


class TestDefaultStateDir(unittest.TestCase):

    def test_default_state_dir_value(self):
        """DEFAULT_STATE_DIR must be '/tmp/claudio'."""
        from claudio.agent import DEFAULT_STATE_DIR
        self.assertEqual(DEFAULT_STATE_DIR, '/tmp/claudio')

    def test_default_state_dir_importable_from_cli(self):
        """cli.py must expose DEFAULT_STATE_DIR (imported from agent)."""
        from claudio.cli import DEFAULT_STATE_DIR
        self.assertEqual(DEFAULT_STATE_DIR, '/tmp/claudio')

    def test_default_state_dir_same_object(self):
        """DEFAULT_STATE_DIR from cli and agent must be the same value."""
        from claudio.agent import DEFAULT_STATE_DIR as from_agent
        from claudio.cli import DEFAULT_STATE_DIR as from_cli
        self.assertEqual(from_agent, from_cli)


class TestRecvBuf(unittest.TestCase):

    def test_recv_buf_defined_in_agent(self):
        """_RECV_BUF must be defined in claudio.agent."""
        from claudio.agent import _RECV_BUF
        self.assertEqual(_RECV_BUF, 65536)

    def test_recv_buf_importable_from_cli(self):
        """_RECV_BUF must be importable from claudio.cli."""
        from claudio.cli import _RECV_BUF
        self.assertEqual(_RECV_BUF, 65536)

    def test_recv_buf_consistent(self):
        """_RECV_BUF from agent and cli must be identical."""
        from claudio.agent import _RECV_BUF as from_agent
        from claudio.cli import _RECV_BUF as from_cli
        self.assertEqual(from_agent, from_cli)


if __name__ == '__main__':
    unittest.main()
