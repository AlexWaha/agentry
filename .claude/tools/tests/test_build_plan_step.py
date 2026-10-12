"""task-0045: the build flow asks the CEO to approve a plan before any code is written.

`pipelines.build` gains two stages in front of `implement`: `plan` (the owning
agent writes what it intends to change and why, gated on its artifact) and
`plan-approval` (the `plan` checkpoint, already a run.db column since task-0089).
Until that approval is recorded `pretool_gate.py` denies Edit, Write and the
file-writing shell commands it can read for the task's branch, and small work
skips the step by task type, from configuration.

Every pipeline in this module is a literal handed to `state.load_pipeline`, so
the module passes whether or not the real pipeline.json carries the new stages;
RealConfigTest pins the real file's shape whichever side of that it is on.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import copy
import gc
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
import agent_gate
import approvals
import approve
import git_state
import pretool_gate
import state
import tmproot

PROJECT_ROOT = Path(__file__).resolve().parents[3]

TASK = "task-0800"
OTHER = "task-0801"
BRANCH = "feature/task-0800"
SKIP = ["bugfix", "techdebt"]
PLAN_FILE = f".agentry/plans/2026-10-09-plan-{TASK}.md"

BUILD_PIPELINE = {
    "retry_budget": 3,
    "pipelines": {"build": {
        "editing_stages": ["plan", "implement", "test", "review"],
        "stages": [
            {"name": "plan", "owner": "dev", "skip_for_types": SKIP,
             "exit_gate": {"cmd": "artifact-gate-stub", "expect_exit": 0}},
            {"name": "plan-approval", "owner": "ceo", "checkpoints": ["plan"],
             "skip_for_types": SKIP},
            {"name": "implement", "owner": "dev"},
            {"name": "test", "owner": "qa-engineer"},
            {"name": "review", "owner": "reviewer"},
            {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit", "push"]},
            {"name": "done", "owner": "ceo", "terminal": True},
        ],
    }},
}

# The same flow as it was before the step existed: no stage declares `plan`.
OLD_BUILD_PIPELINE = {
    "retry_budget": 3,
    "pipelines": {"build": {
        "stages": [
            {"name": "implement", "owner": "dev"},
            {"name": "test", "owner": "qa-engineer"},
            {"name": "review", "owner": "reviewer"},
            {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit", "push"]},
            {"name": "done", "owner": "ceo", "terminal": True},
        ],
    }},
}

GATE_PASSED = {"stage": "plan", "has_gate": True, "configured": True, "cmd": "stub",
               "exit": 0, "passed": True, "output": ""}


def with_stage(pipeline: dict, name: str, **overrides) -> dict:
    out = copy.deepcopy(pipeline)
    for s in out["pipelines"]["build"]["stages"]:
        if s["name"] == name:
            s.update(overrides)
    return out


class Harness(unittest.TestCase):
    """A sandboxed run.db and a pipeline config the test can swap."""

    def setUp(self):
        self.pipeline = BUILD_PIPELINE
        self.tmp = tmproot.sandbox(self, "build_plan_")
        # A connection that failed to initialise lives on in the traceback of the
        # error that was swallowed, and Windows will not delete a locked db file.
        # Cleanups run last-in first, so this collects before the sandbox goes.
        self.addCleanup(gc.collect)
        self._patch(state, "STATE_DIR", self.tmp)
        self._patch(state, "DB_PATH", self.tmp / "run.db")
        self._patch(state, "load_pipeline", side_effect=lambda: self.pipeline)
        self._patch(pretool_gate, "workflow_mode", return_value=pretool_gate.WORKFLOW_PR)
        self._patch(approvals, "read", return_value=approvals.MANUAL)

    def _patch(self, target, attr, value=None, **kwargs):
        p = (unittest.mock.patch.object(target, attr, **kwargs) if kwargs
             else unittest.mock.patch.object(target, attr, value))
        mocked = p.start()
        self.addCleanup(p.stop)
        return mocked

    def seed(self, task=TASK, stage="plan", task_type="feature", **fields):
        conn = state.connect()
        try:
            state.create_run(conn, task, task_type, stage)
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

    def cli(self, module, argv) -> tuple:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
            code = module.main()
        return code, json.loads(buf.getvalue())

    def advance(self, *extra, task=TASK) -> dict:
        with unittest.mock.patch.object(advance, "run_gate", return_value=GATE_PASSED):
            return self.cli(advance, ["advance.py", "--task", task, *extra])[1]

    def approve(self, *extra, task=TASK) -> tuple:
        return self.cli(approve, ["approve.py", "--task", task, *extra])


class StartStageTest(Harness):

    def test_a_feature_starts_at_the_plan_step(self):
        self.assertEqual("plan", advance.start_stage(BUILD_PIPELINE, "build", "feature"))

    def test_an_enhancement_and_an_unknown_type_also_plan(self):
        for task_type in ("enhancement", "chore", ""):
            with self.subTest(task_type=task_type):
                self.assertEqual("plan", advance.start_stage(BUILD_PIPELINE, "build", task_type))

    def test_bugfix_and_techdebt_skip_to_implement(self):
        for task_type in ("bugfix", "techdebt", "BugFix", " TECHDEBT "):
            with self.subTest(task_type=task_type):
                self.assertEqual("implement",
                                 advance.start_stage(BUILD_PIPELINE, "build", task_type))

    def test_the_skip_list_is_configuration_not_code(self):
        wider = copy.deepcopy(BUILD_PIPELINE)
        for s in wider["pipelines"]["build"]["stages"]:
            if "skip_for_types" in s:
                s["skip_for_types"] = ["feature"]
        self.assertEqual("implement", advance.start_stage(wider, "build", "feature"))
        self.assertEqual("plan", advance.start_stage(wider, "build", "bugfix"))

    def test_a_flow_without_the_step_starts_where_it_always_did(self):
        self.assertEqual("implement", advance.start_stage(OLD_BUILD_PIPELINE, "build", "feature"))
        self.assertEqual("implement", advance.start_stage(OLD_BUILD_PIPELINE, "build", "bugfix"))

    def test_a_skip_value_that_is_not_a_list_is_ignored(self):
        broken = with_stage(BUILD_PIPELINE, "plan", skip_for_types="bugfix")
        self.assertEqual("plan", advance.start_stage(broken, "build", "bugfix"))

    def test_registration_records_the_stage_and_says_why_a_step_was_skipped(self):
        with unittest.mock.patch.object(advance, "check_task_ready", return_value=""), \
                unittest.mock.patch.object(state, "move_task", return_value=True):
            feature = self.cli(advance, ["advance.py", "--task", TASK, "--type", "feature",
                                         "--pipeline", "build"])[1]
            bugfix = self.cli(advance, ["advance.py", "--task", OTHER, "--type", "bugfix",
                                        "--pipeline", "build"])[1]
        self.assertEqual("plan", feature["stage"])
        self.assertEqual("plan", self.row(TASK)["stage"])
        self.assertNotIn("skipped", feature["message"])
        self.assertEqual("implement", bugfix["stage"])
        self.assertEqual("implement", self.row(OTHER)["stage"])
        self.assertIn("skipped for type 'bugfix'", bugfix["message"])

    def test_the_default_type_asks_for_a_plan(self):
        with unittest.mock.patch.object(advance, "check_task_ready", return_value=""), \
                unittest.mock.patch.object(state, "move_task", return_value=True):
            out = self.cli(advance, ["advance.py", "--task", TASK, "--pipeline", "build"])[1]
        self.assertEqual("plan", out["stage"])


class PlanStepFlowTest(Harness):

    def test_the_plan_gate_hands_over_to_the_checkpoint_stage_and_says_so(self):
        self.seed()
        out = self.advance()
        self.assertEqual("advanced", out["action"])
        self.assertEqual("plan-approval", self.row()["stage"])
        self.assertIn("awaits the CEO's plan approval", out["message"])

    def test_a_failing_plan_gate_keeps_the_run_at_plan(self):
        self.seed()
        failed = dict(GATE_PASSED, passed=False, exit=1, output="no plan for task-0800")
        with unittest.mock.patch.object(advance, "run_gate", return_value=failed):
            out = self.cli(advance, ["advance.py", "--task", TASK])[1]
        self.assertEqual("gate_failed", out["action"])
        self.assertEqual("plan", self.row()["stage"])

    def test_the_checkpoint_stage_parks_and_will_not_move_unapproved(self):
        self.seed(stage="plan-approval")
        for _ in range(3):
            out = self.advance()
            self.assertEqual("park", out["action"])
        row = self.row()
        self.assertEqual("plan-approval", row["stage"])
        self.assertEqual("plan", row["awaiting_human"])
        self.assertEqual(0, row["plan_approved"])
        self.assertIn("Plan awaiting CEO approval", out["message"])

    def test_no_approvals_level_and_no_auto_approve_list_grants_the_plan(self):
        self._patch(approvals, "read", return_value=approvals.AUTO)
        self.pipeline = with_stage(BUILD_PIPELINE, "plan-approval", auto_approve=["plan"])
        self.seed(stage="plan-approval")
        self.assertEqual("park", self.advance()["action"])
        self.assertEqual("plan-approval", self.row()["stage"])
        self.assertEqual(0, self.row()["plan_approved"])

    def test_after_the_approval_is_recorded_the_run_reaches_implement(self):
        self.seed(stage="plan-approval")
        self.advance()
        code, out = self.approve("--gate", "plan")
        self.assertEqual(0, code)
        self.assertIn("allowed again", out["message"])
        moved = self.advance()
        row = self.row()
        self.assertEqual("advanced", moved["action"])
        self.assertEqual("implement", row["stage"])
        self.assertEqual("", row["awaiting_human"])
        self.assertEqual(1, row["plan_approved"])

    def test_rejecting_the_plan_stays_at_plan_and_clears_the_approval(self):
        self.seed(stage="plan-approval", awaiting_human="plan")
        self.approve("--reject", "--reason", "needs rework")
        row = self.row()
        self.assertEqual("plan", row["stage"])
        self.assertEqual(0, row["plan_approved"])
        self.assertEqual("", row["awaiting_human"])

    def test_rejecting_at_the_plan_stage_itself_also_stays_there(self):
        self.seed(stage="plan", plan_approved=1)
        self.approve("--reject", "--reason", "needs rework")
        row = self.row()
        self.assertEqual("plan", row["stage"])
        self.assertEqual(0, row["plan_approved"])

    def test_rejecting_the_diff_goes_to_implement_and_keeps_the_plan_approval(self):
        self.seed(stage="ready", plan_approved=1, commit_approved=0, awaiting_human="commit")
        self.approve("--reject", "--reason", "needs rework")
        row = self.row()
        self.assertEqual("implement", row["stage"])
        self.assertEqual(1, row["plan_approved"])
        self.assertEqual("", row["awaiting_human"])

    def test_rejecting_a_run_that_skipped_the_plan_goes_to_implement(self):
        self.seed(stage="review", task_type="bugfix")
        self.approve("--reject", "--reason", "needs rework")
        self.assertEqual("implement", self.row()["stage"])

    def test_a_flow_without_the_step_rejects_to_its_first_stage_as_before(self):
        self.pipeline = OLD_BUILD_PIPELINE
        self.seed(stage="ready")
        self.approve("--reject", "--reason", "needs rework")
        self.assertEqual("implement", self.row()["stage"])

    def test_the_plan_flow_reject_is_untouched(self):
        self.pipeline = {"retry_budget": 3, "pipelines": {
            "build": BUILD_PIPELINE["pipelines"]["build"],
            "plan": {"stages": [
                {"name": "formalize", "owner": "architect"},
                {"name": "approval", "owner": "ceo", "checkpoints": ["plan"]},
                {"name": "breakdown", "owner": "product-manager"},
                {"name": "done", "owner": "ceo"}]}}}
        conn = state.connect()
        state.create_run(conn, OTHER, "feature", "breakdown", state.PLAN)
        state.set_fields(conn, OTHER, plan_approved=1)
        conn.close()
        self.approve("--reject", "--reason", "needs rework", task=OTHER)
        row = self.row(OTHER)
        self.assertEqual("formalize", row["stage"])
        self.assertEqual(0, row["plan_approved"])


class PlanPendingTest(Harness):

    def run_row(self, stage, **fields) -> dict:
        return {"task": TASK, "stage": stage, "pipeline": "build", "plan_approved": 0, **fields}

    def test_pending_from_the_first_stage_through_the_checkpoint_stage(self):
        for stage in ("plan", "plan-approval"):
            with self.subTest(stage=stage):
                self.assertTrue(pretool_gate.plan_pending(self.run_row(stage), BUILD_PIPELINE))

    def test_not_pending_past_the_checkpoint_stage(self):
        for stage in ("implement", "test", "review", "ready", "done"):
            with self.subTest(stage=stage):
                self.assertFalse(pretool_gate.plan_pending(self.run_row(stage), BUILD_PIPELINE))

    def test_not_pending_once_approved(self):
        for stage in ("plan", "plan-approval"):
            with self.subTest(stage=stage):
                self.assertFalse(pretool_gate.plan_pending(
                    self.run_row(stage, plan_approved=1), BUILD_PIPELINE))

    def test_not_pending_without_the_stage_in_the_config(self):
        self.assertFalse(pretool_gate.plan_pending(self.run_row("implement"), OLD_BUILD_PIPELINE))
        self.assertFalse(pretool_gate.plan_pending(self.run_row("plan"), OLD_BUILD_PIPELINE))
        self.assertFalse(pretool_gate.plan_pending(self.run_row("plan"), {}))

    def test_not_pending_for_an_unknown_stage_a_missing_run_or_the_plan_flow(self):
        self.assertFalse(pretool_gate.plan_pending(self.run_row("elsewhere"), BUILD_PIPELINE))
        self.assertFalse(pretool_gate.plan_pending(None, BUILD_PIPELINE))
        self.assertFalse(pretool_gate.plan_pending(
            self.run_row("plan", pipeline="plan"), BUILD_PIPELINE))

    def test_pending_tasks_lists_only_the_runs_that_owe_the_approval(self):
        self.seed(TASK, stage="plan")
        self.seed(OTHER, stage="implement")
        self.seed("task-0802", stage="plan-approval", plan_approved=1)
        self.assertEqual([TASK], pretool_gate.plan_pending_tasks())

    def test_pending_task_for_a_branch(self):
        self.seed(TASK, stage="plan")
        self.seed(OTHER, stage="implement")
        self.assertEqual(TASK, pretool_gate.plan_pending_task(BRANCH))
        self.assertEqual("", pretool_gate.plan_pending_task("feature/task-0801"))
        self.assertEqual("", pretool_gate.plan_pending_task("main"))
        self.assertEqual("", pretool_gate.plan_pending_task(""))


class PlanGateTest(Harness):
    """The deny holds before the approval, lifts after it, and is scoped to the
    task whose branch is checked out."""

    def setUp(self):
        super().setUp()
        self.branch = self._patch(pretool_gate, "current_branch", return_value=BRANCH)
        self.seed(stage="plan")

    def edit(self, path, **kwargs) -> int:
        with contextlib.redirect_stderr(io.StringIO()):
            return pretool_gate.handle_edit(path, "x = 1\n", **kwargs)

    def bash(self, command) -> int:
        with contextlib.redirect_stderr(io.StringIO()):
            return pretool_gate.handle_bash(command, str(PROJECT_ROOT))

    # --- Edit / Write ----------------------------------------------------------

    def test_code_edits_are_denied_before_the_approval(self):
        for path in ("src/app/main.py", ".claude/tools/pipeline/advance.py", "docs/guide.md",
                     "README.md", str(PROJECT_ROOT / "app.py")):
            with self.subTest(path=path):
                self.assertEqual(2, self.edit(path))

    def test_the_deny_names_the_task_the_path_and_the_way_out(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            pretool_gate.handle_edit("src/app/main.py", "x = 1\n")
        text = err.getvalue()
        self.assertIn(TASK, text)
        self.assertIn("src/app/main.py", text)
        self.assertIn("--gate plan", text)

    def test_the_plan_and_the_task_files_stay_writable(self):
        for path in (PLAN_FILE, f".agentry/tasks/active/{TASK}.md",
                     str(PROJECT_ROOT / PLAN_FILE)):
            with self.subTest(path=path):
                self.assertEqual(0, self.edit(path))

    def test_a_dotdot_path_cannot_ride_the_agentry_prefix(self):
        self.assertEqual(2, self.edit(".agentry/../src/app/main.py"))

    def test_the_deny_applies_at_the_checkpoint_stage_too(self):
        conn = state.connect()
        state.set_fields(conn, TASK, stage="plan-approval")
        conn.close()
        self.assertEqual(2, self.edit("src/app/main.py"))

    def test_the_deny_lifts_when_the_approval_is_recorded_through_approve(self):
        self.assertEqual(2, self.edit("src/app/main.py"))
        self.approve("--gate", "plan")
        self.assertEqual(0, self.edit("src/app/main.py"))

    def test_the_deny_never_reaches_a_task_past_its_plan(self):
        self.seed(OTHER, stage="implement")
        self.branch.return_value = "feature/task-0801"
        self.assertEqual(0, self.edit("src/app/main.py"))

    def test_a_task_that_skipped_the_plan_is_not_held(self):
        self.seed(OTHER, stage="implement", task_type="bugfix")
        self.branch.return_value = "feature/task-0801"
        self.assertEqual(0, self.edit("src/app/main.py"))

    def test_a_branch_with_no_task_is_not_held(self):
        self.branch.return_value = "main"
        self.assertEqual(0, self.edit("src/app/main.py"))
        self.branch.return_value = ""
        self.assertEqual(0, self.edit("src/app/main.py"))

    def test_a_config_without_the_step_changes_nothing(self):
        self.pipeline = OLD_BUILD_PIPELINE
        self.assertEqual(0, self.edit("src/app/main.py"))

    def test_the_main_thread_is_held_too(self):
        self.pipeline = dict(BUILD_PIPELINE, orchestrator_gate={"enabled": False})
        self.assertEqual(2, self.edit("src/app/main.py", orch=True))

    def test_every_file_writing_tool_is_covered_through_main(self):
        payloads = {
            "Write": {"file_path": "src/app/main.py", "content": "x"},
            "Edit": {"file_path": "src/app/main.py", "new_string": "x"},
            "MultiEdit": {"file_path": "src/app/main.py", "edits": [{"new_string": "x"}]},
            "NotebookEdit": {"notebook_path": "src/app/n.ipynb", "new_source": "x"},
        }
        for tool, tool_input in payloads.items():
            with self.subTest(tool=tool), \
                    unittest.mock.patch.object(pretool_gate, "read_payload", return_value={
                        "tool_name": tool, "tool_input": tool_input, "agent_type": "dev"}), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, pretool_gate.main())

    def test_the_dev_agent_profile_is_held_for_edits_and_shell_writes(self):
        with contextlib.redirect_stderr(io.StringIO()):
            edit = agent_gate.handle_dev("Write", {"file_path": "src/app/main.py",
                                                   "content": "x"}, cwd=".")
            shell = agent_gate.handle_dev("Bash", {"command": "echo x > src/app/main.py"},
                                          cwd=str(PROJECT_ROOT))
            plan = agent_gate.handle_dev("Write", {"file_path": PLAN_FILE, "content": "x"},
                                         cwd=".")
        self.assertEqual((2, 2, 0), (edit, shell, plan))

    # --- Bash --------------------------------------------------------------------

    def test_shell_spellings_of_a_file_write_are_denied(self):
        for command in (
                "echo x > src/app/main.py",
                "echo x >> src/app/main.py",
                "cat a.txt | tee src/app/main.py",
                "sed -i s/a/b/ src/app/main.py",
                "patch -p1 < fix.diff",
                "git apply fix.diff",
                "cp a.py src/app/main.py",
                "mv a.py b.py",
                "rm src/app/main.py",
                "mkdir src/new",
                "git checkout -- src/app/main.py",
                "git restore src/app/main.py",
                "git reset --hard HEAD",
                "git stash pop",
                "bash -c 'echo x > src/app/main.py'",
        ):
            with self.subTest(command=command):
                self.assertEqual(2, self.bash(command))

    def test_reads_and_the_plan_file_are_allowed(self):
        for command in (
                "git status",
                "git log --oneline -5",
                "git diff HEAD -- src/app/main.py",
                "ls -la src",
                "grep -rn plan .claude/rules",
                "python -m unittest test_plan_checkpoint",
                f"echo plan > {PLAN_FILE}",
                f"cat notes.txt | tee {PLAN_FILE}",
                "ls src 2>&1",
        ):
            with self.subTest(command=command):
                self.assertEqual(0, self.bash(command))

    def test_the_session_nul_cleanup_is_allowed_in_any_order_and_case(self):
        for command in ("rm -f nul NUL", "rm -f NUL nul", "rm -f nul", "rm -f Nul NUL nUl"):
            with self.subTest(command=command):
                self.assertEqual(0, self.bash(command))

    def test_the_nul_exemption_covers_only_the_bare_device_names(self):
        for command in ("rm -f nul src/app/main.py", "rm -f src/nul", "rm -f nul NUL main.py",
                        "mv nul src/app/main.py", "echo nul | xargs rm"):
            with self.subTest(command=command):
                self.assertEqual(2, self.bash(command))

    def test_a_mutating_command_whose_every_operand_is_writable_passes(self):
        for command in (f"rm {PLAN_FILE}", "mkdir .agentry/tmp", "mkdir -p .agentry/plans/x",
                        "cp .agentry/a.md .agentry/b.md"):
            with self.subTest(command=command):
                self.assertEqual(0, self.bash(command))

    def test_a_mutating_command_with_one_unwritable_operand_stays_denied(self):
        for command in ("rm src.py", "rm .agentry/a.md src.py", "mkdir .agentry/../src/new",
                        "mkdir tmp", "rm $HOME/.agentry/x", "rm"):
            with self.subTest(command=command):
                self.assertEqual(2, self.bash(command))

    def test_a_config_without_the_step_never_reaches_the_command_scan(self):
        self.pipeline = OLD_BUILD_PIPELINE
        with unittest.mock.patch.object(pretool_gate, "plan_mutating_command") as scan:
            self.assertEqual("", pretool_gate.plan_bash_write("rm src.py"))
            self.assertEqual("", pretool_gate.plan_bash_write("echo x > src/app/main.py"))
        scan.assert_not_called()

    def test_the_shell_deny_lifts_after_the_approval(self):
        self.assertEqual(2, self.bash("echo x > src/app/main.py"))
        self.approve("--gate", "plan")
        self.assertEqual(0, self.bash("echo x > src/app/main.py"))

    def test_the_shell_deny_is_scoped_to_the_checked_out_task(self):
        self.seed(OTHER, stage="implement")
        self.branch.return_value = "feature/task-0801"
        self.assertEqual(0, self.bash("echo x > src/app/main.py"))

    def test_commit_denial_is_unchanged(self):
        self.assertEqual(2, self.bash("git commit -m 'x'"))


class HandoffFreezeStagesTest(Harness):
    """handoff_freeze_task() asks the run's own flow which stages are editing."""

    def freeze(self, pipeline) -> str:
        import handoff
        self.pipeline = pipeline
        with unittest.mock.patch.object(handoff, "cfg", return_value={"hard_edit_gate": True}), \
                unittest.mock.patch.object(handoff, "uncovered_done_tasks",
                                           return_value=[{"task": "task-0999"}]):
            return pretool_gate.handoff_freeze_task()

    def test_a_run_in_a_stage_the_config_does_not_call_editing_does_not_lift_the_freeze(self):
        narrow = copy.deepcopy(BUILD_PIPELINE)
        narrow["pipelines"]["build"]["editing_stages"] = ["implement"]
        self.seed(stage="review", plan_approved=1)
        self.assertEqual("task-0999", self.freeze(narrow))

    def test_a_run_in_a_configured_editing_stage_lifts_the_freeze(self):
        self.seed(stage="review", plan_approved=1)
        self.assertEqual("", self.freeze(BUILD_PIPELINE))

    def test_a_plan_flow_run_is_judged_by_the_plan_flows_stages(self):
        both = copy.deepcopy(BUILD_PIPELINE)
        both["pipelines"]["plan"] = {"editing_stages": ["formalize"], "stages": [
            {"name": "formalize", "owner": "architect"}, {"name": "done", "owner": "ceo"}]}
        conn = state.connect()
        state.create_run(conn, OTHER, "feature", "formalize", state.PLAN)
        conn.close()
        self.assertEqual("", self.freeze(both))


