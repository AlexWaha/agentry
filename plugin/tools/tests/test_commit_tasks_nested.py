"""Security fix L1 (umbrella task-0118): pretool_gate.commit_tasks recurses into
nested shell bodies, so a commit hidden in `sh -c` / `bash -c` / eval / a
substitution is counted and judged as the task its message tags.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import pretool_gate

BRANCH = "feature/task-0200"
OWN = "task-0200"


def tasks(command: str) -> list:
    return pretool_gate.commit_tasks(command, BRANCH)


class NestedCommitTest(unittest.TestCase):

    def test_a_tag_inside_sh_c_is_read(self):
        self.assertEqual(["task-0999"], tasks("sh -c 'git commit -m \"[task-0999] x\"'"))

    def test_a_tag_inside_bash_c_is_read(self):
        self.assertEqual(["task-0999"], tasks("bash -c \"git commit -m '[task-0999] x'\""))

    def test_an_untagged_commit_inside_bash_c_is_judged_as_the_branch_task(self):
        self.assertEqual([OWN], tasks("bash -c 'git commit -m plain'"))

    def test_a_hidden_commit_is_counted_next_to_a_visible_one(self):
        command = "git commit -m '[task-0100] a' && bash -c 'git commit -m \"[task-0999] b\"'"
        self.assertEqual(["task-0100", "task-0999"], tasks(command))

    def test_two_nested_levels_are_walked(self):
        command = "sh -c \"bash -c 'git commit -m \\\"[task-0999] x\\\"'\""
        self.assertEqual(["task-0999"], tasks(command))

    def test_a_commit_in_an_eval_operand_is_counted(self):
        self.assertEqual(["task-0999"], tasks("eval 'git commit -m \"[task-0999] x\"'"))

    def test_a_commit_in_a_substitution_is_counted(self):
        self.assertIn("task-0999", tasks("echo $(git commit -m '[task-0999] x')"))

    def test_a_nested_shell_without_a_commit_adds_nothing(self):
        self.assertEqual([OWN], tasks("bash -c 'git status'"))
        self.assertEqual([OWN], tasks("bash -c 'echo hi'"))

    def test_a_plain_commit_is_unchanged(self):
        self.assertEqual(["task-0100"], tasks("git commit -m '[task-0100] x'"))
        self.assertEqual([OWN], tasks("git commit -m x"))

    def test_past_the_depth_ceiling_the_unread_commit_falls_back_to_the_branch(self):
        inner = "git commit -m '[task-0999] x'"
        command = inner
        for _ in range(pretool_gate.MAX_SHELL_DEPTH + 2):
            command = "bash -c " + _quote(command)
        self.assertIn(OWN, tasks(command))


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    unittest.main()
