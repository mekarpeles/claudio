"""
Integration tests for claudio pairing protocol — uses real sockets in threads.
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


class TestPairRequestEnqueuesNotification(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pair_request_enqueues_notification(self):
        """A pair_request to a running agent enqueues a notification with 'wants to pair'."""
        delivered = []
        name = f'bob-{uuid4().hex[:8]}'
        sender_name = f'alice-{uuid4().hex[:8]}'
        sender_sock = socket_path(sender_name, self.tmpdir)

        _run_agent(name, lambda msg: delivered.append(msg), lambda: True, self.tmpdir)

        bob_sock = socket_path(name, self.tmpdir)

        # send pair_request (don't wait for response — hold connection open in a thread)
        results = []

        def do_pair_request():
            import socket as _socket
            import json
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.connect(bob_sock)
            s.sendall(json.dumps({
                '_claudio': 'pair_request',
                'name': sender_name,
                'socket': sender_sock,
            }).encode())
            s.settimeout(10)
            try:
                data = s.recv(65536)
                results.append(json.loads(data) if data else {})
            except _socket.timeout:
                results.append({'timeout': True})
            finally:
                s.close()

        t = threading.Thread(target=do_pair_request, daemon=True)
        t.start()

        # Wait for notification to be delivered
        deadline = time.time() + 5
        while time.time() < deadline:
            if delivered:
                break
            time.sleep(0.1)

        self.assertEqual(len(delivered), 1, "expected one notification delivered")
        body = delivered[0].get('body', '')
        self.assertIn('wants to pair', body)
        self.assertIn(sender_name, body)


class TestPairApproveUnknown(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pair_approve_unknown_name(self):
        """pair_approve for an unknown name returns ok=False."""
        name = f'bob-{uuid4().hex[:8]}'
        _run_agent(name, lambda msg: None, lambda: True, self.tmpdir)

        bob_sock = socket_path(name, self.tmpdir)
        resp = claudio.send_to(bob_sock, {'_claudio': 'pair_approve', 'name': 'nobody'})
        self.assertFalse(resp.get('ok'))
        self.assertIn('no pending pair request', resp.get('error', ''))


class TestPairEndToEnd(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pair_end_to_end(self):
        """Full handshake: alice sends pair_request to bob, bob approves, both record each other."""
        alice_name = f'alice-{uuid4().hex[:8]}'
        bob_name = f'bob-{uuid4().hex[:8]}'

        bob_notifications = []

        _run_agent(alice_name, lambda msg: None, lambda: True, self.tmpdir)
        _run_agent(bob_name, lambda msg: bob_notifications.append(msg), lambda: True, self.tmpdir)

        alice_sock = socket_path(alice_name, self.tmpdir)
        bob_sock = socket_path(bob_name, self.tmpdir)

        # Alice sends pair_request using cmd_pair (blocks until approved, records peer on success)
        from claudio.cli import cmd_pair
        alice_rc = []

        def alice_pair():
            rc = cmd_pair([bob_sock], state_dir=self.tmpdir, agent_name=alice_name)
            alice_rc.append(rc)

        t = threading.Thread(target=alice_pair, daemon=True)
        t.start()

        # Wait for bob to receive notification
        deadline = time.time() + 5
        while time.time() < deadline:
            if bob_notifications:
                break
            time.sleep(0.05)

        self.assertTrue(bob_notifications, "bob should have received a notification")
        body = bob_notifications[0].get('body', '')
        self.assertIn('wants to pair', body)
        self.assertIn(alice_name, body)

        # Bob approves
        approve_resp = claudio.send_to(bob_sock, {'_claudio': 'pair_approve', 'name': alice_name})
        self.assertTrue(approve_resp.get('ok'), f"pair_approve failed: {approve_resp}")

        # Wait for alice's thread to unblock
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "alice's pair_request thread should have unblocked")
        self.assertTrue(alice_rc, "alice should have completed cmd_pair")
        self.assertEqual(alice_rc[0], 0, f"cmd_pair returned non-zero: {alice_rc}")

        # Alice records peer (done by cmd_pair on success)
        alice_peers = Peers(peers_path(alice_name, self.tmpdir))
        alice_result_peers = alice_peers.all()

        # Bob records peer via the approve handler
        bob_peers = Peers(peers_path(bob_name, self.tmpdir))
        bob_result_peers = bob_peers.all()

        self.assertIn(bob_name, alice_result_peers,
                      f"alice should have bob as peer; got {alice_result_peers}")
        self.assertIn(alice_name, bob_result_peers,
                      f"bob should have alice as peer; got {bob_result_peers}")


class TestPairAndMessage(unittest.TestCase):
    """Full e2e: two ephemeral claudio agents pair via CLI, then exchange messages."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pair_then_send(self):
        """Alice pairs with bob via CLI, bob approves via CLI, alice sends a message bob receives."""
        from claudio.cli import cmd_pair, cmd_send

        alice_name = f'alice-{uuid4().hex[:8]}'
        bob_name = f'bob-{uuid4().hex[:8]}'

        bob_delivered = []

        _run_agent(alice_name, lambda msg: None, lambda: True, self.tmpdir)
        _run_agent(bob_name, lambda msg: bob_delivered.append(msg), lambda: True, self.tmpdir)

        bob_sock = socket_path(bob_name, self.tmpdir)

        # Alice initiates pairing in a thread — blocks until bob approves
        alice_rc = []
        def alice_pair():
            alice_rc.append(cmd_pair([bob_sock], state_dir=self.tmpdir, agent_name=alice_name))
        t = threading.Thread(target=alice_pair, daemon=True)
        t.start()

        # Wait for bob's notification
        deadline = time.time() + 5
        while time.time() < deadline:
            if bob_delivered:
                break
            time.sleep(0.05)
        self.assertTrue(bob_delivered, 'bob should receive pair notification')

        # Bob approves via CLI (sends pair_approve to his own daemon socket)
        self.assertEqual(
            cmd_pair(['--approve', alice_name], state_dir=self.tmpdir, agent_name=bob_name),
            0, 'bob approve should return 0',
        )

        # Alice's thread unblocks
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "alice's pair thread should have completed")
        self.assertEqual(alice_rc[0], 0, 'alice cmd_pair should return 0')

        # Both peers recorded
        alice_peers = Peers(peers_path(alice_name, self.tmpdir)).all()
        bob_peers = Peers(peers_path(bob_name, self.tmpdir)).all()
        self.assertIn(bob_name, alice_peers, f'alice peers: {alice_peers}')
        self.assertIn(alice_name, bob_peers, f'bob peers: {bob_peers}')

        # Alice sends a message to bob by name — resolves via alice's peers file
        self.assertEqual(
            cmd_send([bob_name, 'hello from alice'], state_dir=self.tmpdir, agent_name=alice_name),
            0, 'cmd_send should return 0',
        )

        # Wait for delivery
        deadline = time.time() + 3
        while time.time() < deadline:
            if len(bob_delivered) >= 2:
                break
            time.sleep(0.1)

        payloads = [m.get('body') for m in bob_delivered]
        self.assertIn('hello from alice', payloads, f'bob received: {payloads}')

    def test_bidirectional_messaging(self):
        """After pairing, both agents can send to each other by name."""
        from claudio.cli import cmd_pair, cmd_send

        alice_name = f'alice-{uuid4().hex[:8]}'
        bob_name = f'bob-{uuid4().hex[:8]}'

        alice_delivered = []
        bob_delivered = []

        _run_agent(alice_name, lambda msg: alice_delivered.append(msg), lambda: True, self.tmpdir)
        _run_agent(bob_name, lambda msg: bob_delivered.append(msg), lambda: True, self.tmpdir)

        bob_sock = socket_path(bob_name, self.tmpdir)

        # Pair
        t = threading.Thread(
            target=cmd_pair,
            args=([bob_sock],),
            kwargs=dict(state_dir=self.tmpdir, agent_name=alice_name),
            daemon=True,
        )
        t.start()

        deadline = time.time() + 5
        while time.time() < deadline:
            if bob_delivered:
                break
            time.sleep(0.05)

        cmd_pair(['--approve', alice_name], state_dir=self.tmpdir, agent_name=bob_name)
        t.join(timeout=5)

        # Alice → Bob
        cmd_send([bob_name, 'ping from alice'], state_dir=self.tmpdir, agent_name=alice_name)

        # Bob → Alice
        cmd_send([alice_name, 'pong from bob'], state_dir=self.tmpdir, agent_name=bob_name)

        # Wait for both deliveries
        deadline = time.time() + 3
        while time.time() < deadline:
            alice_bodies = [m.get('body') for m in alice_delivered]
            bob_bodies = [m.get('body') for m in bob_delivered]
            if 'ping from alice' in bob_bodies and 'pong from bob' in alice_bodies:
                break
            time.sleep(0.1)

        self.assertIn('ping from alice', [m.get('body') for m in bob_delivered])
        self.assertIn('pong from bob', [m.get('body') for m in alice_delivered])


if __name__ == '__main__':
    unittest.main()
