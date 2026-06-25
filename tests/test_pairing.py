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

from tests.conftest import run_agent


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

        run_agent(name, lambda msg: delivered.append(msg), lambda: True, self.tmpdir)

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
        run_agent(name, lambda msg: None, lambda: True, self.tmpdir)

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

        run_agent(alice_name, lambda msg: None, lambda: True, self.tmpdir)
        run_agent(bob_name, lambda msg: bob_notifications.append(msg), lambda: True, self.tmpdir)

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


if __name__ == '__main__':
    unittest.main()
