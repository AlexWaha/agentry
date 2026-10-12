"""task-0063: a fresh checkout is a clean slate, not handoff debt.

`.agentry/tasks/handoffs/` and `.agentry/tasks/done/` are gitignored (CEO
decision 2026-10-06), so a clone carries the tagged history of main and none of
the documents. `latest_main_task_undocumented()` read that history and demanded
a doc for a task the checkout never ran.

Clean slate means BOTH no handoff doc on disk AND an empty tasks/done/. Either
condition alone still reports, and each of those three cases has a test: a
guard that is only checked from the exempt side gets widened by the next edit.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = TESTS_DIR.parent / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import handoff  # noqa: E402 - path set above
import tmproot  # noqa: E402 - arms the temp-file policy

CONFIG = {"enabled": True, "document_latest_on_start": True, "baseline": ""}
LOG = "a" * 40 + "\t[task-0042] fix: something merged\n"


class CleanSlateTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "handoff-clean-slate")
        self.handoffs = self.root / ".agentry" / "tasks" / "handoffs"
        self.done = self.root / ".agentry" / "tasks" / "done"
        self.handoffs.mkdir(parents=True)
        self.done.mkdir(parents=True)
        # .gitkeep is what a clone really holds in both folders.
        (self.handoffs / ".gitkeep").write_text("", encoding="utf-8")
        (self.done / ".gitkeep").write_text("", encoding="utf-8")
        for patcher in (
            unittest.mock.patch.object(handoff.state, "ROOT", self.root),
            unittest.mock.patch.object(handoff, "cfg", return_value=dict(CONFIG)),
            unittest.mock.patch.object(handoff, "_main_branch", return_value="main"),
            unittest.mock.patch.object(
                handoff.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=LOG, stderr="")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_fresh_clone_reports_no_debt_for_merged_history(self):
        self.assertTrue(handoff.clean_slate())
        self.assertIsNone(handoff.latest_main_task_undocumented())

    def test_a_done_task_with_no_handoff_doc_still_reports(self):
        (self.done / "task-0042.md").write_text("done\n", encoding="utf-8")
        self.assertFalse(handoff.clean_slate())
        found = handoff.latest_main_task_undocumented()
        self.assertEqual("task-0042", found["task"])
        self.assertEqual("no handoff doc", found["reason"])

    def test_a_handoff_doc_with_an_empty_done_folder_still_reports(self):
        (self.handoffs / "task-0001.md").write_text("old doc\n", encoding="utf-8")
        self.assertFalse(handoff.clean_slate())
        found = handoff.latest_main_task_undocumented()
        self.assertEqual("task-0042", found["task"])

    def test_a_waiver_counts_as_a_doc(self):
        (self.handoffs / "task-0042.md").write_text(
            "---\ntask: task-0042\nwaived: true\n---\n", encoding="utf-8")
        self.assertFalse(handoff.clean_slate())

    def test_an_unreadable_tree_is_not_a_clean_slate(self):
        with unittest.mock.patch.object(handoff, "handoff_dir", side_effect=OSError("boom")):
            self.assertFalse(handoff.clean_slate())


if __name__ == "__main__":
    unittest.main()
