"""Security: the Stop hook's drift reconciler cannot be talked into closing a task.

`reconcile_status_drift()` moves a task file to done/, sweeps an umbrella's
subtasks and sets the run to stage 'done'. It used to decide "merged" from the
task file's own `branch:` and from any local branch whose name held the id, and
both are writable by the agent being judged. These tests build a REAL repo and
forge each signal, then show only a run at 'done' or a run-less file with a
tagged commit on main is ever closed. Also: `repo: .` and `repo: ..` name no repo.
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

TASK = "task-9200"
OLD = "task-9199"
EPIC = "epic-0001"


class ReconcilerForgeryTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "reconciler_forgery_").resolve()
        self.repo = self.root / "app"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.commit("first")

        (self.root / ".agentry").mkdir()
        config = self.root / ".agentry" / "pipeline.json"
        config.write_text(json.dumps({"main_branch": "main",
                                      "repos": {"app": {"path": "app"}}}), encoding="utf-8")
        self.dirs = {}
        for name in ("backlog", "active", "done"):
            self.dirs[name] = self.root / f"tasks-{name}"
            self.dirs[name].mkdir()
        self.runs: list[dict] = []
        self.set_fields = unittest.mock.Mock()
        for target, name, value in (
                (state, "ROOT", self.root), (state, "PIPELINE_PATH", config),
                (state, "TASK_DIRS", self.dirs), (stop_gate, "ACTIVE_DIR", self.dirs["active"]),
                (state, "all_runs", lambda conn: self.runs),
                (state, "get_run", lambda conn, task: next(
                    (r for r in self.runs if r["task"] == task), None)),
                (state, "set_fields", self.set_fields)):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)
        self.assertEqual(0, done.returncode, f"git {' '.join(args)}: {done.stderr}")
        return (done.stdout or "").strip()

    def commit(self, message: str) -> None:
        log = self.repo / "log.txt"
        log.write_text((log.read_text(encoding="utf-8") if log.exists() else "") + message + "\n",
                       encoding="utf-8")
        self.git("add", "log.txt")
        self.git("commit", "-m", message)

    def merge_branch(self, branch: str, message: str) -> None:
        """`branch` with one commit, merged into main with a merge commit."""
        self.git("checkout", "-b", branch)
        self.commit(message)
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "-m", f"merge {branch}", branch)

    def write(self, folder: str, task: str, body: str = "") -> None:
        (self.dirs[folder] / f"{task}.md").write_text(
            f"---\nid: 1\nrepo: app\n{body}\ncompleted:\n---\n", encoding="utf-8")

    def run_row(self, stage: str, awaiting: str = "", commit_approved: int = 0) -> None:
        self.runs.append({"task": TASK, "stage": stage, "awaiting_human": awaiting,
                          "commit_approved": commit_approved,
                          **state.frozen_frontmatter(TASK)})

    def reconcile(self) -> tuple[list, list]:
        return stop_gate.reconcile_status_drift(object())

    def where(self, task: str) -> str | None:
        return state.task_dir(task)

    # --- forged signals change nothing --------------------------------------------

    def test_a_forged_branch_field_on_a_plan_parked_run_changes_nothing(self):
        self.merge_branch(f"feature/{OLD}", f"old work [{OLD}]")
        self.write("active", TASK, f"branch: feature/{OLD}")
        self.run_row("plan", awaiting="plan")

        self.assertEqual(([], []), self.reconcile())
        self.assertEqual("active", self.where(TASK))
        self.set_fields.assert_not_called()

    def test_a_side_branch_forged_at_an_old_merged_tip_changes_nothing(self):
        self.merge_branch(f"feature/{OLD}", f"old work [{OLD}]")
        self.git("branch", f"feature/{TASK}-x", f"feature/{OLD}")
        self.write("active", TASK)
        self.run_row("plan", awaiting="plan")

        self.assertEqual(([], []), self.reconcile())
        self.assertEqual("active", self.where(TASK))
        self.set_fields.assert_not_called()

    def test_forged_signals_do_not_close_a_file_that_has_no_run(self):
        self.merge_branch(f"feature/{OLD}", f"old work [{OLD}]")
        self.git("branch", f"feature/{TASK}-x", f"feature/{OLD}")
        self.write("active", TASK, f"branch: feature/{OLD}")

        self.assertEqual(([], []), self.reconcile())
        self.assertEqual("active", self.where(TASK))

    def test_forged_signals_do_not_sweep_the_subtasks_of_an_umbrella(self):
        self.merge_branch(f"feature/{OLD}", f"old work [{OLD}]")
        self.write("active", TASK,
                   f"epic: {EPIC}\numbrella: true\nsubtasks: [task-9201]\nbranch: feature/{OLD}")
        self.write("backlog", "task-9201", f"epic: {EPIC}")
        self.run_row("implement")

        self.assertEqual(([], []), self.reconcile())
        self.assertEqual("backlog", self.where("task-9201"))

    def test_a_run_at_a_non_done_stage_is_never_moved_even_when_main_carries_the_task(self):
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("active", TASK)
        for stage in ("plan", "implement", "test", "review", "ready"):
            with self.subTest(stage=stage):
                self.runs.clear()
                self.run_row(stage, awaiting="commit")
                self.assertEqual(([], []), self.reconcile())
                self.assertEqual("active", self.where(TASK))
                self.set_fields.assert_not_called()

    def test_a_stale_run_is_not_closed_on_a_forged_branch_either(self):
        self.merge_branch(f"feature/{OLD}", f"old work [{OLD}]")
        self.write("done", TASK, f"branch: feature/{OLD}")
        self.run_row("implement")

        self.assertEqual(([], []), self.reconcile())
        self.set_fields.assert_not_called()

    def test_a_mention_of_the_tag_inside_a_sentence_closes_nothing(self):
        # A trunk commit ABOUT the task, not the task's own: the tag does not lead
        # a line. Both reconcile loops read it as unmerged.
        self.commit(f"docs: revert the retry fix from [{TASK}] for now")
        self.write("done", TASK)
        self.run_row("implement", commit_approved=1)
        self.write("active", "task-9300")

        self.assertEqual(([], []), self.reconcile())
        self.set_fields.assert_not_called()

    def test_a_forged_mention_does_not_close_a_file_that_has_no_run(self):
        self.commit(f"chore: see also [{TASK}] and its follow-ups")
        self.write("active", TASK)

        self.assertEqual(([], []), self.reconcile())
        self.assertEqual("active", self.where(TASK))

    def test_a_stale_run_that_never_had_its_commit_approved_is_not_closed(self):
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("done", TASK)
        self.run_row("implement", commit_approved=0)

        self.assertEqual(([], []), self.reconcile())
        self.set_fields.assert_not_called()

    def test_an_umbrella_whose_commit_was_never_approved_sweeps_no_subtask(self):
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("active", TASK, f"epic: {EPIC}\numbrella: true\nsubtasks: [task-9201]")
        self.write("backlog", "task-9201", f"epic: {EPIC}")
        self.run_row("done", commit_approved=0)

        self.assertEqual(([TASK], []), self.reconcile())
        self.assertEqual("backlog", self.where("task-9201"))

    def test_an_umbrella_file_with_no_run_row_sweeps_no_subtask(self):
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("active", TASK, f"epic: {EPIC}\numbrella: true\nsubtasks: [task-9201]")
        self.write("backlog", "task-9201", f"epic: {EPIC}")

        self.assertEqual(([TASK], []), self.reconcile())
        self.assertEqual("backlog", self.where("task-9201"))

    def test_a_forged_orphan_in_done_is_not_overwritten_by_the_sweep(self):
        # done/task-9201.md already exists (forged or stale); a copy of the same
        # id sits in backlog/. The sweep must leave the done/ record untouched.
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("active", TASK, f"epic: {EPIC}\numbrella: true\nsubtasks: [task-9201]")
        self.write("backlog", "task-9201", f"epic: {EPIC}\nmarker: backlog-copy")
        self.write("done", "task-9201", f"epic: {EPIC}\nmarker: done-original")
        self.run_row("done", commit_approved=1)

        self.assertEqual(([TASK], []), self.reconcile())
        self.assertIn("marker: done-original",
                      (self.dirs["done"] / "task-9201.md").read_text(encoding="utf-8"))
        self.assertTrue((self.dirs["backlog"] / "task-9201.md").is_file())

    def test_a_forged_merge_branch_phrase_inside_a_sentence_closes_nothing(self):
        self.commit(f"docs: quote the old Merge branch 'feature/{TASK}' message")
        self.write("done", TASK)
        self.run_row("implement", commit_approved=1)

        self.assertEqual(([], []), self.reconcile())
        self.set_fields.assert_not_called()

    def test_gits_own_merge_message_at_line_start_still_closes(self):
        self.git("checkout", "-b", f"feature/{TASK}")
        self.commit("untagged work")
        self.git("checkout", "main")
        self.git("merge", "--no-ff", f"feature/{TASK}")
        self.write("done", TASK)
        self.run_row("implement", commit_approved=1)

        self.assertEqual(([], [TASK]), self.reconcile())

    def test_a_tag_after_leading_blanks_still_counts(self):
        # Same grammar as pretool_gate.COMMIT_TAG_RE: blanks, then the tag.
        self.commit(f"  [{TASK}] work with an indented subject")
        self.write("done", TASK)
        self.run_row("implement", commit_approved=1)

        self.assertEqual(([], [TASK]), self.reconcile())

    # --- the legitimate paths still close -----------------------------------------

    def test_a_run_at_done_with_a_tagged_commit_on_main_closes(self):
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("active", TASK)
        self.run_row("done", awaiting="merge")

        self.assertEqual(([TASK], []), self.reconcile())
        self.assertEqual("done", self.where(TASK))
        self.set_fields.assert_called_once()
        self.assertEqual("done", self.set_fields.call_args.kwargs["stage"])

    def test_a_run_at_done_without_a_tagged_commit_stays(self):
        self.git("checkout", "-b", f"feature/{TASK}")
        self.commit(f"[{TASK}] wip")
        self.git("checkout", "main")
        self.write("active", TASK)
        self.run_row("done", awaiting="merge")

        self.assertEqual(([], []), self.reconcile())
        self.assertEqual("active", self.where(TASK))

    def test_a_file_with_no_run_row_and_a_tagged_commit_on_main_closes(self):
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("active", TASK)

        self.assertEqual(([TASK], []), self.reconcile())
        self.assertEqual("done", self.where(TASK))
        self.set_fields.assert_not_called()

    def test_the_subtask_sweep_runs_after_a_legitimate_close(self):
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("active", TASK, f"epic: {EPIC}\numbrella: true\nsubtasks: [task-9201]")
        self.write("backlog", "task-9201", f"epic: {EPIC}")
        self.run_row("done", commit_approved=1)

        self.assertEqual(([TASK, "task-9201"], []), self.reconcile())
        self.assertEqual("done", self.where("task-9201"))

    def test_a_stale_run_closes_when_main_carries_the_task(self):
        self.merge_branch(f"feature/{TASK}", f"[{TASK}] work")
        self.write("done", TASK)
        self.run_row("implement", commit_approved=1)

        self.assertEqual(([], [TASK]), self.reconcile())
        self.assertEqual("done", self.set_fields.call_args.kwargs["stage"])


class RepoDotFailsClosedTest(unittest.TestCase):
    """`repo: .` and `repo: ..` used to widen to every repo through the ancestor
    test in repos_named: the workspace root, or its parent, contains them all."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "repo_dot_").resolve()
        for name in ("backend", "frontend"):
            (self.root / name).mkdir()
        (self.root / ".agentry").mkdir()
        config = self.root / ".agentry" / "pipeline.json"
        config.write_text(json.dumps({"main_branch": "main", "repos": {
            "backend": {"path": "backend"}, "frontend": {"path": "frontend"}}}),
            encoding="utf-8")
        for target, name, value in ((state, "ROOT", self.root), (state, "PIPELINE_PATH", config)):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_dot_and_dotdot_name_no_repo(self):
        for declared in (".", "./", "..", "../", ".\\", "..\\", "backend/..", "./."):
            with self.subTest(declared=declared):
                self.assertEqual([], git_state.repos_named(declared))

    def test_a_directory_inside_a_repo_and_a_named_repo_still_resolve(self):
        (self.root / "backend" / "src").mkdir()
        self.assertEqual([self.root / "backend"], git_state.repos_named("backend"))
        self.assertEqual([self.root / "backend"], git_state.repos_named("backend/src"))
        self.assertEqual([self.root / "backend"], git_state.repos_named("./backend"))


if __name__ == "__main__":
    unittest.main()
