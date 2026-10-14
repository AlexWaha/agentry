"""task-0124 fix round F3: the closure and merge checks read the frozen run row, the
column backfill is atomic and touches only the columns it adds, and an umbrella covers
debt only once it is done AND approved.

Everything runs on a sandbox copy of the folder layout and the run store.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import threading
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "pipeline"))
sys.path.insert(0, str(TESTS_DIR))

import advance
import git_state
import state
import tmproot
from test_gate_human_output import CRITERIA, NEW_TASK, SPEC, RefusalHarness

TASK = "task-9500"
UMBRELLA = "task-9501"
LISTED = "task-9502"
EPIC = "epic-0050"
BRANCH = "feature/task-9500"
LATER_BRANCH = "feature/task-0120"


class Sandbox(unittest.TestCase):

    def setUp(self):
        tmp = tmproot.sandbox(self, "fix_state_").resolve()
        self.dirs = {name: tmp / name for name in ("backlog", "active", "done")}
        for folder in self.dirs.values():
            folder.mkdir()
        for name, value in (("TASK_DIRS", self.dirs), ("STATE_DIR", tmp / "state"),
                            ("DB_PATH", tmp / "state" / "run.db")):
            patcher = unittest.mock.patch.object(state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, folder: str, task: str, head: str = "") -> None:
        for other in self.dirs.values():
            (other / f"{task}.md").unlink(missing_ok=True)
        (self.dirs[folder] / f"{task}.md").write_text(
            f"---\nid: 1\n{head}\n---\nbody\n", encoding="utf-8")

    def register(self, task: str, stage: str = "done", **fields) -> None:
        conn = state.connect()
        try:
            state.create_run(conn, task, "feature", stage)
            if fields:
                state.set_fields(conn, task, **fields)
        finally:
            conn.close()

    def row(self, task: str) -> dict:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()

    def raw_columns(self) -> set[str]:
        conn = sqlite3.connect(state.DB_PATH)
        try:
            return {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
        finally:
            conn.close()


class ClosureReadsTheFrozenBranchTest(Sandbox):

    def finish(self) -> tuple[dict, unittest.mock.MagicMock]:
        buf = io.StringIO()
        with unittest.mock.patch.object(git_state, "task_report", return_value=[]) as report, \
                unittest.mock.patch.object(state, "move_task", return_value=True), \
                contextlib.redirect_stdout(buf):
            advance.finish_or_wait_for_merge(TASK, self.row(TASK))
        return json.loads(buf.getvalue()), report

    def test_a_branch_added_to_the_file_after_registration_is_not_looked_up(self):
        self.write("active", TASK)
        self.register(TASK)
        self.write("active", TASK, f"branch: {LATER_BRANCH}")
        out, _report = self.finish()
        self.assertEqual("park", out["action"])
        self.assertNotIn(LATER_BRANCH, out["message"])
        self.assertIn("declares no 'branch:'", out["message"])

    def test_a_branch_changed_in_the_file_after_registration_keeps_the_recorded_one(self):
        self.write("active", TASK, f"branch: {BRANCH}")
        self.register(TASK)
        self.write("active", TASK, f"branch: {LATER_BRANCH}")
        out, _report = self.finish()
        self.assertIn(f"'origin/{BRANCH}'", out["message"])
        self.assertNotIn(LATER_BRANCH, out["message"])

    def test_the_recovery_hint_names_the_ceo_decision_not_a_dead_end_file_edit(self):
        self.write("active", TASK)
        self.register(TASK)
        out, _report = self.finish()
        self.assertIn("The User decides", out["message"])
        self.assertIn("re-registers the task", out["message"])
        self.assertIn(f"retags the carrying commit '[{TASK}]'", out["message"])
        self.assertNotIn("BEFORE registration", out["message"])

    def test_task_report_without_a_branch_argument_uses_the_recorded_one(self):
        self.write("active", TASK, f"branch: {BRANCH}")
        self.register(TASK)
        self.write("active", TASK, f"branch: {LATER_BRANCH}")
        seen = []
        with unittest.mock.patch.object(git_state, "repos_for_task", return_value=[Path("r")]), \
                unittest.mock.patch.object(git_state, "declared_ref",
                                           side_effect=lambda repo, branch: seen.append(branch)), \
                unittest.mock.patch.object(git_state, "branch_for", return_value=None), \
                unittest.mock.patch.object(git_state, "task_in_main", return_value=False):
            git_state.task_report(TASK, do_fetch=False)
        self.assertEqual([BRANCH], seen)


class RepoIsFrozenTest(Sandbox):

    def test_registration_records_the_repo(self):
        self.write("active", TASK, "repo: backend")
        self.register(TASK)
        self.assertEqual("backend", self.row(TASK)["repo"])
        self.assertIn("repo", state.FROZEN_FIELDS)

    def test_a_repo_rewritten_in_the_file_after_registration_changes_nothing(self):
        self.write("active", TASK, "repo: backend")
        self.register(TASK)
        self.write("active", TASK, "repo: infra")
        self.assertEqual("backend", state.task_repo(TASK))

    def test_a_repo_added_to_the_file_after_registration_is_not_declared(self):
        self.write("active", TASK)
        self.register(TASK)
        self.write("active", TASK, "repo: infra")
        self.assertIsNone(state.task_repo(TASK))

    def test_a_task_with_no_run_row_reads_the_file(self):
        self.write("backlog", TASK, "repo: backend")
        self.assertEqual("backend", state.task_repo(TASK))

    def test_merge_scoping_follows_the_recorded_repo(self):
        self.write("active", TASK, "repo: backend")
        self.register(TASK)
        self.write("active", TASK, "repo: infra")
        with unittest.mock.patch.object(git_state, "repos_named", return_value=[]) as named:
            git_state.repos_for_task(TASK)
        named.assert_called_once_with("backend")

    def test_the_stack_gate_resolves_the_recorded_repo(self):
        import stack_gate
        self.write("active", TASK, "repo: backend")
        self.register(TASK)
        self.write("active", TASK, "repo: infra")
        entries = {"backend": {"path": ".", "stack": "x", "stages": {}},
                   "infra": {"path": ".", "stack": "y", "stages": {}}}
        seen = []
        with unittest.mock.patch.object(git_state, "repo_entries", return_value=entries), \
                unittest.mock.patch.object(stack_gate, "resolve_key",
                                           side_effect=lambda e, repo: seen.append(repo) or ""), \
                unittest.mock.patch.object(sys, "argv",
                                           ["stack_gate.py", "--task", TASK, "--stage", "test"]), \
                contextlib.redirect_stdout(io.StringIO()):
            with contextlib.suppress(SystemExit):
                stack_gate.main()
        self.assertEqual(["backend"], seen)


class FrozenRefsAreValidatedTest(Sandbox):

    def test_option_like_and_traversal_values_are_stored_empty(self):
        for bad in ("--upload-pack=x", "-n", "a/../b", "has space", "feature/x;rm"):
            with self.subTest(bad=bad):
                self.write("active", TASK, f"branch: {bad}\nrepo: {bad}")
                conn = state.connect()
                try:
                    conn.execute("DELETE FROM runs WHERE task = ?", (TASK,))
                    conn.commit()
                finally:
                    conn.close()
                self.register(TASK)
                row = self.row(TASK)
                self.assertEqual("", row["branch"])
                self.assertEqual("", row["repo"])
                self.assertEqual(["branch", "repo"], state.invalid_frozen_refs(TASK))

    def test_plain_names_are_frozen_as_written(self):
        self.write("active", TASK, "branch: feature/task-9500.v2\nrepo: backend/src")
        self.register(TASK)
        row = self.row(TASK)
        self.assertEqual(("feature/task-9500.v2", "backend/src"), (row["branch"], row["repo"]))
        self.assertEqual([], state.invalid_frozen_refs(TASK))

    def test_an_invalid_repo_is_reported_before_any_run_row_exists(self):
        self.write("backlog", TASK, "repo: -x")
        self.assertEqual(["repo"], state.invalid_frozen_refs(TASK))


class UnreadableStoreFailsClosedTest(Sandbox):

    def test_task_repo_is_none_not_the_file_value_when_the_store_cannot_be_opened(self):
        self.write("active", TASK, "repo: backend")
        with unittest.mock.patch.object(state, "connect", side_effect=sqlite3.DatabaseError("bad")):
            self.assertIsNone(state.task_repo(TASK))

    def test_run_row_reads_through_get_run(self):
        self.write("active", TASK, "repo: backend")
        self.register(TASK)
        with unittest.mock.patch.object(state, "get_run", wraps=state.get_run) as get_run:
            self.assertEqual("backend", state._run_row(TASK)["repo"])
        get_run.assert_called_once()


class BackfillTouchesOnlyTheNewColumnsTest(Sandbox):

    def test_adding_the_repo_column_leaves_the_other_frozen_values_alone(self):
        self.write("active", TASK, f"epic: {EPIC}\nbranch: {BRANCH}\nrepo: backend")
        self.register(TASK)
        conn = sqlite3.connect(state.DB_PATH)
        conn.execute("ALTER TABLE runs DROP COLUMN repo")
        conn.commit()
        conn.close()
        # The file now reads differently from what was recorded at registration.
        self.write("active", TASK, f"epic: epic-0099\nbranch: {LATER_BRANCH}\n"
                                   f"umbrella: true\nsubtasks: [{LISTED}]\nrepo: infra")
        row = self.row(TASK)
        self.assertEqual(EPIC, row["epic"])
        self.assertEqual(BRANCH, row["branch"])
        self.assertEqual("", row["umbrella"])
        self.assertEqual("", row["subtasks"])
        self.assertEqual("infra", row["repo"])

    def test_a_store_with_every_frozen_column_is_not_rewritten_on_connect(self):
        self.write("active", TASK, f"epic: {EPIC}")
        self.register(TASK)
        self.write("active", TASK, "epic: epic-0099")
        self.assertEqual(EPIC, self.row(TASK)["epic"])

    def legacy_store(self, *tasks: str) -> None:
        state.STATE_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(state.DB_PATH)
        conn.execute("CREATE TABLE runs (task TEXT PRIMARY KEY, type TEXT, stage TEXT, "
                     "stage_status TEXT, awaiting_human TEXT, commit_approved INTEGER "
                     "DEFAULT 0, push_approved INTEGER DEFAULT 0, retries INTEGER DEFAULT 0, "
                     "continuations INTEGER DEFAULT 0, updated TEXT)")
        for task in tasks:
            conn.execute("INSERT INTO runs (task, type, stage, stage_status, awaiting_human) "
                         "VALUES (?, 'feature', 'done', 'in_progress', '')", (task,))
        conn.commit()
        conn.close()

    def test_a_failure_part_way_through_the_backfill_rolls_everything_back(self):
        self.write("done", "task-9510", f"epic: {EPIC}")
        self.write("done", "task-9511", f"epic: {EPIC}")
        self.legacy_store("task-9510", "task-9511")
        real = state.frozen_frontmatter
        calls = []

        def flaky(task):
            calls.append(task)
            if len(calls) == 2:
                raise OSError("disk went away")
            return real(task)

        with unittest.mock.patch.object(state, "frozen_frontmatter", side_effect=flaky):
            with self.assertRaises(OSError):
                state.connect()
        columns = self.raw_columns()
        for col in ("pipeline", "plan_approved", *state.FROZEN_FIELDS):
            self.assertNotIn(col, columns, col)

        self.assertEqual(EPIC, self.row("task-9510")["epic"])
        self.assertEqual(EPIC, self.row("task-9511")["epic"])
        self.assertTrue(set(state.FROZEN_FIELDS) <= self.raw_columns())

    def test_concurrent_first_connects_all_succeed_and_the_rows_are_frozen_once(self):
        self.write("done", "task-9510", f"epic: {EPIC}")
        self.legacy_store("task-9510")
        barrier = threading.Barrier(6)
        errors = []

        def first_connect():
            try:
                barrier.wait()
                state.connect().close()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=first_connect) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([], errors)
        self.assertEqual(EPIC, self.row("task-9510")["epic"])
        self.assertTrue(set(state.FROZEN_FIELDS) <= self.raw_columns())


class CoveringUmbrellaNeedsDoneAndApprovedTest(Sandbox):

    def prepare(self, **register) -> None:
        self.write("done", UMBRELLA, f"epic: {EPIC}\numbrella: true\nsubtasks: [{LISTED}]")
        self.write("done", LISTED, f"epic: {EPIC}")
        self.register(UMBRELLA, **register)

    def test_a_done_and_approved_umbrella_covers_its_subtask(self):
        self.prepare(stage="done", commit_approved=1)
        self.assertEqual(UMBRELLA, state.covering_umbrella(LISTED))

    def test_a_done_umbrella_whose_commit_was_never_approved_covers_nothing(self):
        self.prepare(stage="done")
        self.assertIsNone(state.covering_umbrella(LISTED))

    def test_an_approved_umbrella_still_in_flight_covers_nothing_though_its_file_is_in_done(self):
        self.prepare(stage="implement", commit_approved=1)
        self.assertIsNone(state.covering_umbrella(LISTED))


class StartedMessageShowsTheFrozenValuesTest(RefusalHarness):
    spec_status = "approved"

    def test_the_started_message_lists_every_frozen_field(self):
        self.task_file.write_text(
            f"---\nid: {NEW_TASK}\nspec: {SPEC}\nepic: {EPIC}\numbrella: true\n"
            f"subtasks: [{LISTED}, task-9503]\nbranch: {BRANCH}\nrepo: backend\n---\n\n{CRITERIA}",
            encoding="utf-8")
        _code, out = self.advance()
        doc = json.loads(out)
        self.assertEqual("started", doc["action"])
        for expected in (f"epic={EPIC}", "umbrella=true", f"subtasks={LISTED} task-9503",
                         f"branch={BRANCH}", "repo=backend"):
            self.assertIn(expected, doc["message"])

    def test_a_refused_branch_is_named_in_the_started_message(self):
        self.task_file.write_text(
            f"---\nid: {NEW_TASK}\nspec: {SPEC}\nbranch: --upload-pack=x\nrepo: backend\n---\n\n{CRITERIA}",
            encoding="utf-8")
        _code, out = self.advance()
        doc = json.loads(out)
        self.assertEqual("started", doc["action"])
        self.assertIn("NOT frozen", doc["message"])
        self.assertIn("branch=-", doc["message"])
        self.assertIn("repo=backend", doc["message"])

    def test_empty_fields_are_shown_as_a_dash(self):
        _code, out = self.advance()
        doc = json.loads(out)
        self.assertIn("epic=-", doc["message"])
        self.assertIn("repo=-", doc["message"])


if __name__ == "__main__":
    unittest.main()
