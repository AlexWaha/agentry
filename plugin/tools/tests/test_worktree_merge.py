"""epic-0004 / task-0129: freshness before the commit checkpoint, and cleanup after the merge.

Real advance.py, real git, real worktrees. Two halves:

  * review -> ready: a branch the trunk has moved past goes back to `test` with the one
    freshness text, keeping the commit approval; nothing else changes stage on it.
  * done: once git confirms the merge, the file moves, the run closes and THEN the tree
    is removed (no --force); a refusal leaves it and reports it while the task still
    completes; a merge that did not happen never touches it; abandon leaves tree and branch.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import approve
import pretool_gate
import state
import tmproot
import worktree
from test_worktree_core import BRANCH, OTHER, TASK
from test_worktree_registration import PIPELINES, RegistrationCase

GATE_PASSED = {"stage": "review", "has_gate": True, "configured": True, "cmd": "stub",
               "exit": 0, "passed": True, "output": ""}
STALE = "main moved {n} commits: `git merge main` in the worktree"


class MergeCase(RegistrationCase):

    def setUp(self):
        super().setUp()
        self.task_file()
        self.advance()
        self.tree = self.root / "src-wt" / TASK

    def passing_gate(self):
        patcher = unittest.mock.patch.object(advance, "run_gate", return_value=GATE_PASSED)
        patcher.start()
        self.addCleanup(patcher.stop)

    def work(self, name: str = "feature.txt") -> None:
        """A tagged commit on the task's branch, made in its tree."""
        self.commit(self.tree, name, f"[{TASK}] feat: work")

    def merge_into_trunk(self) -> None:
        self.git(self.src, "merge", "--no-ff", "-m", f"Merge branch '{BRANCH}'", BRANCH)

    def at(self, stage: str, **fields) -> None:
        conn = state.connect()
        try:
            state.set_fields(conn, TASK, stage=stage, stage_status=state.ST_IN_PROGRESS, **fields)
        finally:
            conn.close()

    def at_done_merged(self, **fields) -> dict:
        """The run parked at done with the branch merged into the trunk."""
        self.work()
        self.merge_into_trunk()
        self.at("done", commit_approved=1, **fields)
        return self.advance()


