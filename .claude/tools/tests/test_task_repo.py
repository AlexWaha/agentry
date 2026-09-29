"""task-0004: the `repo:` field reader and the scan scoped by it.

  state.task_repo()          the single reader of the `repo:` field.
  repos_for_task()           a colliding task id in an unrelated repo is not this
                             task's unmerged branch.
"""

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

import git_state
import state
import tmproot


class TaskRepoTest(unittest.TestCase):
    """state.task_repo() is the single reader of the `repo:` field, shared by
    stack_gate.py and git_state.repos_for_task()."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "task_repo_")
        self.dirs = {}
        for name in ("backlog", "active", "done"):
            d = self.tmp / name
            d.mkdir()
            self.dirs[name] = d
        p = unittest.mock.patch.object(state, "TASK_DIRS", self.dirs)
        p.start()
        self.addCleanup(p.stop)

    def _write(self, task, where="active", repo_line="repo: backend", body=""):
        (self.dirs[where] / f"{task}.md").write_text(
            f"---\nid: {task.split('-')[1]}\n{repo_line}\n---\n\n{body}",
            encoding="utf-8")

    def test_reads_the_field_from_any_folder(self):
        self._write("task-0100", where="done")
        self.assertEqual("backend", state.task_repo("task-0100"))

    def test_quotes_are_stripped(self):
        self._write("task-0101", repo_line="repo: 'frontend'")
        self.assertEqual("frontend", state.task_repo("task-0101"))

    def test_a_blank_field_is_none(self):
        self._write("task-0102", repo_line="repo:")
        self.assertIsNone(state.task_repo("task-0102"))

    def test_a_missing_file_is_none(self):
        self.assertIsNone(state.task_repo("task-0999"))

    def test_the_word_in_the_body_is_not_the_field(self):
        # Only the head of the file is read, so prose cannot pose as frontmatter.
        self._write("task-0103", repo_line="repo:",
                    body="\n" * 60 + "repo: not-the-field\n")
        self.assertIsNone(state.task_repo("task-0103"))


class ReposForTaskTest(unittest.TestCase):
    """Task ids are allocated per workspace and consumed per repo, so they
    collide. An unscoped scan read an unrelated repo's branch as this task's
    unmerged work and parked it forever."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "repo_scope_")
        self.backend = self.root / "backend"
        self.frontend = self.root / "frontend"
        for d in (self.backend, self.frontend):
            d.mkdir()
        p = unittest.mock.patch.object(state, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)
        p = unittest.mock.patch.object(git_state, "repos",
                                       return_value=[self.backend, self.frontend])
        p.start()
        self.addCleanup(p.stop)

    def _declares(self, value):
        return unittest.mock.patch.object(state, "task_repo", return_value=value)

    def test_a_declared_repo_scopes_the_scan_to_it(self):
        with self._declares("backend"):
            self.assertEqual([self.backend], git_state.repos_for_task("task-1236"))

    def test_a_subdirectory_value_still_resolves_to_the_repo(self):
        with self._declares("backend/src"):
            self.assertEqual([self.backend], git_state.repos_for_task("task-1236"))

    def test_no_declared_repo_means_every_repo(self):
        with self._declares(None):
            self.assertEqual([self.backend, self.frontend],
                             git_state.repos_for_task("task-1236"))

    def test_a_value_matching_nothing_fails_open_to_every_repo(self):
        # Fail-open: a stale or misspelled value must not make a task unclosable.
        with self._declares("no-such-repo"):
            self.assertEqual([self.backend, self.frontend],
                             git_state.repos_for_task("task-1236"))


if __name__ == "__main__":
    unittest.main()
