"""task-0119 (M3): state._head_field is the one task-file field reader; task_repo
and task_branch are thin wrappers over it, and codebase_sync takes its repo list
from git_state.repos().
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "memory"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import codebase_sync
import git_state
import state
import tmproot


class HeadFieldTest(unittest.TestCase):
    def setUp(self):
        tmp = tmproot.sandbox(self, "head_field_")
        self.dirs = {name: tmp / name for name in ("backlog", "active", "done")}
        for d in self.dirs.values():
            d.mkdir()
        patcher = unittest.mock.patch.object(state, "TASK_DIRS", self.dirs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, task, text, where="active"):
        (self.dirs[where] / f"{task}.md").write_text(text, encoding="utf-8")

    def test_task_branch_reads_the_branch_field_and_strips_quotes(self):
        self.write("task-0200", "---\nid: 200\nbranch: \"feature/task-0120\"\n---\n")
        self.assertEqual("feature/task-0120", state.task_branch("task-0200"))

    def test_task_branch_is_empty_when_the_field_is_absent(self):
        self.write("task-0201", "---\nid: 201\n---\n")
        self.assertEqual("", state.task_branch("task-0201"))

    def test_task_repo_and_task_branch_read_their_own_keys(self):
        self.write("task-0202", "---\nrepo: agentry\nbranch: fix/task-0202\n---\n", where="done")
        self.assertEqual("agentry", state.task_repo("task-0202"))
        self.assertEqual("fix/task-0202", state.task_branch("task-0202"))

    def test_prose_below_the_frontmatter_is_not_a_field(self):
        self.write("task-0203", "---\nid: 203\n---\n\nbranch: not-the-field\nrepo: nor-this\n")
        self.assertEqual("", state.task_branch("task-0203"))
        self.assertIsNone(state.task_repo("task-0203"))

    def test_a_file_without_frontmatter_reads_only_its_first_40_lines(self):
        self.write("task-0204", "repo: early\n" + "\n" * 60 + "branch: late\n")
        self.assertEqual("early", state.task_repo("task-0204"))
        self.assertEqual("", state.task_branch("task-0204"))

    def test_a_key_is_matched_whole_not_as_a_prefix(self):
        self.write("task-0205", "---\nrepository: wrong\n---\n")
        self.assertIsNone(state.task_repo("task-0205"))

    def test_a_missing_task_reads_as_empty(self):
        self.assertEqual("", state.task_branch("task-0999"))
        self.assertIsNone(state.task_repo("task-0999"))


class CodebaseSyncReposTest(unittest.TestCase):
    def test_codebase_sync_has_no_scanner_of_its_own(self):
        self.assertFalse(hasattr(codebase_sync, "_git_repos"))

    def test_current_heads_come_from_git_state_repos(self):
        root = codebase_sync.ROOT
        with unittest.mock.patch.object(git_state, "repos", return_value=[root / "alpha"]), \
                unittest.mock.patch.object(codebase_sync, "_head", return_value="abc123"):
            self.assertEqual({"alpha": "abc123"}, codebase_sync.current_heads())

    def test_a_failing_repo_lookup_yields_no_heads(self):
        with unittest.mock.patch.object(git_state, "repos", side_effect=OSError("boom")):
            self.assertEqual({}, codebase_sync.current_heads())


if __name__ == "__main__":
    unittest.main()
