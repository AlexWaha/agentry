"""task-0046 S2: every run-store writer leaves its row in the event journal.

events.py (S1) is the journal; this module pins the WRITERS. Each site named in
the plan (advance, approve, stop_gate, supervisor, gate, create_run) is driven
once against a sandboxed run.db and the row it must leave is read back from the
sandboxed events.db beside it. The journal follows state.DB_PATH, so nothing here
touches the live one.

Also pinned: a write that changes nothing writes no row (a repeated approval, a
second merge park), `merged` is emitted once, an unwritable journal never changes
what run.db says, the old gate_log table is gone from the schema but never
dropped from a live store, and no gate module reads the journal back.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import ast
import contextlib
import gc
import io
import json
import sqlite3
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
import approve
import events
import gate
import git_state
import pretool_gate
import state
import stop_gate
import supervisor
import tmproot
from test_conveyor_gaps import decide_output_with
from test_conveyor_gaps import run as synthetic_run

TASK = "task-0800"

PIPELINE = {
    "retry_budget": 3,
    "pipelines": {"build": {
        "editing_stages": ["plan", "implement", "test", "review"],
        "stages": [
            {"name": "plan", "owner": "dev"},
            {"name": "plan-approval", "owner": "user", "checkpoints": ["plan"]},
            {"name": "implement", "owner": "dev"},
            {"name": "test", "owner": "qa-engineer"},
            {"name": "review", "owner": "reviewer"},
            {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit", "push"]},
            {"name": "done", "owner": "user", "terminal": True},
        ],
    }},
}

GATE_PASSED = {"stage": "implement", "has_gate": True, "configured": True, "cmd": "stub",
               "exit": 0, "passed": True, "output": ""}
GATE_FAILED = {**GATE_PASSED, "exit": 1, "passed": False, "output": "boom"}
GATE_UNCONFIGURED = {**GATE_PASSED, "configured": False, "passed": False,
                     "output": "run onboarding"}


class Harness(unittest.TestCase):
    """A sandboxed run.db with its journal beside it, and a pipeline literal."""

    def setUp(self):
        self.pipeline = PIPELINE
        self.tmp = tmproot.sandbox(self, "event_writers_")
        # A connection that failed to initialise lives on in a swallowed
        # traceback, and Windows will not delete a locked db file.
        self.addCleanup(gc.collect)
        self._patch(state, "STATE_DIR", self.tmp)
        self._patch(state, "DB_PATH", self.tmp / "run.db")
        self._patch(state, "load_pipeline", side_effect=lambda: self.pipeline)
        self._patch(pretool_gate, "workflow_mode", return_value=pretool_gate.WORKFLOW_PR)
        self._patch(approvals, "read", return_value=approvals.MANUAL)
        self._patch(sys, "argv", ["test_event_writers.py"])

    def _patch(self, target, attr, value=None, **kwargs):
        p = (unittest.mock.patch.object(target, attr, **kwargs) if kwargs
             else unittest.mock.patch.object(target, attr, value))
        mocked = p.start()
        self.addCleanup(p.stop)
        return mocked

    def seed(self, task=TASK, stage="implement", **fields):
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

    def journal(self, task=TASK, skip=("registered",)) -> list[dict]:
        """The journal rows of `task` in order, minus the kinds in `skip` (the seed
        itself writes `registered`), with `detail` parsed."""
        path = self.tmp / "events.db"
        if not path.is_file():
            return []
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            found = conn.execute("SELECT * FROM events WHERE task = ? ORDER BY id",
                                 (task,)).fetchall()
        finally:
            conn.close()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in found
                if r["kind"] not in skip]

    def kinds(self, task=TASK, skip=("registered",)) -> list[str]:
        return [r["kind"] for r in self.journal(task, skip)]

    def cli(self, module, argv) -> tuple:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
            code = module.main()
        return code, json.loads(buf.getvalue())

    def advance(self, gate_result=GATE_PASSED, *extra, task=TASK) -> dict:
        with unittest.mock.patch.object(advance, "run_gate", return_value=gate_result), \
                unittest.mock.patch.object(advance.diff_checkpoint, "deliver", return_value=""):
            return self.cli(advance, ["advance.py", "--task", task, *extra])[1]

    def approve(self, *extra, task=TASK) -> tuple:
        return self.cli(approve, ["approve.py", "--task", task, *extra])


class RegistrationAndGateTest(Harness):

    def test_registering_a_task_appends_a_registered_row(self):
        with unittest.mock.patch.object(advance, "check_task_ready", return_value=""), \
                unittest.mock.patch.object(state, "move_task", return_value=True):
            self.cli(advance, ["advance.py", "--task", TASK, "--type", "feature",
                               "--pipeline", "build"])
        (row,) = self.journal(skip=())
        self.assertEqual("registered", row["kind"])
        self.assertEqual(("plan", "build", "advance", {"type": "feature"}),
                         (row["stage"], row["pipeline"], row["via"], row["detail"]))
        self.assertRegex(row["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_a_passing_gate_appends_a_pass_row_with_command_exit_and_duration(self):
        pipeline = {"pipelines": {"build": {"stages": [
            {"name": "implement", "exit_gate": {"cmd": "echo SECRET-OUTPUT && exit 0"}}]}}}
        result = gate.run_gate(pipeline, "implement", TASK)
        self.assertTrue(result["passed"])
        (row,) = self.journal(skip=())
        self.assertEqual(("gate", "implement", "pass"),
                         (row["kind"], row["stage"], row["outcome"]))
        self.assertEqual(0, row["detail"]["exit"])
        self.assertIn("exit 0", row["detail"]["cmd"])
        self.assertIsInstance(row["detail"]["duration_ms"], int)
        self.assertGreaterEqual(row["detail"]["duration_ms"], 0)

    def test_a_failing_gate_appends_a_fail_row_and_never_stores_the_output(self):
        noisy = self.tmp / "noisy.py"
        noisy.write_text("import sys\nprint('LEAKED-' + 'TOKEN')\nsys.exit(3)\n",
                         encoding="utf-8")
        pipeline = {"pipelines": {"build": {"stages": [
            {"name": "test", "exit_gate": {"cmd": f'"{sys.executable}" "{noisy}"'}}]}}}
        result = gate.run_gate(pipeline, "test", TASK)
        self.assertFalse(result["passed"])
        self.assertIn("LEAKED-TOKEN", result["output"])
        (row,) = self.journal(skip=())
        self.assertEqual(("fail", 3), (row["outcome"], row["detail"]["exit"]))
        self.assertNotIn("LEAKED-TOKEN", json.dumps(row))

    def test_a_gate_run_without_a_task_or_without_a_command_writes_nothing(self):
        pipeline = {"pipelines": {"build": {"stages": [
            {"name": "test", "exit_gate": {"cmd": "exit 0"}}, {"name": "ready"}]}}}
        gate.run_gate(pipeline, "test")
        gate.run_gate(pipeline, "ready", TASK)
        self.assertEqual([], self.journal(skip=()))
        self.assertFalse((self.tmp / "events.db").exists())


class AdvanceWritersTest(Harness):

    def test_a_passing_gate_appends_the_transition(self):
        self.seed(stage="implement")
        self.advance(GATE_PASSED)
        (row,) = self.journal()
        self.assertEqual(("transition", "test", "implement", "advance"),
                         (row["kind"], row["stage"], row["from_stage"], row["via"]))

    def test_a_failed_gate_below_the_budget_appends_no_state_row(self):
        self.seed(stage="implement")
        self.assertEqual("gate_failed", self.advance(GATE_FAILED)["action"])
        self.assertEqual([], self.journal())

    def test_the_retry_budget_running_out_appends_blocked_with_the_count(self):
        self.seed(stage="implement", retries=2)
        self.assertEqual("blocked", self.advance(GATE_FAILED)["action"])
        (row,) = self.journal()
        self.assertEqual("blocked", row["kind"])
        self.assertEqual("gate failed 3/3 times at 'implement'", row["detail"]["reason"])

    def test_a_stage_the_flow_does_not_define_appends_blocked(self):
        self.seed(stage="ghost")
        self.assertEqual("blocked", self.advance()["action"])
        (row,) = self.journal()
        self.assertEqual("blocked", row["kind"])
        self.assertIn("'ghost' is not part of the build flow", row["detail"]["reason"])

    def test_an_unconfigured_gate_appends_blocked(self):
        self.seed(stage="implement")
        self.assertEqual("blocked", self.advance(GATE_UNCONFIGURED)["action"])
        (row,) = self.journal()
        self.assertEqual("blocked", row["kind"])
        self.assertIn("gate is not configured", row["detail"]["reason"])

    def test_a_flow_with_no_stage_after_the_passed_one_appends_blocked(self):
        self.pipeline = {"retry_budget": 3, "pipelines": {"build": {"stages": [
            {"name": "implement", "owner": "dev"}]}}}
        self.seed(stage="implement")
        self.assertEqual("blocked", self.advance()["action"])
        (row,) = self.journal()
        self.assertEqual("blocked", row["kind"])
        self.assertIn("no next stage", row["detail"]["reason"])

    def test_parking_at_a_checkpoint_appends_parked_and_no_approval(self):
        self.seed(stage="ready")
        self.assertEqual("park", self.advance()["action"])
        (row,) = self.journal()
        self.assertEqual(("parked", "commit", "ready", "advance"),
                         (row["kind"], row["outcome"], row["stage"], row["via"]))

    def test_a_level_grant_appends_parked_then_approved_via_advance(self):
        self.seed(stage="ready")
        with unittest.mock.patch.object(approvals, "granted", return_value=True):
            self.advance()
        parked, approved = self.journal()
        self.assertEqual(("parked", "commit"), (parked["kind"], parked["outcome"]))
        self.assertEqual(("approved", "commit", "advance"),
                         (approved["kind"], approved["outcome"], approved["via"]))

    def test_parking_on_the_same_checkpoint_again_appends_nothing_new(self):
        self.seed(stage="ready")
        self.advance()
        self.advance()
        self.assertEqual(["parked"], self.kinds())

    def test_finishing_the_checkpoints_appends_the_transition_to_done(self):
        self.seed(stage="ready", commit_approved=1, push_approved=1)
        with unittest.mock.patch.object(
                advance, "finish_or_wait_for_merge",
                side_effect=lambda task, run: advance.result("done", task, run, "stub")):
            self.advance()
        self.assertEqual([("transition", "done", "ready")],
                         [(r["kind"], r["stage"], r["from_stage"]) for r in self.journal()
                          if r["kind"] == "transition"])

    def test_waiting_for_the_merge_appends_parked_merge_once(self):
        self.seed(stage="done")
        with unittest.mock.patch.object(git_state, "task_report", return_value=[]), \
                contextlib.redirect_stdout(io.StringIO()):
            advance.finish_or_wait_for_merge(TASK, self.row())
            advance.finish_or_wait_for_merge(TASK, self.row())
        (row,) = [r for r in self.journal() if r["kind"] == "parked"]
        self.assertEqual("merge", row["outcome"])
        self.assertEqual(["parked"], self.kinds())

    def test_a_confirmed_merge_appends_merged_last_and_only_once(self):
        self.seed(stage="done", awaiting_human="merge")
        report = [{"repo": "agentry", "branch": "feature/task-0800", "in_main": True}]
        with unittest.mock.patch.object(git_state, "task_report", return_value=report), \
                unittest.mock.patch.object(state, "move_task", return_value=True), \
                unittest.mock.patch.object(state, "move_swarm_subtasks", return_value=[]), \
                contextlib.redirect_stdout(io.StringIO()):
            advance.finish_or_wait_for_merge(TASK, self.row())
            advance.finish_or_wait_for_merge(TASK, self.row())
        self.assertEqual(["parked", "merged"], self.kinds())
        self.assertEqual("merged", self.journal()[-1]["kind"])


class ApproveWritersTest(Harness):

    def test_each_checkpoint_approval_appends_approved_via_approve(self):
        for task, gate_name, stage in (("task-0901", "commit", "ready"),
                                       ("task-0902", "push", "ready"),
                                       ("task-0903", "plan", "plan-approval")):
            with self.subTest(gate=gate_name):
                self.seed(task=task, stage=stage)
                self.assertEqual(0, self.approve("--gate", gate_name, task=task)[0])
                (row,) = self.journal(task)
                self.assertEqual(("approved", gate_name, "approve", stage),
                                 (row["kind"], row["outcome"], row["via"], row["stage"]))

    def test_approving_twice_appends_one_row(self):
        self.seed(stage="ready")
        self.approve("--gate", "commit")
        self.approve("--gate", "commit")
        self.assertEqual(["approved"], self.kinds())

    def test_reject_without_a_reason_is_refused_and_changes_nothing(self):
        self.seed(stage="ready", awaiting_human="commit")
        written = self.journal()
        for extra in (("--reject",), ("--reject", "--reason", "   ")):
            with self.subTest(extra=extra):
                code, out = self.approve(*extra)
                self.assertEqual(2, code)
                self.assertIn("--reason", out["error"])
        self.assertEqual("ready", self.row()["stage"])
        self.assertEqual("commit", self.row()["awaiting_human"])
        self.assertEqual(written, self.journal())

    def test_reject_with_a_reason_appends_rejected_then_the_transition(self):
        self.seed(stage="ready", plan_approved=1, commit_approved=0, awaiting_human="commit")
        code, _ = self.approve("--reject", "--reason", "diff touches an unrelated file")
        self.assertEqual(0, code)
        rejected, transition = self.journal()[-2:]
        self.assertEqual(("rejected", "implement", "ready", "approve"),
                         (rejected["kind"], rejected["stage"], rejected["from_stage"],
                          rejected["via"]))
        self.assertEqual({"reason": "diff touches an unrelated file"}, rejected["detail"])
        self.assertEqual(("transition", "implement", "ready"),
                         (transition["kind"], transition["stage"], transition["from_stage"]))

    def test_reject_at_the_plan_step_still_counts_a_new_attempt(self):
        self.seed(stage="plan-approval", awaiting_human="plan")
        self.approve("--reject", "--reason", "plan too broad")
        rejected, transition = self.journal()[-2:]
        self.assertEqual(("rejected", "plan", "plan-approval"),
                         (rejected["kind"], rejected["stage"], rejected["from_stage"]))
        self.assertEqual(("transition", "plan"), (transition["kind"], transition["stage"]))

    def test_pause_and_resume_append_their_rows(self):
        self.seed(stage="implement")
        with unittest.mock.patch.object(approve, "assess_pause",
                                        return_value=(approve.PAUSE_CLEAN, "")):
            self.assertEqual(0, self.approve("--pause", "User force majeure")[0])
        self.assertEqual(0, self.approve("--resume")[0])
        paused, resumed = self.journal()
        self.assertEqual("paused", paused["kind"])
        self.assertEqual({"reason": "User force majeure", "kind": "clean"}, paused["detail"])
        self.assertEqual(("resumed", "approve"), (resumed["kind"], resumed["via"]))

    def test_the_trunk_push_approval_appends_a_taskless_row(self):
        with unittest.mock.patch.object(pretool_gate, "workflow_mode",
                                        return_value=pretool_gate.WORKFLOW_SOLO):
            code, _ = self.cli(approve, ["approve.py", "--trunk-push"])
        self.assertEqual(0, code)
        (row,) = self.journal(task="", skip=())
        self.assertEqual(("approved", "trunk_push", "approve"),
                         (row["kind"], row["outcome"], row["via"]))
        self.assertEqual("origin", row["detail"]["remote"])


class StopGateWritersTest(Harness):

    def test_a_looping_run_parked_by_the_hook_appends_blocked(self):
        self.seed(stage="implement")
        conn = state.connect()
        self.addCleanup(conn.close)
        ctx = {"conn": conn, "runs": [state.get_run(conn, TASK)]}
        with unittest.mock.patch.dict(stop_gate._CTX, ctx):
            self.assertEqual(TASK, stop_gate.park_looping_run(f"{TASK} is stuck"))
        (row,) = self.journal()
        self.assertEqual("blocked", row["kind"])
        self.assertIn("stop hook", row["detail"]["reason"])

    def test_each_continuation_appends_continued_and_the_last_one_appends_blocked(self):
        self.seed(stage="implement")
        conn = state.connect()
        self.addCleanup(conn.close)
        for _ in range(2):
            self.assertTrue(stop_gate.charge(conn, state.get_run(conn, TASK), 2))
        self.assertFalse(stop_gate.charge(conn, state.get_run(conn, TASK), 2))
        first, second, blocked = self.journal()
        self.assertEqual(("continued", {"count": 1}), (first["kind"], first["detail"]))
        self.assertEqual(("continued", {"count": 2}), (second["kind"], second["detail"]))
        self.assertEqual("blocked", blocked["kind"])
        self.assertIn("ceiling of 2", blocked["detail"]["reason"])

    def test_surfacing_a_blocked_run_appends_parked_blocked(self):
        self.seed(stage="implement", stage_status=state.ST_BLOCKED)
        real = state.set_fields
        conn = state.connect()
        self.addCleanup(conn.close)
        decide_output_with(
            [synthetic_run(task=TASK, stage="implement", status=state.ST_BLOCKED)],
            backlog=(), pipeline=PIPELINE,
            set_fields=lambda _fake, task, **kw: real(conn, task, **kw))
        self.assertEqual(["blocked", "parked"], self.kinds())
        self.assertEqual("blocked", self.journal()[-1]["outcome"])

    def test_the_drift_reconciler_appends_merged_for_a_finished_run_once(self):
        self.seed(stage="done", awaiting_human="merge")
        active = self.tmp / "active"
        active.mkdir()
        (active / f"{TASK}.md").write_text("---\nid: 0800\n---\n", encoding="utf-8")
        conn = state.connect()
        self.addCleanup(conn.close)
        with unittest.mock.patch.object(stop_gate, "ACTIVE_DIR", active), \
                unittest.mock.patch.object(git_state, "task_tagged_in_main", return_value=True), \
                unittest.mock.patch.object(state, "move_task_to_done", return_value=True), \
                unittest.mock.patch.object(state, "move_swarm_subtasks", return_value=[]):
            moved, _closed = stop_gate.reconcile_status_drift(conn)
            stop_gate.reconcile_status_drift(conn)
        self.assertEqual([TASK], moved)
        self.assertEqual(["parked", "merged"], self.kinds())
        self.assertEqual("done", self.row()["stage"])

    def test_the_drift_reconciler_appends_closed_after_the_transition(self):
        self.seed(stage="ready", commit_approved=1, awaiting_human="commit")
        active = self.tmp / "active"
        active.mkdir()
        conn = state.connect()
        self.addCleanup(conn.close)
        with unittest.mock.patch.object(stop_gate, "ACTIVE_DIR", active), \
                unittest.mock.patch.object(git_state, "task_tagged_in_main", return_value=True):
            _moved, closed = stop_gate.reconcile_status_drift(conn)
        self.assertEqual([TASK], closed)
        transition, row = self.journal()[-2:]
        self.assertEqual(("transition", "done", "ready"),
                         (transition["kind"], transition["stage"], transition["from_stage"]))
        self.assertEqual("closed", row["kind"])
        self.assertIn("main carries its tag", row["detail"]["reason"])


class SupervisorWriterTest(Harness):

    def test_parking_a_run_blocked_appends_blocked_with_the_supervisor_reason(self):
        self.seed(stage="implement")
        with unittest.mock.patch.object(supervisor, "notify"), \
                unittest.mock.patch.object(sys, "argv", ["supervisor.py"]):
            self.assertTrue(supervisor.park_blocked(TASK, "no progress for 3600s"))
        (row,) = self.journal()
        self.assertEqual(("blocked", "supervisor"), (row["kind"], row["via"]))
        self.assertEqual("supervisor: no progress for 3600s", row["detail"]["reason"])

    def test_the_liveness_check_reads_the_newest_gate_row_of_the_task(self):
        self.seed(stage="implement")
        self.assertEqual("", state.last_event_at(TASK, "gate"))
        pipeline = {"pipelines": {"build": {"stages": [
            {"name": "implement", "exit_gate": {"cmd": "exit 0"}}]}}}
        gate.run_gate(pipeline, "implement", TASK)
        self.assertRegex(state.last_event_at(TASK, "gate"), r"^\d{4}-\d\d-\d\dT")
        self.assertEqual("", state.last_event_at("task-0801", "gate"))


class FailOpenTest(Harness):

    def unwritable(self):
        """A journal path that cannot be created: its parent is a regular file."""
        blocker = self.tmp / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        return self._patch(events, "db_path", return_value=blocker / "events.db")

    def test_advance_still_moves_the_run_when_the_journal_is_unwritable(self):
        self.seed(stage="implement")
        self.unwritable()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = self.advance(GATE_PASSED)
        self.assertEqual("advanced", out["action"])
        self.assertEqual("test", self.row()["stage"])
        self.assertIn("[event-skipped]", err.getvalue())

    def test_registration_approval_and_reject_survive_an_unwritable_journal(self):
        self.unwritable()
        with contextlib.redirect_stderr(io.StringIO()):
            self.seed(stage="ready")
            self.assertEqual(0, self.approve("--gate", "commit")[0])
            self.assertEqual(1, self.row()["commit_approved"])
            self.assertEqual(0, self.approve("--reject", "--reason", "x")[0])
        self.assertEqual("implement", self.row()["stage"])
        self.assertEqual(0, self.row()["commit_approved"])

    def test_a_gate_run_returns_its_result_when_the_journal_is_unwritable(self):
        self.unwritable()
        pipeline = {"pipelines": {"build": {"stages": [
            {"name": "test", "exit_gate": {"cmd": "exit 3"}}]}}}
        with contextlib.redirect_stderr(io.StringIO()):
            result = gate.run_gate(pipeline, "test", TASK)
        self.assertEqual((False, 3), (result["passed"], result["exit"]))

    def test_a_missing_events_module_does_not_stop_a_write(self):
        self.seed(stage="implement")
        with unittest.mock.patch.object(state, "events", None):
            conn = state.connect()
            try:
                state.set_fields(conn, TASK, stage="test", stage_status=state.ST_IN_PROGRESS)
            finally:
                conn.close()
            self.assertEqual("", state.last_event_at(TASK))
        self.assertEqual("test", self.row()["stage"])
        self.assertEqual([], self.journal())


class GateLogRetiredTest(Harness):

    def old_store(self) -> Path:
        """A run.db as an older harness left it, with a gate_log table and rows."""
        path = self.tmp / "run.db"
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE gate_log (task TEXT, stage TEXT, cmd TEXT, "
                         "exit INTEGER, at TEXT)")
            conn.executemany("INSERT INTO gate_log VALUES (?, ?, ?, ?, ?)", [
                (TASK, "test", "cmd-a", 1, "2026-10-01T10:00:00Z"),
                (TASK, "test", "cmd-b", 0, "2026-10-01T10:05:00Z")])
            conn.commit()
        finally:
            conn.close()
        return path

    def tables(self, path: Path) -> set[str]:
        conn = sqlite3.connect(path)
        try:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master "
                                               "WHERE type = 'table'")}
        finally:
            conn.close()

    def test_the_log_gate_writer_is_gone(self):
        self.assertFalse(hasattr(state, "log_gate"))

    def test_a_fresh_run_store_has_no_gate_log_table(self):
        state.connect().close()
        self.assertNotIn("gate_log", self.tables(self.tmp / "run.db"))

    def test_opening_an_old_store_never_drops_its_gate_log(self):
        path = self.old_store()
        state.connect().close()
        conn = sqlite3.connect(path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM gate_log").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(2, count)

    def test_the_first_journal_write_copies_the_old_rows_and_leaves_the_table(self):
        path = self.old_store()
        self.seed(stage="implement")
        migrated = [r for r in self.journal(skip=("registered",)) if r["via"] == "migrated"]
        self.assertEqual(["fail", "pass"], [r["outcome"] for r in migrated])
        self.assertEqual({"cmd": "cmd-a", "exit": 1}, migrated[0]["detail"])
        conn = sqlite3.connect(path)
        try:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM gate_log").fetchone()[0])
        finally:
            conn.close()


class JournalNeverFeedsAGateTest(unittest.TestCase):
    """The journal is evidence. A module that decides something must not read it,
    except through the two readers named in ALLOWED."""

    DECIDERS = ("pretool_gate.py", "agent_gate.py", "stop_gate.py", "advance.py",
                "approve.py", "approvals.py", "git_state.py", "handoff.py", "mode.py",
                "busy.py", "stack_gate.py", "diff_checkpoint.py", "supervisor.py",
                "state.py")
    # (module, enclosing function, read). The only readers of the journal:
    # state._journal_write de-duplicates `merged` and supervisor.progress_at uses
    # the newest gate row to veto a relaunch. last_event_at is the accessor both
    # go through, and the import is the writer's own.
    ALLOWED = frozenset({
        ("state.py", "<module>", "import events"),
        ("state.py", "last_event_at", "journal.last_at"),
        ("state.py", "_journal_write", "last_event_at"),
        ("supervisor.py", "progress_at", "state.last_event_at"),
    })
    JOURNAL_NAMES = ("events", "journal")
    JOURNAL_READS = ("last_at", "task_report", "summary")

    @classmethod
    def reads(cls, name: str, text: str) -> set:
        """Every (module, function, read) where `text` touches the journal."""
        found = set()

        def visit(node, scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = node.name
            what = None
            if isinstance(node, ast.Import) and any(a.name == "events" for a in node.names):
                what = "import events"
            elif isinstance(node, ast.ImportFrom) and node.module == "events":
                what = "import events"
            elif isinstance(node, ast.Attribute) and node.attr == "last_event_at":
                what = (f"{node.value.id}.last_event_at" if isinstance(node.value, ast.Name)
                        else "last_event_at")
            elif (isinstance(node, ast.Attribute) and node.attr in cls.JOURNAL_READS
                  and isinstance(node.value, ast.Name) and node.value.id in cls.JOURNAL_NAMES):
                what = f"{node.value.id}.{node.attr}"
            elif isinstance(node, ast.Name) and node.id == "last_event_at":
                what = "last_event_at"
            if what:
                found.add((name, scope, what))
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(ast.parse(text), "<module>")
        return found

    def test_no_deciding_module_reads_the_journal_outside_the_allowlist(self):
        for name in self.DECIDERS:
            path = PIPELINE_DIR / name
            self.assertTrue(path.is_file(), name)
            with self.subTest(module=name):
                self.assertEqual(set(), self.reads(name, path.read_text(encoding="utf-8"))
                                 - self.ALLOWED)

    def test_the_allowlisted_readers_are_still_there(self):
        # A stale allowlist entry would permit a read nobody is making today.
        seen = set()
        for name in ("state.py", "supervisor.py"):
            seen |= self.reads(name, (PIPELINE_DIR / name).read_text(encoding="utf-8"))
        self.assertEqual(self.ALLOWED, seen & self.ALLOWED)

    def test_the_scan_flags_a_planted_read(self):
        planted = {
            "advance.py": "def go():\n    return state.last_event_at('t', 'gate')\n",
            "stop_gate.py": "import events\n",
            "approve.py": "from events import last_at\n",
            "mode.py": "def m():\n    return events.task_report('t')\n",
            "supervisor.py": "def classify():\n    return state.last_event_at('t')\n",
            "state.py": "def other():\n    return last_event_at('t')\n",
        }
        for name, text in planted.items():
            with self.subTest(module=name):
                self.assertTrue(self.reads(name, text) - self.ALLOWED)


if __name__ == "__main__":
    unittest.main()
