"""task-0031, FR-46: merge detection is scoped to the repo a task declares, and it
fails closed when the declared repo cannot be resolved.

Task ids are allocated per workspace and consumed per repo, so two repos can
both hold a `task-9100`. These tests build two REAL git repositories carrying
the same id and show that a merge in one never closes the other, through both
paths that decide "merged": `git_state.task_report()` (advance.py) and
`stop_gate.reconcile_status_drift()` (the Stop hook). A stand-in for git would
share the assumption under test, so none is used.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

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

import git_state
import state
import stop_gate
import tmproot

TASK = "task-9100"
BRANCH = f"feature/{TASK}"


class TwoRepoMergeTest(unittest.TestCase):
    """A workspace with `backend` and `frontend`, each a real repo, both
    configured under repos{}, and one active task file declaring `repo:`."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "scoped_merge_").resolve()
        self.repos = {name: self.root / name for name in ("backend", "frontend")}
        for repo in self.repos.values():
            repo.mkdir()
            self.git(repo, "init", "-b", "main")
            self.git(repo, "config", "user.email", "test@example.com")
            self.git(repo, "config", "user.name", "Test")
            self.commit(repo, "first")

        (self.root / ".agentry").mkdir()
        config = self.root / ".agentry" / "pipeline.json"
        config.write_text(json.dumps({"main_branch": "main", "repos": {
            name: {"path": name} for name in self.repos}}), encoding="utf-8")
        self.dirs = {name: self.root / name_dir for name, name_dir in
                     (("backlog", "tasks-backlog"), ("active", "tasks-active"),
                      ("done", "tasks-done"))}
        for folder in self.dirs.values():
            folder.mkdir()
        for target, name, value in (
                (state, "ROOT", self.root), (state, "PIPELINE_PATH", config),
                (state, "TASK_DIRS", self.dirs), (stop_gate, "ACTIVE_DIR", self.dirs["active"]),
                (state, "all_runs", lambda conn: []), (state, "set_fields", lambda *a, **k: None)):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def git(self, repo: Path, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        self.assertEqual(0, done.returncode, f"git {' '.join(args)}: {done.stderr}")
        return (done.stdout or "").strip()

    def commit(self, repo: Path, message: str) -> None:
        log = repo / "log.txt"
        log.write_text((log.read_text(encoding="utf-8") if log.exists() else "") + message + "\n",
                       encoding="utf-8")
        self.git(repo, "add", "log.txt")
        self.git(repo, "commit", "-m", message)

    def merge_task(self, name: str) -> None:
        """The task's branch in `name`, tagged and merged into that repo's main."""
        repo = self.repos[name]
        self.git(repo, "checkout", "-b", BRANCH)
        self.commit(repo, f"feat: work [{TASK}]")
        self.git(repo, "checkout", "main")
        self.git(repo, "merge", "--no-ff", "-m", f"merge {BRANCH}", BRANCH)

    def unmerged_task(self, name: str) -> None:
        """The task's branch in `name` with one tagged commit, never merged."""
        repo = self.repos[name]
        self.git(repo, "checkout", "-b", BRANCH)
        self.commit(repo, f"wip [{TASK}]")
        self.git(repo, "checkout", "main")

    def declare(self, repo_line: str) -> None:
        (self.dirs["active"] / f"{TASK}.md").write_text(
            f"---\nid: 9100\n{repo_line}\ncompleted:\n---\n", encoding="utf-8")

    def folder(self) -> str | None:
        return state.task_dir(TASK)

    def report(self) -> list[dict]:
        return git_state.task_report(TASK, do_fetch=False)

    def reconcile(self) -> list:
        moved, _ = stop_gate.reconcile_status_drift(object())
        return moved

    # --- a stranger's merge in the other repo -------------------------------------

    def test_a_merge_in_the_other_repo_does_not_mark_an_unmerged_task_done(self):
        self.unmerged_task("backend")
        self.merge_task("frontend")
        self.declare("repo: backend")

        report = self.report()

        self.assertEqual(1, len(report))
        self.assertEqual("backend", report[0]["repo"])
        self.assertIs(False, report[0]["in_main"])

    def test_the_stop_gate_leaves_the_file_when_only_the_other_repo_merged(self):
        self.unmerged_task("backend")
        self.merge_task("frontend")
        self.declare("repo: backend")

        self.assertEqual([], self.reconcile())
        self.assertEqual("active", self.folder())

    def test_a_tagged_merge_only_in_the_other_repo_is_unknown_not_merged(self):
        # The task's own repo knows nothing about the id: no branch, no commit.
        self.merge_task("frontend")
        self.declare("repo: backend")

        self.assertEqual([], self.report())
        self.assertEqual([], self.reconcile())
        self.assertEqual("active", self.folder())

    # --- the task's own merge -----------------------------------------------------

    def test_a_merge_in_the_declared_repo_is_reported_merged(self):
        self.merge_task("backend")
        self.unmerged_task("frontend")
        self.declare("repo: backend")

        report = self.report()

        self.assertEqual([("backend", True)], [(r["repo"], r["in_main"]) for r in report])

    def test_the_stop_gate_moves_the_file_when_the_declared_repo_merged(self):
        self.merge_task("backend")
        self.unmerged_task("frontend")
        self.declare("repo: backend")

        self.assertEqual([TASK], self.reconcile())
        self.assertEqual("done", self.folder())

    # --- fail closed --------------------------------------------------------------

    def test_a_declared_repo_that_resolves_to_nothing_is_never_reported_merged(self):
        self.merge_task("backend")
        self.merge_task("frontend")
        self.declare("repo: no-such-repo")

        self.assertEqual([], git_state.repos_for_task(TASK))
        self.assertEqual([], self.report())

    def test_the_stop_gate_leaves_a_file_whose_declared_repo_resolves_to_nothing(self):
        self.merge_task("backend")
        self.merge_task("frontend")
        self.declare("repo: no-such-repo")

        self.assertEqual([], self.reconcile())
        self.assertEqual("active", self.folder())

    # --- an empty repo: keeps today's behaviour -----------------------------------

    def test_an_empty_repo_field_still_asks_every_repo(self):
        self.merge_task("frontend")
        self.declare("repo:")

        self.assertEqual(list(self.repos.values()), git_state.repos_for_task(TASK))
        self.assertEqual([("frontend", True)],
                         [(r["repo"], r["in_main"]) for r in self.report()])
        self.assertEqual([TASK], self.reconcile())

    def test_an_empty_repo_field_waits_for_every_repo_that_has_the_task(self):
        self.merge_task("frontend")
        self.unmerged_task("backend")
        self.declare("repo:")

        self.assertEqual([], self.reconcile())
        self.assertEqual("active", self.folder())

    # --- the branch a task rides on -----------------------------------------------

    def test_a_declared_carrier_branch_alone_does_not_close_the_task(self):
        # `branch:` is written by the agent being judged and a merged ref proves
        # nothing about THIS task, so the Stop hook ignores it: only a commit on
        # main naming the task closes the file.
        repo = self.repos["backend"]
        self.git(repo, "checkout", "-b", "feature/task-9000")
        self.commit(repo, "work on the carrier")
        self.git(repo, "checkout", "main")
        self.git(repo, "merge", "--no-ff", "-m", "merge the carrier", "feature/task-9000")
        self.declare("repo: backend\nbranch: feature/task-9000")

        self.assertEqual([], self.reconcile())
        self.assertEqual("active", self.folder())


if __name__ == "__main__":
    unittest.main()
