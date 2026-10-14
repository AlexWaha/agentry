"""epic-0004 / task-0129: registration creates, records and announces the task's worktree.

Real advance.py runs against a sandbox workspace holding a real git repo. Covered:
what the flag governs (creation only), what a refusal leaves behind (nothing), the
run row column and its journal row, the dispatch line, the umbrella and plan-flow
exemptions, and that a registration that cannot finish does not leak a tree.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import approvals
import git_state
import pretool_gate
import state
import tmproot
import worktree
from test_worktree_core import BRANCH, OTHER, TASK, WorkspaceCase

PIPELINES = {"pipelines": {
    "build": {"editing_stages": ["implement", "test", "review"], "stages": [
        {"name": "implement", "owner": "dev"},
        {"name": "test", "owner": "qa-engineer"},
        {"name": "review", "owner": "reviewer"},
        {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit"]},
        {"name": "done", "owner": "user", "terminal": True}]},
    "plan": {"editing_stages": ["formalize"], "stages": [
        {"name": "formalize", "owner": "architect"},
        {"name": "done", "owner": "user", "terminal": True}]}}}


class RegistrationCase(WorkspaceCase):
    """WorkspaceCase plus the pieces advance.py needs to register a task."""

    def setUp(self):
        super().setUp()
        self.config("on", **PIPELINES)
        for target, name, value in (
                (advance, "check_task_ready", lambda task: ""),
                (pretool_gate, "workflow_mode", lambda: pretool_gate.WORKFLOW_SOLO),
                (approvals, "read", lambda: approvals.MANUAL),
                (git_state, "fetch", lambda repo: False)):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def task_file(self, task: str = TASK, head: str = "", folder: str = "backlog") -> None:
        (self.dirs[folder] / f"{task}.md").write_text(
            f"---\nid: 1\n{head}\ncompleted:\n---\n## Acceptance Criteria\n- [ ] one\n",
            encoding="utf-8")

    def advance(self, task: str = TASK, *extra: str) -> dict:
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", task, *extra]), \
                contextlib.redirect_stdout(out):
            self.assertEqual(0, advance.main())
        return json.loads(out.getvalue())

    def journal(self, task: str = TASK, kind: str = "registered") -> list[dict]:
        conn = sqlite3.connect(self.root / ".agentry" / "state" / "events.db")
        try:
            rows = conn.execute("SELECT detail FROM events WHERE task = ? AND kind = ? ORDER BY id",
                                (task, kind)).fetchall()
        finally:
            conn.close()
        return [json.loads(r[0]) for r in rows]

    def branch_exists(self, branch: str = BRANCH) -> bool:
        done = subprocess.run(["git", "-C", str(self.src), "rev-parse", "--verify", "--quiet",
                               f"refs/heads/{branch}"], capture_output=True, text=True)
        return done.returncode == 0

    def tree(self, task: str = TASK) -> Path:
        return self.root / "src-wt" / task


class FlagOnTest(RegistrationCase):

    def test_a_build_task_gets_its_branch_and_tree_and_the_row_records_them(self):
        self.task_file()
        out = self.advance()
        self.assertEqual("started", out["action"])
        self.assertTrue((self.tree() / ".git").is_file())
        self.assertTrue(self.branch_exists())
        self.assertEqual(f"src-wt/{TASK}", self.row()["worktree"])
        self.assertEqual("implement", self.row()["stage"])
        self.assertEqual("active", state.task_dir(TASK))

    def test_the_started_message_ends_with_the_dispatch_line(self):
        self.task_file()
        message = self.advance()["message"]
        line = worktree.dispatch_line(TASK)
        self.assertTrue(line.startswith("Worktree: "))
        self.assertTrue(message.endswith(line), message)
        self.assertIn(f'cd "{self.tree().as_posix()}"', message)

    def test_the_journal_row_carries_the_path(self):
        self.task_file()
        self.advance()
        self.assertEqual([{"type": "feature", "worktree": f"src-wt/{TASK}"}], self.journal())

    def test_the_type_names_the_branch(self):
        self.task_file()
        self.advance(TASK, "--type", "bugfix")
        self.assertTrue(self.branch_exists(f"bugfix/{TASK}"))
        self.assertEqual("bugfix", self.row()["type"])

    def test_the_trunk_checkout_is_left_on_main_and_clean(self):
        self.task_file()
        self.advance()
        self.assertEqual("main", self.git(self.src, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual("", self.git(self.src, "status", "--porcelain"))

    def test_two_tasks_get_two_trees_on_two_branches(self):
        self.task_file()
        self.task_file(OTHER)
        self.advance()
        self.advance(OTHER)
        self.assertEqual({f"src-wt/{TASK}", f"src-wt/{OTHER}"},
                         {r["worktree"] for r in (self.row(), self.row(OTHER))})
        self.assertEqual(f"feature/{TASK}", self.git(self.tree(), "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual(f"feature/{OTHER}",
                         self.git(self.tree(OTHER), "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual(2, worktree.slots_used())

    def test_a_declared_repo_picks_that_repos_tree(self):
        api = self.root / "api"
        api.mkdir()
        self.git(api, "init", "-b", "main")
        self.git(api, "config", "user.email", "t@example.com")
        self.git(api, "config", "user.name", "T")
        self.commit(api, "a.txt", "api")
        self.config("on", repos={"agentry": {"path": "src"}, "api": {"path": "api"}}, **PIPELINES)
        self.task_file(head="repo: api")
        self.advance()
        self.assertEqual(f"api-wt/{TASK}", self.row()["worktree"])
        self.assertTrue((self.root / "api-wt" / TASK / ".git").is_file())

    def test_a_second_run_of_advance_does_not_create_again(self):
        self.task_file()
        self.advance()
        head = self.git(self.tree(), "rev-parse", "HEAD")
        again = self.advance()
        self.assertNotEqual("started", again["action"])
        self.assertEqual(head, self.git(self.tree(), "rev-parse", "HEAD"))


class RefusalTest(RegistrationCase):

    def assertNothingRegistered(self) -> None:
        self.assertIsNone(self.row())
        self.assertEqual("backlog", state.task_dir(TASK))

    def test_an_ambiguous_repo_refuses_and_creates_nothing(self):
        other = self.root / "api"
        other.mkdir()
        self.git(other, "init", "-b", "main")
        self.config("on", repos={"agentry": {"path": "src"}, "api": {"path": "api"}}, **PIPELINES)
        self.task_file()
        out = self.advance()
        self.assertEqual("refused", out["action"])
        self.assertIn("2 candidates", out["message"])
        self.assertNothingRegistered()
        self.assertFalse((self.root / "src-wt").exists())
        self.assertFalse((self.root / "api-wt").exists())

    def test_a_declared_repo_that_resolves_to_nothing_refuses(self):
        self.task_file(head="repo: nowhere")
        out = self.advance()
        self.assertEqual("refused", out["action"])
        self.assertNothingRegistered()

    def test_a_type_that_cannot_name_a_branch_refuses(self):
        self.task_file()
        out = self.advance(TASK, "--type", "../evil")
        self.assertEqual("refused", out["action"])
        self.assertNothingRegistered()
        self.assertFalse((self.root / "src-wt").exists())

    def test_a_leftover_branch_refuses_and_names_the_command_to_run(self):
        self.git(self.src, "branch", BRANCH)
        self.task_file()
        out = self.advance()
        self.assertEqual("refused", out["action"])
        self.assertIn(f"worktree add {self.tree().as_posix()} {BRANCH}", out["message"])
        self.assertNothingRegistered()

    def test_a_stray_directory_at_the_path_refuses_and_is_left_alone(self):
        self.tree().mkdir(parents=True)
        (self.tree() / "mine.txt").write_text("keep", encoding="utf-8")
        self.task_file()
        self.assertEqual("refused", self.advance()["action"])
        self.assertTrue((self.tree() / "mine.txt").is_file())
        self.assertNothingRegistered()

    def test_a_refusal_before_the_tree_is_made_makes_no_tree(self):
        self.task_file()
        with unittest.mock.patch.object(advance, "check_task_ready", return_value="not ready"):
            out = self.advance()
        self.assertEqual("refused", out["action"])
        self.assertFalse((self.root / "src-wt").exists())
        self.assertFalse(self.branch_exists())


class ReuseTest(RegistrationCase):

    def test_exactly_the_same_tree_and_branch_is_reused(self):
        pre = worktree.add(TASK, "feature")
        self.task_file()
        out = self.advance()
        self.assertEqual("started", out["action"])
        self.assertEqual(pre["path"], self.row()["worktree"])

    def test_the_same_tree_on_another_branch_is_refused(self):
        worktree.add(TASK, "bugfix")
        self.task_file()
        out = self.advance(TASK, "--type", "feature")
        self.assertEqual("refused", out["action"])
        self.assertIsNone(self.row())

    def test_a_tree_deleted_by_hand_is_refused_with_the_remove_command_for_that_tree(self):
        worktree.add(TASK, "feature")
        tmproot.rmtree(self.tree())
        self.task_file()
        out = self.advance()
        self.assertEqual("refused", out["action"])
        self.assertIn("its directory is gone", out["message"])
        self.assertIn(f"worktree remove {(self.root / 'src-wt' / TASK).as_posix()}", out["message"])
        self.assertNotIn("prune", out["message"])
        self.assertIsNone(self.row())

    def test_a_failed_insert_removes_a_tree_it_created(self):
        self.task_file()
        with unittest.mock.patch.object(state, "create_run", side_effect=sqlite3.OperationalError("x")):
            with self.assertRaises(sqlite3.OperationalError):
                self.advance()
        self.assertFalse(self.tree().exists())
        self.assertFalse(self.branch_exists())
        self.assertIsNone(self.row())
        self.assertEqual("started", self.advance()["action"])

    def test_a_failed_insert_leaves_a_reused_tree_alone(self):
        worktree.add(TASK, "feature")
        self.commit(self.tree(), "mine.txt", "work in progress")
        self.task_file()
        with unittest.mock.patch.object(state, "create_run", side_effect=sqlite3.OperationalError("x")):
            with self.assertRaises(sqlite3.OperationalError):
                self.advance()
        self.assertTrue((self.tree() / "mine.txt").is_file())
        self.assertTrue(self.branch_exists())


class ExemptionTest(RegistrationCase):

    def test_an_umbrella_registers_with_no_worktree_and_is_otherwise_unchanged(self):
        self.task_file(head=f"epic: epic-0004\numbrella: true\nsubtasks: [{OTHER}]")
        out = self.advance()
        self.assertEqual("started", out["action"])
        row = self.row()
        self.assertEqual("", row["worktree"])
        self.assertEqual("true", row["umbrella"])
        self.assertFalse((self.root / "src-wt").exists())
        self.assertFalse(self.branch_exists())
        self.assertNotIn("Task worktree", out["message"])
        self.assertEqual([{"type": "feature"}], self.journal())

    def test_a_plan_flow_task_gets_no_worktree(self):
        self.task_file()
        out = self.advance(TASK, "--pipeline", "plan")
        self.assertEqual("started", out["action"])
        self.assertEqual("", self.row()["worktree"])
        self.assertFalse((self.root / "src-wt").exists())


class FlagOffTest(RegistrationCase):

    def assertUnchanged(self, mode) -> None:
        self.config(mode, **PIPELINES)
        self.task_file()
        out = self.advance()
        self.assertEqual("started", out["action"])
        self.assertEqual("", self.row()["worktree"])
        self.assertNotIn("Task worktree", out["message"])
        self.assertFalse((self.root / "src-wt").exists())
        self.assertFalse(self.branch_exists())
        self.assertEqual([{"type": "feature"}], self.journal())

    def test_a_block_with_the_flag_false_changes_nothing(self):
        self.assertUnchanged("off")

    def test_no_block_at_all_changes_nothing(self):
        self.assertUnchanged(None)

    def test_a_flag_that_is_not_a_literal_true_changes_nothing(self):
        self.assertUnchanged({"enabled": "true", "max_parallel": 2})

    def test_a_block_that_is_not_an_object_changes_nothing(self):
        self.assertUnchanged("garbage-block")

    def test_a_task_that_has_a_tree_keeps_it_when_the_flag_goes_off(self):
        self.task_file()
        self.advance()
        self.config("off", **PIPELINES)
        self.assertEqual("OK", worktree.status(TASK))
        self.assertEqual(f"src-wt/{TASK}", worktree.recorded(TASK))


class CreateRunTest(WorkspaceCase):

    def create(self, **kwargs) -> dict:
        conn = state.connect()
        try:
            return state.create_run(conn, TASK, "feature", "implement", **kwargs)
        finally:
            conn.close()

    def test_the_column_exists_defaults_empty_and_is_not_a_frozen_field(self):
        self.assertIn(("worktree", "TEXT DEFAULT ''"), state._late_columns())
        self.assertNotIn("worktree", state.FROZEN_FIELDS)
        self.assertEqual("", self.create()["worktree"])

    def test_a_valid_path_is_stored_as_given(self):
        self.assertEqual(f"src-wt/{TASK}", self.create(worktree=f"src-wt/{TASK}")["worktree"])
        self.assertEqual(f"src-wt/{TASK}", self.row()["worktree"])

    def test_a_path_that_is_not_a_tree_of_this_task_is_refused_and_nothing_is_inserted(self):
        for bad in ("../evil", f"src-wt/{OTHER}", "src/task-0130", f"src-wt\\{TASK}",
                    f"C:/src-wt/{TASK}", f"/src-wt/{TASK}", f"other-wt/{TASK}"):
            with self.subTest(worktree=bad):
                with self.assertRaises(ValueError):
                    self.create(worktree=bad)
                self.assertIsNone(self.row())

    def test_a_row_without_a_path_journals_the_same_detail_as_before(self):
        self.create()
        conn = sqlite3.connect(self.root / ".agentry" / "state" / "events.db")
        try:
            detail = conn.execute("SELECT detail FROM events WHERE kind = 'registered'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual('{"type": "feature"}', detail)

    def test_the_column_is_added_to_a_store_that_predates_it(self):
        db = self.root / ".agentry" / "state" / "run.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        old = sqlite3.connect(db)
        old.executescript(
            "CREATE TABLE runs (task TEXT PRIMARY KEY, type TEXT, stage TEXT, stage_status TEXT,"
            " awaiting_human TEXT DEFAULT '', commit_approved INTEGER DEFAULT 0,"
            " push_approved INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,"
            " continuations INTEGER DEFAULT 0, updated TEXT);"
            "INSERT INTO runs (task, type, stage, stage_status) VALUES"
            " ('task-0001', 'feature', 'implement', 'in_progress');")
        old.commit()
        old.close()
        row = self.row("task-0001")
        self.assertEqual("", row["worktree"])
        self.assertEqual(worktree.STATUS_LEGACY, worktree.status("task-0001"))


if __name__ == "__main__":
    unittest.main()