class FreshnessTest(MergeCase):

    def setUp(self):
        super().setUp()
        self.passing_gate()
        self.at("review", commit_approved=1)

    def test_a_fresh_branch_passes_review_into_the_checkpoint_stage(self):
        out = self.advance()
        self.assertEqual("advanced", out["action"])
        self.assertEqual("ready", self.row()["stage"])

    def test_a_branch_the_trunk_moved_past_goes_back_to_test_with_the_one_text(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.commit(self.src, "elsewhere2.txt", "and again")
        out = self.advance()
        self.assertEqual("stale", out["action"])
        self.assertEqual("test", out["stage"])
        self.assertEqual("test", self.row()["stage"])
        self.assertIn(STALE.format(n=2), out["message"])
        self.assertIn("Back to 'test'", out["message"])

    def test_the_message_ends_with_the_dispatch_line(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        out = self.advance()
        self.assertTrue(out["message"].endswith(worktree.dispatch_line(TASK)), out["message"])

    def test_the_commit_approval_and_the_run_status_are_kept(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.advance()
        row = self.row()
        self.assertEqual(1, row["commit_approved"])
        self.assertEqual(state.ST_IN_PROGRESS, row["stage_status"])
        self.assertEqual((0, 0), (row["retries"], row["continuations"]))

    def test_after_the_sync_merge_the_run_goes_through(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.assertEqual("stale", self.advance()["action"])
        self.git(self.tree, "merge", "main", "-m", "sync")
        self.at("review")
        self.assertEqual("advanced", self.advance()["action"])
        self.assertEqual("ready", self.row()["stage"])

    def test_work_on_the_branch_alone_is_not_stale(self):
        self.work()
        self.assertEqual("advanced", self.advance()["action"])

    def test_only_the_step_into_the_commit_checkpoint_is_checked(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.at("implement")
        out = self.advance()
        self.assertEqual("advanced", out["action"])
        self.assertEqual("test", self.row()["stage"])
        self.at("test")
        self.assertEqual("advanced", self.advance()["action"])
        self.assertEqual("review", self.row()["stage"])

    def test_a_run_with_no_tree_of_its_own_is_not_checked(self):
        self.sql("UPDATE runs SET worktree = '' WHERE task = ?", TASK)
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.assertEqual("advanced", self.advance()["action"])

    def test_a_branch_that_cannot_be_measured_is_not_called_stale(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        with unittest.mock.patch.object(advance.git_state, "behind_trunk", return_value=None):
            self.assertEqual("advanced", self.advance()["action"])

    def test_the_message_tells_the_agent_to_commit_or_stash_before_the_merge(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        out = self.advance()
        self.assertIn("Commit or stash uncommitted work in the worktree first", out["message"])
        self.assertIn(STALE.format(n=1), out["message"])

    def test_every_send_back_is_counted_and_the_budget_th_one_blocks_the_run(self):
        budget = int(state.load_pipeline().get("retry_budget", 3))
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        for sent in range(1, budget):
            out = self.advance()
            self.assertEqual("stale", out["action"], out)
            self.assertEqual(sent, self.row()["stale_sendbacks"])
            self.assertIn(f"send-back {sent}/{budget}", out["message"])
            self.at("review", commit_approved=1)
        out = self.advance()
        self.assertEqual("blocked", out["action"], out)
        self.assertIn("BLOCKED, needs the User", out["message"])
        row = self.row()
        self.assertEqual((state.ST_BLOCKED, "review", budget), (row["stage_status"], row["stage"],
                                                                row["stale_sendbacks"]))
        self.assertEqual(1, row["commit_approved"])

    def test_a_blocked_run_is_not_sent_back_again(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.sql("UPDATE runs SET stale_sendbacks = 99, stage_status = ? WHERE task = ?",
                 state.ST_BLOCKED, TASK)
        self.assertEqual("blocked", self.advance()["action"])
        self.assertEqual("review", self.row()["stage"])

    def test_a_failing_gate_is_still_a_failing_gate_on_a_stale_branch(self):
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        failed = dict(GATE_PASSED, passed=False, exit=1, output="lint failed")
        with unittest.mock.patch.object(advance, "run_gate", return_value=failed):
            out = self.advance()
        self.assertEqual("gate_failed", out["action"])
        self.assertEqual("review", self.row()["stage"])


class CleanupAfterMergeTest(MergeCase):

    def test_a_confirmed_merge_closes_the_run_then_removes_the_tree(self):
        out = self.at_done_merged()
        self.assertEqual("done", out["action"])
        self.assertIn("Merged into main", out["message"])
        self.assertIn("Worktree removed (the branch is kept)", out["message"])
        self.assertFalse(self.tree.exists())
        self.assertEqual("done", state.task_dir(TASK))
        self.assertEqual("", self.row()["awaiting_human"])
        self.assertNotIn(TASK, self.git(self.src, "worktree", "list"))

    def test_the_branch_and_the_recorded_path_are_kept(self):
        self.at_done_merged()
        self.assertTrue(self.git(self.src, "rev-parse", "--verify", f"refs/heads/{BRANCH}"))
        self.assertEqual(f"src-wt/{TASK}", self.row()["worktree"])

    def test_the_trunk_checkout_is_untouched(self):
        self.at_done_merged()
        self.assertEqual("main", self.git(self.src, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertTrue((self.src / "feature.txt").is_file())

    def test_a_dirty_tree_is_left_and_reported_while_the_task_completes(self):
        (self.tree / "README.md").write_text("uncommitted\n", encoding="utf-8")
        out = self.at_done_merged()
        self.assertEqual("done", out["action"])
        self.assertIn("Worktree LEFT in place: dirty", out["message"])
        self.assertIn(f"worktree.py\" --remove --task {TASK}", out["message"])
        self.assertEqual("uncommitted\n", (self.tree / "README.md").read_text(encoding="utf-8"))
        self.assertEqual("done", state.task_dir(TASK))

    def test_the_next_advance_removes_it_once_it_is_clean(self):
        (self.tree / "README.md").write_text("uncommitted\n", encoding="utf-8")
        self.at_done_merged()
        self.git(self.tree, "checkout", "--", "README.md")
        out = self.advance()
        self.assertIn("Worktree removed", out["message"])
        self.assertFalse(self.tree.exists())

    def test_a_locked_tree_is_left_and_reported(self):
        self.git(self.src, "worktree", "lock", str(self.tree))
        out = self.at_done_merged()
        self.assertEqual("done", out["action"])
        self.assertIn("Worktree LEFT in place: locked", out["message"])
        self.assertTrue(self.tree.is_dir())

    def test_a_tree_held_open_by_a_process_is_left_and_reported(self):
        holder = self.holder(self.tree)
        self.assertIsNone(holder.poll())
        with unittest.mock.patch.object(worktree.time, "sleep"):
            out = self.at_done_merged()
        self.assertEqual("done", out["action"])
        self.assertIn("Worktree LEFT in place: in use", out["message"])
        self.assertEqual("done", state.task_dir(TASK))

    def test_removal_comes_after_the_file_move_and_the_run_close(self):
        order = []
        real_move, real_wait, real_remove = state.move_task, advance._merge_wait, worktree.remove

        def move(*a, **k):
            order.append("move")
            return real_move(*a, **k)

        def wait(*a, **k):
            order.append("close")
            return real_wait(*a, **k)

        def remove(*a, **k):
            order.append("remove")
            return real_remove(*a, **k)

        with unittest.mock.patch.object(state, "move_task", side_effect=move), \
                unittest.mock.patch.object(advance, "_merge_wait", side_effect=wait), \
                unittest.mock.patch.object(worktree, "remove", side_effect=remove):
            self.at_done_merged()
        self.assertEqual(["move", "close", "remove"], order)

    def test_a_cleanup_that_raises_does_not_stop_the_task_completing(self):
        with unittest.mock.patch.object(worktree, "remove", side_effect=RuntimeError("boom")):
            out = self.at_done_merged()
        self.assertEqual("done", out["action"])
        self.assertIn("Worktree cleanup failed", out["message"])
        self.assertEqual("done", state.task_dir(TASK))

    def test_removal_follows_the_recorded_path_even_with_the_flag_off(self):
        self.config("off", **PIPELINES)
        out = self.at_done_merged()
        self.assertIn("Worktree removed", out["message"])

    def test_a_tree_already_deleted_by_hand_is_nothing_to_report(self):
        self.work()
        self.merge_into_trunk()
        tmproot.rmtree(self.tree)
        self.at("done", commit_approved=1)
        out = self.advance()
        self.assertEqual("done", out["action"])
        self.assertNotIn("Worktree", out["message"])
        self.assertNotIn(TASK, self.git(self.src, "worktree", "list"))

    def test_a_run_without_a_tree_says_nothing_about_one(self):
        self.sql("UPDATE runs SET worktree = '' WHERE task = ?", TASK)
        out = self.at_done_merged()
        self.assertEqual("done", out["action"])
        self.assertNotIn("Worktree", out["message"])
        self.assertTrue(self.tree.is_dir())


class NoMergeNoRemovalTest(MergeCase):

    def test_an_unmerged_branch_parks_and_the_tree_is_untouched(self):
        self.work()
        self.at("done", commit_approved=1)
        out = self.advance()
        self.assertEqual("park", out["action"])
        self.assertEqual("merge", self.row()["awaiting_human"])
        self.assertTrue((self.tree / "feature.txt").is_file())
        self.assertEqual("OK", worktree.status(TASK))
        self.assertEqual("active", state.task_dir(TASK))

    def test_an_empty_branch_cannot_be_called_merged_and_keeps_its_tree(self):
        self.at("done", commit_approved=1)
        out = self.advance()
        self.assertEqual("park", out["action"])
        self.assertTrue(self.tree.is_dir())

    def test_repeating_the_park_never_removes_anything(self):
        self.work()
        self.at("done", commit_approved=1)
        for _ in range(3):
            self.assertEqual("park", self.advance()["action"])
        self.assertEqual("OK", worktree.status(TASK))

    def test_a_merge_that_is_only_in_another_tasks_branch_does_not_remove_this_tree(self):
        self.task_file(OTHER)
        self.advance(OTHER)
        self.commit(self.root / "src-wt" / OTHER, "other.txt", f"[{OTHER}] feat: other")
        self.git(self.src, "merge", "--no-ff", "-m", f"Merge branch 'feature/{OTHER}'", f"feature/{OTHER}")
        self.git(self.tree, "merge", "main", "-m", "sync")
        self.work()
        self.at("done", commit_approved=1)
        self.assertEqual("park", self.advance()["action"])
        self.assertTrue(self.tree.is_dir())
        self.assertEqual("OK", worktree.status(OTHER))


class StaleAtMergeTest(MergeCase):
    """A run parked at done for its merge on a tree the trunk has moved past cannot merge
    (the merge gate refuses it) and nothing else would ever re-test it, so advance.py sends
    it back to `test` the way review to ready does."""

    def trunk_moves(self, n: int = 1) -> None:
        for i in range(n):
            self.commit(self.src, f"elsewhere{i}.txt", "someone else merged")

    def test_a_stale_tree_waiting_for_its_merge_goes_back_to_test(self):
        self.work()
        self.trunk_moves(2)
        self.at("done", commit_approved=1, awaiting_human="merge")
        out = self.advance()
        self.assertEqual("stale", out["action"], out)
        self.assertIn(STALE.format(n=2), out["message"])
        self.assertIn("Back to 'test'", out["message"])
        self.assertIn("Commit or stash uncommitted work in the worktree first", out["message"])
        self.assertTrue(out["message"].endswith(worktree.dispatch_line(TASK)))
        row = self.row()
        self.assertEqual(("test", state.ST_IN_PROGRESS, "", 1, 1),
                         (row["stage"], row["stage_status"], row["awaiting_human"],
                          row["commit_approved"], row["stale_sendbacks"]))
        self.assertEqual("active", state.task_dir(TASK))
        self.assertTrue(self.tree.is_dir())

    def test_a_tree_the_trunk_has_not_moved_past_still_just_parks(self):
        self.work()
        self.at("done", commit_approved=1)
        self.assertEqual("park", self.advance()["action"])
        self.assertEqual("done", self.row()["stage"])

    def test_a_merged_branch_is_done_even_though_the_trunk_moved_on_since(self):
        self.work()
        self.merge_into_trunk()
        self.trunk_moves()
        self.at("done", commit_approved=1)
        out = self.advance()
        self.assertEqual("done", out["action"], out)
        self.assertEqual("done", state.task_dir(TASK))

    def test_a_distance_that_cannot_be_measured_parks_as_before(self):
        self.work()
        self.trunk_moves()
        self.at("done", commit_approved=1)
        with unittest.mock.patch.object(advance.git_state, "behind_trunk", return_value=None):
            self.assertEqual("park", self.advance()["action"])

    def test_the_stale_send_back_at_done_is_capped_too(self):
        budget = int(state.load_pipeline().get("retry_budget", 3))
        self.work()
        self.trunk_moves()
        for sent in range(1, budget):
            self.at("done", commit_approved=1)
            self.assertEqual("stale", self.advance()["action"])
            self.assertEqual(sent, self.row()["stale_sendbacks"])
        self.at("done", commit_approved=1)
        out = self.advance()
        self.assertEqual("blocked", out["action"], out)
        self.assertEqual(state.ST_BLOCKED, self.row()["stage_status"])
        self.assertEqual("done", self.row()["stage"])

    def test_after_the_sync_merge_the_run_walks_back_to_the_checkpoint_and_merges(self):
        self.work()
        self.trunk_moves()
        # The sync merge does not touch the task's files, so the change set the commit
        # was approved with survives it (task-0134).
        self.at("done", commit_approved=1,
                approved_tree=pretool_gate.tree_binding(self.tree, snapshot=True))
        self.assertEqual("stale", self.advance()["action"])
        self.git(self.tree, "merge", "main", "-m", "sync")
        self.passing_gate()
        for expected in ("review", "ready"):
            self.assertEqual("advanced", self.advance()["action"])
            self.assertEqual(expected, self.row()["stage"])
        self.assertEqual("park", self.advance()["action"])
        self.assertEqual("done", self.row()["stage"])
        self.merge_into_trunk()
        self.assertEqual("done", self.advance()["action"])


class AbandonTest(MergeCase):

    def abandon(self) -> tuple[int, dict]:
        out = io.StringIO()
        argv = ["approve.py", "--task", TASK, "--abandon", "--reason", "dropped"]
        with unittest.mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            code = approve.main()
        return code, json.loads(out.getvalue())

    def test_abandon_leaves_the_tree_and_the_branch(self):
        self.work()
        code, _ = self.abandon()
        self.assertEqual(0, code)
        self.assertEqual("abandoned", self.row()["stage_status"])
        self.assertTrue((self.tree / "feature.txt").is_file())
        self.assertTrue(self.git(self.src, "rev-parse", "--verify", f"refs/heads/{BRANCH}"))
        self.assertEqual("OK", worktree.status(TASK))

    def test_an_abandoned_run_reports_nothing_to_merge_and_does_not_remove(self):
        self.abandon()
        out = self.advance()
        self.assertIn("abandoned", out["message"])
        self.assertNotIn("Worktree removed", out["message"])
        self.assertTrue(self.tree.is_dir())

    def test_the_sweep_reports_the_abandoned_tree_and_removes_nothing(self):
        self.abandon()
        self.assertEqual([{"path": f"src-wt/{TASK}", "task": TASK, "why": "run abandoned"}],
                         worktree.orphans())
        self.assertTrue(self.tree.is_dir())


if __name__ == "__main__":
    unittest.main()
