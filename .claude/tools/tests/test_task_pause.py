"""task-0059: the pipeline can represent a preempted task.

A run can be PAUSED: registered, set aside by the User's force-majeure call, not
driven, not holding the working tree. These tests pin the state, the supported
way in and out, the Stop hook's silence about it, and the approval-flag
invariant in both directions.

Git is real (a throwaway repo, because "no commits and no edits" is a question
only git answers); run.db is a sandbox; the Stop hook goes through the same
decide_output_with() harness as the rest of the conveyor tests.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
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
import approve
import git_state
import pretool_gate
import state
import stop_gate
import tmproot
from test_conveyor_gaps import BUILD_PIPELINE, decide_output_with, run

TASK = "task-0042"
URGENT = "task-0058"
BRANCH = "feature/task-0042"


def git(args: list, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                   timeout=20, check=True)


class PauseHarness(unittest.TestCase):
    """A sandboxed run.db, a pipeline config and one real repo."""

    def setUp(self):
        sandbox = tmproot.sandbox(self, "pause_")
        self.repo = sandbox / "repo"
        self.repo.mkdir()
        git(["init", "-q", "-b", "main"], self.repo)
        git(["config", "user.email", "qa@example.com"], self.repo)
        git(["config", "user.name", "QA"], self.repo)
        (self.repo / "seed.txt").write_text("x", encoding="utf-8")
        git(["add", "seed.txt"], self.repo)
        git(["commit", "-q", "-m", "seed"], self.repo)
        git(["branch", BRANCH], self.repo)

        for patcher in (
                unittest.mock.patch.object(state, "STATE_DIR", sandbox),
                unittest.mock.patch.object(state, "DB_PATH", sandbox / "run.db"),
                unittest.mock.patch.object(state, "load_pipeline", return_value=BUILD_PIPELINE),
                unittest.mock.patch.object(git_state, "repos_for_task",
                                           return_value=[self.repo])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def register(self, task=TASK, stage="implement", **fields) -> None:
        conn = state.connect()
        try:
            state.create_run(conn, task, "feature", stage)
            if fields:
                state.set_fields(conn, task, **fields)
        finally:
            conn.close()

    def row(self, task=TASK) -> dict:
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

    def commit_on_branch(self) -> None:
        git(["checkout", "-q", BRANCH], self.repo)
        (self.repo / "work.txt").write_text("work", encoding="utf-8")
        git(["add", "work.txt"], self.repo)
        git(["commit", "-q", "-m", f"[{TASK}] preempted mid-stage"], self.repo)


class PauseAndResumeTest(PauseHarness):

    def test_a_task_with_no_commits_and_no_edits_pauses_and_the_record_says_clean(self):
        self.register()
        code, out = self.cli("--task", TASK, "--pause", "User force majeure: task-0058 first")
        self.assertEqual(code, 0, out)
        row = self.row()
        self.assertEqual(row["stage_status"], state.ST_PAUSED)
        self.assertEqual(row["stage"], "implement")
        self.assertEqual(row["pause_kind"], "clean")
        self.assertEqual(row["pause_reason"], "User force majeure: task-0058 first")
        self.assertEqual(row["paused_status"], state.ST_IN_PROGRESS)
        self.assertIn("no commits and no edits", out["message"])

    def test_a_task_whose_work_is_committed_pauses_and_the_record_says_committed(self):
        self.register()
        self.commit_on_branch()
        code, out = self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.row()["pause_kind"], "committed")

    def test_uncommitted_edits_refuse_the_pause_and_point_at_the_commit_step(self):
        self.register()
        (self.repo / "dirty.txt").write_text("half done", encoding="utf-8")
        code, out = self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual(code, 2)
        self.assertIn("uncommitted work", out["error"])
        self.assertIn("dirty.txt", out["error"])
        self.assertIn("force-majeure step 1", out["error"])
        self.assertEqual(self.row()["stage_status"], state.ST_IN_PROGRESS)
        self.assertEqual(self.row()["pause_kind"], "")

    def test_modified_tracked_file_also_refuses_the_pause(self):
        self.register()
        (self.repo / "seed.txt").write_text("changed", encoding="utf-8")
        code, out = self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual(code, 2)
        self.assertIn("seed.txt", out["error"])

    def test_pipeline_bookkeeping_is_not_uncommitted_work(self):
        self.register()
        (self.repo / ".agentry" / "tasks").mkdir(parents=True)
        (self.repo / ".agentry" / "tasks" / "task-0042.md").write_text("x", encoding="utf-8")
        code, out = self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual(code, 0, out)

    def test_a_git_state_that_cannot_be_read_refuses_the_pause(self):
        self.register()
        not_a_repo = tmproot.sandbox(self, "pause_nogit_")
        with unittest.mock.patch.object(git_state, "repos_for_task", return_value=[not_a_repo]):
            code, out = self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual(code, 2)
        self.assertIn("could not be read", out["error"])

    def test_pause_needs_a_reason(self):
        self.register()
        code, out = self.cli("--task", TASK, "--pause", "  ")
        self.assertEqual(code, 2)
        self.assertIn("reason", out["error"])
        self.assertEqual(self.row()["stage_status"], state.ST_IN_PROGRESS)

    def test_a_finished_task_cannot_be_paused(self):
        self.register(stage="done")
        code, out = self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual(code, 2)
        self.assertIn("already done", out["error"])

    def test_pausing_twice_is_refused_and_keeps_the_first_record(self):
        self.register()
        self.cli("--task", TASK, "--pause", "first reason")
        code, out = self.cli("--task", TASK, "--pause", "second reason")
        self.assertEqual(code, 2)
        self.assertIn("already paused", out["error"])
        self.assertEqual(self.row()["pause_reason"], "first reason")

    def test_resume_restores_the_status_and_keeps_retries_and_continuations(self):
        self.register(stage="test", stage_status=state.ST_GATE_FAILED, retries=2, continuations=5)
        self.cli("--task", TASK, "--pause", "preempted")
        paused = self.row()
        self.assertEqual((paused["retries"], paused["continuations"]), (2, 5))
        self.assertEqual(paused["paused_status"], state.ST_GATE_FAILED)

        code, out = self.cli("--task", TASK, "--resume")
        self.assertEqual(code, 0, out)
        row = self.row()
        self.assertEqual(row["stage"], "test")
        self.assertEqual(row["stage_status"], state.ST_GATE_FAILED)
        self.assertEqual((row["retries"], row["continuations"]), (2, 5))
        self.assertEqual((row["paused_status"], row["pause_reason"], row["pause_kind"]),
                         ("", "", ""))

    def test_resume_of_a_run_that_is_not_paused_is_refused(self):
        self.register()
        code, out = self.cli("--task", TASK, "--resume")
        self.assertEqual(code, 2)
        self.assertIn("not paused", out["error"])

    def test_resume_is_refused_while_another_task_holds_an_editing_stage(self):
        self.register()
        self.cli("--task", TASK, "--pause", "preempted")
        self.register(task=URGENT, stage="test")
        code, out = self.cli("--task", TASK, "--resume")
        self.assertEqual(code, 2)
        self.assertIn(URGENT, out["error"])
        self.assertEqual(self.row()["stage_status"], state.ST_PAUSED)

    def test_resume_is_allowed_once_the_other_task_is_paused_or_past_its_editing_stages(self):
        self.register()
        self.cli("--task", TASK, "--pause", "preempted")
        self.register(task=URGENT, stage="ready")
        code, out = self.cli("--task", TASK, "--resume")
        self.assertEqual(code, 0, out)

    def test_pause_and_resume_do_not_combine_with_other_actions(self):
        self.register()
        for argv in (("--pause", "x", "--resume"), ("--pause", "x", "--reject"),
                     ("--resume", "--gate", "commit")):
            with self.subTest(argv=argv):
                code, out = self.cli("--task", TASK, *argv)
                self.assertEqual(code, 2)
                self.assertIn("stand alone", out["error"])
        self.assertEqual(self.row()["stage_status"], state.ST_IN_PROGRESS)

    def test_reject_clears_the_pause_record(self):
        self.register()
        self.cli("--task", TASK, "--pause", "preempted")
        self.cli("--task", TASK, "--reject", "--reason", "needs rework")
        row = self.row()
        self.assertEqual(row["stage_status"], state.ST_IN_PROGRESS)
        self.assertEqual((row["paused_status"], row["pause_reason"], row["pause_kind"]),
                         ("", "", ""))


class ApprovalFlagsSurviveAPauseTest(PauseHarness):
    """Parking never clears an approval flag, in both directions."""

    def test_task_paused_after_its_commit_checkpoint_keeps_the_approval_through_resume(self):
        self.register(stage="ready", awaiting_human="commit", commit_approved=1)
        self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual(self.row()["commit_approved"], 1)
        self.cli("--task", TASK, "--resume")
        row = self.row()
        self.assertEqual(row["commit_approved"], 1)
        self.assertEqual(row["awaiting_human"], "commit")

    def test_task_paused_before_its_commit_checkpoint_never_appears_approved(self):
        self.register(stage="review")
        self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual((self.row()["commit_approved"], self.row()["push_approved"]), (0, 0))
        self.cli("--task", TASK, "--resume")
        self.assertEqual((self.row()["commit_approved"], self.row()["push_approved"]), (0, 0))

    def test_resume_at_an_editing_stage_drops_the_preempt_commit_approval(self):
        # The force-majeure sequence records the commit approval to save the work;
        # it must not let the FINISHED work skip the User's read of the diff.
        self.register(stage="implement", commit_approved=1)
        self.cli("--task", TASK, "--pause", "preempted")
        self.assertEqual(self.row()["commit_approved"], 1)  # the pause itself keeps it
        code, out = self.cli("--task", TASK, "--resume")
        self.assertEqual(code, 0)
        self.assertEqual(self.row()["commit_approved"], 0)
        self.assertIn("Cleared commit_approved", out["message"])

    def test_pushed_approval_survives_a_pause_at_a_checkpoint_stage(self):
        self.register(stage="ready", awaiting_human="push", commit_approved=1, push_approved=1)
        self.cli("--task", TASK, "--pause", "preempted")
        self.cli("--task", TASK, "--resume")
        row = self.row()
        self.assertEqual((row["commit_approved"], row["push_approved"]), (1, 1))


class AdvanceRefusesAPausedRunTest(PauseHarness):

    def test_advance_neither_runs_the_gate_nor_changes_the_paused_status(self):
        self.register()
        self.cli("--task", TASK, "--pause", "preempted")
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", TASK]), \
                unittest.mock.patch.object(advance, "run_gate") as gate, \
                contextlib.redirect_stdout(out):
            advance.main()
        gate.assert_not_called()
        printed = json.loads(out.getvalue())
        self.assertEqual(printed["action"], "paused")
        self.assertIn("--resume", printed["message"])
        self.assertIn("preempted", printed["message"])
        self.assertEqual(self.row()["stage_status"], state.ST_PAUSED)


class StopHookIgnoresAPausedRunTest(unittest.TestCase):
    """The hook must not demand work on a paused run, must not need a busy marker
    to stay quiet about it, and must not count it as holding the tree."""

    def test_control_the_same_run_unpaused_is_driven(self):
        out = decide_output_with([run(task=TASK, stage="implement")], backlog=())
        self.assertIsNotNone(out)
        self.assertIn(TASK, out["reason"])

    def test_paused_editing_run_is_not_driven_and_the_next_task_is_offered(self):
        paused = run(task=TASK, stage="implement", status=state.ST_PAUSED)
        out = decide_output_with([paused], backlog=(URGENT,), busy=False, any_busy=False)
        self.assertIsNotNone(out)
        self.assertIn(f"Start the next ready task {URGENT}", out["reason"])
        self.assertNotIn(TASK, out["reason"])

    def test_paused_run_with_nothing_else_to_do_lets_the_stop_through_silently(self):
        paused = run(task=TASK, stage="implement", status=state.ST_PAUSED)
        self.assertIsNone(decide_output_with([paused], backlog=()))

    def test_paused_run_needs_no_busy_marker_and_is_never_charged_a_continuation(self):
        paused = run(task=TASK, stage="test", status=state.ST_PAUSED, continuations=7)
        set_fields = unittest.mock.MagicMock()
        out = decide_output_with([paused], backlog=(), busy=False, set_fields=set_fields)
        self.assertIsNone(out)
        set_fields.assert_not_called()

    def test_paused_run_at_an_approved_checkpoint_is_not_told_to_commit(self):
        control = run(task=TASK, stage="ready", awaiting_human="commit", commit_approved=1)
        self.assertIn("checkpoint approved",
                      decide_output_with([control], backlog=())["reason"])
        paused = run(task=TASK, stage="ready", awaiting_human="commit", commit_approved=1,
                     status=state.ST_PAUSED)
        self.assertIsNone(decide_output_with([paused], backlog=()))

    def test_paused_run_parked_on_the_ceo_is_not_announced_as_owed(self):
        control = run(task=TASK, stage="ready", awaiting_human="commit")
        self.assertIn(TASK, decide_output_with([control], backlog=())["systemMessage"])
        paused = run(task=TASK, stage="ready", awaiting_human="commit", status=state.ST_PAUSED)
        self.assertIsNone(decide_output_with([paused], backlog=()))

    def test_another_run_is_still_driven_while_a_sibling_is_paused(self):
        runs = [run(task=TASK, stage="implement", status=state.ST_PAUSED),
                run(task=URGENT, stage="test")]
        out = decide_output_with(runs, backlog=())
        self.assertIn(URGENT, out["reason"])
        self.assertNotIn(TASK, out["reason"])

    def test_the_looping_backstop_never_turns_a_paused_run_blocked(self):
        paused = run(task=TASK, stage="implement", status=state.ST_PAUSED)
        set_fields = unittest.mock.MagicMock()
        with unittest.mock.patch.dict(
                stop_gate._CTX, {"conn": object(), "runs": [paused]}), \
                unittest.mock.patch.object(state, "set_fields", set_fields):
            self.assertIsNone(stop_gate.park_looping_run(f"{TASK} keeps being repeated"))
        set_fields.assert_not_called()


class ParkedDefinitionsTest(unittest.TestCase):

    def test_is_paused_only_for_the_paused_status(self):
        self.assertTrue(state.is_paused(run(status=state.ST_PAUSED)))
        for status in (state.ST_IN_PROGRESS, state.ST_GATE_FAILED, state.ST_GATE_PASSED,
                       state.ST_BLOCKED):
            with self.subTest(status=status):
                self.assertFalse(state.is_paused(run(status=status)))
        self.assertFalse(state.is_paused(None))

    def test_is_parked_covers_every_state_where_no_agent_is_asked_to_work(self):
        self.assertTrue(state.is_parked(run(status=state.ST_PAUSED)))
        self.assertTrue(state.is_parked(run(status=state.ST_BLOCKED)))
        self.assertTrue(state.is_parked(run(stage="ready", awaiting_human="commit")))
        self.assertTrue(state.is_parked(run(stage="done")))

    def test_an_active_run_is_not_parked(self):
        for status in (state.ST_IN_PROGRESS, state.ST_GATE_FAILED):
            with self.subTest(status=status):
                self.assertFalse(state.is_parked(run(stage="implement", status=status)))
        self.assertFalse(state.is_parked(None))


class PauseColumnsTest(PauseHarness):

    def test_a_database_from_before_the_pause_columns_gains_them_and_keeps_its_rows(self):
        old = state.DB_PATH
        old.unlink(missing_ok=True)
        import sqlite3
        raw = sqlite3.connect(old)
        raw.executescript(
            "CREATE TABLE runs (task TEXT PRIMARY KEY, type TEXT, stage TEXT, "
            "stage_status TEXT, awaiting_human TEXT DEFAULT '', commit_approved INTEGER "
            "DEFAULT 0, push_approved INTEGER DEFAULT 0, retries INTEGER DEFAULT 0, "
            "continuations INTEGER DEFAULT 0, updated TEXT);"
            "INSERT INTO runs (task, type, stage, stage_status) "
            "VALUES ('task-0001', 'feature', 'review', 'in_progress');")
        raw.commit()
        raw.close()
        row = self.row("task-0001")
        self.assertEqual(row["stage"], "review")
        for column in ("paused_status", "pause_reason", "pause_kind"):
            self.assertEqual(row[column], "")

    def test_session_start_summary_tells_the_orchestrator_not_to_drive_a_paused_run(self):
        self.register()
        self.cli("--task", TASK, "--pause", "User asked for task-0058")
        conn = state.connect()
        try:
            text = state._resume_text(conn)
        finally:
            conn.close()
        self.assertIn("PAUSED by the User, do not drive", text)
        self.assertIn(TASK, text)
        self.assertIn("User asked for task-0058", text)

    def test_a_paused_review_run_does_not_freeze_code_edits(self):
        self.register(stage="review")
        conn = state.connect()
        try:
            self.assertIsNotNone(pretool_gate.active_review_run(conn))
            self.cli("--task", TASK, "--pause", "preempted")
            self.assertIsNone(pretool_gate.active_review_run(conn))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
