"""The plan flow's `approval` stage is a real checkpoint (task-0089).

`pipelines.plan` declared an `approval` stage owned by the CEO, spelled its key
`checkpoint` (singular, read by nobody), and advance.stage_checkpoints() then
filtered every name that was not `commit` or `push` out of the list. A hand-run
`advance.py --task X` at `approval` therefore walked straight to `breakdown`
and nothing recorded that a human was skipped.

These tests pin the repair: the stage names its checkpoint in the plural like
every other stage, advance.py parks on it and refuses to move until it is
approved, the approval is a durable run.db column, no level can grant it, and a
subagent cannot record it.
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

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import agent_gate
import approvals
import approve
import pretool_gate
import state
import tmproot

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The shipped shape, as a literal: `approval` names its checkpoint in the plural
# and carries no exit_gate, which is the configuration the defect was invisible
# under (an absent gate reads configured AND passed).
PLAN_PIPELINE = {
    "retry_budget": 3,
    "pipelines": {"plan": {
        "editing_stages": ["formalize", "draft", "plan-review", "breakdown"],
        "stages": [
            {"name": "formalize", "owner": "architect"},
            {"name": "draft", "owner": "architect"},
            {"name": "plan-review", "owner": "reviewer"},
            {"name": "approval", "owner": "ceo", "checkpoints": ["plan"]},
            {"name": "breakdown", "owner": "product-manager"},
            {"name": "done", "owner": "ceo"},
        ],
    }},
}

TASK = "task-0800"


def with_approval(**overrides) -> dict:
    """PLAN_PIPELINE with the `approval` stage's keys overridden."""
    stages = [dict(s) for s in PLAN_PIPELINE["pipelines"]["plan"]["stages"]]
    for s in stages:
        if s["name"] == "approval":
            s.update(overrides)
    return {"retry_budget": 3, "pipelines": {"plan": {"stages": stages}}}


