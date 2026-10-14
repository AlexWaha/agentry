"""task-0124 (M2): the task-file decisions that gate closure and approval borrowing
are recorded in the run row at registration and read from there afterwards.

`active/` is gitignored, so the User's diff never shows the file an agent can rewrite
after the commit approval. These tests register a run, then edit the file, and assert
that the sweep (move_swarm_subtasks), the umbrella debt cover (covering_umbrella) and
the commit-tag branch rule (task_branch) did not notice. Everything runs on a sandbox
copy of the folder layout and run store; the live ones are never touched.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sqlite3
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "pipeline"))
sys.path.insert(0, str(TESTS_DIR))

import state
import tmproot

EPIC = "epic-0042"
UMBRELLA = "task-9400"
LISTED = "task-9401"
LATE = "task-9402"
BRANCH = "feature/task-9400"


class FrozenFrontmatterTest(unittest.TestCase):

    def setUp(self):
        tmp = tmproot.sandbox(self, "frozen_frontmatter_").resolve()
        self.dirs = {name: tmp / name for name in ("backlog", "active", "done")}
        for folder in self.dirs.values():
            folder.mkdir()
        for name, value in (("TASK_DIRS", self.dirs), ("STATE_DIR", tmp / "state"),
                            ("DB_PATH", tmp / "state" / "run.db")):
            patcher = unittest.mock.patch.object(state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- fixtures ----------------------------------------------------------------

    def write(self, folder: str, task: str, head: str = "") -> None:
        """Write the task file into `folder`; a copy in another folder is removed
        first, so a rewrite or a move leaves exactly one file."""
        for other in self.dirs.values():
            (other / f"{task}.md").unlink(missing_ok=True)
        (self.dirs[folder] / f"{task}.md").write_text(
            f"---\nid: 1\n{head}\n---\nbody\n", encoding="utf-8")

    def umbrella_head(self, subtasks: str = LISTED, epic: str = EPIC, flag: str = "true",
                      branch: str = "") -> str:
        extra = f"\nbranch: {branch}" if branch else ""
        return f"epic: {epic}\numbrella: {flag}\nsubtasks: [{subtasks}]{extra}"

    def register(self, task: str, stage: str = "done", **fields) -> None:
        conn = state.connect()
        try:
            state.create_run(conn, task, "feature", stage)
            if fields:
                state.set_fields(conn, task, **fields)
        finally:
            conn.close()

    def where(self, task: str) -> str | None:
        return state.task_dir(task)


class RegistrationRecordsTest(FrozenFrontmatterTest):

    def test_registration_records_epic_umbrella_subtasks_and_branch(self):
        self.write("backlog", UMBRELLA,
                   self.umbrella_head(subtasks=f"{LISTED}, {LATE}", branch=BRANCH))
        self.register(UMBRELLA)
        conn = state.connect()
        try:
            row = state.get_run(conn, UMBRELLA)
        finally:
            conn.close()
        self.assertEqual(EPIC, row["epic"])
        self.assertEqual("true", row["umbrella"])
        self.assertEqual(f"{LISTED} {LATE}", row["subtasks"])
        self.assertEqual(BRANCH, row["branch"])

    def test_a_task_that_declares_none_of_them_records_empty_values(self):
        self.write("backlog", LISTED)
        self.register(LISTED)
        conn = state.connect()
        try:
            row = state.get_run(conn, LISTED)
        finally:
            conn.close()
        for column in state.FROZEN_FIELDS:
            self.assertEqual("", row[column], column)

    def test_a_run_with_no_task_file_records_empty_values(self):
        self.register("task-9499")
        self.assertEqual("", state.task_branch("task-9499"))

    def test_an_umbrella_flag_other_than_true_is_recorded_as_empty(self):
        self.write("backlog", UMBRELLA, self.umbrella_head(flag="false"))
        self.assertEqual("", state.frozen_frontmatter(UMBRELLA)["umbrella"])


class SweepReadsTheRunRowTest(FrozenFrontmatterTest):

    def prepare(self) -> None:
        self.write("active", UMBRELLA, self.umbrella_head())
        self.write("backlog", LISTED, f"epic: {EPIC}")
        self.write("backlog", LATE, f"epic: {EPIC}")
        self.register(UMBRELLA, commit_approved=1)

    def test_a_subtask_added_to_the_file_after_registration_is_not_swept(self):
        self.prepare()
        self.write("active", UMBRELLA, self.umbrella_head(subtasks=f"{LISTED}, {LATE}"))
        self.assertEqual([LISTED], state.move_swarm_subtasks(UMBRELLA))
        self.assertEqual("done", self.where(LISTED))
        self.assertEqual("backlog", self.where(LATE))

    def test_a_subtask_removed_from_the_file_after_registration_is_still_swept(self):
        self.prepare()
        self.write("active", UMBRELLA, self.umbrella_head(subtasks=LATE))
        self.assertEqual([LISTED], state.move_swarm_subtasks(UMBRELLA))

    def test_an_epic_rewritten_in_the_file_after_registration_changes_nothing(self):
        self.prepare()
        self.write("active", UMBRELLA, self.umbrella_head(subtasks=f"{LISTED}, {LATE}",
                                                          epic="epic-0099"))
        self.assertEqual([LISTED], state.move_swarm_subtasks(UMBRELLA))

    def test_the_umbrella_flag_added_to_the_file_after_registration_sweeps_nothing(self):
        self.write("active", UMBRELLA, self.umbrella_head(flag="false"))
        self.write("backlog", LISTED, f"epic: {EPIC}")
        self.register(UMBRELLA, commit_approved=1)
        self.write("active", UMBRELLA, self.umbrella_head(flag="true"))
        self.assertEqual([], state.move_swarm_subtasks(UMBRELLA))
        self.assertEqual("backlog", self.where(LISTED))

    def test_subtasks_added_to_a_file_registered_without_any_sweep_nothing(self):
        self.write("active", UMBRELLA, "")
        self.write("backlog", LISTED, f"epic: {EPIC}")
        self.register(UMBRELLA, commit_approved=1)
        self.write("active", UMBRELLA, self.umbrella_head())
        self.assertEqual([], state.move_swarm_subtasks(UMBRELLA))
        self.assertEqual("backlog", self.where(LISTED))

    def test_a_file_with_no_run_row_sweeps_nothing(self):
        self.write("active", UMBRELLA, self.umbrella_head())
        self.write("backlog", LISTED, f"epic: {EPIC}")
        self.assertEqual([], state.move_swarm_subtasks(UMBRELLA))


class DebtCoverReadsTheRunRowTest(FrozenFrontmatterTest):

    def prepare(self) -> None:
        self.write("backlog", UMBRELLA, self.umbrella_head())
        self.register(UMBRELLA, commit_approved=1)
        self.write("done", UMBRELLA, self.umbrella_head())
        self.write("done", LISTED, f"epic: {EPIC}")
        self.write("done", LATE, f"epic: {EPIC}")

    def test_a_listed_subtask_is_covered(self):
        self.prepare()
        self.assertEqual(UMBRELLA, state.covering_umbrella(LISTED))

    def test_a_subtask_added_to_the_file_after_registration_is_not_covered(self):
        self.prepare()
        self.write("done", UMBRELLA, self.umbrella_head(subtasks=f"{LISTED}, {LATE}"))
        self.assertEqual(UMBRELLA, state.covering_umbrella(LISTED))
        self.assertIsNone(state.covering_umbrella(LATE))

    def test_the_umbrella_flag_cleared_in_the_file_after_registration_changes_nothing(self):
        self.prepare()
        self.write("done", UMBRELLA, self.umbrella_head(flag="false"))
        self.assertEqual(UMBRELLA, state.covering_umbrella(LISTED))

    def test_the_umbrella_epic_rewritten_in_the_file_after_registration_changes_nothing(self):
        self.prepare()
        self.write("done", UMBRELLA, self.umbrella_head(epic="epic-0099"))
        self.assertEqual(UMBRELLA, state.covering_umbrella(LISTED))

    def test_a_file_that_was_not_an_umbrella_at_registration_covers_nothing(self):
        self.write("backlog", UMBRELLA, "")
        self.register(UMBRELLA)
        self.write("done", UMBRELLA, self.umbrella_head())
        self.write("done", LISTED, f"epic: {EPIC}")
        self.assertIsNone(state.covering_umbrella(LISTED))

    def test_an_umbrella_file_with_no_run_row_covers_nothing(self):
        self.write("done", UMBRELLA, self.umbrella_head())
        self.write("done", LISTED, f"epic: {EPIC}")
        self.assertIsNone(state.covering_umbrella(LISTED))

    def test_an_unreadable_run_store_covers_nothing(self):
        self.prepare()
        with unittest.mock.patch.object(state, "connect", side_effect=OSError("locked")):
            self.assertIsNone(state.covering_umbrella(LISTED))


class CommitTagBranchReadsTheRunRowTest(FrozenFrontmatterTest):

    def test_a_branch_added_to_the_file_after_registration_is_not_declared(self):
        self.write("active", LISTED)
        self.register(LISTED, stage="implement")
        self.write("active", LISTED, f"branch: {BRANCH}")
        self.assertEqual("", state.task_branch(LISTED))

    def test_a_branch_changed_in_the_file_after_registration_keeps_the_recorded_one(self):
        self.write("active", LISTED, f"branch: {BRANCH}")
        self.register(LISTED, stage="implement")
        self.write("active", LISTED, "branch: feature/task-9999")
        self.assertEqual(BRANCH, state.task_branch(LISTED))

    def test_a_task_with_no_run_row_declares_no_branch(self):
        self.write("active", LISTED, f"branch: {BRANCH}")
        self.assertEqual("", state.task_branch(LISTED))


class RowsThatPredateTheColumnsTest(FrozenFrontmatterTest):
    """A run store written before task-0124 has no frozen columns. They are added on
    the next connect and the existing rows are frozen once, from the files as they
    read at that moment (the fallback chosen: empty would strip the debt cover from
    the subtasks of every umbrella that is already done)."""

    def legacy_store(self, *tasks: str) -> None:
        state.STATE_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(state.DB_PATH)
        conn.execute("CREATE TABLE runs (task TEXT PRIMARY KEY, type TEXT, stage TEXT, "
                     "stage_status TEXT, awaiting_human TEXT, commit_approved INTEGER "
                     "DEFAULT 0, push_approved INTEGER DEFAULT 0, retries INTEGER DEFAULT 0, "
                     "continuations INTEGER DEFAULT 0, updated TEXT)")
        for task in tasks:
            conn.execute("INSERT INTO runs (task, type, stage, stage_status, awaiting_human, "
                         "commit_approved) VALUES (?, 'feature', 'done', 'in_progress', '', 1)",
                         (task,))
        conn.commit()
        conn.close()

    def test_an_old_done_umbrella_keeps_covering_its_subtasks_after_the_upgrade(self):
        self.write("done", UMBRELLA, self.umbrella_head())
        self.write("done", LISTED, f"epic: {EPIC}")
        self.legacy_store(UMBRELLA)
        self.assertEqual(UMBRELLA, state.covering_umbrella(LISTED))

    def test_the_upgrade_freezes_once_and_later_file_edits_change_nothing(self):
        self.write("done", UMBRELLA, self.umbrella_head())
        self.write("done", LISTED, f"epic: {EPIC}")
        self.write("done", LATE, f"epic: {EPIC}")
        self.legacy_store(UMBRELLA)
        conn = state.connect()
        conn.close()
        self.write("done", UMBRELLA, self.umbrella_head(subtasks=f"{LISTED}, {LATE}"))
        self.assertEqual(UMBRELLA, state.covering_umbrella(LISTED))
        self.assertIsNone(state.covering_umbrella(LATE))

    def test_the_upgrade_adds_the_columns_and_is_idempotent(self):
        self.legacy_store(UMBRELLA)
        for _ in range(2):
            conn = state.connect()
            try:
                columns = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
            finally:
                conn.close()
            self.assertTrue(set(state.FROZEN_FIELDS) <= columns)


if __name__ == "__main__":
    unittest.main()
