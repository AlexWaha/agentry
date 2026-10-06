"""A push is judged by the ref it pushes, not by the branch checked out (task-0111).

Observed live on 2026-10-06: with `bugfix/task-0110` checked out and a recorded
`approve.py --trunk-push`, `git push origin main` was denied as "Push for task-0110
is not approved yet". handle_bash() took the task from HEAD and never looked at the
refspec, so the solo-mode trunk publication needed the trunk checked out, which
forced the orchestrator to wait for (or disturb) an in-flight task's working tree.

Every class below drives handle_bash() against a real git repo. The first group was
red on the unchanged code; the second pins what must NOT loosen.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import json
import sys
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import pretool_gate
import state
from test_pretool_gate_git import TempRepo, denial_reason, gate_state, run_git

TASK = "task-9111"
BRANCH = "bugfix/task-9111"
OTHER_TASK = "task-9112"
OTHER_BRANCH = "bugfix/task-9112"

TRUNK_SPELLINGS = (
    "git push origin main",
    "git push -u origin main",
    "git push origin main:main",
    "git push origin refs/heads/main",
    "git push origin refs/heads/main:refs/heads/main",
)

# Forms whose source is HEAD or @: the trunk only while the trunk is checked out.
HEAD_SOURCE_SPELLINGS = (
    "git push origin HEAD:main",
    "git push origin HEAD:refs/heads/main",
    "git push origin @:main",
)

BOTH_APPROVED = {TASK: {"push_approved": 1}, OTHER_TASK: {"push_approved": 1}}

TASK_NOT_APPROVED = f"Push for {TASK} is not approved yet"
TRUNK_FORBIDDEN = "Push to protected branch 'main' is forbidden"


def approve_trunk() -> Path:
    path = pretool_gate.trunk_push_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trunk": "main", "lane": state.LANE,
                                "approved": "2026-10-06T00:00:00+00:00"}),
                    encoding="utf-8")
    return path


@contextlib.contextmanager
def task_branch_checked_out(runs: dict | None = None):
    """A solo-mode repo with BRANCH checked out. `runs` maps task id to the run
    fields to set; a task absent from it has no row in the run store at all."""
    with TempRepo() as repo, gate_state(mode="solo", runs=runs):
        repo.commit()
        repo.checkout_new(BRANCH)
        yield repo


@contextlib.contextmanager
def with_other_task_branch(runs: dict | None = None):
    """BRANCH checked out, and OTHER_BRANCH existing locally beside the trunk."""
    with task_branch_checked_out(runs) as repo:
        run_git(["branch", OTHER_BRANCH, "main"], repo.path)
        yield repo


@contextlib.contextmanager
def trunk_checked_out(runs: dict | None = None):
    """The same repo with BRANCH existing and the trunk checked out."""
    with task_branch_checked_out(runs) as repo:
        run_git(["checkout", "-q", "main"], repo.path)
        yield repo


class TrunkPushFromTaskBranchTest(unittest.TestCase):
    """Red on the unchanged code: HEAD's task was asked for an approval that the
    pushed ref never needed."""

    def test_the_trunk_push_is_allowed_in_every_ref_form_with_the_task_unapproved(self):
        for command in TRUNK_SPELLINGS:
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {}}) as repo:
                    approve_trunk()
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(0, code, err)

    def test_the_trunk_push_is_allowed_when_the_task_has_no_run_row(self):
        for command in TRUNK_SPELLINGS:
            with self.subTest(command=command):
                with task_branch_checked_out() as repo:
                    approve_trunk()
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(0, code, err)

    def test_the_trunk_push_consumes_the_approval_and_a_second_one_is_refused(self):
        with task_branch_checked_out({TASK: {}}) as repo:
            marker = approve_trunk()
            self.assertEqual(0, pretool_gate.handle_bash("git push origin main", cwd=repo.path))
            self.assertFalse(marker.exists())
            code, err = denial_reason("git push origin main", repo.path)
        self.assertEqual(2, code)
        self.assertIn(TRUNK_FORBIDDEN, err)

    def test_a_trunk_push_without_the_marker_is_refused_as_a_trunk_push(self):
        with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
            code, err = denial_reason("git push origin main", repo.path)
        self.assertEqual(2, code)
        self.assertIn(TRUNK_FORBIDDEN, err)

    def test_a_task_branch_push_from_the_trunk_checkout_needs_that_tasks_approval(self):
        with TempRepo() as repo, gate_state(mode="solo", runs={TASK: {}}):
            repo.commit()
            repo.checkout_new(BRANCH)
            repo.commit("task.txt", "task work")
            repo.checkout_new("scratch", "main")
            code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
        self.assertEqual(2, code)
        self.assertIn(TASK_NOT_APPROVED, err)

    def test_head_as_the_destination_is_the_checked_out_branch_not_an_unnamed_ref(self):
        """`git push origin HEAD` on the trunk landed on main and was not gated at
        all: 'HEAD' was read as a branch called HEAD, which is not protected."""
        with TempRepo() as repo, gate_state(mode="solo"):
            repo.commit()
            code, err = denial_reason("git push origin HEAD", repo.path)
            self.assertEqual(2, code)
            self.assertIn(TRUNK_FORBIDDEN, err)
            marker = approve_trunk()
            self.assertEqual(0, pretool_gate.handle_bash("git push origin HEAD", cwd=repo.path))
            self.assertFalse(marker.exists())

    def test_head_as_the_destination_is_unresolvable_when_the_branch_cannot_be_read(self):
        """Fail closed: guessing what HEAD names is how a protected branch gets pushed."""
        self.assertIsNone(pretool_gate.push_target_branches("git push origin HEAD", ""))
        self.assertIsNone(pretool_gate.push_target_branches("git push origin HEAD:main HEAD", ""))
        self.assertIsNone(pretool_gate.push_target_branches("git push origin HEAD:main", ""))
        self.assertIsNone(pretool_gate.push_target_branches("git push origin @:main", ""))
        self.assertEqual(["main"], pretool_gate.push_target_branches("git push origin main:main", ""))


class MixedPushNeedsBothApprovalsTest(unittest.TestCase):
    """Red on the unchanged code only in the sense that it was refused for the
    wrong reason; the allow cases are what the fix has to get right."""

    MIXED = ("git push origin main bugfix/task-9111",
             "git push origin main HEAD",
             "git push origin HEAD:main HEAD:bugfix/task-9111")

    def test_both_approved_allows_and_spends_the_trunk_marker(self):
        for command in self.MIXED:
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(0, code, err)
                    self.assertFalse(marker.exists())

    def test_the_trunk_approval_alone_is_refused_for_the_task_and_stays_unspent(self):
        for command in self.MIXED:
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code)
                    self.assertIn(TASK_NOT_APPROVED, err)
                    self.assertTrue(marker.exists())

    def test_the_task_approval_alone_is_refused_for_the_trunk(self):
        for command in self.MIXED:
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(2, code)
                self.assertIn(TRUNK_FORBIDDEN, err)

    def test_neither_approval_is_refused(self):
        with task_branch_checked_out({TASK: {}}) as repo:
            code, _ = denial_reason("git push origin main bugfix/task-9111", repo.path)
        self.assertEqual(2, code)

    def test_from_the_trunk_checkout_the_trunk_approval_alone_does_not_cover_the_task(self):
        """HEAD names no task there, so the old code let the task branch ride on
        the trunk approval."""
        command = f"git push origin main {BRANCH}"
        with trunk_checked_out({TASK: {}}) as repo:
            marker = approve_trunk()
            code, err = denial_reason(command, repo.path)
            self.assertEqual(2, code)
            self.assertIn(TASK_NOT_APPROVED, err)
            self.assertTrue(marker.exists())

    def test_from_the_trunk_checkout_both_approvals_allow_and_spend_the_marker(self):
        command = f"git push origin main {BRANCH}"
        with trunk_checked_out({TASK: {"push_approved": 1}}) as repo:
            marker = approve_trunk()
            code, err = denial_reason(command, repo.path)
            self.assertEqual(0, code, err)
            self.assertFalse(marker.exists())


class TaskPushStillNeedsItsApprovalTest(unittest.TestCase):
    """Must stay denied, as it was before the fix."""

    TASK_PUSHES = ("git push origin bugfix/task-9111",
                   "git push -u origin bugfix/task-9111",
                   "git push origin refs/heads/bugfix/task-9111",
                   "git push origin HEAD:bugfix/task-9111",
                   "git push origin HEAD",
                   "git push")

    def test_an_unapproved_task_push_is_refused_in_every_form(self):
        for command in self.TASK_PUSHES:
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {}}) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(2, code)
                self.assertIn(TASK_NOT_APPROVED, err)

    def test_an_approved_task_push_is_allowed_in_every_form(self):
        for command in self.TASK_PUSHES:
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(0, code, err)

    def test_a_trunk_marker_does_not_stand_in_for_the_task_approval(self):
        with task_branch_checked_out({TASK: {}}) as repo:
            marker = approve_trunk()
            code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
            self.assertEqual(2, code)
            self.assertIn(TASK_NOT_APPROVED, err)
            self.assertTrue(marker.exists())

    def test_a_task_push_with_no_run_row_is_refused_closed(self):
        with task_branch_checked_out() as repo:
            code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
        self.assertEqual(2, code)
        self.assertIn("no row in the run store", err)

    def test_every_named_task_branch_needs_its_own_approval(self):
        command = f"git push origin {BRANCH} {OTHER_BRANCH}"
        with task_branch_checked_out({TASK: {"push_approved": 1}, OTHER_TASK: {}}) as repo:
            code, err = denial_reason(command, repo.path)
        self.assertEqual(2, code)
        self.assertIn(f"Push for {OTHER_TASK} is not approved yet", err)
        with task_branch_checked_out({TASK: {"push_approved": 1},
                                      OTHER_TASK: {"push_approved": 1}}) as repo:
            code, err = denial_reason(command, repo.path)
        self.assertEqual(0, code, err)

    def test_a_non_task_destination_falls_back_to_the_checked_out_tasks_approval(self):
        """Renaming the destination must not shed the approval the work needs."""
        for command in ("git push origin HEAD:scratch-name", "git push origin scratch-name"):
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {}}) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(2, code)
                self.assertIn(TASK_NOT_APPROVED, err)
                with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(0, code, err)

    def test_a_commit_chained_to_a_trunk_push_still_needs_the_commit_approval(self):
        command = "git commit -m x && git push origin main"
        with task_branch_checked_out({TASK: {}}) as repo:
            approve_trunk()
            code, err = denial_reason(command, repo.path)
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK} is not approved yet", err)
        with task_branch_checked_out({TASK: {"commit_approved": 1}}) as repo:
            approve_trunk()
            code, err = denial_reason(command, repo.path)
        self.assertEqual(0, code, err)


class SourceAwareTrunkPushTest(unittest.TestCase):
    """A protected destination skips the checked-out task only when the SOURCE is
    the trunk. `HEAD:main` from a task checkout ships that task's unmerged tip as
    remote main, so it needs the task's approval as well as the marker."""

    def test_head_source_on_the_trunk_checkout_is_the_trunk_and_needs_only_the_marker(self):
        for command in HEAD_SOURCE_SPELLINGS:
            with self.subTest(command=command):
                with trunk_checked_out({TASK: {}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(0, code, err)
                    self.assertFalse(marker.exists())

    def test_head_source_on_a_task_checkout_needs_both_approvals(self):
        for command in HEAD_SOURCE_SPELLINGS:
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code)
                    self.assertIn(TASK_NOT_APPROVED, err)
                    self.assertTrue(marker.exists())
                with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code)
                    self.assertIn(TRUNK_FORBIDDEN, err)
                with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(0, code, err)
                    self.assertFalse(marker.exists())

    def test_a_named_task_branch_as_the_source_needs_its_own_task_not_the_checked_out_one(self):
        command = f"git push origin {OTHER_BRANCH}:main"
        with with_other_task_branch({TASK: {"push_approved": 1}, OTHER_TASK: {}}) as repo:
            marker = approve_trunk()
            code, err = denial_reason(command, repo.path)
            self.assertEqual(2, code)
            self.assertIn(f"Push for {OTHER_TASK} is not approved yet", err)
            self.assertTrue(marker.exists())
        with with_other_task_branch({TASK: {}, OTHER_TASK: {"push_approved": 1}}) as repo:
            marker = approve_trunk()
            code, err = denial_reason(command, repo.path)
            self.assertEqual(0, code, err)
            self.assertFalse(marker.exists())

    def test_a_source_that_names_no_task_falls_back_to_the_checked_out_task(self):
        command = "git push origin HEAD~1:main"
        with task_branch_checked_out({TASK: {}}) as repo:
            approve_trunk()
            code, err = denial_reason(command, repo.path)
        self.assertEqual(2, code)
        self.assertIn(TASK_NOT_APPROVED, err)

    def test_a_trunk_source_to_a_non_protected_destination_needs_the_checked_out_task(self):
        """The marker covers a protected destination only: `main:other` publishes
        nothing the trunk approval was given for."""
        for command in ("git push origin main:other",
                        "git push origin refs/heads/main:refs/heads/x"):
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code)
                    self.assertIn(TASK_NOT_APPROVED, err)
                    self.assertTrue(marker.exists())
                with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(0, code, err)

    def test_a_trunk_source_to_the_trunk_stays_marker_only_from_a_task_checkout(self):
        for command in ("git push origin main:main", "git push origin main"):
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(0, code, err)
                    self.assertFalse(marker.exists())

    def test_a_deletion_from_a_task_checkout_needs_the_checked_out_tasks_approval(self):
        """`:scratch` has an empty source; `--delete scratch` already needed the task."""
        for command in ("git push origin :scratch", "git push --delete origin scratch"):
            with self.subTest(command=command):
                with task_branch_checked_out({TASK: {}}) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(2, code)
                self.assertIn(TASK_NOT_APPROVED, err)
                with task_branch_checked_out({TASK: {"push_approved": 1}}) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(0, code, err)

    def test_a_source_that_names_no_task_anywhere_is_refused_and_leaves_the_marker(self):
        with TempRepo() as repo, gate_state(mode="solo"):
            repo.commit()
            repo.checkout_new("scratch")
            marker = approve_trunk()
            for command in ("git push origin scratch:main", "git push origin HEAD:main"):
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code)
                    self.assertIn("names no task", err)
                    self.assertTrue(marker.exists())