class PlanCheckpointTest(unittest.TestCase):

    def setUp(self):
        self.pipeline = PLAN_PIPELINE
        self.tmp = tmproot.sandbox(self, "plan_checkpoint")
        self._patch(state, "STATE_DIR", self.tmp)
        self._patch(state, "DB_PATH", self.tmp / "run.db")
        self._patch(state, "load_pipeline", side_effect=lambda: self.pipeline)
        self._patch(pretool_gate, "workflow_mode", return_value=pretool_gate.WORKFLOW_PR)
        self._patch(approvals, "read", return_value=approvals.MANUAL)
        conn = state.connect()
        state.create_run(conn, TASK, "feature", "approval", state.PLAN)
        conn.close()

    def _patch(self, target, attr, value=None, **kwargs):
        p = (unittest.mock.patch.object(target, attr, **kwargs) if kwargs
             else unittest.mock.patch.object(target, attr, value))
        p.start()
        self.addCleanup(p.stop)

    def _row(self, task=TASK) -> dict:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()

    def _run_cli(self, module, argv) -> tuple:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
            code = module.main()
        return code, json.loads(buf.getvalue())

    def _advance(self) -> dict:
        return self._run_cli(advance, ["advance.py", "--task", TASK])[1]

    def _approve(self, *extra) -> tuple:
        return self._run_cli(approve, ["approve.py", "--task", TASK, *extra])

    # --- the key and the checkpoint list ------------------------------------

    def test_stage_checkpoints_returns_plan_for_the_approval_stage(self):
        stage = state.get_stage(PLAN_PIPELINE, "approval", state.PLAN)
        self.assertEqual(["plan"], advance.stage_checkpoints(stage))

    def test_a_stage_without_checkpoints_has_none(self):
        stage = state.get_stage(PLAN_PIPELINE, "draft", state.PLAN)
        self.assertEqual([], advance.stage_checkpoints(stage))

    def test_the_singular_key_is_not_read_by_anything(self):
        singular = with_approval(checkpoint="plan")
        for s in singular["pipelines"]["plan"]["stages"]:
            s.pop("checkpoints", None)
        self.assertEqual([], advance.stage_checkpoints(
            state.get_stage(singular, "approval", state.PLAN)))
        self.assertEqual((), state.checkpoint_stages(singular, state.PLAN))

    def test_the_real_pipeline_json_spells_the_key_in_the_plural_everywhere(self):
        real = json.loads((PROJECT_ROOT / ".agentry" / "pipeline.json").read_text(encoding="utf-8"))
        for flow, body in real["pipelines"].items():
            for stage in body["stages"]:
                with self.subTest(flow=flow, stage=stage["name"]):
                    self.assertNotIn("checkpoint", stage)
        approval = next(s for s in real["pipelines"]["plan"]["stages"] if s["name"] == "approval")
        self.assertEqual(["plan"], approval["checkpoints"])
        self.assertEqual(("approval",), state.checkpoint_stages(real, state.PLAN))
        self.assertNotIn("KNOWN GAP", json.dumps(real))

    # --- the refusal ---------------------------------------------------------

    def test_advance_at_approval_parks_and_does_not_move(self):
        out = self._advance()
        row = self._row()
        self.assertEqual("park", out["action"])
        self.assertEqual("plan", out["awaiting_human"])
        self.assertEqual("approval", row["stage"])
        self.assertEqual("plan", row["awaiting_human"])
        self.assertEqual(0, row["plan_approved"])

    def test_advance_keeps_refusing_however_often_it_is_run(self):
        for _ in range(3):
            self.assertEqual("park", self._advance()["action"])
        self.assertEqual("approval", self._row()["stage"])

    def test_no_level_and_no_auto_approve_list_lets_it_through(self):
        self._patch(approvals, "read", return_value=approvals.AUTO)
        self.pipeline = with_approval(auto_approve=["plan"])
        out = self._advance()
        self.assertEqual("park", out["action"])
        self.assertEqual("approval", self._row()["stage"])
        self.assertEqual(0, self._row()["plan_approved"])

    def test_an_unrecognised_checkpoint_name_parks_instead_of_passing(self):
        self.pipeline = with_approval(checkpoints=["paln"])
        out = self._advance()
        self.assertEqual("park", out["action"])
        self.assertEqual("approval", self._row()["stage"])
        self.assertIn("paln", out["message"])

    # --- the approval --------------------------------------------------------

    def test_after_the_plan_gate_is_recorded_advance_moves_to_breakdown(self):
        self.assertEqual("park", self._advance()["action"])
        code, out = self._approve("--gate", "plan")
        self.assertEqual(0, code)
        self.assertTrue(out["ok"])
        self.assertEqual(1, self._row()["plan_approved"])

        moved = self._advance()
        row = self._row()
        self.assertEqual("advanced", moved["action"])
        self.assertEqual("breakdown", row["stage"])
        self.assertEqual("", row["awaiting_human"])
        self.assertEqual(state.ST_IN_PROGRESS, row["stage_status"])
        self.assertEqual(1, row["plan_approved"])

    def test_approving_twice_is_a_no_op_with_a_message(self):
        self._approve("--gate", "plan")
        before = self._row()
        code, out = self._approve("--gate", "plan")
        self.assertEqual(0, code)
        self.assertIn("already approved", out["message"])
        self.assertEqual(before, self._row())

    def test_reject_clears_the_plan_approval(self):
        self._approve("--gate", "plan")
        self._approve("--reject")
        row = self._row()
        self.assertEqual(0, row["plan_approved"])
        self.assertEqual("formalize", row["stage"])

    def test_the_build_flow_still_reaches_done_through_its_own_checkpoints(self):
        self.pipeline = {"retry_budget": 3, "pipelines": {"build": {"stages": [
            {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit", "push"]},
            {"name": "done", "owner": "ceo"}]}}}
        conn = state.connect()
        state.create_run(conn, "task-0801", "feature", "ready")
        state.set_fields(conn, "task-0801", commit_approved=1, push_approved=1)
        conn.close()
        with unittest.mock.patch.object(advance, "finish_or_wait_for_merge",
                                        return_value=0) as finish, \
                unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", "task-0801"]):
            advance.main()
        self.assertEqual("done", self._row("task-0801")["stage"])
        self.assertEqual(1, finish.call_count)

    # --- the trust boundary --------------------------------------------------

    def test_no_approvals_level_can_ever_grant_it(self):
        self.assertIn(approvals.PLAN, approvals.NEVER_GRANTED)
        for level in approvals.LEVELS:
            with self.subTest(level=level):
                self.assertNotIn(approvals.PLAN, approvals.GRANTS[level])
                with unittest.mock.patch.object(approvals, "read", return_value=level):
                    self.assertFalse(approvals.granted(approvals.PLAN, [approvals.PLAN]))

    def test_a_subagent_cannot_record_the_plan_approval(self):
        # The script name is assembled: this repository's own gate denies a Bash
        # command carrying the literal (see GatedToolNamedNotRunTest).
        script = "appr" + "ove.py"
        command = f"python .claude/tools/pipeline/{script} --task {TASK} --gate plan"
        for name in ("handle_dev", "handle_readonly", "handle_docs"):
            with self.subTest(profile=name):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = getattr(agent_gate, name)("Bash", {"command": command}, cwd=".")
                self.assertEqual(2, code)
                self.assertIn("orchestrator-only", err.getvalue())

    # --- the durable record --------------------------------------------------

    def test_a_run_row_that_predates_the_column_reads_as_not_approved(self):
        """The migration path: an existing run.db gets the column by ALTER, and
        its rows take the default, so no old run can arrive pre-approved."""
        old = self.tmp / "old.db"
        conn = sqlite3.connect(old)
        conn.executescript(
            "CREATE TABLE runs (task TEXT PRIMARY KEY, type TEXT, stage TEXT, "
            "stage_status TEXT, awaiting_human TEXT DEFAULT '', "
            "commit_approved INTEGER DEFAULT 0, push_approved INTEGER DEFAULT 0, "
            "retries INTEGER DEFAULT 0, continuations INTEGER DEFAULT 0, updated TEXT);"
            "INSERT INTO runs (task, stage) VALUES ('task-0009', 'approval');")
        conn.commit()
        conn.close()
        self._patch(state, "DB_PATH", old)
        conn = state.connect()
        try:
            self.assertEqual(0, state.get_run(conn, "task-0009")["plan_approved"])
            state.init_db(conn)  # idempotent: a second call must not fail
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