class FailOpenTest(Harness):

    def setUp(self):
        super().setUp()
        self._patch(pretool_gate, "current_branch", return_value=BRANCH)
        self.seed(stage="plan")
        self.write_payload = {"tool_name": "Write", "agent_type": "dev",
                              "tool_input": {"file_path": "src/app/main.py", "content": "x"}}

    def main_exit(self) -> int:
        with unittest.mock.patch.object(pretool_gate, "read_payload",
                                        return_value=self.write_payload), \
                contextlib.redirect_stderr(io.StringIO()):
            return pretool_gate.main()

    def test_precondition_the_deny_holds_with_a_readable_store(self):
        self.assertEqual(2, self.main_exit())

    def test_an_unreadable_run_store_allows(self):
        broken = self.tmp / "broken.db"
        broken.write_bytes(b"this is not a sqlite database" * 40)
        self._patch(state, "DB_PATH", broken)
        self.assertEqual([], pretool_gate.plan_pending_tasks())
        self.assertEqual("", pretool_gate.plan_blocked_task(path="src/app/main.py"))
        self.assertEqual(0, self.main_exit())

    def test_a_run_store_path_that_cannot_be_opened_allows(self):
        folder = self.tmp / "a_directory"
        folder.mkdir()
        self._patch(state, "DB_PATH", folder)
        self.assertEqual([], pretool_gate.plan_pending_tasks())
        self.assertEqual(0, self.main_exit())

    def test_an_unreadable_pipeline_config_allows(self):
        self.pipeline = {}
        self.assertEqual([], pretool_gate.plan_pending_tasks())
        self.assertEqual(0, self.main_exit())

    def test_a_checker_that_raises_allows(self):
        with unittest.mock.patch.object(pretool_gate, "plan_pending", side_effect=RuntimeError):
            self.assertEqual([], pretool_gate.plan_pending_tasks())
            self.assertEqual(0, self.main_exit())


