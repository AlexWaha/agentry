"""task-0028, FR-43 and FR-44: stack_gate.py resolves its commands and paths from
the `repos{}` config, and an empty stage is an explicit pass, never a failure.

Each test writes its own config into a temp workspace, so none of them depends
on the real pipeline.json carrying `repos`.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
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
import tmproot


def exits(code: int) -> str:
    """A shell command that exits with `code`, whatever the host shell is."""
    return f'"{sys.executable}" -c "import sys; sys.exit({code})"'


class StackGateTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "stack_gate_").resolve()
        (self.root / ".agentry").mkdir()
        for name, value in (("ROOT", self.root),
                            ("PIPELINE_PATH", self.root / ".agentry" / "pipeline.json")):
            patcher = unittest.mock.patch.object(state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def config(self, repos):
        (self.root / ".agentry" / "pipeline.json").write_text(
            json.dumps({"repos": repos}), encoding="utf-8")

    def gate(self, stage="test", repo="agentry", task="task-0100", via_flag=False):
        """Drive the CLI entry point; returns (exit code, stdout)."""
        argv = ["stack_gate.py", "--task", task, "--stage", stage]
        if via_flag:
            argv += ["--repo", repo]
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", argv), \
                unittest.mock.patch.object(state, "task_repo", return_value=repo), \
                contextlib.redirect_stdout(out):
            return stack_gate.main(), out.getvalue()

    # --- FR-43: empty is a pass, loudly -------------------------------------

    def test_an_empty_command_list_passes_and_names_the_repo_and_stage(self):
        self.config({"agentry": {"path": ".", "stack": "python",
                                 "stages": {"test": []}}})
        with unittest.mock.patch.object(stack_gate, "run") as never_run:
            code, printed = self.gate("test")
        self.assertEqual(0, code)
        never_run.assert_not_called()
        self.assertEqual("agentry: no test gate - explicit pass\n", printed)

    def test_an_absent_stage_key_behaves_exactly_like_an_empty_array(self):
        self.config({"agentry": {"path": ".", "stages": {"test": []}}})
        empty = self.gate("test")
        self.config({"agentry": {"path": "."}})
        absent = self.gate("test")
        self.config({"agentry": {"path": ".", "stages": {"review": ["x"]}}})
        other_stage_only = self.gate("test")
        self.assertEqual(empty, absent)
        self.assertEqual(empty, other_stage_only)
        self.assertEqual(0, empty[0])

    def test_an_empty_list_never_fails_in_any_stage(self):
        self.config({"agentry": {"path": "."}})
        for stage in stack_gate.STAGES:
            code, printed = self.gate(stage)
            self.assertEqual(0, code, stage)
            self.assertIn(f"agentry: no {stage} gate - explicit pass", printed)

    # --- FR-44: commands and paths come from the config ---------------------

    def test_the_commands_run_in_the_repo_path_and_return_their_exit_code(self):
        (self.root / "svc").mkdir()
        self.config({"agentry": {"path": "svc", "stages": {"test": ["the suite"]}}})
        with unittest.mock.patch.object(stack_gate, "run", return_value=3) as ran:
            code, printed = self.gate("test")
        self.assertEqual(3, code)
        ran.assert_called_once_with(["the suite"], self.root / "svc")
        self.assertEqual("", printed)

    def test_a_real_failing_command_fails_the_gate_and_a_passing_one_does_not(self):
        self.config({"agentry": {"path": ".", "stages": {
            "test": [exits(3)], "review": [exits(0)]}}})
        self.assertEqual(3, self.gate("test")[0])
        self.assertEqual(0, self.gate("review")[0])

    def test_the_first_failing_command_stops_the_list(self):
        self.config({"agentry": {"path": ".", "stages": {
            "test": [exits(0), exits(5), exits(7)]}}})
        code, printed = self.gate("test")
        self.assertEqual(5, code)
        self.assertIn(exits(5), printed)
        self.assertNotIn(exits(7), printed)

    def test_a_second_repo_with_another_stack_changes_what_runs_without_a_code_edit(self):
        (self.root / "api").mkdir()
        (self.root / "web").mkdir()
        self.config({
            "api": {"path": "api", "stack": "python", "stages": {"test": ["pytest"]}},
            "web": {"path": "web", "stack": "node", "stages": {"test": ["npm test"]}},
        })
        with unittest.mock.patch.object(stack_gate, "run", return_value=0) as ran:
            self.gate("test", repo="api")
            self.gate("test", repo="web")
        self.assertEqual([unittest.mock.call(["pytest"], self.root / "api"),
                          unittest.mock.call(["npm test"], self.root / "web")],
                         ran.call_args_list)

    def test_the_repo_flag_overrides_the_task_file(self):
        (self.root / "web").mkdir()
        self.config({"api": {"path": "."}, "web": {"path": "web",
                                                   "stages": {"test": ["npm test"]}}})
        with unittest.mock.patch.object(stack_gate, "run", return_value=0) as ran:
            self.gate("test", repo="web", via_flag=True)
        ran.assert_called_once_with(["npm test"], self.root / "web")

    def test_the_stack_tables_are_gone(self):
        self.assertFalse(hasattr(stack_gate, "STACKS"))
        self.assertFalse(hasattr(stack_gate, "STACK_CWD"))

    # --- resolving the task's `repo:` to a key ------------------------------

    def test_a_repo_value_resolves_by_key_by_first_segment_or_by_path(self):
        entries = {"backend": {"path": "services/api"}, "agentry": {"path": "src"}}
        resolve = stack_gate.resolve_key
        self.assertEqual("backend", resolve(entries, "backend"))
        self.assertEqual("backend", resolve(entries, "backend/src"))
        self.assertEqual("backend", resolve(entries, "/backend/"))
        self.assertEqual("agentry", resolve(entries, "src"))
        self.assertEqual("", resolve(entries, "frontend"))
        self.assertEqual("", resolve(entries, ""))

    # --- fail open: NFR-4 ---------------------------------------------------

    def test_no_repos_config_is_a_not_configured_pass_that_says_nothing_was_checked(self):
        (self.root / ".agentry" / "pipeline.json").write_text("{}", encoding="utf-8")
        code, printed = self.gate("test")
        self.assertEqual(0, code)
        self.assertIn("NOT CONFIGURED", printed)
        self.assertIn("nothing was checked", printed)

    def test_an_unknown_repo_fails_when_repos_is_configured_and_names_the_repo(self):
        self.config({"agentry": {"path": ".", "stages": {"test": ["x"]}}})
        with unittest.mock.patch.object(stack_gate, "run") as never_run:
            code, printed = self.gate("test", repo="elsewhere")
        self.assertNotEqual(0, code)
        never_run.assert_not_called()
        self.assertIn("FAILED", printed)
        self.assertNotIn("NOT CONFIGURED", printed)
        self.assertIn("'elsewhere'", printed)
        self.assertIn("agentry", printed)

    def test_a_task_with_no_repo_fails_when_repos_is_configured(self):
        self.config({"agentry": {"path": ".", "stages": {"test": ["x"]}}})
        with unittest.mock.patch.object(stack_gate, "run") as never_run:
            code, printed = self.gate("test", repo="")
        self.assertNotEqual(0, code)
        never_run.assert_not_called()
        self.assertIn("'unknown'", printed)

    def test_an_unresolved_repo_fails_in_every_stage_even_with_empty_stage_lists(self):
        self.config({"agentry": {"path": ".", "stages": {"test": []}}})
        for stage in stack_gate.STAGES:
            for repo in ("", "elsewhere", "../agentry"):
                with self.subTest(stage=stage, repo=repo):
                    self.assertNotEqual(0, self.gate(stage, repo=repo)[0])

    def test_a_configured_empty_stage_still_passes_for_a_resolved_repo(self):
        self.config({"agentry": {"path": ".", "stages": {"test": []}}})
        self.assertEqual(0, self.gate("test", repo="agentry")[0])

    def test_the_repo_flag_naming_an_unknown_key_fails_too(self):
        self.config({"agentry": {"path": ".", "stages": {"test": ["x"]}}})
        code, _ = self.gate("test", repo="elsewhere", via_flag=True)
        self.assertNotEqual(0, code)

    def test_commands_on_a_path_that_is_not_a_directory_fail_loudly_not_silently(self):
        self.config({"agentry": {"path": "missing", "stages": {"test": ["the suite"]}}})
        with unittest.mock.patch.object(stack_gate, "run") as never_run:
            code, printed = self.gate("test")
        self.assertEqual(1, code)
        never_run.assert_not_called()
        self.assertIn("not a directory", printed)

    def test_a_malformed_entry_degrades_to_a_diagnostic_pass(self):
        self.config({"agentry": {"stages": {"test": ["x"]}}})
        with contextlib.redirect_stderr(io.StringIO()):
            code, printed = self.gate("test")
        self.assertEqual(0, code)
        self.assertIn("NOT CONFIGURED", printed)


if __name__ == "__main__":
    unittest.main()
