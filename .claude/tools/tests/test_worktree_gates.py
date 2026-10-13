"""epic-0004 / task-0130: the hook gates judge a task by its OWN worktree.

Every test builds a real workspace under the project's tmp/: a `src` repo on `main`, real
`git worktree add` trees under `src-wt/`, a run.db of its own and a pipeline.json of its
own (state.ROOT, PIPELINE_PATH, STATE_DIR and DB_PATH are patched), so the live store and
the live config are never read. The gates run against the REAL worktree.py: nothing about
the module is faked.

What is pinned:

- the review freeze is per task (a review run with a recorded tree freezes that tree only;
  a legacy run keeps the global freeze);
- with `worktrees.enabled` the trunk checkout takes no edits and no file-writing command;
- the solo merge gains condition 4 (freshness) for a task that works in a worktree;
- commit and merge checks are placed by `-C`, `cd` or the payload cwd, through main();
- `git worktree add -b/-B` is judged for naming and base like any branch creation;
- dev agents may not add, move, remove or re-link a worktree, nor run worktree.py
  --remove / --repair;
- with the flag off (and no recorded tree) none of the new checks can fire, and none of
  the read-only checks starts a subprocess.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
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

import agent_gate
import pretool_gate
import state
import tmproot
import worktree

TASK_A = "task-9301"
TASK_B = "task-9302"
TASK_C = "task-9303"


def hook(module, payload: dict, argv: list | None = None) -> tuple:
    """(exit code, stdout, stderr) of module.main() fed one hook payload, as Claude Code
    feeds it on stdin."""
    out, err = io.StringIO(), io.StringIO()
    with unittest.mock.patch.object(pretool_gate, "read_payload", return_value=payload), \
            unittest.mock.patch.object(sys, "argv", argv or ["gate"]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main()
    return code, out.getvalue(), err.getvalue()


def bash_payload(command: str, cwd: str, **extra) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd, **extra}


def edit_payload(path: str, cwd: str, tool: str = "Write", **extra) -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": path, "content": "x = 1\n"},
            "cwd": cwd, **extra}


SUBAGENT = {"agent_type": "senior-backend-dev", "agent_id": "a1"}


class WorkspaceCase(unittest.TestCase):
    """A workspace root with a `src` repo on main, a pipeline.json and an empty run.db."""

    ENABLED: bool | None = True  # None = no worktrees block at all
    MODE = "solo"

    def setUp(self):
        self.root = tmproot.sandbox(self, "wt_gates_").resolve()
        self.src = self.root / "src"
        self.src.mkdir()
        self.git(self.src, "init", "-q", "-b", "main")
        self.git(self.src, "config", "user.email", "qa@example.com")
        self.git(self.src, "config", "user.name", "QA")
        self.write(self.src / "app" / "main.py", "x = 1\n")
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-q", "-m", "first")
        (self.root / ".agentry").mkdir()
        self.config = self.root / ".agentry" / "pipeline.json"
        self.set_config(self.ENABLED)
        for target, name, value in (
                (state, "ROOT", self.root), (state, "PIPELINE_PATH", self.config),
                (state, "STATE_DIR", self.root / ".agentry" / "state"),
                (state, "DB_PATH", self.root / ".agentry" / "state" / "run.db")):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def set_config(self, enabled: bool | None) -> None:
        cfg = {"main_branch": "main", "workflow": {"mode": self.MODE}}
        if enabled is not None:
            cfg["worktrees"] = {"enabled": enabled, "max_parallel": 2}
        self.config.write_text(json.dumps(cfg), encoding="utf-8")

    def git(self, repo: Path, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        self.assertEqual(0, done.returncode, f"git {' '.join(args)}: {done.stderr}")
        return (done.stdout or "").strip()

    @staticmethod
    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, repo: Path, name: str) -> None:
        self.write(repo / f"{name}.txt", name + "\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", name)

    def add_tree(self, task: str, base: str = "main") -> Path:
        tree = self.root / "src-wt" / task
        self.git(self.src, "worktree", "add", "-q", "-b", f"feature/{task}", str(tree), base)
        return tree

    def register(self, task: str, stage: str, tree: bool = True, **fields) -> None:
        rel = f"src-wt/{task}" if tree else ""
        if tree and fields.get("commit_approved") and "approved_tree" not in fields \
                and (self.root / rel).is_dir():
            # An approved task in a tree carries the binding of the tree as it stands
            # (task-0134): the commit and the merge check it against the tree later.
            fields["approved_tree"] = pretool_gate.tree_binding(self.root / rel, snapshot=True)
        conn = state.connect()
        try:
            state.create_run(conn, task, "feature", stage, worktree=rel)
            if fields:
                state.set_fields(conn, task, **fields)
        finally:
            conn.close()

    def bind(self, task: str) -> None:
        """Record the tree as it stands now as the change set `task` was approved with."""
        conn = state.connect()
        try:
            state.set_fields(conn, task, approved_tree=pretool_gate.tree_binding(
                self.root / "src-wt" / task, snapshot=True))
        finally:
            conn.close()

    def stderr_of(self, call, *args, **kwargs) -> tuple:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = call(*args, **kwargs)
        return code, err.getvalue()

    def edit(self, path, orch: bool = False) -> tuple:
        return self.stderr_of(pretool_gate.handle_edit, str(path), "x = 1\n", orch=orch,
                              cwd=str(self.root))

    def bash(self, command: str, cwd: Path | None = None, orch: bool = True) -> tuple:
        return self.stderr_of(pretool_gate.handle_bash, command,
                              cwd=str(cwd or self.root), orch=orch)


# --- F4: the review freeze is per task -------------------------------------------------

class ReviewFreezeTest(WorkspaceCase):
    """The freeze follows the recorded tree, so it holds with the flag off as well (a
    rollback keeps the trees that are already recorded)."""

    ENABLED = False

    def setUp(self):
        super().setUp()
        self.tree_a = self.add_tree(TASK_A)
        self.tree_b = self.add_tree(TASK_B)

    def test_a_review_run_freezes_only_the_edits_inside_its_own_tree(self):
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")

        code, err = self.edit(self.tree_a / "app" / "main.py")

        self.assertEqual(2, code)
        self.assertIn(f"{TASK_A} is in the read-only 'review' stage", err)

    def test_another_tasks_tree_stays_editable_while_one_task_is_in_review(self):
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")

        self.assertEqual(0, self.edit(self.tree_b / "app" / "main.py")[0])

    def test_the_trunk_checkout_and_other_paths_are_not_frozen_by_a_tree_in_review(self):
        self.register(TASK_A, "review")

        self.assertEqual(0, self.edit(self.src / "app" / "main.py")[0])
        self.assertEqual(0, self.edit(self.root / "elsewhere" / "main.py")[0])

    def test_a_legacy_review_run_keeps_the_global_freeze(self):
        self.register(TASK_C, "review", tree=False)
        self.register(TASK_B, "implement")

        for path in (self.tree_b / "app" / "main.py", self.src / "app" / "main.py"):
            with self.subTest(path=str(path)):
                code, err = self.edit(path)
                self.assertEqual(2, code)
                self.assertIn(f"{TASK_C} is in the read-only 'review' stage", err)

    def test_a_legacy_review_run_freezes_even_when_a_tree_task_is_not_in_review(self):
        self.register(TASK_A, "implement")
        self.register(TASK_C, "review", tree=False)

        self.assertEqual(2, self.edit(self.tree_a / "app" / "main.py")[0])

    def test_without_a_path_the_first_review_run_answers_as_before(self):
        self.register(TASK_A, "review")
        conn = state.connect()
        try:
            run = pretool_gate.active_review_run(conn)
        finally:
            conn.close()
        self.assertEqual(TASK_A, run["task"])

    def test_a_blocked_or_paused_review_run_freezes_nothing(self):
        self.register(TASK_A, "review", stage_status=state.ST_BLOCKED)
        self.assertEqual(0, self.edit(self.tree_a / "app" / "main.py")[0])

    def test_when_ownership_cannot_be_read_the_freeze_stays_global(self):
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")
        with unittest.mock.patch.object(worktree, "owner_of", side_effect=RuntimeError("x")):
            self.assertEqual(2, self.edit(self.tree_b / "app" / "main.py")[0])


# --- D6: the trunk checkout takes no edits ---------------------------------------------

class TrunkFreezeEditTest(WorkspaceCase):
    def test_a_code_edit_in_the_trunk_checkout_is_denied(self):
        code, err = self.edit(self.src / "app" / "main.py")

        self.assertEqual(2, code)
        self.assertIn("inside the trunk checkout", err)
        self.assertIn("worktree", err)

    def test_the_harness_code_inside_the_trunk_checkout_is_frozen_too(self):
        # .claude/ is bookkeeping for the live harness, but in this repo it is also code.
        code, _ = self.edit(self.src / ".claude" / "tools" / "pipeline" / "x.py")
        self.assertEqual(2, code)

    def test_every_editing_tool_is_covered(self):
        for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                ti = {"Edit": {"file_path": str(self.src / "a.py"), "new_string": "x"},
                      "Write": {"file_path": str(self.src / "a.py"), "content": "x"},
                      "MultiEdit": {"file_path": str(self.src / "a.py"),
                                    "edits": [{"new_string": "x"}]},
                      "NotebookEdit": {"notebook_path": str(self.src / "a.ipynb"),
                                       "new_source": "x"}}[tool]
                code, _, err = hook(pretool_gate, {"tool_name": tool, "tool_input": ti,
                                                   "cwd": str(self.root), **SUBAGENT})
                self.assertEqual(2, code, err)
                self.assertIn("trunk checkout", err)

    def test_the_orchestrator_is_frozen_out_of_the_trunk_checkout_as_well(self):
        code, _ = self.edit(self.src / "docs" / "note.md", orch=True)
        self.assertEqual(2, code)

    def test_agentry_bookkeeping_inside_the_trunk_checkout_is_not_frozen(self):
        self.assertEqual(0, self.edit(self.src / ".agentry" / "note.md")[0])
        self.assertEqual(0, self.edit(self.root / ".agentry" / "tasks" / "x.md")[0])

    def test_a_task_worktree_is_editable(self):
        tree = self.add_tree(TASK_A)
        self.register(TASK_A, "implement")

        self.assertEqual(0, self.edit(tree / "app" / "main.py")[0])

    def test_a_relative_path_is_placed_by_the_payload_cwd(self):
        tree = self.add_tree(TASK_A)
        code, _, err = hook(pretool_gate, edit_payload("app/main.py", str(self.src), **SUBAGENT))
        self.assertEqual(2, code, err)
        code, _, err = hook(pretool_gate, edit_payload("app/main.py", str(tree), **SUBAGENT))
        self.assertEqual(0, code, err)

    def test_a_repo_whose_head_is_a_feature_branch_is_not_trunk(self):
        # A legacy run or a swarm umbrella holds src/ on a task branch: it stays editable.
        self.git(self.src, "checkout", "-q", "-b", "feature/task-9399")

        self.assertEqual(0, self.edit(self.src / "app" / "main.py")[0])

    def test_a_path_that_is_not_in_any_repo_is_not_frozen(self):
        self.assertEqual(0, self.edit(self.root / "scratch" / "a.py")[0])


class FlagOffTest(WorkspaceCase):
    """Ships dark: with worktrees.enabled false, or no block at all, nothing new fires."""

    def commands(self) -> list:
        return [
            f"echo x > {self.src.as_posix()}/app/new.py",
            f"cp a.txt {self.src.as_posix()}/app/b.py",
            (f"git -C {self.src.as_posix()} worktree add -b feature/task-9304 "
             f"{self.root.as_posix()}/src-wt/task-9304 main"),
        ]

    def check_dark(self):
        self.assertEqual(0, self.edit(self.src / "app" / "main.py")[0])
        self.assertEqual(0, self.edit(self.src / ".claude" / "tools" / "x.py")[0])
        for command in self.commands():
            with self.subTest(command=command):
                self.assertEqual(0, self.bash(command)[0])
        for command in ("git worktree add -b feature/task-9305 ../src-wt/task-9305 main",
                        "git worktree remove ../src-wt/task-9305",
                        "python .claude/tools/pipeline/worktree.py --remove --task task-9305",
                        "python .claude/tools/pipeline/worktree.py --repair --task task-9305"):
            with self.subTest(command=command):
                for who in (agent_gate.DEV_WHO, "Read-only agent", "Docs agent"):
                    self.assertEqual(
                        0, agent_gate.bash_prelude(who, command, str(self.root)))

    def test_with_the_flag_false_no_new_deny_fires(self):
        self.set_config(False)
        self.check_dark()

    def test_with_no_worktrees_block_no_new_deny_fires(self):
        self.set_config(None)
        self.check_dark()

    def test_a_non_boolean_flag_is_off(self):
        cfg = json.loads(self.config.read_text(encoding="utf-8"))
        cfg["worktrees"] = {"enabled": "true"}
        self.config.write_text(json.dumps(cfg), encoding="utf-8")
        self.check_dark()

    def test_a_legacy_merge_is_judged_by_conditions_one_to_three_only(self):
        self.set_config(False)
        self.git(self.src, "checkout", "-q", "-b", f"feature/{TASK_C}")
        self.commit(self.src, "work")
        self.git(self.src, "checkout", "-q", "main")
        self.commit(self.src, "trunk moved on")
        self.register(TASK_C, "ready", tree=False, commit_approved=1)

        code, err = self.bash(f"git -C {self.src.as_posix()} merge --no-ff feature/{TASK_C}")

        self.assertEqual(0, code, err)


class TrunkFreezeBashTest(WorkspaceCase):
    def denied(self, command: str, cwd: Path | None = None) -> str:
        code, err = self.bash(command, cwd)
        self.assertEqual(2, code, f"{command!r} was not denied")
        self.assertIn("trunk checkout", err)
        return err

    def test_the_file_writing_commands_are_denied_in_the_trunk_checkout(self):
        src = self.src.as_posix()
        for command in (f"echo x > {src}/app/new.py",
                        f"echo x >> {src}/app/main.py",
                        f"echo x | tee {src}/app/new.py",
                        f"cp notes.txt {src}/app/new.py",
                        f"mv notes.txt {src}/app/new.py",
                        f"rm {src}/app/main.py",
                        f"mkdir {src}/app/sub",
                        f"sed -i s/1/2/ {src}/app/main.py",
                        f'bash -c "echo x > {src}/app/new.py"'):
            with self.subTest(command=command):
                self.denied(command)

    def test_a_relative_target_is_placed_by_cd_and_by_the_payload_cwd(self):
        self.denied("cd src && echo x > app/new.py")
        self.denied("echo x > app/new.py", cwd=self.src)

    def test_reads_and_the_solo_merge_are_not_file_writes(self):
        src = self.src.as_posix()
        for command in (f"cat {src}/app/main.py", f"git -C {src} status",
                        f"git -C {src} log --oneline", f"ls {src}/app"):
            with self.subTest(command=command):
                self.assertEqual(0, self.bash(command)[0])

    def test_bookkeeping_and_scratch_outside_the_trunk_checkout_are_not_frozen(self):
        for command in (f"echo x > {self.src.as_posix()}/.agentry/note.md",
                        f"echo x > {self.root.as_posix()}/.agentry/note.md",
                        f"echo x > {self.root.as_posix()}/tmp/out.txt",
                        "rm -f nul NUL"):
            with self.subTest(command=command):
                self.assertEqual(0, self.bash(command, cwd=self.src)[0])

    def test_a_task_worktree_is_writable_from_bash(self):
        tree = self.add_tree(TASK_A)
        self.register(TASK_A, "implement")

        self.assertEqual(0, self.bash(f"echo x > {tree.as_posix()}/app/new.py")[0])
        self.assertEqual(0, self.bash("echo x > app/new.py", cwd=tree)[0])
        self.assertEqual(0, self.bash(f"cd {tree.as_posix()} && echo x > app/new.py")[0])

    def test_a_dev_agent_goes_through_the_same_freeze(self):
        code, _, err = hook(
            agent_gate, bash_payload(f"echo x > {self.src.as_posix()}/app/new.py", str(self.root)),
            ["agent_gate", "--profile", "dev"])
        self.assertEqual(2, code, err)
        self.assertIn("trunk checkout", err)


# --- 2.7 condition 4: a worktree task's branch must contain the trunk ---------------------

class MergeFreshnessTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.tree = self.add_tree(TASK_A)
        self.commit(self.tree, "task work")
        self.branch = f"feature/{TASK_A}"
        self.merge = f"git -C {self.src.as_posix()} merge --no-ff {self.branch}"

    def merge_verdict(self, command: str | None = None, cwd: Path | None = None) -> tuple:
        out = hook(pretool_gate, bash_payload(command or self.merge, str(cwd or self.root)))
        return out[0], out[2]

    def test_a_branch_cut_from_the_current_trunk_may_merge(self):
        self.register(TASK_A, "ready", commit_approved=1)

        code, err = self.merge_verdict()

        self.assertEqual(0, code, err)

    def test_a_branch_the_trunk_has_left_behind_is_refused_with_the_sync_command(self):
        self.register(TASK_A, "ready", commit_approved=1)
        self.commit(self.src, "sibling merged")
        self.commit(self.src, "and another")

        code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("condition 4 failed (freshness)", err)
        self.assertIn(worktree.stale_message(2), err)
        self.assertIn("`git merge main` in the worktree", err)

    def test_after_the_sync_merge_in_the_worktree_the_merge_is_allowed(self):
        self.register(TASK_A, "ready", commit_approved=1)
        self.commit(self.src, "sibling merged")
        self.assertEqual(2, self.merge_verdict()[0])

        self.git(self.tree, "merge", "-q", "main", "-m", "sync with main")

        code, err = self.merge_verdict()
        self.assertEqual(0, code, err)

    def test_conditions_one_to_three_keep_their_own_messages(self):
        self.commit(self.src, "sibling merged")
        # condition 3: not approved
        self.register(TASK_A, "ready", commit_approved=0)
        code, err = self.merge_verdict()
        self.assertEqual(2, code)
        self.assertIn("condition 3 of 3 failed", err)
        self.assertNotIn("condition 4", err)
        # condition 2: no row
        code, err = self.merge_verdict(f"git -C {self.src.as_posix()} merge --no-ff feature/task-9398")
        self.assertEqual(2, code)
        self.assertIn("condition 2 of 3 failed", err)
        # condition 1: not a task branch
        code, err = self.merge_verdict(f"git -C {self.src.as_posix()} merge --no-ff topic")
        self.assertEqual(2, code)
        self.assertIn("condition 1 of 3 failed", err)

    def test_a_legacy_task_is_not_asked_for_freshness(self):
        self.git(self.src, "checkout", "-q", "-b", f"feature/{TASK_C}")
        self.commit(self.src, "legacy work")
        self.git(self.src, "checkout", "-q", "main")
        self.commit(self.src, "sibling merged")
        self.register(TASK_C, "ready", tree=False, commit_approved=1)

        code, err = self.merge_verdict(
            f"git -C {self.src.as_posix()} merge --no-ff feature/{TASK_C}")

        self.assertEqual(0, code, err)

    def test_the_repository_is_placed_by_cd_and_by_the_payload_cwd(self):
        self.register(TASK_A, "ready", commit_approved=1)
        self.commit(self.src, "sibling merged")

        for command, cwd in ((f"cd {self.src.as_posix()} && git merge --no-ff {self.branch}", None),
                             (f"git merge --no-ff {self.branch}", self.src)):
            with self.subTest(command=command, cwd=str(cwd)):
                code, err = self.merge_verdict(command, cwd)
                self.assertEqual(2, code)
                self.assertIn("condition 4", err)

    def test_a_pull_of_the_branch_is_held_to_the_same_condition(self):
        self.register(TASK_A, "ready", commit_approved=1)
        self.commit(self.src, "sibling merged")

        code, err = self.merge_verdict(
            f"git -C {self.src.as_posix()} pull --no-ff . {self.branch}")

        self.assertEqual(2, code)
        self.assertIn("condition 4", err)

    def test_an_unreadable_freshness_fails_closed(self):
        self.register(TASK_A, "ready", commit_approved=1)
        self.git(self.src, "worktree", "remove", "--force", str(self.tree))
        self.git(self.src, "branch", "-D", self.branch)

        # Condition 3 would stop it first (the branch is gone, so its change set cannot be
        # read); this test is about what condition 4 does on its own.
        with unittest.mock.patch.object(pretool_gate, "check_merge_binding", return_value=0):
            code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("condition 4 failed", err)
        self.assertIn("could not be read", err)

    def test_an_unreadable_change_set_fails_closed_at_condition_3(self):
        self.register(TASK_A, "ready", commit_approved=1)
        self.git(self.src, "worktree", "remove", "--force", str(self.tree))
        self.git(self.src, "branch", "-D", self.branch)

        code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("condition 3 of 3 failed", err)
        self.assertIn("could not be read", err)

    def test_the_recorded_tree_decides_not_the_flag(self):
        # Turning the flag off is a rollback: trees already recorded keep their freshness rule.
        self.set_config(False)
        self.register(TASK_A, "ready", commit_approved=1)
        self.commit(self.src, "sibling merged")

        code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("condition 4", err)

    def test_the_freshness_check_costs_exactly_one_extra_git_call(self):
        self.register(TASK_A, "ready", commit_approved=1)
        calls = []
        real = pretool_gate.git_run

        def spy(argv, repo, *rest, **kw):
            calls.append(argv)
            return real(argv, repo, *rest, **kw)

        with unittest.mock.patch.object(pretool_gate, "git_run", spy):
            self.assertEqual(0, self.merge_verdict()[0])

        self.assertEqual(1, len([a for a in calls if "rev-list" in a]))
        # the branch read, the two calls of the change-set binding (merge-base, diff-tree)
        # and the freshness count
        self.assertEqual(4, len(calls))

    def test_the_freshness_call_runs_under_the_push_budget(self):
        self.register(TASK_A, "ready", commit_approved=1)
        real = pretool_gate.git_run

        def expire_on_rev_list(argv, repo):
            if "rev-list" in argv:
                raise pretool_gate.PushBudgetExpired()
            return real(argv, repo)

        with unittest.mock.patch.object(pretool_gate, "git_run", expire_on_rev_list):
            code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("time budget", err)

    def test_a_merge_inside_the_worktree_is_not_a_trunk_merge(self):
        self.register(TASK_A, "implement")
        self.commit(self.src, "sibling merged")

        code, err = self.merge_verdict(f"git -C {self.tree.as_posix()} merge main")

        self.assertEqual(0, code, err)


# --- 2.7: the commit and branch checks resolve the task's worktree ------------------------

class PlacementTest(WorkspaceCase):
    """Two trees at once, one approved: each commit is judged by the tree it runs in."""

    def setUp(self):
        super().setUp()
        self.tree_a = self.add_tree(TASK_A)
        self.tree_b = self.add_tree(TASK_B)
        self.register(TASK_A, "ready", commit_approved=1)
        self.register(TASK_B, "implement", commit_approved=0)

    def commit_verdict(self, command: str, cwd: Path) -> tuple:
        code, _, err = hook(pretool_gate, bash_payload(command, str(cwd)))
        return code, err

    def test_git_dash_c_places_the_commit(self):
        msg = f'-m "[{TASK_A}] feat: x"'
        self.assertEqual(0, self.commit_verdict(
            f"git -C {self.tree_a.as_posix()} commit {msg}", self.root)[0])
        code, err = self.commit_verdict(
            f"git -C {self.tree_b.as_posix()} commit -m x", self.root)
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK_B} is not approved", err)

    def test_cd_places_the_commit(self):
        self.assertEqual(0, self.commit_verdict(
            f"cd {self.tree_a.as_posix()} && git commit -m x", self.root)[0])
        code, err = self.commit_verdict(
            f"cd {self.tree_b.as_posix()} && git commit -m x", self.root)
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK_B} is not approved", err)

    def test_the_payload_cwd_places_the_commit(self):
        self.assertEqual(0, self.commit_verdict("git commit -m x", self.tree_a)[0])
        code, err = self.commit_verdict("git commit -m x", self.tree_b)
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK_B} is not approved", err)

    def test_a_commit_in_the_trunk_checkout_is_not_a_task_commit(self):
        code, err = self.commit_verdict("git commit -m x", self.src)
        self.assertEqual(2, code)
        self.assertIn("Not on a feature branch", err)

    def test_a_commit_from_the_workspace_root_names_no_tree(self):
        # The cd of an earlier Bash call does not persist: the hook cannot place this one.
        code, err = self.commit_verdict("git commit -m x", self.root)
        self.assertEqual(2, code)
        self.assertIn("Not on a feature branch", err)

    def test_a_subagent_is_judged_like_the_main_thread(self):
        code, _, err = hook(pretool_gate, bash_payload(
            f"git -C {self.tree_b.as_posix()} commit -m x", str(self.root), **SUBAGENT))
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK_B} is not approved", err)


class WorktreeAddBranchTest(WorkspaceCase):
    def test_new_branch_name_reads_dash_b_and_dash_capital_b(self):
        expect = ("feature/task-0130", ["main"])
        for command in (
                "git worktree add -b feature/task-0130 ../src-wt/task-0130 main",
                "git worktree add -B feature/task-0130 ../src-wt/task-0130 main",
                "git worktree add ../src-wt/task-0130 -b feature/task-0130 main",
                "git worktree add -bfeature/task-0130 ../src-wt/task-0130 main",
                "git -C src worktree add --lock -b feature/task-0130 ../src-wt/task-0130 main",
                "git worktree add --reason why --lock -b feature/task-0130 ../src-wt/task-0130 main",
                "git status && git worktree add -b feature/task-0130 ../src-wt/task-0130 main"):
            with self.subTest(command=command):
                self.assertEqual(expect, pretool_gate.new_branch_name(command))

    def test_without_a_start_point_the_rest_is_empty(self):
        self.assertEqual(("feature/task-0130", []), pretool_gate.new_branch_name(
            "git worktree add -b feature/task-0130 ../src-wt/task-0130"))

    def test_forms_that_create_no_branch_name_none(self):
        for command in ("git worktree add ../src-wt/task-0130 feature/task-0130",
                        "git worktree add --detach ../x main",
                        "git worktree list", "git worktree remove ../x",
                        "git worktree add -b"):
            with self.subTest(command=command):
                self.assertEqual(("", []), pretool_gate.new_branch_name(command))

    def test_the_branch_naming_rule_applies(self):
        code, err = self.bash(f"git -C {self.src.as_posix()} worktree add -b badname "
                              f"{self.root.as_posix()}/src-wt/x main")
        self.assertEqual(2, code)
        self.assertIn("breaks the naming rule", err)
        code, err = self.bash(f"git -C {self.src.as_posix()} worktree add -B hotfix/oops "
                              f"{self.root.as_posix()}/src-wt/x main")
        self.assertEqual(2, code)
        self.assertIn("breaks the naming rule", err)

    def test_the_base_rule_applies(self):
        code, err = self.bash(f"git -C {self.src.as_posix()} worktree add -b feature/task-9310 "
                              f"{self.root.as_posix()}/src-wt/task-9310 develop")
        self.assertEqual(2, code)
        self.assertIn("must be cut from up-to-date 'main'", err)

    def test_a_task_branch_from_the_trunk_is_allowed_with_and_without_a_start_point(self):
        for tail in ("main", "origin/main", ""):
            with self.subTest(start=tail):
                command = (f"git -C {self.src.as_posix()} worktree add -b feature/task-9310 "
                           f"{self.root.as_posix()}/src-wt/task-9310 {tail}")
                self.assertEqual(0, self.bash(command)[0])

    def test_without_a_start_point_the_current_branch_is_the_base(self):
        tree = self.add_tree(TASK_A)  # HEAD of this tree is a task branch, not the trunk
        command = (f"git worktree add -b feature/task-9310 {self.root.as_posix()}/src-wt/task-9310")
        code, err = self.bash(command, cwd=tree)
        self.assertEqual(2, code)
        self.assertIn("must be cut from up-to-date 'main'", err)

    def test_an_existing_branch_is_not_a_creation(self):
        self.assertEqual(0, self.bash(
            f"git -C {self.src.as_posix()} worktree add {self.root.as_posix()}/src-wt/x main")[0])


# --- dev agents may not change the worktree layout ----------------------------------------

class DevLayoutDenyTest(WorkspaceCase):
    def prelude(self, who: str, command: str) -> tuple:
        return self.stderr_of(agent_gate.bash_prelude, who, command, str(self.root))

    def test_every_git_worktree_writer_is_denied_for_a_dev_agent(self):
        for sub in ("add", "remove", "move", "prune", "repair", "lock", "unlock"):
            with self.subTest(sub=sub):
                code, err = self.prelude(agent_gate.DEV_WHO, f"git worktree {sub} x y")
                self.assertEqual(2, code)
                self.assertIn(f"git worktree {sub}", err)
                self.assertIn("changes the worktree layout", err)

    def test_the_full_dev_handler_refuses_them_too(self):
        for command in ("git worktree add -b feature/task-9310 ../src-wt/task-9310 main",
                        "git -C src worktree remove ../src-wt/task-9301",
                        "git worktree prune"):
            with self.subTest(command=command):
                code, err = self.stderr_of(agent_gate.handle_dev, "Bash", {"command": command},
                                           str(self.root))
                self.assertEqual(2, code)
                self.assertIn("changes the worktree layout", err)

    def test_the_read_forms_stay_open(self):
        for command in ("git worktree list", "git worktree list --porcelain",
                        "git -C src worktree list", "git status",
                        "python .claude/tools/pipeline/worktree.py --list",
                        "python .claude/tools/pipeline/worktree.py --sweep"):
            with self.subTest(command=command):
                self.assertEqual(0, self.prelude(agent_gate.DEV_WHO, command)[0])

    def test_worktree_py_remove_and_repair_are_denied_in_every_spelling(self):
        for command in ("python .claude/tools/pipeline/worktree.py --remove --task task-9301",
                        "python .claude/tools/pipeline/worktree.py --repair --task task-9301",
                        "python .claude/tools/pipeline/worktree.py --rem --task task-9301",
                        "python .claude/tools/pipeline/worktree.py --rep --task task-9301",
                        "python .claude/tools/pipeline/worktree.py --task task-9301 --remove",
                        "python -m worktree --repair --task task-9301",
                        "cd .claude/tools/pipeline && python3 worktree.py --remove --task task-9301",
                        "python .claude/tools/pipeline/worktree.py --sweep --remove --task task-9301"):
            with self.subTest(command=command):
                code, err = self.prelude(agent_gate.DEV_WHO, command)
                self.assertEqual(2, code)
                self.assertIn("changes the worktree layout", err)

    def test_nested_shell_bodies_are_unwrapped(self):
        for command in ('bash -c "git worktree prune"',
                        'sh -c "python .claude/tools/pipeline/worktree.py --remove --task t"',
                        'echo $(git worktree remove x)'):
            with self.subTest(command=command):
                self.assertEqual(2, self.prelude(agent_gate.DEV_WHO, command)[0])

    def test_the_text_that_merely_names_a_subcommand_is_not_a_command(self):
        for command in ('echo "git worktree remove is for the orchestrator"',
                        "git log --grep='worktree add'",
                        "git show HEAD -- worktree.py"):
            with self.subTest(command=command):
                self.assertEqual(0, self.prelude(agent_gate.DEV_WHO, command)[0])

    def test_the_prelude_is_shared_by_the_readonly_and_docs_profiles(self):
        for who in ("Read-only agent", "Docs agent"):
            with self.subTest(who=who):
                code, err = self.prelude(who, "git worktree remove x")
                self.assertEqual(2, code)
                self.assertIn(who, err)

    def test_the_orchestrator_is_not_bound_by_the_agent_profile(self):
        # advance.py and the orchestrator create and remove trees; only the branch rules apply.
        code, err = self.bash(
            f"git -C {self.src.as_posix()} worktree add -b feature/task-9310 "
            f"{self.root.as_posix()}/src-wt/task-9310 main")
        self.assertEqual(0, code, err)
        self.assertEqual(0, self.bash(f"git -C {self.src.as_posix()} worktree remove x")[0])

    def test_the_deny_site_is_one_call_in_the_shared_prelude(self):
        import ast
        tree = ast.parse((PIPELINE_DIR / "agent_gate.py").read_text(encoding="utf-8"))
        holders = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                   for call in ast.walk(node)
                   if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                   and call.func.id == "deny"
                   and any(isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute)
                           and isinstance(a.func.value, ast.Name)
                           and a.func.value.id == "WORKTREE_LAYOUT_MSG" for a in call.args)}
        self.assertEqual({"bash_prelude"}, holders)


# --- fix round: every git call is placed by its own -C and cd ---------------------------------

class PerCallPlacementTest(WorkspaceCase):
    """Tree A is approved, tree B is not. A command with several git calls is judged call by
    call, each in the repository it runs in (the whole-command rule read the first -C, or
    the last cd in front of the first `git `)."""

    def setUp(self):
        super().setUp()
        self.tree_a = self.add_tree(TASK_A)
        self.tree_b = self.add_tree(TASK_B)
        self.register(TASK_A, "ready", commit_approved=1)
        self.register(TASK_B, "implement", commit_approved=0)
        self.a, self.b = self.tree_a.as_posix(), self.tree_b.as_posix()

    def verdict(self, command: str, cwd: Path | None = None, orch: bool = True) -> tuple:
        return self.bash(command, cwd, orch=orch)

    def assert_b_refused(self, command: str, cwd: Path | None = None) -> None:
        for orch in (True, False):
            code, err = self.verdict(command, cwd, orch=orch)
            self.assertEqual(2, code, f"{command!r} (orch={orch}) was not refused")
            self.assertIn(f"Commit for {TASK_B} is not approved", err)

    def test_a_second_commit_is_judged_in_its_own_tree_in_every_chaining(self):
        for command in (f"cd {self.a} && git commit -m a && cd {self.b} && git commit -m b",
                        f"git -C {self.a} commit -m a && git -C {self.b} commit -m b",
                        f"cd {self.a}; git commit -m a; cd {self.b}; git commit -m b",
                        f"cd {self.a} && git commit -m a || git -C {self.b} commit -m b"):
            with self.subTest(command=command):
                self.assert_b_refused(command)

    def test_an_echo_of_the_word_git_does_not_move_the_placement(self):
        self.assert_b_refused(f"cd {self.a} && echo git && cd {self.b} && git commit -m b")

    def test_a_later_cd_does_not_re_place_an_earlier_commit(self):
        self.assert_b_refused(f"cd {self.b} && git commit -m b && cd {self.a} && git commit -m a")

    def test_commits_in_two_approved_trees_pass(self):
        conn = state.connect()
        try:
            state.set_fields(conn, TASK_B, commit_approved=1)
        finally:
            conn.close()
        self.bind(TASK_B)

        for command in (f"cd {self.a} && git commit -m a && cd {self.b} && git commit -m b",
                        f"git -C {self.a} commit -m a && git -C {self.b} commit -m b"):
            with self.subTest(command=command):
                code, err = self.verdict(command)
                self.assertEqual(0, code, err)

    def test_a_dash_c_on_an_earlier_read_does_not_place_a_later_merge(self):
        self.commit(self.tree_a, "work")
        self.bind(TASK_A)
        self.commit(self.src, "sibling merged")
        s = self.src.as_posix()
        for command in (f"git -C {self.b} status && git -C {s} merge --no-ff feature/{TASK_A}",
                        f"cd {self.b} && git status && cd {s} && git merge --no-ff feature/{TASK_A}"):
            with self.subTest(command=command):
                code, err = self.verdict(command)
                self.assertEqual(2, code)
                self.assertIn("condition 4 failed (freshness)", err)

    def test_a_merge_into_a_tree_after_a_trunk_read_is_still_not_a_trunk_merge(self):
        self.commit(self.src, "sibling merged")

        code, err = self.verdict(f"git -C {self.src.as_posix()} log --oneline && "
                                 f"git -C {self.b} merge main")

        self.assertEqual(0, code, err)

    def test_a_pull_and_a_push_are_placed_per_call_too(self):
        code, err = self.verdict(f"git -C {self.a} status && git -C {self.b} pull . main")
        self.assertEqual(0, code, err)  # main into a task branch is the sync, not a trunk merge
        code, err = self.verdict(
            f"git -C {self.b} status && git -C {self.src.as_posix()} push origin main")
        self.assertEqual(2, code)
        self.assertIn("Push to protected branch 'main' is forbidden", err)

    def test_a_nested_body_is_placed_by_its_own_cd(self):
        self.assert_b_refused(f"bash -c 'cd {self.b} && git commit -m b'")
        code, err = self.verdict(f"bash -c 'cd {self.a} && git commit -m a'")
        self.assertEqual(0, code, err)

    def test_a_nested_body_is_judged_wherever_the_outer_command_could_run_it(self):
        # The outer command visits the workspace root and tree B; the body runs in one of them
        # and the gate cannot tell which, so the unapproved one decides.
        self.assert_b_refused(f"cd {self.b} && bash -c 'git commit -m b'")
        self.assert_b_refused(f"cd {self.b} && echo $(git commit -m b)")

    def test_a_call_the_text_does_not_place_is_refused(self):
        for command in (f"(cd {self.b} && true); git commit -m b",
                        f"pushd {self.b} && popd && git commit -m x",
                        'cd "$TREE" && git commit -m x',
                        'git -C "$TREE" commit -m x',
                        "cd ~ && git commit -m x",
                        "cd && git commit -m x"):
            with self.subTest(command=command):
                code, err = self.verdict(command, self.tree_a)
                self.assertEqual(2, code)
                self.assertIn("judged in the repository it runs in", err)

    def test_a_group_without_a_cd_is_placed_as_before(self):
        code, err = self.verdict("(git commit -m a)", self.tree_a)
        self.assertEqual(0, code, err)

    def test_a_heredoc_message_that_talks_about_git_is_still_one_commit(self):
        body = ("git commit -m \"$(cat <<'EOF'\n[" + TASK_A + "] fix: git commit and git push gates\n\n"
                "Body line with git merge in it.\nEOF\n)\"")

        code, err = self.verdict(body, self.tree_a)
        self.assertEqual(0, code, err)
        code, err = self.verdict(body.replace(TASK_A, TASK_B), self.tree_a)
        self.assertEqual(2, code)

    def test_the_message_tag_still_overrides_the_tree_of_the_place(self):
        code, err = self.verdict(f'git -C {self.a} commit -m "[{TASK_B}] feat: x"')
        self.assertEqual(2, code)
        self.assertIn(f"This commit is tagged [{TASK_B}] but the checked-out branch", err)

    def test_a_commit_message_file_is_read_from_the_place_of_the_call(self):
        (self.tree_b / "msg.txt").write_text(f"[{TASK_A}] feat: x\n", encoding="utf-8")
        (self.tree_a / "msg.txt").write_text(f"[{TASK_A}] feat: x\n", encoding="utf-8")
        self.bind(TASK_A)

        code, err = self.verdict(f"cd {self.b} && git commit -F msg.txt")
        self.assertEqual(2, code)
        self.assertIn(f"This commit is tagged [{TASK_A}] but the checked-out branch", err)
        code, err = self.verdict(f"cd {self.a} && git commit -F msg.txt")
        self.assertEqual(0, code, err)

    def test_with_the_flag_off_and_no_recorded_tree_the_whole_command_is_judged_as_before(self):
        self.set_config(False)
        conn = state.connect()
        try:
            conn.execute("UPDATE runs SET worktree = ''")
            conn.commit()
        finally:
            conn.close()
        boom = AssertionError("per-call judgement ran while worktrees are not in play")
        with unittest.mock.patch.object(pretool_gate, "judge_git_per_call", side_effect=boom):
            self.verdict(f"git -C {self.b} status && git -C {self.a} commit -m a")
            self.verdict(f"(cd {self.b} && git commit -m b)")

    def test_a_recorded_tree_keeps_the_per_call_judgement_with_the_flag_off(self):
        self.set_config(False)

        self.assert_b_refused(f"cd {self.a} && git commit -m a && cd {self.b} && git commit -m b")


class RepoRedirectTest(WorkspaceCase):
    """--git-dir, --work-tree, -c core.worktree and the GIT_* assignments move the repository
    a git call runs in, so a commit, merge, pull or push that uses one is refused."""

    def setUp(self):
        super().setUp()
        self.tree = self.add_tree(TASK_A)
        self.register(TASK_A, "ready", commit_approved=1)
        self.t = self.tree.as_posix()

    def refused(self, command: str, hit: str) -> None:
        for orch in (True, False):
            code, err = self.bash(command, self.tree, orch=orch)
            self.assertEqual(2, code, f"{command!r} (orch={orch}) was allowed")
            self.assertIn(hit, err)

    def test_every_redirecting_option_is_refused_for_every_guarded_subcommand(self):
        for sub in ("commit -m x", "merge main", "pull . main", "push origin feature/task-9301"):
            for option, hit in ((f"--git-dir={self.t}/.git", "--git-dir"),
                                (f"--git-dir {self.t}/.git", "--git-dir"),
                                (f"--work-tree={self.t}", "--work-tree"),
                                (f"--work-tree {self.t}", "--work-tree"),
                                ("-c core.worktree=/x", "core.worktree"),
                                ("-c Core.WorkTree=/x", "core.worktree"),
                                ("--config-env=core.worktree=WT", "core.worktree"),
                                ("--config-env core.worktree=WT", "core.worktree")):
                with self.subTest(command=f"git {option} {sub}"):
                    self.refused(f"git {option} {sub}", hit)

    def test_every_redirecting_assignment_is_refused(self):
        for prefix, hit in (("GIT_DIR=/x/.git", "GIT_DIR"), ("GIT_WORK_TREE=/x", "GIT_WORK_TREE"),
                            ("GIT_INDEX_FILE=/x", "GIT_INDEX_FILE"),
                            ("env GIT_DIR=/x/.git", "GIT_DIR"),
                            ("export GIT_WORK_TREE=/x;", "GIT_WORK_TREE"),
                            ("FOO=1 GIT_DIR=/x", "GIT_DIR")):
            with self.subTest(prefix=prefix):
                self.refused(f"{prefix} git commit -m x", hit)

    def test_nested_bodies_are_read_too(self):
        for command, hit in ((f"bash -c 'git --git-dir={self.t}/.git commit -m x'", "--git-dir"),
                             (f"sh -c \"GIT_DIR={self.t}/.git git commit -m x\"", "GIT_DIR"),
                             (f"echo $(git --work-tree={self.t} commit -m x)", "--work-tree"),
                             ("eval 'git -c core.worktree=/x merge main'", "core.worktree")):
            with self.subTest(command=command):
                self.refused(command, hit)

    def test_a_redirect_on_a_call_that_is_not_guarded_is_not_this_checks_business(self):
        code, err = self.bash(f"git --git-dir={self.t}/.git status", self.tree)
        self.assertEqual(0, code, err)

    def test_ordinary_global_options_and_text_pass(self):
        for command in ("git -c user.name=x commit -m x", "git --no-pager commit -m x",
                        'git commit -m "documents GIT_DIR=x and --git-dir"',
                        f"git -C {self.t} commit -m x"):
            with self.subTest(command=command):
                code, err = self.bash(command, self.tree)
                self.assertEqual(0, code, err)

    def test_a_recorded_tree_keeps_the_check_with_the_flag_off(self):
        self.set_config(False)

        self.refused(f"git --work-tree={self.t} commit -m x", "--work-tree")
        self.refused("GIT_DIR=/x/.git git commit -m x", "GIT_DIR")

    def test_nothing_is_refused_while_worktrees_are_not_in_play(self):
        self.set_config(False)
        conn = state.connect()
        try:
            conn.execute("UPDATE runs SET worktree = ''")
            conn.commit()
        finally:
            conn.close()
        boom = AssertionError("the redirect check ran while worktrees are not in play")
        with unittest.mock.patch.object(pretool_gate, "repo_redirect", side_effect=boom):
            self.bash(f"git --work-tree={self.t} commit -m x", self.tree)


class HeadMoveTest(WorkspaceCase):
    """With the flag on a subagent may not move HEAD of the trunk checkout: that would lift
    the freeze. Its own linked worktree, and a checkout a feature branch holds, are free."""

    def prelude(self, command: str, cwd: Path | None = None) -> tuple:
        return self.stderr_of(agent_gate.bash_prelude, agent_gate.DEV_WHO, command,
                              str(cwd or self.root))

    def refused(self, command: str, cwd: Path | None = None) -> None:
        code, err = self.prelude(command, cwd)
        self.assertEqual(2, code, f"{command!r} was allowed")
        self.assertIn("moves HEAD of the trunk checkout", err)

    def test_every_head_moving_call_is_refused_in_the_trunk_checkout(self):
        s = self.src.as_posix()
        for tail in ("checkout main", "checkout -b feature/task-9400", "checkout -B feature/task-9400",
                     "checkout --detach", "checkout -fb feature/task-9400", "checkout HEAD~1",
                     "switch -c feature/task-9400", "switch --detach", "switch main",
                     "reset --hard", "reset --soft HEAD~1", "reset", "rebase main feature/task-9301",
                     "branch -M feature/task-9400", "branch -m feature/task-9400",
                     "branch -f feature/task-9400 main", "branch --force x main", "branch --move x",
                     "branch -fM x", "symbolic-ref HEAD refs/heads/x", "update-ref HEAD HEAD~1"):
            with self.subTest(tail=tail):
                self.refused(f"git -C {s} {tail}")

    def test_the_trunk_is_placed_by_cd_cwd_and_nested_bodies(self):
        s = self.src.as_posix()
        self.refused(f"cd {s} && git checkout -b feature/task-9400")
        self.refused("git checkout -b feature/task-9400", self.src)
        self.refused("git checkout -b feature/task-9400", self.src / "app")
        self.refused(f"bash -c 'cd {s} && git switch -c feature/task-9400'")
        self.refused(f"git -C {self.root.as_posix()}/src/app reset --hard")
        self.refused(f"cd {self.root.as_posix()} && git -C src checkout main")

    def test_a_place_the_text_does_not_give_is_refused(self):
        self.refused('git -C "$TREE" checkout main')
        self.refused('cd "$TREE" && git reset --hard')

    def test_the_reading_forms_and_other_branch_calls_pass(self):
        s = self.src.as_posix()
        for tail in ("status", "log --oneline", "branch", "branch --list", "branch -a", "branch -vv",
                     "branch feature/task-9401", "branch -d feature/task-9401",
                     "diff", "fetch origin", "stash list", "show HEAD", "rev-parse HEAD"):
            with self.subTest(tail=tail):
                self.assertEqual(0, self.prelude(f"git -C {s} {tail}")[0], tail)

    def test_a_task_worktree_may_move_its_own_head(self):
        tree = self.add_tree(TASK_A)
        t = tree.as_posix()
        for command, cwd in ((f"git -C {t} checkout -b feature/task-9402", None),
                             (f"cd {t} && git reset --hard", None),
                             ("git checkout main~0 -- app/main.py", tree),
                             ("git switch -c feature/task-9403", tree),
                             ("git branch -M feature/task-9404", tree)):
            with self.subTest(command=command):
                code, err = self.prelude(command, cwd)
                self.assertEqual(0, code, err)

    def test_a_checkout_a_feature_branch_holds_is_not_the_trunk(self):
        # A legacy run or a swarm umbrella holds src/ on its task branch.
        self.git(self.src, "checkout", "-q", "-b", "feature/task-9399")

        self.assertEqual(0, self.prelude("git checkout -b feature/task-9405", self.src)[0])
        self.assertEqual(0, self.prelude("git reset --hard", self.src)[0])

    def test_a_detached_head_in_the_trunk_checkout_is_still_the_trunk(self):
        self.git(self.src, "checkout", "-q", "--detach")

        self.refused("git checkout main", self.src)

    def test_the_orchestrator_is_not_bound_by_the_prelude(self):
        code, err = self.bash(f"git -C {self.src.as_posix()} checkout -b feature/task-9406")
        self.assertEqual(0, code, err)

    def test_nothing_is_refused_with_the_flag_off(self):
        self.set_config(False)

        self.assertEqual(0, self.prelude("git checkout -b feature/task-9407", self.src)[0])

    def test_the_full_dev_handler_refuses_it_too(self):
        code, err = self.stderr_of(agent_gate.handle_dev, "Bash",
                                   {"command": f"git -C {self.src.as_posix()} checkout --detach"},
                                   str(self.root))
        self.assertEqual(2, code)
        self.assertIn("moves HEAD of the trunk checkout", err)

    def test_a_lookup_that_fails_is_a_refusal(self):
        with unittest.mock.patch.object(pretool_gate, "primary_trunk_checkout",
                                        side_effect=RuntimeError("x")):
            self.refused(f"git -C {self.src.as_posix()} checkout main")


class TrunkWriterTest(WorkspaceCase):
    """The shell writers beyond redirect/tee/sed -i/cp/mv/rm, and a shell expansion in an
    operand, are frozen out of the trunk checkout as well."""

    def setUp(self):
        super().setUp()
        self.s = self.src.as_posix()

    def refused(self, command: str, cwd: Path | None = None, hit: str = "trunk checkout") -> None:
        for orch in (True, False):
            code, err = self.bash(command, cwd, orch=orch)
            self.assertEqual(2, code, f"{command!r} (orch={orch}) was allowed")
            self.assertIn(hit, err)

    def test_the_other_file_writers_are_refused(self):
        s = self.s
        for command in (f"touch {s}/app/x", f"touch -d 2020-01-01 {s}/app/x", f"touch -r a {s}/app/x",
                        f"install -m 644 a {s}/app/b.py", f"install -t {s}/app a b",
                        f"install -d {s}/app/new", f"dd if=a of={s}/app/main.py",
                        f"dd of={s}/app/main.py", f"rmdir {s}/app", f"unlink {s}/app/main.py",
                        f"find {s} -name main.py -delete", f"find {s} -name x -exec rm {{}} +",
                        f"find {s} -fprint {s}/app/list.txt", f"patch -p1 -d {s} < x.diff",
                        f"patch -d{s} < x.diff", f"patch --directory={s} < x.diff",
                        f"patch {s}/app/main.py x.diff",
                        f"git -C {s} checkout -- app/main.py", f"git -C {s} checkout main -- app",
                        f"git -C {s} restore app/main.py", f"git -C {s} restore --staged .",
                        f"git -C {s} apply x.patch", f"git -C {s} am x.mbox",
                        f"git -C {s} clean -fd", f"git -C {s} rm app/main.py",
                        f"git -C {s} mv app/main.py app/other.py", f"git -C {s} stash pop",
                        f"git -C {s} stash apply"):
            with self.subTest(command=command):
                self.refused(command)

    def test_the_trunk_is_placed_by_cd_cwd_and_nested_bodies(self):
        self.refused(f"cd {self.s} && touch app/x")
        self.refused("touch app/x", self.src)
        self.refused("git checkout -- app/main.py", self.src)
        self.refused(f"bash -c 'cd {self.s} && rmdir app'")
        self.refused(f"cd {self.root.as_posix()} && git -C src restore .")
        self.refused("find . -name main.py -delete", self.src)

    def test_a_shell_expansion_in_a_write_operand_is_refused(self):
        s = self.s
        for command in (f"for f in a b; do echo x > {s}/app/$f; done",
                        f"echo x > {s}/app/$(echo n).py", f"cp a {s}/app/$NAME",
                        'echo x > "$OUT"', "echo x > $D/f", "cp a $T", "tee $F", "rm -f $FILE",
                        "mkdir -p $DIR/sub", "sed -i s/a/b/ $F", "touch $F", "echo x > `pwd`/f",
                        f"cd {self.root.as_posix()}/tmp && echo x > $F"):
            with self.subTest(command=command):
                self.refused(command, hit="shell expansion")

    def test_an_expansion_that_is_not_an_operand_passes(self):
        for command in (f'echo "$HOME" > {self.root.as_posix()}/tmp/o',
                        'echo $(date) done', 'ls "$HOME"', 'cat $FILE', 'grep -r "$PATTERN" app',
                        f"cp $SRC {self.root.as_posix()}/tmp/b"):
            with self.subTest(command=command):
                code, err = self.bash(command, self.root, orch=False)
                self.assertEqual(0, code, err)

    def test_reads_and_read_only_forms_pass(self):
        s = self.s
        for command in (f"find {s} -name main.py", f"find {s} -name main.py -exec grep x {{}} +",
                        f"git -C {s} apply --check x.patch", f"git -C {s} apply --stat x.patch",
                        f"git -C {s} clean -n", f"git -C {s} clean --dry-run",
                        f"git -C {s} stash list", f"git -C {s} checkout main",
                        f"git -C {s} diff", f"dd if={s}/app/main.py of={self.root.as_posix()}/tmp/o",
                        f"install -m 644 {s}/app/main.py {self.root.as_posix()}/tmp/o",
                        f"touch {self.root.as_posix()}/tmp/x", f"patch -d {self.root.as_posix()}/tmp x.diff"):
            with self.subTest(command=command):
                code, err = self.bash(command, self.root, orch=True)
                self.assertEqual(0, code, err)

    def test_a_task_worktree_may_use_every_writer(self):
        tree = self.add_tree(TASK_A)
        self.register(TASK_A, "implement")
        t = tree.as_posix()
        for command in (f"touch {t}/app/x", f"cd {t} && git checkout -- app/main.py",
                        f"git -C {t} restore .", f"find {t} -name main.py -delete",
                        f"dd of={t}/app/main.py", f"cd {t} && patch -p1 < x.diff"):
            with self.subTest(command=command):
                code, err = self.bash(command, self.root, orch=False)
                self.assertEqual(0, code, err)

    def test_the_writers_are_free_with_the_flag_off(self):
        self.set_config(False)

        self.assertEqual(0, self.bash(f"touch {self.s}/app/x $F; echo x > $D/f")[0])

    def test_a_failed_check_refuses_a_subagent_and_leaves_the_main_thread_alone(self):
        with unittest.mock.patch.object(agent_gate, "trunk_write_operands",
                                        side_effect=RuntimeError("x")):
            code, err = self.bash("echo hi", orch=False)
            self.assertEqual(2, code)
            self.assertIn("the command is refused rather than allowed unchecked", err)
            self.assertEqual(0, self.bash("echo hi", orch=True)[0])


class StrictModuleFailureTest(WorkspaceCase):
    """With the flag on, a question about trees that cannot be answered reads against a
    subagent. The main thread is never bricked by a broken module."""

    def test_a_trunk_lookup_that_raises_freezes_a_subagent_only(self):
        with unittest.mock.patch.object(worktree, "is_trunk_checkout", side_effect=OSError("x")):
            self.assertEqual(2, self.edit(self.src / "app" / "main.py", orch=False)[0])
            self.assertEqual(2, self.edit(self.root / "elsewhere" / "a.py", orch=False)[0])
            self.assertEqual(0, self.edit(self.src / "app" / "main.py", orch=True)[0])
            self.assertEqual(0, self.edit(self.src / ".agentry" / "n.md", orch=False)[0])

    def test_a_missing_module_with_the_flag_on_in_the_config_freezes_a_subagent_only(self):
        with unittest.mock.patch.object(pretool_gate, "worktree_mod", return_value=None):
            self.assertTrue(pretool_gate.worktrees_on())
            self.assertEqual(2, self.edit(self.src / "app" / "main.py", orch=False)[0])
            self.assertEqual(2, self.bash("echo x > a.py", orch=False)[0])
            self.assertEqual(0, self.edit(self.src / "app" / "main.py", orch=True)[0])
            self.assertEqual(0, self.bash("echo x > a.py", orch=True)[0])

    def test_a_missing_module_with_the_flag_off_changes_nothing(self):
        self.set_config(False)
        with unittest.mock.patch.object(pretool_gate, "worktree_mod", return_value=None):
            self.assertFalse(pretool_gate.worktrees_on())
            self.assertEqual(0, self.edit(self.src / "app" / "main.py", orch=False)[0])
            self.assertEqual(0, self.bash("echo x > a.py", orch=False)[0])

    def test_a_flag_lookup_that_raises_falls_back_to_the_config_literal(self):
        with unittest.mock.patch.object(worktree, "enabled", side_effect=RuntimeError("x")):
            self.assertTrue(pretool_gate.worktrees_on())
            self.set_config(False)
            self.assertFalse(pretool_gate.worktrees_on())

    def test_an_ownership_lookup_that_raises_keeps_the_review_freeze(self):
        tree = self.add_tree(TASK_A)
        other = self.add_tree(TASK_B)
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")
        with unittest.mock.patch.object(worktree, "owner_of", side_effect=RuntimeError("doubt")):
            for path in (tree / "app" / "main.py", other / "app" / "main.py"):
                code, err = self.edit(path, orch=False)
                self.assertEqual(2, code)
                self.assertIn(f"{TASK_A} is in the read-only 'review' stage", err)

    def test_an_unreadable_store_in_the_ownership_lookup_keeps_the_review_freeze(self):
        tree = self.add_tree(TASK_A)
        other = self.add_tree(TASK_B)
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")
        with unittest.mock.patch.object(worktree, "_rows", return_value=None), \
                unittest.mock.patch.object(worktree.time, "sleep"):
            with self.assertRaises(worktree.Doubt):
                worktree.owner_of(other / "app" / "main.py")
            code, err = self.edit(other / "app" / "main.py", orch=False)

        self.assertEqual(2, code)
        self.assertIn(f"{TASK_A} is in the read-only 'review' stage", err)
        self.assertEqual(0, self.edit(other / "app" / "main.py", orch=False)[0])
        self.assertEqual(2, self.edit(tree / "app" / "main.py", orch=False)[0])

    def test_a_path_the_module_cannot_place_keeps_the_review_freeze(self):
        # Doubt for a path owner_of cannot place (empty, UNC) must not free it.
        self.add_tree(TASK_A)
        self.register(TASK_A, "review")

        self.assertTrue(pretool_gate.owns_path(TASK_A, "", str(self.root)))
        self.assertTrue(pretool_gate.owns_path(TASK_A, "//host/share/app/main.py", str(self.root)))

    def test_a_recorded_path_that_is_not_a_tree_path_keeps_the_review_freeze_global(self):
        tree = self.add_tree(TASK_A)
        self.register(TASK_A, "review")
        for bad in ("../escaped", "src-wt/task-9399", "C:/elsewhere/task-9301", "src-wt\\task-9301",
                    "src-wt/task-9301/sub", "nonsense"):
            conn = state.connect()
            try:
                conn.execute("UPDATE runs SET worktree = ? WHERE task = ?", (bad, TASK_A))
                conn.commit()
            finally:
                conn.close()
            with self.subTest(recorded=bad):
                self.assertEqual(2, self.edit(tree / "app" / "main.py")[0])
                self.assertEqual(2, self.edit(self.root / "elsewhere" / "a.py")[0])

    def test_a_clean_answer_naming_another_task_or_no_tree_frees_the_path(self):
        tree = self.add_tree(TASK_A)
        other = self.add_tree(TASK_B)
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")

        self.assertEqual(2, self.edit(tree / "app" / "main.py")[0])
        self.assertEqual(0, self.edit(other / "app" / "main.py")[0])
        self.assertEqual(0, self.edit(self.root / "elsewhere" / "a.py")[0])


class LayoutDenyKeyTest(WorkspaceCase):
    """The layout denies hold while the flag is on OR a run that is not done records a tree,
    so a rollback (flag off) does not hand the recorded trees to the agents."""

    ENABLED = False

    def prelude(self, command: str = "git worktree remove x") -> int:
        return self.stderr_of(agent_gate.bash_prelude, agent_gate.DEV_WHO, command, str(self.root))[0]

    def test_a_recorded_tree_of_a_live_run_keeps_the_denies_with_the_flag_off(self):
        self.add_tree(TASK_A)
        self.register(TASK_A, "implement")

        for command in ("git worktree remove x", "git worktree prune",
                        "python .claude/tools/pipeline/worktree.py --remove --task task-9301",
                        "python .claude/tools/pipeline/worktree.py --repair --task task-9301"):
            with self.subTest(command=command):
                self.assertEqual(2, self.prelude(command))

    def test_a_parked_run_still_holds_its_tree(self):
        self.add_tree(TASK_A)
        self.register(TASK_A, "ready", commit_approved=1)

        self.assertEqual(2, self.prelude())

    def test_a_finished_run_releases_the_denies(self):
        self.add_tree(TASK_A)
        self.register(TASK_A, "done")

        self.assertEqual(0, self.prelude())

    def test_a_legacy_run_does_not_hold_them(self):
        self.register(TASK_C, "implement", tree=False)

        self.assertEqual(0, self.prelude())

    def test_no_runs_and_no_flag_means_no_denies(self):
        self.assertEqual(0, self.prelude())

    def test_the_reading_forms_stay_open_while_a_tree_is_recorded(self):
        self.add_tree(TASK_A)
        self.register(TASK_A, "implement")

        for command in ("git worktree list", "python .claude/tools/pipeline/worktree.py --list",
                        "python .claude/tools/pipeline/worktree.py --sweep"):
            with self.subTest(command=command):
                self.assertEqual(0, self.prelude(command))


class ClusteredBranchFlagTest(WorkspaceCase):
    """Short options cluster: `-fb NAME` creates NAME just as `-b NAME` does."""

    def test_new_branch_name_reads_a_cluster_in_every_creating_command(self):
        for command, expect in (
                ("git worktree add -fb feature/task-9310 ../x main", ("feature/task-9310", ["main"])),
                ("git worktree add -qb feature/task-9310 ../x main", ("feature/task-9310", ["main"])),
                ("git worktree add -fB feature/task-9310 ../x main", ("feature/task-9310", ["main"])),
                ("git worktree add -fbfeature/task-9310 ../x main", ("feature/task-9310", ["main"])),
                ("git worktree add ../x -fb feature/task-9310 main", ("feature/task-9310", ["main"])),
                ("git checkout -fb feature/task-9310 main", ("feature/task-9310", ["main"])),
                ("git checkout -qB feature/task-9310", ("feature/task-9310", [])),
                ("git checkout -fbfeature/task-9310", ("feature/task-9310", [])),
                ("git checkout -b feature/task-9310 main", ("feature/task-9310", ["main"])),
                ("git switch -fc feature/task-9310 main", ("feature/task-9310", ["main"])),
                ("git switch -fC feature/task-9310", ("feature/task-9310", [])),
                ("git switch -c feature/task-9310", ("feature/task-9310", []))):
            with self.subTest(command=command):
                self.assertEqual(expect, pretool_gate.new_branch_name(command))

    def test_clusters_without_a_creating_letter_name_nothing(self):
        for command in ("git checkout -f main", "git checkout -fq main", "git checkout -p",
                        "git switch -f main", "git switch -d main", "git worktree add -fq ../x main",
                        "git worktree add -f ../x main", "git checkout -b", "git checkout -fb"):
            with self.subTest(command=command):
                self.assertEqual(("", []), pretool_gate.new_branch_name(command))

    def test_a_bad_name_in_a_cluster_is_refused_by_the_naming_and_base_rules(self):
        s, r = self.src.as_posix(), self.root.as_posix()
        for command, text in (
                (f"git -C {s} worktree add -fb badname {r}/src-wt/x main", "breaks the naming rule"),
                (f"git -C {s} worktree add -fb feature/task-9310 {r}/src-wt/x develop",
                 "must be cut from up-to-date 'main'"),
                (f"git -C {s} checkout -fb badname", "breaks the naming rule"),
                (f"git -C {s} checkout -qb feature/task-9310 develop",
                 "must be cut from up-to-date 'main'"),
                (f"git -C {s} switch -fc badname", "breaks the naming rule")):
            with self.subTest(command=command):
                code, err = self.bash(command, orch=True)
                self.assertEqual(2, code)
                self.assertIn(text, err)

    def test_a_good_cluster_from_the_trunk_passes(self):
        s, r = self.src.as_posix(), self.root.as_posix()
        code, err = self.bash(f"git -C {s} worktree add -fb feature/task-9310 {r}/src-wt/x main")
        self.assertEqual(0, code, err)


# --- R4: no subprocess in the read-only checks -----------------------------------------------

class NoSubprocessTest(WorkspaceCase):
    def test_the_freeze_checks_spawn_nothing(self):
        tree = self.add_tree(TASK_A)
        self.register(TASK_A, "review")
        boom = AssertionError("a gate check started a subprocess")
        with unittest.mock.patch.object(subprocess, "run", side_effect=boom), \
                unittest.mock.patch.object(subprocess, "Popen", side_effect=boom), \
                unittest.mock.patch.object(pretool_gate, "git_run", side_effect=boom):
            self.assertEqual(2, self.edit(self.src / "app" / "main.py")[0])
            self.assertEqual(2, self.edit(tree / "app" / "main.py")[0])
            self.assertEqual(2, self.bash(f"echo x > {self.src.as_posix()}/a.py")[0])
            self.assertEqual(0, self.bash(f"echo x > {self.root.as_posix()}/tmp/a.py")[0])
            self.assertEqual(2, self.stderr_of(agent_gate.bash_prelude, agent_gate.DEV_WHO,
                                               "git worktree add x", str(self.root))[0])


if __name__ == "__main__":
    unittest.main()
