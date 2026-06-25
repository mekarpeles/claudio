"""
Unit/integration tests for cmd_pair_initiate and cmd_pair_approve in isolation.
These test each function independently after the refactor.
"""

import io
import json
import os
import shutil
import socket as _socket
import sys
import tempfile
import threading
import time
import unittest
from uuid import uuid4

import claudio
from claudio.agent import socket_path
from claudio.cli import cmd_pair_approve, cmd_pair_initiate
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


# ---------------------------------------------------------------------------
# cmd_pair_approve tests
# ---------------------------------------------------------------------------

class TestCmdPairApproveNoArgs(unittest.TestCase):
    """cmd_pair_approve with missing --approve argument prints usage and returns 1."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stderr = io.StringIO()
        self._old_stderr = sys.stderr
        sys.stderr = self.stderr

    def tearDown(self):
        sys.stderr = self._old_stderr
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_name_arg(self):
        # args would normally be ['--approve', '<name>'] — pass without the name
        rc = cmd_pair_approve(['--approve'], state_dir=self.tmpdir, agent_name='alice')
        self.assertEqual(rc, 1)
        self.assertIn('usage', self.stderr.getvalue())


class TestCmdPairApproveNoAgentName(unittest.TestCase):
    """cmd_pair_approve without agent_name set returns 1."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stderr = io.StringIO()
        self._old_stderr = sys.stderr
        sys.stderr = self.stderr

    def tearDown(self):
        sys.stderr = self._old_stderr
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_agent_name(self):
        # Unset env vars so _agent_name() returns None
        env_backup = {}
        for k in ('CLAUDIO_AGENT_NAME', 'CMUX_SESSION_NAME'):
            env_backup[k] = os.environ.pop(k, None)
        try:
            rc = cmd_pair_approve(['--approve', 'bob'], state_dir=self.tmpdir, agent_name=None)
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v
        self.assertEqual(rc, 1)
        self.assertIn('CLAUDIO_AGENT_NAME', self.stderr.getvalue())


class TestCmdPairApproveSuccess(unittest.TestCase):
    """cmd_pair_approve successfully contacts own daemon and approves a pending request."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_approve_after_pending_request(self):
        alice_name = f'alice-{uuid4().hex[:8]}'
        bob_name = f'bob-{uuid4().hex[:8]}'

        bob_notifications = []
        _run_agent(alice_name, lambda msg: None, lambda: True, self.tmpdir)
        _run_agent(bob_name, lambda msg: bob_notifications.append(msg), lambda: True, self.tmpdir)

        alice_sock = socket_path(alice_name, self.tmpdir)
        bob_sock = socket_path(bob_name, self.tmpdir)

        # Alice sends a pair_request to bob — blocks waiting for approval
        alice_rc = []

        def alice_pair():
            rc = cmd_pair_initiate([bob_sock], state_dir=self.tmpdir, agent_name=alice_name)
            alice_rc.append(rc)

        t = threading.Thread(target=alice_pair, daemon=True)
        t.start()

        # Wait for bob to get the notification
        deadline = time.time() + 5
        while time.time() < deadline:
            if bob_notifications:
                break
            time.sleep(0.05)
        self.assertTrue(bob_notifications)

        # Bob approves via cmd_pair_approve (calls his own daemon)
        rc = cmd_pair_approve(['--approve', alice_name], state_dir=self.tmpdir, agent_name=bob_name)
        self.assertEqual(rc, 0)

        # Alice's thread should unblock
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertEqual(alice_rc[0], 0)


class TestCmdPairApproveUnknownPeer(unittest.TestCase):
    """cmd_pair_approve for a name with no pending request returns non-zero."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stderr = io.StringIO()
        self._old_stderr = sys.stderr
        sys.stderr = self.stderr

    def tearDown(self):
        sys.stderr = self._old_stderr
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_approve_unknown_returns_error(self):
        name = f'bob-{uuid4().hex[:8]}'
        _run_agent(name, lambda msg: None, lambda: True, self.tmpdir)
        rc = cmd_pair_approve(['--approve', 'nobody'], state_dir=self.tmpdir, agent_name=name)
        self.assertEqual(rc, 1)
        self.assertIn('pair approve failed', self.stderr.getvalue())


# ---------------------------------------------------------------------------
# cmd_pair_initiate tests
# ---------------------------------------------------------------------------

