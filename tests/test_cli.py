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


class TestSendUnknownPeer(unittest.TestCase):
    """Unknown peer name should fail immediately with a clear error, not a silent OS error."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_send_unknown_name_no_peers_exits_nonzero(self):
        """cmd_send with an unknown name and no peers file returns non-zero."""
        caller_name = f'caller-{uuid4().hex[:8]}'
        rc = cmd_send(['unknownagent', 'hi'], state_dir=self.tmpdir, agent_name=caller_name)
        self.assertNotEqual(rc, 0)

    def test_send_unknown_name_no_peers_emits_clear_error(self):
        """cmd_send with an unknown name prints a helpful 'no peer named' message, not an OSError."""
        caller_name = f'caller-{uuid4().hex[:8]}'
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            cmd_send(['unknownagent', 'hi'], state_dir=self.tmpdir, agent_name=caller_name)
        finally:
            sys.stderr = old_stderr
        err = captured.getvalue()
        self.assertIn('unknownagent', err)
        self.assertIn('pair', err)
        # Must NOT be a raw OS/socket error
        self.assertNotIn('OSError', err)
        self.assertNotIn('No such file', err)
        self.assertNotIn('Connection refused', err)

    def test_send_unknown_name_with_peers_file_but_not_listed(self):
        """cmd_send with a name not in the peers file (but file exists) also fails clearly."""
        caller_name = f'caller-{uuid4().hex[:8]}'
        # Seed a peers file with a different entry so the file exists
        p = Peers(peers_path(caller_name, self.tmpdir))
        p.add('someotherpeer', '/tmp/other.sock')

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            rc = cmd_send(['unknownagent', 'hi'], state_dir=self.tmpdir, agent_name=caller_name)
        finally:
            sys.stderr = old_stderr
        self.assertNotEqual(rc, 0)
        err = captured.getvalue()
        self.assertIn('unknownagent', err)
        self.assertIn('pair', err)


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