class RestartTest(Harness):
    """The deny is read from run.db, not from anything a session holds in memory."""

    def test_a_fresh_process_sees_the_pending_plan_and_then_the_approval(self):
        config = self.tmp / "pipeline.json"
        config.write_text(json.dumps(BUILD_PIPELINE), encoding="utf-8")
        self.seed(stage="plan-approval")

        def fresh_process() -> list:
            script = (
                "import sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(PIPELINE_DIR)!r})\n"
                "import state, pretool_gate\n"
                f"state.STATE_DIR = Path({str(self.tmp)!r})\n"
                f"state.DB_PATH = Path({str(self.tmp / 'run.db')!r})\n"
                f"state.PIPELINE_PATH = Path({str(config)!r})\n"
                "print(pretool_gate.plan_pending_tasks())\n")
            proc = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True,
                                  text=True, timeout=60, check=True)
            return json.loads(proc.stdout.replace("'", '"'))

        self.assertEqual([TASK], fresh_process())
        self.approve("--gate", "plan")
        self.assertEqual([], fresh_process())


class MergeWaitWordingTest(Harness):
    """Relayed from task-0031: an empty merge report names the repo: value."""

    def park_message(self, repo, resolved) -> str:
        self.seed(stage="done")
        repos = [Path("somewhere")] if resolved else []
        with unittest.mock.patch.object(git_state, "task_report", return_value=[]), \
                unittest.mock.patch.object(git_state, "repos_for_task", return_value=repos), \
                unittest.mock.patch.object(state, "task_repo", return_value=repo):
            out = self.cli(advance, ["advance.py", "--task", TASK])[1]
        self.assertEqual("park", out["action"])
        return out["message"]

    def test_an_unresolved_repo_value_is_named_as_the_cause(self):
        message = self.park_message("agentri", resolved=False)
        self.assertIn("repo: 'agentri' did not resolve to any known repo", message)
        self.assertIn("so no repo was searched", message)

    def test_a_resolved_repo_value_is_shown_but_not_blamed(self):
        message = self.park_message("agentry", resolved=True)
        self.assertIn("(task repo: 'agentry')", message)
        self.assertNotIn("did not resolve", message)

    def test_a_task_without_a_repo_keeps_the_old_wording(self):
        message = self.park_message("", resolved=True)
        self.assertIn("no branch matching this task id exists in any repo, and the task "
                      "declares no 'branch:'", message)
        self.assertNotIn("repo:", message.split("(1)", 1)[1].split("(2)", 1)[0])


