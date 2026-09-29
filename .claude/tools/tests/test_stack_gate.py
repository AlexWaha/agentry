"""task-0004, FR-6 stack_gate.py: a stage's gate resolved from the task's `repo:`,
and a no-op rather than a red gate for a repo with no application stack.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import stack_gate
import state


class StackGateTest(unittest.TestCase):
    """FR-6. The template ships the mechanism with an EMPTY stack table, so the
    honest default for every repo is a no-op gate rather than another stack's
    suite. task-0029 (FR-44) moves the table into pipeline.json."""

    def test_template_ships_no_project_literals(self):
        self.assertEqual({}, stack_gate.STACKS)
        self.assertEqual({}, stack_gate.STACK_CWD)

    def test_an_unconfigured_repo_has_no_commands(self):
        self.assertEqual([], stack_gate.commands("infra", "test"))

    def test_a_configured_repo_dispatches_its_own_commands(self):
        with unittest.mock.patch.object(
                stack_gate, "STACKS",
                {"backend": {"test": ["run the backend suite"]}}):
            self.assertEqual(["run the backend suite"],
                             stack_gate.commands("backend", "test"))

    def test_a_subdirectory_value_resolves_to_its_repo(self):
        # Task files carry both `backend` and `backend/src` for one repo.
        with unittest.mock.patch.object(
                stack_gate, "STACKS", {"backend": {"test": ["x"]}}):
            self.assertEqual("backend", stack_gate.resolve_stack("backend/src"))
            self.assertEqual("backend", stack_gate.resolve_stack("/backend/"))

    def test_a_stage_the_stack_omits_is_a_no_op_not_an_error(self):
        with unittest.mock.patch.object(
                stack_gate, "STACKS", {"backend": {"test": ["x"]}}):
            self.assertEqual([], stack_gate.commands("backend", "review"))

    def _main(self, task="task-0100", stage="test"):
        argv = ["stack_gate.py", "--task", task, "--stage", stage]
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out):
            return stack_gate.main(), out.getvalue()

    def test_an_empty_table_exits_zero_and_runs_nothing(self):
        # The property that matters: with no stack configured the gate is a
        # no-op that PASSES, and it says so rather than implying it checked.
        with unittest.mock.patch.object(state, "task_repo", return_value="infra"), \
                unittest.mock.patch.object(stack_gate, "run") as never_run:
            code, printed = self._main()
        self.assertEqual(0, code)
        never_run.assert_not_called()
        self.assertIn("NOT CONFIGURED", printed)

    def test_a_configured_repo_runs_its_commands_and_returns_their_code(self):
        cwd = stack_gate.ROOT / "backend"
        with unittest.mock.patch.object(
                    stack_gate, "STACKS", {"backend": {"test": ["the suite"]}}), \
                unittest.mock.patch.object(stack_gate, "STACK_CWD", {"backend": cwd}), \
                unittest.mock.patch.object(state, "task_repo", return_value="backend"), \
                unittest.mock.patch.object(stack_gate, "run", return_value=3) as ran:
            code, printed = self._main()
        self.assertEqual(3, code)
        ran.assert_called_once_with(["the suite"], cwd)
        self.assertEqual("", printed)

    def test_a_missing_cwd_entry_falls_back_to_the_workspace_root(self):
        # A half-filled table degrades to a working gate rather than raising.
        with unittest.mock.patch.object(
                    stack_gate, "STACKS", {"backend": {"test": ["the suite"]}}), \
                unittest.mock.patch.object(stack_gate, "STACK_CWD", {}), \
                unittest.mock.patch.object(state, "task_repo", return_value="backend"), \
                unittest.mock.patch.object(stack_gate, "run", return_value=0) as ran:
            code, _ = self._main()
        self.assertEqual(0, code)
        ran.assert_called_once_with(["the suite"], stack_gate.ROOT)


if __name__ == "__main__":
    unittest.main()
