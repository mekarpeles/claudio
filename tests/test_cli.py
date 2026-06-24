"""
Unit tests for claudio.cli command functions — uses real sockets.
"""

import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from uuid import uuid4

import claudio
from claudio.agent import socket_path
from claudio.cli import cmd_peers, cmd_send
from claudio.peers import Peers, peers_path


def _run_agent(name, deliver, is_idle, state_dir):
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


class TestSendBySockPath(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_send_by_socket_path(self):
        """cmd_send with a full socket path delivers the message."""
        delivered = []
        name = f'agent-{uuid4().hex[:8]}'
        _run_agent(name, lambda msg: delivered.append(msg), lambda: True, self.tmpdir)

        sock = socket_path(name, self.tmpdir)
        rc = cmd_send([sock, 'hello from test'], state_dir=self.tmpdir, agent_name=name)
        self.assertEqual(rc, 0)

        deadline = time.time() + 3
        while time.time() < deadline:
            if delivered:
                break
            time.sleep(0.1)
        self.assertTrue(delivered, "message should have been delivered")
        self.assertEqual(delivered[0]['body'], 'hello from test')


class TestSendByPeerName(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_send_by_peer_name(self):
        """cmd_send resolves target via peers file when given a name."""
        alice_name = f'alice-{uuid4().hex[:8]}'
        bob_name = f'bob-{uuid4().hex[:8]}'
        delivered = []

        _run_agent(bob_name, lambda msg: delivered.append(msg), lambda: True, self.tmpdir)
        bob_sock = socket_path(bob_name, self.tmpdir)

        # Add bob to alice's peers file
        p = Peers(peers_path(alice_name, self.tmpdir))
        p.add(bob_name, bob_sock)

        rc = cmd_send([bob_name, 'hi from alice'], state_dir=self.tmpdir, agent_name=alice_name)
        self.assertEqual(rc, 0)

        deadline = time.time() + 3
        while time.time() < deadline:
            if delivered:
                break
            time.sleep(0.1)
        self.assertTrue(delivered)
        self.assertEqual(delivered[0]['body'], 'hi from alice')


class TestSendFallbackConvention(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_send_falls_back_to_convention(self):
        """Unknown peer name falls back to state_dir/<name>.sock convention."""
        target_name = f'target-{uuid4().hex[:8]}'
        delivered = []

        # Start agent using convention socket path (which is what the fallback resolves to)
        _run_agent(target_name, lambda msg: delivered.append(msg), lambda: True, self.tmpdir)

        caller_name = f'caller-{uuid4().hex[:8]}'
        # No peers file entry for target_name, so it falls back to socket_path(target_name, state_dir)
        rc = cmd_send([target_name, 'fallback msg'], state_dir=self.tmpdir, agent_name=caller_name)
        self.assertEqual(rc, 0)

        deadline = time.time() + 3
        while time.time() < deadline:
            if delivered:
                break
            time.sleep(0.1)
        self.assertTrue(delivered)
        self.assertEqual(delivered[0]['body'], 'fallback msg')


class TestPeersEmpty(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_peers_empty(self):
        """No peers file → prints '(no peers)'."""
        name = f'agent-{uuid4().hex[:8]}'
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            rc = cmd_peers([], state_dir=self.tmpdir, agent_name=name)
        finally:
            sys.stdout = old_stdout
        self.assertEqual(rc, 0)
        self.assertIn('(no peers)', captured.getvalue())


class TestPeersList(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_peers_lists(self):
        """Two peers in the peers file are both printed."""
        name = f'agent-{uuid4().hex[:8]}'
        p = Peers(peers_path(name, self.tmpdir))
        p.add('alice', '/tmp/alice.sock')
        p.add('bob', '/tmp/bob.sock')

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            rc = cmd_peers([], state_dir=self.tmpdir, agent_name=name)
        finally:
            sys.stdout = old_stdout

        self.assertEqual(rc, 0)
        output = captured.getvalue()
        self.assertIn('alice', output)
        self.assertIn('bob', output)
        self.assertIn('/tmp/alice.sock', output)
        self.assertIn('/tmp/bob.sock', output)


if __name__ == '__main__':
    unittest.main()
