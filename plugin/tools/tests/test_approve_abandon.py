"""task-0122: `approve.py --task T --abandon --reason TEXT` retires a task without a merge.

The User drops a task for good; the run goes to stage `done` with stage_status
`abandoned`, the file moves to done/, git is never touched, and the handoff and
memory debt readers owe nothing for it. Everything runs on a sandbox copy of the
folder layout and run store (tmproot.sandbox); the live ones are never touched.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "memory"))
sys.path.insert(0, str(TESTS_DIR.parent / "pipeline"))
sys.path.insert(0, str(TESTS_DIR))

import advance
import agent_gate
import approve
import events
import git_state
import handoff
import state
import tmproot
import update

TASK = "task-9500"
OTHER = "task-9501"
REASON = "User dropped the work"
CMD = f"python plugin/tools/pipeline/approve.py --task {TASK} --abandon --reason x"
WRAPPED = f"cd .claude && env X=1 python tools/pipeline/approve.py --task {TASK} --abandon --reason x"


class AbandonCase(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "approve_abandon_").resolve()
        tasks = self.root / ".agentry" / "tasks"
        self.dirs = {"backlog": tasks / "backlog", "active": tasks / "active",
                     "done": tasks / "done"}
        self.handoffs = tasks / "handoffs"
        self.stamps = self.root / ".agentry" / "state" / "memory"
        for folder in (*self.dirs.values(), self.handoffs, self.stamps):
            folder.mkdir(parents=True)
        state_dir = self.root / ".agentry" / "state"
        config = lambda: {"enabled": True, "baseline": ""}  # noqa: E731
        patches = [
            unittest.mock.patch.object(state, "ROOT", self.root),
            unittest.mock.patch.object(state, "TASK_DIRS", self.dirs),
            unittest.mock.patch.object(state, "STATE_DIR", state_dir),
            unittest.mock.patch.object(state, "DB_PATH", state_dir / "run.db"),
            unittest.mock.patch.object(events, "STATE_DIR", state_dir),
            unittest.mock.patch.object(update, "DONE_DIR", self.dirs["done"]),
            unittest.mock.patch.object(update, "STAMP_DIR", self.stamps),
            unittest.mock.patch.object(handoff, "cfg", side_effect=config),
            unittest.mock.patch.object(update, "cfg", side_effect=config),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- fixtures ----------------------------------------------------------------

    def put(self, folder: str, task: str = TASK, head: str = "") -> Path:
        path = self.dirs[folder] / f"{task}.md"
        path.write_text(f"---\nid: 1\ncompleted:\n{head}\n---\nbody\n", encoding="utf-8")
        return path

    def register(self, task: str = TASK, stage: str = "implement", **fields) -> None:
        conn = state.connect()
        try:
            state.create_run(conn, task, "feature", stage)
            if fields:
                state.set_fields(conn, task, **fields)
        finally:
            conn.close()

    def row(self, task: str = TASK) -> dict:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()

    def cli(self, *argv) -> tuple:
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["approve.py", *argv]), \
                contextlib.redirect_stdout(out):
            code = approve.main()
        return code, json.loads(out.getvalue())

    def abandon(self, reason: str = REASON, task: str = TASK) -> tuple:
        return self.cli("--task", task, "--abandon", "--reason", reason)

    def journal(self, kind: str) -> list:
        if not events.db_path().exists():
            return []
        conn = sqlite3.connect(events.db_path())
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM events WHERE task = ? AND kind = ? ORDER BY id",
                                (TASK, kind)).fetchall()
        finally:
            conn.close()
        return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]


class AbandonRunTest(AbandonCase):

    def test_an_active_run_closes_as_done_abandoned_with_the_reason_and_the_file_moves(self):
        self.put("active")
        self.register(awaiting_human="commit")
        code, out = self.abandon()
        self.assertEqual(0, code, out)
        row = self.row()
        self.assertEqual("done", row["stage"])
        self.assertEqual(state.ST_ABANDONED, row["stage_status"])
        self.assertEqual(REASON, row["abandon_reason"])
        self.assertEqual("", row["awaiting_human"])
        self.assertEqual("done", state.task_dir(TASK))
        self.assertFalse((self.dirs["active"] / f"{TASK}.md").exists())
        filed = (self.dirs["done"] / f"{TASK}.md").read_text(encoding="utf-8")
        self.assertIn(f"\nabandoned: {json.dumps(REASON)}\n", filed)
        self.assertRegex(filed, r"(?m)^completed:$")

    def test_exactly_one_abandoned_journal_row_carries_the_reason_and_no_transition_row(self):
        self.put("active")
        self.register()
        self.abandon()
        rows = self.journal("abandoned")
        self.assertEqual(1, len(rows))
        self.assertEqual("implement", rows[0]["from_stage"])
        self.assertEqual("done", rows[0]["stage"])
        self.assertEqual(REASON, rows[0]["detail"]["reason"])
        self.assertEqual([], [e for e in self.journal("transition") if e["stage"] == "done"])

    def test_a_run_whose_file_sits_in_backlog_is_abandoned_and_moved(self):
        self.put("backlog")
        self.register(stage="plan")
        code, out = self.abandon()
        self.assertEqual(0, code, out)
        self.assertEqual("done", state.task_dir(TASK))
        self.assertEqual(state.ST_ABANDONED, self.row()["stage_status"])

    def test_a_paused_run_is_abandoned_and_its_pause_fields_are_cleared(self):
        self.put("active")
        self.register(stage_status=state.ST_PAUSED, paused_status=state.ST_IN_PROGRESS,
                      pause_reason="urgent", pause_kind="clean")
        code, out = self.abandon()
        self.assertEqual(0, code, out)
        row = self.row()
        self.assertEqual(state.ST_ABANDONED, row["stage_status"])
        self.assertEqual(("", "", ""), (row["paused_status"], row["pause_reason"],
                                        row["pause_kind"]))

    def test_the_commit_push_and_plan_approvals_are_cleared(self):
        self.put("active")
        self.register(stage="ready", commit_approved=1, push_approved=1, plan_approved=1)
        self.abandon()
        row = self.row()
        self.assertEqual((0, 0, 0), (row["commit_approved"], row["push_approved"],
                                     row["plan_approved"]))

    def test_a_run_parked_at_done_awaiting_its_merge_is_abandoned_and_its_file_moves(self):
        self.put("active")
        self.register(stage="done", awaiting_human="merge", commit_approved=1)
        boom = AssertionError("git was touched")
        with unittest.mock.patch("subprocess.run", side_effect=boom), \
                unittest.mock.patch.object(git_state, "_git", side_effect=boom):
            code, out = self.abandon()
        self.assertEqual(0, code, out)
        row = self.row()
        self.assertEqual(("done", state.ST_ABANDONED, "", 0),
                         (row["stage"], row["stage_status"], row["awaiting_human"],
                          row["commit_approved"]))
        self.assertEqual("done", state.task_dir(TASK))
        self.assertFalse((self.dirs["active"] / f"{TASK}.md").exists())
        self.assertEqual(1, len(self.journal("abandoned")))

    def test_the_file_is_stamped_abandoned_with_the_reason_and_not_completed(self):
        self.put("active")
        self.register()
        reason = 'said "stop": now'
        self.abandon(reason)
        filed = (self.dirs["done"] / f"{TASK}.md").read_text(encoding="utf-8")
        self.assertIn(f"\nabandoned: {json.dumps(reason)}\n", filed)
        self.assertNotRegex(filed, r"(?m)^completed: \d")

    def test_the_output_lists_the_backlog_tasks_that_depend_on_it_and_says_to_clean_the_tree(self):
        self.put("active")
        self.put("backlog", OTHER, "depends_on: [task-9400, " + TASK + "]")
        self.put("backlog", "task-9502", "depends_on: [task-9400]")
        self.register()
        code, out = self.abandon()
        self.assertEqual(0, code, out)
        self.assertIn(f"Dependents are ready at once: {OTHER}", out["message"])
        self.assertNotIn("task-9502", out["message"])
        self.assertIn("clean the tree", out["message"])

    def test_the_output_says_so_when_nothing_depends_on_it(self):
        self.put("active")
        self.register()
        _, out = self.abandon()
        self.assertIn("No backlog task depends on it", out["message"])


class AbandonRefusalTest(AbandonCase):

    def assertUntouched(self, before: dict, folder: str = "active") -> None:
        self.assertEqual(before, self.row())
        self.assertEqual(folder, state.task_dir(TASK))

    def test_a_missing_reason_is_refused_and_changes_nothing(self):
        self.put("active")
        self.register()
        before = self.row()
        code, out = self.cli("--task", TASK, "--abandon")
        self.assertEqual(2, code)
        self.assertIn("--reason", out["error"])
        self.assertUntouched(before)

    def test_a_blank_reason_and_a_control_character_only_reason_are_refused(self):
        self.put("active")
        self.register()
        before = self.row()
        for reason in ("", "   ", "\n\t\x1b\u202e"):
            code, out = self.abandon(reason)
            self.assertEqual(2, code, repr(reason))
            self.assertIn("--reason", out["error"])
        self.assertUntouched(before)

    def test_an_overlong_reason_is_cut_to_500_characters_and_control_characters_flattened(self):
        self.put("active")
        self.register()
        code, out = self.abandon("a\nb" + "x" * 600)
        self.assertEqual(0, code, out)
        stored = self.row()["abandon_reason"]
        self.assertEqual(approve.REASON_MAX, len(stored))
        self.assertTrue(stored.startswith("a b"))

    def test_an_unknown_task_exits_1_and_writes_nothing(self):
        code, out = self.abandon(task="task-9999")
        self.assertEqual(1, code)
        self.assertIn("no active run", out["error"])
        self.assertIsNone(self.row("task-9999"))

    def test_a_merged_done_task_is_refused_and_nothing_changes(self):
        self.put("done")
        self.register(stage="done", commit_approved=1)
        before = self.row()
        code, out = self.abandon()
        self.assertEqual(2, code)
        self.assertIn("already merged", out["error"])
        self.assertUntouched(before, "done")

    def test_an_already_abandoned_task_is_refused_a_second_time(self):
        self.put("active")
        self.register()
        self.abandon()
        before = self.row()
        code, out = self.abandon("again")
        self.assertEqual(2, code)
        self.assertIn("already abandoned", out["error"])
        self.assertUntouched(before, "done")
        self.assertEqual(1, len(self.journal("abandoned")))

    def test_a_done_file_with_other_text_refuses_and_leaves_the_run_row_unchanged(self):
        self.put("active", head="note: the active copy")
        (self.dirs["done"] / f"{TASK}.md").write_text("a different record\n", encoding="utf-8")
        self.register()
        before = self.row()
        code, out = self.abandon()
        self.assertEqual(2, code)
        self.assertIn("could not be moved", out["error"])
        self.assertUntouched(before)
        self.assertEqual("a different record\n",
                         (self.dirs["done"] / f"{TASK}.md").read_text(encoding="utf-8"))

    def test_reject_and_gate_on_an_abandoned_run_exit_2_and_change_nothing(self):
        self.put("active")
        self.register()
        self.abandon()
        before = self.row()
        for extra in (["--reject", "--reason", "why"], ["--gate", "commit"],
                      ["--gate", "plan"], ["--gate", "push"]):
            code, out = self.cli("--task", TASK, *extra)
            self.assertEqual(2, code, extra)
            self.assertIn("was abandoned", out["error"])
        self.assertUntouched(before, "done")

    def test_abandon_combined_with_another_action_exits_2_and_writes_nothing(self):
        self.put("active")
        self.register()
        before = self.row()
        for extra in (["--gate", "commit"], ["--reject"], ["--pause", "why"], ["--resume"]):
            code, out = self.cli("--task", TASK, "--abandon", "--reason", REASON, *extra)
            self.assertEqual(2, code, extra)
            self.assertIn("stands alone", out["error"])
        self.assertUntouched(before)
        self.assertEqual([], self.journal("abandoned"))


class NeverTouchesGitTest(AbandonCase):

    def test_the_abandon_succeeds_with_every_git_entry_point_raising(self):
        self.put("active")
        self.register()
        boom = AssertionError("git was touched")
        with unittest.mock.patch("subprocess.run", side_effect=boom), \
                unittest.mock.patch("subprocess.Popen", side_effect=boom), \
                unittest.mock.patch.object(approve, "_git", side_effect=boom), \
                unittest.mock.patch.object(git_state, "_git", side_effect=boom), \
                unittest.mock.patch.object(git_state, "task_report", side_effect=boom), \
                unittest.mock.patch.object(git_state, "repos_for_task", side_effect=boom):
            code, out = self.abandon()
        self.assertEqual(0, code, out)
        self.assertEqual(state.ST_ABANDONED, self.row()["stage_status"])


class AdvanceOnAbandonedRunTest(AbandonCase):

    def test_advance_reports_done_without_a_merge_check_or_a_park(self):
        self.put("active")
        self.register()
        self.abandon()
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", TASK]), \
                unittest.mock.patch.object(advance, "finish_or_wait_for_merge",
                                           side_effect=AssertionError("merge check")) as merge, \
                contextlib.redirect_stdout(out):
            code = advance.main()
        self.assertEqual(0, code)
        merge.assert_not_called()
        report = json.loads(out.getvalue())
        self.assertEqual("done", report["action"])
        self.assertEqual("", report["awaiting_human"])
        self.assertEqual("", self.row()["awaiting_human"])
        self.assertIn(REASON, report["message"])


class DebtReadersTest(AbandonCase):

    def handoff_debt(self) -> list:
        return [d["task"] for d in handoff.uncovered_done_tasks()]

    def memory_debt(self) -> list:
        return [d["task"] for d in update.unstamped_done_tasks()]

    def test_an_abandoned_done_task_owes_neither_a_doc_nor_a_stamp(self):
        self.put("active")
        self.register()
        self.abandon()
        self.assertEqual([], self.handoff_debt())
        self.assertEqual([], self.memory_debt())

    def test_a_neighbouring_merged_done_task_is_still_reported(self):
        self.put("active")
        self.register()
        self.abandon()
        self.put("done", OTHER)
        self.register(OTHER, stage="done", commit_approved=1)
        self.assertEqual([OTHER], self.handoff_debt())
        self.assertEqual([OTHER], self.memory_debt())

    def test_a_done_file_with_no_run_row_is_still_reported(self):
        self.put("done")
        self.assertEqual([TASK], self.handoff_debt())
        self.assertEqual([TASK], self.memory_debt())

    def test_is_abandoned_is_false_when_the_store_cannot_be_read(self):
        self.put("active")
        self.register()
        self.abandon()
        with unittest.mock.patch.object(state, "connect", side_effect=sqlite3.OperationalError("x")):
            self.assertFalse(state.is_abandoned(TASK))


class AbandonedUmbrellaTest(AbandonCase):
    UMBRELLA = "task-9510"
    SUB = "task-9511"
    EPIC = "epic-0042"

    def umbrella(self, abandoned: bool) -> None:
        self.put("done", self.UMBRELLA,
                 f"epic: {self.EPIC}\numbrella: true\nsubtasks: [{self.SUB}]")
        self.put("done", self.SUB, f"epic: {self.EPIC}")
        self.register(self.UMBRELLA, stage="done", commit_approved=1)
        if abandoned:
            self.register_status(self.UMBRELLA, state.ST_ABANDONED)

    def register_status(self, task: str, status: str) -> None:
        conn = state.connect()
        try:
            state.set_fields(conn, task, stage_status=status)
        finally:
            conn.close()

    def test_a_merged_umbrella_covers_its_subtask(self):
        self.umbrella(abandoned=False)
        self.assertEqual(self.UMBRELLA, state.covering_umbrella(self.SUB))

    def test_an_abandoned_once_approved_umbrella_covers_nothing(self):
        self.umbrella(abandoned=True)
        self.assertIsNone(state.covering_umbrella(self.SUB))

    def test_an_abandoned_umbrella_sweeps_no_subtask(self):
        self.put("active", self.SUB, f"epic: {self.EPIC}")
        self.put("done", self.UMBRELLA,
                 f"epic: {self.EPIC}\numbrella: true\nsubtasks: [{self.SUB}]")
        self.register(self.UMBRELLA, stage="done", commit_approved=1)
        self.register_status(self.UMBRELLA, state.ST_ABANDONED)
        self.assertEqual([], state.move_swarm_subtasks(self.UMBRELLA))
        self.assertEqual("active", state.task_dir(self.SUB))


class SubagentsDeniedTest(unittest.TestCase):

    def test_every_agent_profile_denies_the_abandon_command_and_a_wrapped_spelling(self):
        handlers = {"dev": agent_gate.handle_dev, "readonly": agent_gate.handle_readonly,
                    "docs": agent_gate.handle_docs}
        for profile, handler in handlers.items():
            for command in (CMD, WRAPPED):
                with self.subTest(profile=profile, command=command):
                    err = io.StringIO()
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(err):
                        code = handler("Bash", {"command": command})
                    self.assertEqual(2, code)
                    self.assertIn("approve.py is orchestrator-only", err.getvalue())


class LegacySchemaTest(AbandonCase):

    def test_a_legacy_runs_table_without_abandon_reason_gains_it_idempotently(self):
        db = state.DB_PATH
        db.parent.mkdir(parents=True, exist_ok=True)
        legacy = sqlite3.connect(db)
        legacy.execute("CREATE TABLE runs (task TEXT PRIMARY KEY, type TEXT, stage TEXT, "
                       "stage_status TEXT, awaiting_human TEXT DEFAULT '', commit_approved "
                       "INTEGER DEFAULT 0, push_approved INTEGER DEFAULT 0, retries INTEGER "
                       "DEFAULT 0, continuations INTEGER DEFAULT 0, updated TEXT)")
        legacy.execute("INSERT INTO runs (task, type, stage, stage_status) "
                       "VALUES ('task-9520', 'feature', 'implement', 'in_progress')")
        legacy.commit()
        legacy.close()
        for _ in range(2):
            state.connect().close()
        raw = sqlite3.connect(db)
        self.addCleanup(raw.close)
        columns = [r[1] for r in raw.execute("PRAGMA table_info(runs)")]
        self.assertEqual(1, columns.count("abandon_reason"))
        self.assertEqual("", self.row("task-9520")["abandon_reason"])


if __name__ == "__main__":
    unittest.main()
