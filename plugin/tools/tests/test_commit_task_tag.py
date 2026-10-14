"""task-0059: the commit gate judges a commit by its `[task-NNNN]` tag.

Before, `git commit` was judged by the BRANCH name alone. With two tasks worked
on one branch - what a preemption leaves behind - an approved task could not
commit because an unfinished sibling shared its branch, and when the commit
message named one task while the branch named another the gate answered about
the wrong one without saying so.

Real git repositories, a real run.db in a sandbox, and handle_bash() end to end.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import subprocess
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

TASK_A = "task-0100"   # approved, rides on task B's branch
TASK_B = "task-0200"   # owns the branch, NOT approved
BRANCH = "feature/task-0200"


def git(args: list, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                   timeout=20, check=True)


def task_file(directory: Path, task: str, branch: str = "") -> None:
    head = f"---\nid: {task}\n" + (f"branch: {branch}\n" if branch else "") + "---\n"
    (directory / f"{task}.md").write_text(head + "body\n", encoding="utf-8")


class CommitTagGateTest(unittest.TestCase):

    def setUp(self):
        sandbox = tmproot.sandbox(self, "commit_tag_")
        self.repo = sandbox / "repo"
        self.repo.mkdir()
        git(["init", "-q", "-b", "main"], self.repo)
        git(["config", "user.email", "qa@example.com"], self.repo)
        git(["config", "user.name", "QA"], self.repo)
        (self.repo / "seed.txt").write_text("x", encoding="utf-8")
        git(["add", "seed.txt"], self.repo)
        git(["commit", "-q", "-m", "seed"], self.repo)
        git(["checkout", "-q", "-b", BRANCH], self.repo)

        self.tasks = sandbox / "tasks"
        for name in ("backlog", "active", "done"):
            (self.tasks / name).mkdir(parents=True)
        self.dirs = {name: self.tasks / name for name in ("backlog", "active", "done")}

        for patcher in (
                unittest.mock.patch.object(state, "STATE_DIR", sandbox),
                unittest.mock.patch.object(state, "DB_PATH", sandbox / "run.db"),
                unittest.mock.patch.object(state, "load_pipeline", return_value={}),
                unittest.mock.patch.dict(state.TASK_DIRS, self.dirs)):
            patcher.start()
            self.addCleanup(patcher.stop)

        # The branch each task declares is recorded when its run is created.
        task_file(self.dirs["active"], TASK_A, branch=BRANCH)
        task_file(self.dirs["active"], TASK_B)
        conn = state.connect()
        try:
            state.create_run(conn, TASK_A, "feature", "ready")
            state.set_fields(conn, TASK_A, commit_approved=1)
            state.create_run(conn, TASK_B, "feature", "ready")
        finally:
            conn.close()

    def gate(self, command: str) -> tuple:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pretool_gate.handle_bash(command, cwd=str(self.repo))
        return code, err.getvalue()

    def set_run(self, task: str, **fields) -> None:
        conn = state.connect()
        try:
            state.set_fields(conn, task, **fields)
        finally:
            conn.close()

    # -- the two-tasks-one-branch case, end to end ---------------------------

    def test_approved_task_commits_while_its_unapproved_sibling_shares_the_branch(self):
        code, err = self.gate(f'git commit -m "[{TASK_A}] finish A"')
        self.assertEqual(code, 0, err)

    def test_unapproved_sibling_is_still_refused_when_it_tags_its_own_commit(self):
        code, err = self.gate(f'git commit -m "[{TASK_B}] half of B"')
        self.assertEqual(code, 2)
        self.assertIn(f"Commit for {TASK_B} is not approved yet", err)

    def test_untagged_commit_is_judged_by_the_branch_as_before(self):
        code, err = self.gate('git commit -m "no tag here"')
        self.assertEqual(code, 2)
        self.assertIn(f"Commit for {TASK_B} is not approved yet", err)
        self.set_run(TASK_B, commit_approved=1)
        self.assertEqual(self.gate('git commit -m "no tag here"')[0], 0)

    def test_tag_matching_the_branch_task_is_the_old_behaviour(self):
        self.assertEqual(self.gate(f'git commit -m "[{TASK_B}] x"')[0], 2)
        self.set_run(TASK_B, commit_approved=1)
        self.assertEqual(self.gate(f'git commit -m "[{TASK_B}] x"')[0], 0)

    # -- tag and branch disagree: say so, name both -------------------------

    def test_unapproved_tagged_task_is_refused_and_the_disagreement_is_named(self):
        self.set_run(TASK_A, commit_approved=0)
        code, err = self.gate(f'git commit -m "[{TASK_A}] x"')
        self.assertEqual(code, 2)
        self.assertIn(f"Commit for {TASK_A} is not approved yet", err)
        self.assertIn(f"tagged [{TASK_A}]", err)
        self.assertIn(TASK_B, err)

    def test_tag_naming_a_task_that_does_not_declare_this_branch_is_denied(self):
        self.set_run(TASK_A, branch="")  # registered without a `branch:` line
        code, err = self.gate(f'git commit -m "[{TASK_A}] x"')
        self.assertEqual(code, 2)
        for needle in (TASK_A, TASK_B, BRANCH, "did not declare"):
            self.assertIn(needle, err)

    def test_tag_declaring_a_different_branch_is_denied(self):
        self.set_run(TASK_A, branch="feature/task-0999")
        code, err = self.gate(f'git commit -m "[{TASK_A}] x"')
        self.assertEqual(code, 2)
        self.assertIn("did not declare", err)

    def test_tag_naming_a_task_with_no_run_row_is_denied_with_both_ids(self):
        code, err = self.gate('git commit -m "[task-0777] x"')
        self.assertEqual(code, 2)
        for needle in ("task-0777", TASK_B, "no run row"):
            self.assertIn(needle, err)

    def test_tag_naming_a_finished_task_is_denied(self):
        self.set_run(TASK_A, stage="done")
        code, err = self.gate(f'git commit -m "[{TASK_A}] x"')
        self.assertEqual(code, 2)
        self.assertIn("already done", err)
        self.assertIn(TASK_B, err)

    # -- where the tag is read from ------------------------------------------

    def test_tag_in_a_message_file_is_read(self):
        (self.repo / "msg.txt").write_text(f"[{TASK_A}] from a file\n\nbody\n", encoding="utf-8")
        self.assertEqual(self.gate("git commit -F msg.txt")[0], 0)

    def test_tag_glued_into_an_am_cluster_is_read(self):
        self.assertEqual(self.gate(f'git commit -am "[{TASK_A}] x"')[0], 0)

    def test_long_message_option_is_read(self):
        self.assertEqual(self.gate(f'git commit --message "[{TASK_A}] x"')[0], 0)
        self.assertEqual(self.gate(f'git commit --message="[{TASK_A}] x"')[0], 0)

    def test_a_cited_task_in_the_body_does_not_repoint_the_commit(self):
        message = f"[{TASK_B}] subject\n\ndepends on\n[{TASK_A}]"
        # Only a tag LEADING A LINE counts, and only the first one decides.
        code, err = self.gate(f'git commit -m "{message}"')
        self.assertEqual(code, 2)
        self.assertIn(f"Commit for {TASK_B}", err)

    def test_a_second_untagged_commit_in_the_same_command_is_still_judged(self):
        code, err = self.gate(f'git commit -m "[{TASK_A}] a" && git commit -m "b"')
        self.assertEqual(code, 2)
        self.assertIn(f"Commit for {TASK_B} is not approved yet", err)

    def test_unreadable_message_file_falls_back_to_the_branch(self):
        code, err = self.gate("git commit -F missing.txt")
        self.assertEqual(code, 2)
        self.assertIn(f"Commit for {TASK_B}", err)


class CommitTasksTest(unittest.TestCase):
    """The parser on its own, no repo and no run store."""

    def tasks(self, command: str, branch: str = BRANCH) -> list:
        return pretool_gate.commit_tasks(command, branch)

    def test_tag_wins_over_the_branch(self):
        self.assertEqual(self.tasks('git commit -m "[task-0100] x"'), ["task-0100"])

    def test_no_tag_falls_back_to_the_branch_task(self):
        self.assertEqual(self.tasks('git commit -m "x"'), [TASK_B])

    def test_mixed_case_tag_is_read_as_the_lowercase_task_id(self):
        self.assertEqual(self.tasks('git commit -m "[Task-0100] x"'), ["task-0100"])

    def test_two_commits_yield_two_entries(self):
        self.assertEqual(
            self.tasks('git commit -m "[task-0100] a" && git commit -m "b"'),
            ["task-0100", TASK_B])

    def test_tag_on_a_later_message_option_is_found(self):
        self.assertEqual(self.tasks('git commit -m "subject" -m "[task-0100] body"'),
                         ["task-0100"])

    def test_unrelated_branch_and_no_tag_gives_empty_task(self):
        self.assertEqual(self.tasks('git commit -m "x"', branch="main"), [""])


if __name__ == "__main__":
    unittest.main()
