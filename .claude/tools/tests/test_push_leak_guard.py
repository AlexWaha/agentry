"""The leak guard on the push path (task-0041, stage L2).

leak_guard.py answers one question about a ref: does it add anything under
`.agentry/` that origin/main does not already have. These tests drive handle_bash()
and check the gate asks that question for every pushed source, in the repo the push
actually runs in, and treats any failure of the check as a leak.

Real repos, deliberately: the answer comes from `git diff origin/main REF`, and a
fixture that stood in for it would test the stand-in. The gate-state helper stubs
the guard out for the older push tests (a throwaway repo has no origin/main); this
module puts the real one back.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
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
import tmproot
from test_pretool_gate_git import TempRepo, denial_reason, gate_state, run_git
from test_trunk_push_by_ref import approve_trunk

BRANCH = "bugfix/task-9141"
TASK = "task-9141"
LEAK = ".agentry/memory/lessons.md"
GUARD_REFUSAL = "Push refused by the leak guard"
REAL_LEAK_CHECK = pretool_gate.leak_check


def real_guard():
    """Undo gate_state's stub: the module under test is the real guard."""
    return unittest.mock.patch.object(pretool_gate, "leak_check", REAL_LEAK_CHECK)


@contextlib.contextmanager
def published_repo():
    """A repo whose origin/main is the seed commit, as if main had been published."""
    with TempRepo() as repo:
        repo.commit()
        remote = tmproot.mkdtemp("gate_remote_")
        try:
            run_git(["init", "-q", "--bare"], str(remote))
            run_git(["remote", "add", "origin", str(remote)], repo.path)
            run_git(["push", "-q", "origin", "main"], repo.path)
            yield repo
        finally:
            tmproot.rmtree(remote)


@contextlib.contextmanager
def guarded(runs: dict | None = None, mode: str = "solo"):
    with published_repo() as repo, gate_state(mode=mode, runs=runs), real_guard():
        yield repo


def work_branch(repo, *files: str) -> None:
    repo.checkout_new(BRANCH)
    for index, name in enumerate(files or ("work.txt",)):
        repo.commit(name, f"work {index}")


class PushLeakGuardTest(unittest.TestCase):
    def test_a_branch_adding_an_internal_file_is_refused_even_when_approved(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, LEAK)
            code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
        self.assertEqual(2, code)
        self.assertIn(GUARD_REFUSAL, err)
        self.assertIn(LEAK, err)

    def test_a_clean_branch_is_allowed(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, "feature.txt")
            code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
        self.assertEqual(0, code, err)

    def test_a_file_origin_already_has_is_not_a_leak(self):
        with TempRepo() as repo, gate_state(mode="solo", runs={TASK: {"push_approved": 1}}), \
                real_guard():
            repo.commit(".agentry/project/stack.md", "published template")
            remote = tmproot.mkdtemp("gate_remote_")
            try:
                run_git(["init", "-q", "--bare"], str(remote))
                run_git(["remote", "add", "origin", str(remote)], repo.path)
                run_git(["push", "-q", "origin", "main"], repo.path)
                work_branch(repo, "feature.txt")
                code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
            finally:
                tmproot.rmtree(remote)
        self.assertEqual(0, code, err)

    def test_the_trunk_push_is_checked_too_and_the_marker_stays_unspent(self):
        with guarded() as repo:
            repo.commit(LEAK, "internal notes")
            marker = approve_trunk()
            code, err = denial_reason("git push origin main", repo.path)
            self.assertEqual(2, code)
            self.assertIn(GUARD_REFUSAL, err)
            self.assertTrue(marker.exists())

    def test_a_clean_trunk_push_still_spends_the_marker(self):
        with guarded() as repo:
            repo.commit("more.txt", "more work")
            marker = approve_trunk()
            code, err = denial_reason("git push origin main", repo.path)
            self.assertEqual(0, code, err)
            self.assertFalse(marker.exists())

    def test_every_pushed_source_is_checked_not_just_the_first(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, "feature.txt")
            repo.checkout_new("bugfix/task-9142", "main")
            repo.commit(LEAK, "internal notes")
            code, err = denial_reason(f"git push origin {BRANCH} bugfix/task-9142", repo.path)
        self.assertEqual(2, code)
        self.assertIn("bugfix/task-9142", err)

    def test_head_is_checked_as_the_branch_it_is(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, LEAK)
            code, err = denial_reason("git push origin HEAD", repo.path)
        self.assertEqual(2, code)
        self.assertIn(LEAK, err)

    def test_a_fan_out_checks_the_local_branches_it_would_publish(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, LEAK)
            repo.checkout_new("scratch", "main")
            approve_trunk()
            code, err = denial_reason("git push --all origin", repo.path)
        self.assertEqual(2, code)
        self.assertIn(GUARD_REFUSAL, err)

    def test_a_deletion_publishes_nothing_and_needs_no_check(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, LEAK)
            code, err = denial_reason("git push origin :scratch2", repo.path)
        self.assertEqual(0, code, err)


