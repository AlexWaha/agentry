"""pipeline.json `allow_push_to_main` (default false) in pretool_gate.

The flag lets a plain push reach the trunk without the one-shot marker. It must
default to closed, open only on a literal true, never cover a force or delete
push, never cover another protected branch, and leave the solo-mode
`approve.py --trunk-push` marker working exactly as before.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import pretool_gate
import state
from test_pretool_gate_git import TempRepo, denial_reason, gate_state
from test_trunk_push_by_ref import TRUNK_FORBIDDEN, approve_trunk, task_branch_checked_out


def flag(value):
    return unittest.mock.patch.object(pretool_gate, "allow_push_to_main", return_value=value)


class FlagReadingTest(unittest.TestCase):

    def read_with(self, cfg):
        with unittest.mock.patch.object(state, "load_pipeline", return_value=cfg):
            return pretool_gate.allow_push_to_main()

    def test_the_default_is_closed(self):
        self.assertFalse(self.read_with({}))

    def test_only_a_literal_true_opens_it(self):
        self.assertTrue(self.read_with({"allow_push_to_main": True}))
        for junk in (False, None, "true", "yes", 1, [True]):
            with self.subTest(value=junk):
                self.assertFalse(self.read_with({"allow_push_to_main": junk}))

    def test_the_shipped_config_keeps_it_closed(self):
        self.assertFalse(pretool_gate.allow_push_to_main())


class TrunkPushWithTheFlagTest(unittest.TestCase):

    def test_a_trunk_push_is_denied_when_the_flag_is_off_and_no_marker_exists(self):
        with task_branch_checked_out({}) as repo, flag(False):
            code, err = denial_reason("git push origin main", repo.path)
        self.assertEqual(2, code)
        self.assertIn(TRUNK_FORBIDDEN, err)
        self.assertNotIn("allow_push_to_main", err)

    def test_a_trunk_push_is_allowed_when_the_flag_is_on_without_a_marker(self):
        with task_branch_checked_out({}) as repo, flag(True):
            code, err = denial_reason("git push origin main", repo.path)
        self.assertEqual(0, code, err)

    def test_the_flag_does_not_cover_a_force_push_to_the_trunk(self):
        for command in ("git push --force origin main", "git push origin +main",
                        "git push origin :main"):
            with self.subTest(command=command):
                with task_branch_checked_out({}) as repo, flag(True):
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(2, code, err)
                self.assertIn(TRUNK_FORBIDDEN, err)

    def test_the_flag_does_not_cover_a_prune_push(self):
        # --prune deletes remote branches that have no local counterpart.
        for command in ("git push --all --prune origin", "git push --prune origin main",
                        "git push --prune=true origin main"):
            with self.subTest(command=command):
                with task_branch_checked_out({}) as repo, flag(True):
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(2, code, err)
                self.assertIn("--prune", err)

    def test_the_flag_does_not_cover_another_protected_branch(self):
        with task_branch_checked_out({}) as repo, flag(True):
            code, err = denial_reason("git push origin staging", repo.path)
        self.assertEqual(2, code)
        self.assertIn("Push to protected branch 'staging' is forbidden", err)

    def test_the_flag_does_not_waive_a_task_push_approval(self):
        with task_branch_checked_out({"task-9111": {}}) as repo, flag(True):
            code, err = denial_reason("git push origin bugfix/task-9111", repo.path)
        self.assertEqual(2, code)
        self.assertIn("Push for task-9111 is not approved yet", err)


class TrunkPushMarkerStillWorksTest(unittest.TestCase):
    """The solo-mode one-shot trunk push, with the flag at its shipped default."""

    def test_the_marker_allows_one_trunk_push_and_is_spent(self):
        with task_branch_checked_out({"task-9111": {}}) as repo, flag(False):
            marker = approve_trunk()
            self.assertEqual(0, pretool_gate.handle_bash("git push origin main", cwd=repo.path))
            self.assertFalse(marker.exists())
            code, err = denial_reason("git push origin main", repo.path)
        self.assertEqual(2, code)
        self.assertIn(TRUNK_FORBIDDEN, err)

    def test_with_the_flag_on_the_marker_is_still_spent_by_its_own_push(self):
        with task_branch_checked_out({}) as repo, flag(True):
            marker = approve_trunk()
            self.assertEqual(0, pretool_gate.handle_bash("git push origin main", cwd=repo.path))
            self.assertFalse(marker.exists())

    def test_the_marker_is_ignored_in_pr_mode_with_the_flag_off(self):
        with TempRepo() as repo, gate_state(mode="pr", runs={}), flag(False):
            repo.commit()
            approve_trunk()
            code, err = denial_reason("git push origin main", repo.path)
        self.assertEqual(2, code)
        self.assertIn(TRUNK_FORBIDDEN, err)


if __name__ == "__main__":
    unittest.main()
