"""task-0120 SEC-C (M4): a move that crashed between the exclusive create and the
unlink leaves two copies; a retry finishes it when they match and the Stop hook
names it when they do not."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import state
import stop_gate
import tmproot
from test_conveyor_gaps import decide_output_with


class MoveRetryTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "move_conf_")
        self.dirs = {}
        for name in ("backlog", "active", "done"):
            self.dirs[name] = self.tmp / name
            self.dirs[name].mkdir()
        patcher = unittest.mock.patch.object(state, "TASK_DIRS", self.dirs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, folder, text, task="task-0050"):
        (self.dirs[folder] / f"{task}.md").write_text(text, encoding="utf-8")

    def test_identical_copy_in_done_finishes_the_move(self):
        self.write("active", "id: 50\ncompleted:\nbody\n")
        self.write("done", "id: 50\ncompleted:\nbody\n")
        self.assertTrue(state.move_task("task-0050", "done"))
        self.assertFalse((self.dirs["active"] / "task-0050.md").exists())
        self.assertEqual("id: 50\ncompleted:\nbody\n",
                         (self.dirs["done"] / "task-0050.md").read_text(encoding="utf-8"))

    def test_copy_stamped_on_another_day_still_finishes_the_move(self):
        self.write("active", "id: 50\ncompleted:\nbody\n")
        self.write("done", "id: 50\ncompleted: 2020-01-01\nbody\n")
        self.assertTrue(state.move_task("task-0050", "done"))
        self.assertFalse((self.dirs["active"] / "task-0050.md").exists())
        self.assertIn("2020-01-01", (self.dirs["done"] / "task-0050.md").read_text(encoding="utf-8"))

    def test_different_copy_in_done_is_still_refused_and_both_stay(self):
        self.write("active", "id: 50\ncompleted:\nfrom active\n")
        self.write("done", "id: 50\ncompleted:\ndone original\n")
        self.assertFalse(state.move_task("task-0050", "done"))
        self.assertTrue((self.dirs["active"] / "task-0050.md").is_file())
        self.assertIn("done original", (self.dirs["done"] / "task-0050.md").read_text(encoding="utf-8"))

    def test_move_conflicts_lists_only_the_differing_pairs(self):
        self.write("active", "id: 50\nfrom active\n", "task-0050")
        self.write("done", "id: 50\ndone original\n", "task-0050")
        self.write("backlog", "id: 51\nsame\n", "task-0051")
        self.write("done", "id: 51\nsame\n", "task-0051")
        self.write("done", "id: 52\nalone\n", "task-0052")
        self.assertEqual(["task-0050"], state.move_conflicts())

    def test_move_conflicts_is_empty_without_twins(self):
        self.write("done", "id: 52\nalone\n", "task-0052")
        self.assertEqual([], state.move_conflicts())


class StopHookNamesTheConflictTest(unittest.TestCase):

    def decide(self, conflicts):
        with unittest.mock.patch.object(stop_gate.state, "move_conflicts",
                                        return_value=conflicts):
            return decide_output_with([], queue=[])

    def test_conflict_is_named_in_the_block_reason(self):
        out = self.decide(["task-0050"])
        self.assertEqual("block", out["decision"])
        self.assertIn("task-0050", out["reason"])
        self.assertIn("differ", out["reason"])

    def test_no_conflict_means_no_block(self):
        self.assertIsNone(self.decide([]))


if __name__ == "__main__":
    unittest.main()