class SourceAndDestinationTasksTest(unittest.TestCase):
    """A pair needs the task of its source AND the task of its destination."""

    COMMAND = f"git push origin HEAD:{OTHER_BRANCH}"

    def test_head_to_another_task_branch_needs_both_tasks(self):
        cases = (({TASK: {"push_approved": 1}, OTHER_TASK: {}}, OTHER_TASK, 2),
                 ({TASK: {}, OTHER_TASK: {"push_approved": 1}}, TASK, 2),
                 ({TASK: {"push_approved": 1}, OTHER_TASK: {"push_approved": 1}}, "", 0))
        for runs, unapproved, expected in cases:
            with self.subTest(unapproved=unapproved):
                with with_other_task_branch(runs) as repo:
                    code, err = denial_reason(self.COMMAND, repo.path)
                self.assertEqual(expected, code, err)
                if unapproved:
                    self.assertIn(f"Push for {unapproved} is not approved yet", err)


class ForcedRefspecTest(unittest.TestCase):
    """A leading + is a force push, so the trunk marker never covers it."""

    FORCED = ("git push origin +main", "git push origin +main:main",
              "git push origin +HEAD:main", "git push origin +refs/heads/main")

    def test_a_plus_refspec_is_destructive(self):
        for spec in ("+main", "+HEAD:main", "+a:b"):
            with self.subTest(spec=spec):
                self.assertIsNotNone(pretool_gate.push_destructive_reason(["origin", spec]))
        for spec in ("main", "HEAD:main", "a:b"):
            with self.subTest(spec=spec):
                self.assertIsNone(pretool_gate.push_destructive_reason(["origin", spec]))

    def test_a_forced_trunk_push_is_refused_even_with_the_marker(self):
        for command in self.FORCED:
            with self.subTest(command=command):
                with trunk_checked_out({TASK: {}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code)
                    self.assertIn(TRUNK_FORBIDDEN, err)
                    self.assertIn("forced", err)
                    self.assertTrue(marker.exists())


class FanOutPushTest(unittest.TestCase):
    """--all, --branches, --mirror and a branch wildcard push the trunk and every
    task branch, so they need the marker and every task's approval."""

    FAN_OUT = ("git push --all origin",
               "git push origin --branches",
               "git push origin 'refs/heads/*:refs/heads/*'")

    def test_a_fan_out_push_counts_as_pushing_the_trunk(self):
        for command in self.FAN_OUT:
            with self.subTest(command=command):
                with with_other_task_branch(BOTH_APPROVED) as repo:
                    code, err = denial_reason(command, repo.path)
                self.assertEqual(2, code)
                self.assertIn(TRUNK_FORBIDDEN, err)

    def test_a_fan_out_push_needs_every_local_task_branch_approved(self):
        for command in self.FAN_OUT:
            with self.subTest(command=command):
                with with_other_task_branch({TASK: {"push_approved": 1}, OTHER_TASK: {}}) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code)
                    self.assertIn(f"Push for {OTHER_TASK} is not approved yet", err)
                    self.assertTrue(marker.exists())

    def test_a_fan_out_push_with_the_marker_and_every_task_approved_is_allowed(self):
        for command in self.FAN_OUT:
            with self.subTest(command=command):
                with with_other_task_branch(BOTH_APPROVED) as repo:
                    marker = approve_trunk()
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(0, code, err)
                    self.assertFalse(marker.exists())

    def test_a_mirror_or_forced_wildcard_is_refused_even_with_everything_approved(self):
        for command in ("git push --mirror origin",
                        "git push origin '+refs/heads/*:refs/heads/*'"):
            with self.subTest(command=command):
                with with_other_task_branch(BOTH_APPROVED) as repo:
                    marker = approve_trunk()
                    code, _ = denial_reason(command, repo.path)
                    self.assertEqual(2, code)
                    self.assertTrue(marker.exists())

    def test_a_wildcard_over_tags_is_not_a_branch_fan_out(self):
        self.assertFalse(pretool_gate.push_fans_out(["origin", "refs/tags/*:refs/tags/*"]))
        self.assertTrue(pretool_gate.push_fans_out(["origin", "refs/heads/*:refs/heads/*"]))

    def test_a_fan_out_whose_branches_cannot_be_listed_is_unresolvable(self):
        for command in ("git push --all origin", "git push --mirror origin"):
            with self.subTest(command=command):
                self.assertIsNone(pretool_gate.push_target_branches(command, "main"))
                self.assertEqual(["main", BRANCH], pretool_gate.push_target_branches(
                    command, "main", ["main", BRANCH]))
                # a wildcard spec is expanded, never kept as a branch named "*"
                self.assertEqual(["main"], pretool_gate.push_target_branches(
                    "git push origin 'refs/heads/*:refs/heads/*'", "main", ["main"]))
                # the configured trunk is counted even when no local branch has its name
                self.assertEqual(["scratch", "main"], pretool_gate.push_target_branches(
                    command, "scratch", ["scratch"]))


if __name__ == "__main__":
    unittest.main()