class TestCmdPairInitiateNoArgs(unittest.TestCase):
    """cmd_pair_initiate with no args prints usage and returns 1."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stderr = io.StringIO()
        self._old_stderr = sys.stderr
        sys.stderr = self.stderr

    def tearDown(self):
        sys.stderr = self._old_stderr
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_args(self):
        rc = cmd_pair_initiate([], state_dir=self.tmpdir, agent_name='alice')
        self.assertEqual(rc, 1)
        self.assertIn('usage', self.stderr.getvalue())


class TestCmdPairInitiateNoAgentName(unittest.TestCase):
    """cmd_pair_initiate without agent_name returns 1."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stderr = io.StringIO()
        self._old_stderr = sys.stderr
        sys.stderr = self.stderr

    def tearDown(self):
        sys.stderr = self._old_stderr
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_agent_name(self):
        env_backup = {}
        for k in ('CLAUDIO_AGENT_NAME', 'CMUX_SESSION_NAME'):
            env_backup[k] = os.environ.pop(k, None)
        try:
            rc = cmd_pair_initiate(['/tmp/fake.sock'], state_dir=self.tmpdir, agent_name=None)
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v
        self.assertEqual(rc, 1)
        self.assertIn('CLAUDIO_AGENT_NAME', self.stderr.getvalue())


class TestCmdPairInitiateCannotConnect(unittest.TestCase):
    """cmd_pair_initiate to a non-existent socket returns 1."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stderr = io.StringIO()
        self._old_stderr = sys.stderr
        sys.stderr = self.stderr

    def tearDown(self):
        sys.stderr = self._old_stderr
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cannot_connect(self):
        rc = cmd_pair_initiate(
            ['/tmp/nonexistent-claudio-test.sock'],
            state_dir=self.tmpdir,
            agent_name='alice',
        )
        self.assertEqual(rc, 1)
        self.assertIn('cannot connect', self.stderr.getvalue())


class TestCmdPairInitiateEndToEnd(unittest.TestCase):
    """cmd_pair_initiate blocks until the remote approves, then records the peer."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_initiate_end_to_end(self):
        alice_name = f'alice-{uuid4().hex[:8]}'
        bob_name = f'bob-{uuid4().hex[:8]}'

        bob_notifications = []
        _run_agent(alice_name, lambda msg: None, lambda: True, self.tmpdir)
        _run_agent(bob_name, lambda msg: bob_notifications.append(msg), lambda: True, self.tmpdir)

        alice_sock = socket_path(alice_name, self.tmpdir)
        bob_sock = socket_path(bob_name, self.tmpdir)

        alice_rc = []

        def alice_pair():
            rc = cmd_pair_initiate([bob_sock], state_dir=self.tmpdir, agent_name=alice_name)
            alice_rc.append(rc)

        t = threading.Thread(target=alice_pair, daemon=True)
        t.start()

        # Wait for bob's notification
        deadline = time.time() + 5
        while time.time() < deadline:
            if bob_notifications:
                break
            time.sleep(0.05)
        self.assertTrue(bob_notifications)
        body = bob_notifications[0].get('body', '')
        self.assertIn('wants to pair', body)
        self.assertIn(alice_name, body)

        # Bob approves via the raw protocol (no cmd_pair_approve needed here)
        approve_resp = claudio.send_to(bob_sock, {'_claudio': 'pair_approve', 'name': alice_name})
        self.assertTrue(approve_resp.get('ok'))

        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertEqual(alice_rc[0], 0)

        # Alice has bob in her peers
        alice_peers = Peers(peers_path(alice_name, self.tmpdir))
        self.assertIn(bob_name, alice_peers.all())


# ---------------------------------------------------------------------------
# cmd_pair dispatcher still works
# ---------------------------------------------------------------------------

class TestCmdPairDispatcher(unittest.TestCase):
    """cmd_pair still routes correctly after the refactor."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.stderr = io.StringIO()
        self._old_stderr = sys.stderr
        sys.stderr = self.stderr

    def tearDown(self):
        sys.stderr = self._old_stderr
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dispatch_no_args_goes_to_initiate(self):
        from claudio.cli import cmd_pair
        rc = cmd_pair([], state_dir=self.tmpdir, agent_name='alice')
        self.assertEqual(rc, 1)
        self.assertIn('usage', self.stderr.getvalue())

    def test_dispatch_approve_flag_goes_to_approve(self):
        from claudio.cli import cmd_pair
        # No running daemon — should fail trying to contact own daemon
        rc = cmd_pair(['--approve'], state_dir=self.tmpdir, agent_name='alice')
        self.assertEqual(rc, 1)
        # Should hit the approve usage error (missing name argument)
        self.assertIn('usage', self.stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
