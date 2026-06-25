"""
Tests for claudio.agent — no tmux required.

Each test starts claudio.run() in a thread with mock deliver/is_idle callbacks,
sends messages via claudio.send(), and asserts delivery behavior.
"""

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from uuid import uuid4

import claudio
from claudio.agent import socket_path

from tests.conftest import run_agent, wait_for


STATE_DIR = '/tmp/claudio-test'


class TestDelivery(unittest.TestCase):

    def test_message_delivered_when_idle(self):
        """A message sent to an idle agent is delivered."""
        delivered = []
        name = f't-idle-{uuid4().hex[:8]}'
        run_agent(name, lambda msg: delivered.append(msg), lambda: True, STATE_DIR)

        claudio.send(name, {'body': 'hello'}, state_dir=STATE_DIR)

        self.assertTrue(wait_for(lambda: len(delivered) >= 1),
                        "message should have been delivered")
        self.assertEqual(delivered[0]['body'], 'hello')

    def test_message_held_while_busy(self):
        """Messages queue up while is_idle returns False, then deliver when idle."""
        delivered = []
        idle = threading.Event()
        name = f't-busy-{uuid4().hex[:8]}'

        run_agent(name, lambda msg: delivered.append(msg), lambda: idle.is_set(), STATE_DIR)

        claudio.send(name, {'body': 'first'}, state_dir=STATE_DIR)
        claudio.send(name, {'body': 'second'}, state_dir=STATE_DIR)

        # Give delivery loop a chance to run; nothing should arrive while busy
        time.sleep(1.0)
        self.assertEqual(len(delivered), 0, "should not deliver while busy")

        idle.set()
        self.assertTrue(wait_for(lambda: len(delivered) >= 2, timeout=15.0),
                        f"both messages should have been delivered; got {len(delivered)}")

    def test_delivery_order(self):
        """Messages are delivered in the order they were sent."""
        delivered = []
        name = f't-order-{uuid4().hex[:8]}'
        run_agent(name, lambda msg: delivered.append(msg['body']), lambda: True, STATE_DIR)

        for i in range(5):
            claudio.send(name, {'body': str(i)}, state_dir=STATE_DIR)

        self.assertTrue(wait_for(lambda: len(delivered) >= 5, timeout=15.0),
                        f"all 5 messages should have been delivered; got {len(delivered)}")
        self.assertEqual(delivered, ['0', '1', '2', '3', '4'])

    def test_send_returns_ack(self):
        """claudio.send() returns {'ok': True} on success."""
        name = f't-ack-{uuid4().hex[:8]}'
        run_agent(name, lambda msg: None, lambda: True, STATE_DIR)

        ack = claudio.send(name, {'body': 'ping'}, state_dir=STATE_DIR)
        self.assertEqual(ack, {'ok': True})

    def test_multiple_senders(self):
        """Messages from different senders all arrive and are attributed correctly."""
        delivered = []
        name = f't-multi-{uuid4().hex[:8]}'
        run_agent(name, lambda msg: delivered.append(msg), lambda: True, STATE_DIR)

        claudio.send(name, {'from': 'alice', 'body': 'hi'}, state_dir=STATE_DIR)
        claudio.send(name, {'from': 'bob',   'body': 'hey'}, state_dir=STATE_DIR)

        self.assertTrue(wait_for(lambda: len(delivered) >= 2, timeout=10.0),
                        f"both messages should have arrived; got {len(delivered)}")
        senders = [m['from'] for m in delivered]
        self.assertIn('alice', senders)
        self.assertIn('bob', senders)


class TestErrorPaths(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_malformed_json_does_not_crash_daemon(self):
        """Sending malformed JSON to a running agent closes the connection gracefully."""
        name = f't-malformed-{uuid4().hex[:8]}'
        run_agent(name, lambda msg: None, lambda: True, self.tmpdir)

        sock_path = socket_path(name, self.tmpdir)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        s.sendall(b'this is not json{{{')
        s.settimeout(3.0)
        try:
            data = s.recv(65536)
            # The agent may send an error response or close the connection
            if data:
                resp = json.loads(data)
                self.assertFalse(resp.get('ok', True),
                                 "error response should have ok=False")
        except (socket.timeout, ConnectionResetError):
            # Connection closed by daemon — also acceptable
            pass
        finally:
            s.close()

        # Daemon must still be alive and accept new connections
        ack = claudio.send(name, {'body': 'still alive?'}, state_dir=self.tmpdir)
        self.assertEqual(ack, {'ok': True}, "daemon should still be running after malformed JSON")

    def test_send_to_nonexistent_socket_raises(self):
        """send_to() with a socket path that doesn't exist raises an error."""
        missing_path = os.path.join(self.tmpdir, 'ghost.sock')
        with self.assertRaises(Exception):
            claudio.send_to(missing_path, {'body': 'hello'})

    def test_send_when_agent_not_running_raises(self):
        """send() to a name with no socket raises an error."""
        with self.assertRaises(Exception):
            claudio.send(f'not-running-{uuid4().hex[:8]}', {'body': 'hi'},
                         state_dir=self.tmpdir)


if __name__ == '__main__':
    unittest.main()
