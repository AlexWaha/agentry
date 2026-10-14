"""task-0004: the `repo:` field reader and the scan scoped by it.

  state.task_repo()          the single reader of the `repo:` field.
  repos_for_task()           a colliding task id in an unrelated repo is not this
                             task's unmerged branch; a declared `repo:` that names
                             no known repo is UNKNOWN (task-0031, FR-46), never
                             "every repo".
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
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

    def test_a_value_matching_nothing_is_unknown_not_every_repo(self):
        # Fail closed (FR-46): merge detection moves the task file to done/, so a
        # stale or misspelled value that widened to every repo let a stranger's
        # merge close the task.
        with self._declares("no-such-repo"):
            self.assertEqual([], git_state.repos_for_task("task-1236"))

    def test_a_value_pointing_outside_the_workspace_is_unknown(self):
        with self._declares("../elsewhere"):
            self.assertEqual([], git_state.repos_for_task("task-1236"))


class EmptyRepoFieldTest(unittest.TestCase):
    """An EMPTY `repo:` keeps the behaviour from before the field existed: every
    repo. Read through the real task-file reader, not a mock of it."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "repo_empty_").resolve()
        self.active = self.root / "active"
        self.active.mkdir()
        for name, value in (("ROOT", self.root), ("TASK_DIRS", {"active": self.active})):
            p = unittest.mock.patch.object(state, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.backend, self.frontend = self.root / "backend", self.root / "frontend"
        p = unittest.mock.patch.object(git_state, "repos",
                                       return_value=[self.backend, self.frontend])
        p.start()
        self.addCleanup(p.stop)

    def _task(self, repo_line):
        (self.active / "task-0500.md").write_text(
            f"---\nid: 500\n{repo_line}\n---\n", encoding="utf-8")

    def test_a_blank_repo_field_means_every_repo(self):
        self._task("repo:")
        self.assertEqual([self.backend, self.frontend], git_state.repos_for_task("task-0500"))

    def test_a_quoted_empty_repo_field_means_every_repo(self):
        self._task('repo: ""')
        self.assertEqual([self.backend, self.frontend], git_state.repos_for_task("task-0500"))

    def test_no_repo_field_at_all_means_every_repo(self):
        (self.active / "task-0500.md").write_text("---\nid: 500\n---\n", encoding="utf-8")
        self.assertEqual([self.backend, self.frontend], git_state.repos_for_task("task-0500"))

    def test_a_task_file_that_does_not_exist_means_every_repo(self):
        self.assertEqual([self.backend, self.frontend], git_state.repos_for_task("task-0999"))


class RepoKeyResolutionTest(unittest.TestCase):
    """With a `repos{}` object configured, a `repo:` value is a KEY of it first
    (contract C-3), so a key that differs from its directory name resolves."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "repo_keys_").resolve()
        (self.root / ".agentry").mkdir()
        config = self.root / ".agentry" / "pipeline.json"
        config.write_text(json.dumps({"repos": {
            "agentry": {"path": "src"},
            "docs-site": {"path": "sites/docs"},
        }}), encoding="utf-8")
        for name, value in (("ROOT", self.root), ("PIPELINE_PATH", config)):
            p = unittest.mock.patch.object(state, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _declares(self, value):
        return unittest.mock.patch.object(state, "task_repo", return_value=value)

    def test_a_key_resolves_to_its_entry_path(self):
        with self._declares("agentry"):
            self.assertEqual([self.root / "src"], git_state.repos_for_task("task-0001"))

    def test_a_key_that_differs_from_its_directory_name_resolves(self):
        with self._declares("docs-site"):
            self.assertEqual([self.root / "sites" / "docs"],
                             git_state.repos_for_task("task-0001"))

    def test_a_key_with_a_subpath_resolves_by_its_first_segment(self):
        with self._declares("agentry/.claude"):
            self.assertEqual([self.root / "src"], git_state.repos_for_task("task-0001"))

    def test_an_entry_path_still_resolves_like_a_key(self):
        with self._declares("src"):
            self.assertEqual([self.root / "src"], git_state.repos_for_task("task-0001"))

    def test_a_subdirectory_of_an_entry_path_resolves_by_the_loose_match(self):
        with self._declares("src/plugin/tools"):
            self.assertEqual([self.root / "src"], git_state.repos_for_task("task-0001"))

    def test_an_unconfigured_name_is_unknown(self):
        with self._declares("backend"):
            self.assertEqual([], git_state.repos_for_task("task-0001"))

    def test_resolve_key_is_the_one_rule_stack_gate_uses(self):
        import stack_gate
        entries = git_state.repo_entries()
        for value in ("agentry", "agentry/x", "src", "docs-site", "sites/docs", "nope", ""):
            self.assertEqual(stack_gate.resolve_key(entries, value),
                             git_state.resolve_key(entries, value), value)


if __name__ == "__main__":
    unittest.main()
