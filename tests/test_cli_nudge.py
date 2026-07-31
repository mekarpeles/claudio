"""
Unit tests for the `claudio nudge` CLI commands.

Mirrors tests/test_cli.py: call the cmd_* function directly with an explicit
state_dir/agent_name, capture stdout/stderr, assert on the exit code and text.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from claudio.cli import cmd_nudge
from claudio.nudges import Nudges, nudges_path


class _NudgeCLITestCase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.name = f'agent-{uuid4().hex[:8]}'

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, args):
        """Run cmd_nudge, returning (rc, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = cmd_nudge(args, state_dir=self.tmpdir, agent_name=self.name)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return rc, out.getvalue(), err.getvalue()

    def _registered(self):
        return Nudges(nudges_path(self.name, self.tmpdir)).all()


class TestNudgeList(_NudgeCLITestCase):

    def test_empty_list(self):
        rc, out, _ = self._run(['list'])
        self.assertEqual(rc, 0)
        self.assertIn('(no nudges)', out)

    def test_bare_nudge_is_list(self):
        """`claudio nudge` with no subcommand lists, like `claudio peers`."""
        rc, out, _ = self._run([])
        self.assertEqual(rc, 0)
        self.assertIn('(no nudges)', out)

    def test_list_shows_indices_and_prompts(self):
        """Indices are printed so the index form of `remove` is usable."""
        self._run(['add', 'eat something'])
        self._run(['add', 'check the stove'])

        rc, out, _ = self._run(['list'])
        self.assertEqual(rc, 0)
        self.assertIn('eat something', out)
        self.assertIn('check the stove', out)
        self.assertIn('0', out)
        self.assertIn('1', out)
        # Registration order is preserved in the output
        self.assertLess(out.index('eat something'), out.index('check the stove'))


class TestNudgeAdd(_NudgeCLITestCase):

    def test_add_registers_the_prompt(self):
        rc, out, _ = self._run(['add', 'eat something'])
        self.assertEqual(rc, 0)
        self.assertEqual(self._registered(), ['eat something'])

    def test_add_is_idempotent_and_still_succeeds(self):
        """Re-adding is a no-op success — the prompt is registered, as asked."""
        self._run(['add', 'eat something'])
        rc, out, _ = self._run(['add', 'eat something'])
        self.assertEqual(rc, 0)
        self.assertEqual(self._registered(), ['eat something'])
        self.assertIn('already', out.lower())

    def test_add_joins_unquoted_words(self):
        """`claudio nudge add eat something` works without shell quoting."""
        rc, _, _ = self._run(['add', 'eat', 'something'])
        self.assertEqual(rc, 0)
        self.assertEqual(self._registered(), ['eat something'])

    def test_add_without_prompt_is_usage_error(self):
        rc, _, err = self._run(['add'])
        self.assertEqual(rc, 1)
        self.assertIn('usage', err)

    def test_add_blank_prompt_is_rejected(self):
        rc, _, err = self._run(['add', '   '])
        self.assertEqual(rc, 1)
        self.assertEqual(self._registered(), [])
        self.assertIn('claudio:', err)


class TestNudgeRemove(_NudgeCLITestCase):

    def test_remove_by_exact_text(self):
        self._run(['add', 'eat something'])
        self._run(['add', 'check the stove'])

        rc, _, _ = self._run(['remove', 'eat something'])
        self.assertEqual(rc, 0)
        self.assertEqual(self._registered(), ['check the stove'])

    def test_remove_by_index(self):
        self._run(['add', 'eat something'])
        self._run(['add', 'check the stove'])

        rc, _, _ = self._run(['remove', '1'])
        self.assertEqual(rc, 0)
        self.assertEqual(self._registered(), ['eat something'])

    def test_remove_prefers_exact_text_over_index(self):
        """A prompt whose literal text is '1' stays removable by text."""
        self._run(['add', 'zero'])
        self._run(['add', '1'])

        rc, _, _ = self._run(['remove', '1'])
        self.assertEqual(rc, 0)
        self.assertEqual(self._registered(), ['zero'])

    def test_rm_is_an_alias(self):
        self._run(['add', 'eat something'])
        rc, _, _ = self._run(['rm', 'eat something'])
        self.assertEqual(rc, 0)
        self.assertEqual(self._registered(), [])

    def test_remove_unknown_fails_clearly(self):
        self._run(['add', 'eat something'])
        rc, _, err = self._run(['remove', 'never registered'])
        self.assertEqual(rc, 1)
        self.assertIn('claudio:', err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(self._registered(), ['eat something'])

    def test_remove_out_of_range_index_fails_clearly(self):
        self._run(['add', 'eat something'])
        rc, _, err = self._run(['remove', '99'])
        self.assertEqual(rc, 1)
        self.assertIn('claudio:', err)
        self.assertEqual(self._registered(), ['eat something'])

    def test_remove_without_target_is_usage_error(self):
        rc, _, err = self._run(['remove'])
        self.assertEqual(rc, 1)
        self.assertIn('usage', err)


class TestNudgeErrors(_NudgeCLITestCase):

    def test_unknown_subcommand_is_usage_error(self):
        rc, _, err = self._run(['frobnicate'])
        self.assertEqual(rc, 1)
        self.assertIn('usage', err)

    def test_missing_agent_name_fails(self):
        """Like cmd_peers, the nudge file is per-agent so a name is required."""
        env_backup = {}
        for k in ('CLAUDIO_AGENT_NAME', 'CMUX_SESSION_NAME'):
            env_backup[k] = os.environ.pop(k, None)
        err = io.StringIO()
        old_err = sys.stderr
        sys.stderr = err
        try:
            rc = cmd_nudge(['list'], state_dir=self.tmpdir, agent_name=None)
        finally:
            sys.stderr = old_err
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v
        self.assertEqual(rc, 1)
        self.assertIn('CLAUDIO_AGENT_NAME', err.getvalue())


class TestNudgeDispatch(unittest.TestCase):
    """`claudio nudge ...` must route to cmd_nudge, not be treated as a session name."""

    def test_main_routes_nudge_to_cmd_nudge(self):
        import claudio.cli as cli
        with patch.object(cli, 'cmd_nudge', return_value=0) as mock_nudge, \
             patch.object(sys, 'argv', ['claudio', 'nudge', 'add', 'eat']):
            with self.assertRaises(SystemExit):
                cli.main()
        mock_nudge.assert_called_once()
        self.assertEqual(mock_nudge.call_args[0][0], ['add', 'eat'])

    def test_main_routes_bare_nudge(self):
        import claudio.cli as cli
        with patch.object(cli, 'cmd_nudge', return_value=0) as mock_nudge, \
             patch.object(sys, 'argv', ['claudio', 'nudge']):
            with self.assertRaises(SystemExit):
                cli.main()
        self.assertEqual(mock_nudge.call_args[0][0], [])

    def test_usage_documents_nudge(self):
        from claudio.cli import USAGE
        self.assertIn('nudge', USAGE)


if __name__ == '__main__':
    unittest.main()