class RealConfigTest(unittest.TestCase):
    """Pins the shipped pipeline.json whichever side of the config change it is on:
    unchanged and plan-less, or carrying the two stages in their agreed shape."""

    @classmethod
    def setUpClass(cls):
        path = PROJECT_ROOT / ".agentry" / "pipeline.json"
        cls.real = json.loads(path.read_text(encoding="utf-8"))
        cls.build = cls.real["pipelines"]["build"]
        cls.names = [s["name"] for s in cls.build["stages"]]

    def has_plan_step(self) -> bool:
        return pretool_gate.plan_checkpoint_index(self.real) is not None

    def test_the_plan_step_when_present_has_the_agreed_shape(self):
        if not self.has_plan_step():
            self.skipTest("the plan step has not been applied to pipeline.json yet")
        by_name = {s["name"]: s for s in self.build["stages"]}
        self.assertEqual(["plan", "plan-approval"], self.names[:2])
        self.assertEqual(["plan"], by_name["plan-approval"]["checkpoints"])
        self.assertEqual(by_name["plan"]["skip_for_types"], by_name["plan-approval"]["skip_for_types"])
        self.assertEqual(["bugfix", "techdebt"], by_name["plan"]["skip_for_types"])
        self.assertIn("artifact_gate.py --task {task} --kind plan",
                      by_name["plan"]["exit_gate"]["cmd"])
        self.assertIn("plan", self.build["editing_stages"])
        self.assertNotIn("plan-approval", self.build["editing_stages"])

    def test_the_plan_step_when_present_sits_in_front_of_implement(self):
        if not self.has_plan_step():
            self.skipTest("the plan step has not been applied to pipeline.json yet")
        self.assertLess(self.names.index("plan-approval"), self.names.index("implement"))

    def test_registration_follows_the_real_config_either_way(self):
        start = advance.start_stage(self.real, "build", "feature")
        self.assertEqual("plan" if self.has_plan_step() else "implement", start)
        self.assertEqual("implement", advance.start_stage(self.real, "build", "bugfix"))
        self.assertEqual("implement", advance.start_stage(self.real, "build", "techdebt"))

    def test_the_plan_is_still_never_granted_and_the_never_granted_list_is_unchanged(self):
        self.assertIn(approvals.PLAN, approvals.ALWAYS_WITHHELD)
        self.assertIn(approvals.PLAN, approvals.never_granted())
        self.assertEqual(["push", "trunk_push"], self.real["approvals_never_granted"])
        for level in approvals.LEVELS:
            with self.subTest(level=level), \
                    unittest.mock.patch.object(approvals, "read", return_value=level):
                self.assertFalse(approvals.granted(approvals.PLAN, [approvals.PLAN]))


if __name__ == "__main__":
    unittest.main()