class PushLeakGuardRepoTest(unittest.TestCase):
    def test_the_repo_checked_is_the_one_named_by_dash_c_not_the_working_directory(self):
        with guarded({TASK: {"push_approved": 1}}) as clean:
            work_branch(clean, "feature.txt")
            with published_repo() as dirty:
                work_branch(dirty, LEAK)
                code, err = denial_reason(f"git -C {dirty.path} push origin {BRANCH}", clean.path)
                self.assertEqual(2, code)
                self.assertIn(LEAK, err)
                code, err = denial_reason(f"git push origin {BRANCH}", clean.path)
                self.assertEqual(0, code, err)

    def test_a_push_run_from_a_directory_that_is_no_repo_is_refused(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, "feature.txt")
            with unittest.mock.patch.object(pretool_gate, "push_repo", return_value=None):
                code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
        self.assertEqual(2, code)
        self.assertIn("could not be found", err)


class PushLeakGuardFailsClosedTest(unittest.TestCase):
    def deny_with(self, stand_in) -> tuple:
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, "feature.txt")
            with unittest.mock.patch.object(pretool_gate, "leak_check", stand_in):
                return denial_reason(f"git push origin {BRANCH}", repo.path)

    def test_an_exception_from_the_guard_is_a_deny(self):
        def boom(ref, repo):
            raise RuntimeError("guard crashed")

        code, err = self.deny_with(boom)
        self.assertEqual(2, code)
        self.assertIn("the check itself failed (RuntimeError)", err)

    def test_an_exit_two_is_a_deny_with_the_guards_own_words(self):
        code, err = self.deny_with(lambda ref, repo: (2, ["no origin/main to compare against"]))
        self.assertEqual(2, code)
        self.assertIn("could not be checked (no origin/main to compare against)", err)

    def test_an_answer_that_is_not_a_pair_is_a_deny(self):
        for junk in (None, 0, "ok", (), ("0",)):
            with self.subTest(answer=junk):
                code, _ = self.deny_with(lambda ref, repo, junk=junk: junk)
                self.assertEqual(2, code)

    def test_a_missing_origin_main_is_a_deny_with_the_real_guard(self):
        with TempRepo() as repo, gate_state(mode="solo", runs={TASK: {"push_approved": 1}}), \
                real_guard():
            repo.commit()
            repo.checkout_new(BRANCH)
            code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
        self.assertEqual(2, code)
        self.assertIn("no origin/main", err)

    def test_the_guard_module_failing_to_import_is_a_deny(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, "feature.txt")
            with unittest.mock.patch.dict(sys.modules, {"leak_guard": None}):
                code, err = denial_reason(f"git push origin {BRANCH}", repo.path)
        self.assertEqual(2, code)
        self.assertIn("the check itself failed", err)

    def test_a_deny_leaves_no_state_behind(self):
        with guarded({TASK: {"push_approved": 1}}) as repo:
            work_branch(repo, LEAK)
            denial_reason(f"git push origin {BRANCH}", repo.path)
            conn = state.connect()
            try:
                run = state.get_run(conn, TASK)
            finally:
                conn.close()
            self.assertEqual(1, run["push_approved"])
            self.assertFalse(pretool_gate.trunk_push_marker_path().exists())


if __name__ == "__main__":
    unittest.main()
