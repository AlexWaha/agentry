"""epic-0004 / task-0129: the exit gates run in the task's own worktree.

gate.run_gate and stack_gate.main judge the code a task wrote, so with a worktree
recorded they run there; a run with none behaves exactly as before; a recorded tree
that is missing or malformed blocks the gate and never falls back to the trunk
checkout. `{root}` points a gate command at the live harness. Also covered here:
git_state's view of linked worktrees (repos(), work_trees_for_task(), behind_trunk()).

The gate commands are real child processes; a command that must not run writes a
marker file, and the test asserts the marker is absent.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import gate
import git_state
import stack_gate
import state
import tmproot
import worktree
from test_worktree_core import BRANCH, OTHER, TASK, WorkspaceCase

PY = f'"{sys.executable}"'
PRINT_CWD = f'{PY} -c "import os; print(os.getcwd())"'
KEYS = {"stage", "has_gate", "configured", "cmd", "exit", "passed", "output"}


def pipeline_with(cmd: str, cwd: str = "src", stage_cwd: str = "") -> dict:
    exit_gate = {"cmd": cmd, "expect_exit": 0, **({"cwd": stage_cwd} if stage_cwd else {})}
    return {"cwd": cwd, "pipelines": {"build": {"stages": [
        {"name": "test", "owner": "qa-engineer", "exit_gate": exit_gate}]}}}


class GateCase(WorkspaceCase):

    def run_gate(self, cmd: str = PRINT_CWD, task: str | None = TASK, **kw) -> dict:
        return gate.run_gate(pipeline_with(cmd, **kw), "test", task)

    def cwd_of(self, result: dict) -> Path:
        self.assertTrue(result["passed"], result)
        return Path(result["output"].strip().splitlines()[-1]).resolve()

    def marker_cmd(self, name: str = "ran.marker") -> str:
        return f'{PY} -c "open(\'{(self.root / name).as_posix()}\', \'w\').write(\'x\')"'


class GateCwdTest(GateCase):

    def test_a_run_with_no_tree_runs_in_the_stage_cwd_as_before(self):
        self.run_row()
        self.assertEqual(self.src, self.cwd_of(self.run_gate()))

    def test_a_task_with_no_run_at_all_runs_in_the_stage_cwd(self):
        self.assertEqual(self.src, self.cwd_of(self.run_gate()))

    def test_no_task_means_no_tree_lookup(self):
        self.make_tree()
        self.assertEqual(self.src, self.cwd_of(self.run_gate(task=None)))

    def test_a_recorded_usable_tree_is_where_the_command_runs(self):
        self.make_tree()
        self.assertEqual(self.root / "src-wt" / TASK, self.cwd_of(self.run_gate()))

    def test_the_command_judges_the_trees_code_not_the_trunks(self):
        self.make_tree()
        self.commit(self.root / "src-wt" / TASK, "only_here.txt", "mine")
        probe = f'{PY} -c "import os, sys; sys.exit(0 if os.path.exists(\'only_here.txt\') else 3)"'
        self.assertTrue(self.run_gate(probe)["passed"])
        self.assertEqual(3, self.run_gate(probe, task=None)["exit"])

    def test_a_stage_cwd_inside_the_repo_keeps_its_place_inside_the_tree(self):
        (self.src / "sub").mkdir()
        self.commit(self.src, "sub/f.txt", "sub")
        self.make_tree()
        out = self.run_gate(cwd=".", stage_cwd="src/sub")
        self.assertEqual(self.root / "src-wt" / TASK / "sub", self.cwd_of(out))

    def test_a_stage_cwd_outside_the_repo_is_none_of_the_trees_business(self):
        self.make_tree()
        self.assertEqual(self.root, self.cwd_of(self.run_gate(cwd=".")))

    def test_each_task_runs_in_its_own_tree(self):
        self.make_tree(TASK)
        self.make_tree(OTHER)
        self.assertEqual(self.root / "src-wt" / TASK, self.cwd_of(self.run_gate(task=TASK)))
        self.assertEqual(self.root / "src-wt" / OTHER, self.cwd_of(self.run_gate(task=OTHER)))

    def test_the_result_keeps_its_keys_and_the_exit_code_decides(self):
        self.make_tree()
        passed = self.run_gate(f'{PY} -c "import sys; sys.exit(0)"')
        failed = self.run_gate(f'{PY} -c "import sys; sys.exit(5)"')
        self.assertEqual(KEYS, set(passed))
        self.assertEqual((True, 0), (passed["passed"], passed["exit"]))
        self.assertEqual((False, 5), (failed["passed"], failed["exit"]))
        self.assertTrue(failed["configured"])


class GateMissingTreeTest(GateCase):

    def assertBlocked(self, result: dict, status: str) -> None:
        self.assertEqual(KEYS, set(result))
        self.assertEqual((True, False, False, None), (result["has_gate"], result["configured"],
                                                      result["passed"], result["exit"]))
        self.assertTrue(result["output"].startswith(f"task worktree {status}: "), result["output"])
        self.assertIn(f"worktree.py\" --repair --task {TASK}", result["output"])
        self.assertFalse((self.root / "ran.marker").exists(), "the command ran")

    def test_a_deleted_tree_blocks_the_gate_and_runs_nothing(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        result = self.run_gate(self.marker_cmd())
        self.assertBlocked(result, "MISSING")
        self.assertIn(f"src-wt/{TASK}", result["output"])

    def test_it_does_not_fall_back_to_the_trunk_checkout(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        (self.src / "trunk_only.txt").write_text("t", encoding="utf-8")
        probe = f'{PY} -c "import os, sys; sys.exit(0 if os.path.exists(\'trunk_only.txt\') else 1)"'
        self.assertFalse(self.run_gate(probe)["passed"])
        self.assertIsNone(self.run_gate(probe)["exit"])

    def test_a_malformed_recorded_path_blocks_the_gate(self):
        self.run_row()
        for bad in ("../evil", "src-wt/task-0999", "src/task-0130"):
            with self.subTest(recorded=bad):
                self.sql("UPDATE runs SET worktree = ? WHERE task = ?", bad, TASK)
                self.assertBlocked(self.run_gate(self.marker_cmd()), "INVALID")

    def test_an_unreadable_run_store_fails_the_gate_for_a_retry_rather_than_blocking(self):
        """MISSING and INVALID are verdicts on the tree and BLOCK (configured False). A store
        that could not be read says nothing about the tree: a FAILED gate (configured True), so
        advance.py spends a retry instead of parking the run for the User."""
        import sqlite3
        with unittest.mock.patch.object(state, "connect", side_effect=sqlite3.OperationalError("x")):
            result = self.run_gate(self.marker_cmd())
        self.assertEqual(KEYS, set(result))
        self.assertEqual((True, True, False, None), (result["has_gate"], result["configured"],
                                                     result["passed"], result["exit"]))
        self.assertIn("unreadable", result["output"])
        self.assertIn("retry", result["output"])
        self.assertFalse((self.root / "ran.marker").exists())

    def test_a_garbage_run_store_fails_the_gate_for_a_retry_and_runs_nothing(self):
        self.make_tree()
        db = state.DB_PATH
        for suffix in ("", "-wal", "-shm", "-journal"):
            part = Path(f"{db}{suffix}")
            if part.exists():
                part.unlink()
        db.write_bytes(b"this is not a sqlite database " * 20)
        result = self.run_gate(self.marker_cmd())
        self.assertEqual((True, False, None), (result["configured"], result["passed"], result["exit"]))
        self.assertFalse((self.root / "ran.marker").exists())

    def test_a_store_that_fails_once_is_read_again_before_the_gate_gives_up(self):
        self.make_tree()
        calls = []
        real = state.connect

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("busy for a moment")
            return real()

        with unittest.mock.patch.object(state, "connect", side_effect=flaky):
            result = self.run_gate()
        self.assertTrue(result["passed"], result)

    def test_a_stage_without_a_command_still_auto_passes(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        pipeline = {"pipelines": {"build": {"stages": [{"name": "ready", "owner": "orchestrator"}]}}}
        result = gate.run_gate(pipeline, "ready", TASK)
        self.assertEqual((False, True, True), (result["has_gate"], result["configured"], result["passed"]))

    def test_a_blocked_gate_writes_no_gate_journal_row(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        with unittest.mock.patch.object(state, "emit_gate") as emitted:
            self.run_gate(self.marker_cmd())
        emitted.assert_not_called()


class RootPlaceholderTest(GateCase):

    def echo_root(self) -> str:
        return f'{PY} -c "import sys; print(sys.argv[1])" {{root}}'

    def test_root_resolves_to_the_live_harness_even_from_a_tree(self):
        self.make_tree()
        out = self.run_gate(self.echo_root())
        self.assertTrue(out["passed"], out)
        self.assertEqual(self.root.as_posix(), out["output"].strip())
        self.assertIn(self.root.as_posix(), out["cmd"])
        self.assertNotIn("{root}", out["cmd"])

    def test_root_resolves_for_a_run_with_no_tree_and_for_no_task(self):
        self.run_row()
        for task in (TASK, None):
            out = self.run_gate(self.echo_root(), task=task)
            self.assertEqual(self.root.as_posix(), out["output"].strip())

    def test_task_is_replaced_first_and_both_work_in_one_command(self):
        self.run_row()
        cmd = f'{PY} -c "import sys; print(sys.argv[1], sys.argv[2])" {{task}} {{root}}'
        out = self.run_gate(cmd)
        self.assertEqual(f"{TASK} {self.root.as_posix()}", out["output"].strip())

    def test_a_root_outside_the_allowlist_refuses_the_gate(self):
        """Only [A-Za-z0-9_./:-] reaches a shell command line: whitespace and quotes, and
        every shell metacharacter besides."""
        for bad in ("E:/with space/ws", "E:/it's/ws", 'E:/say"hi/ws', "E:/tab\there/ws",
                    "E:/cash$HOME/ws", "E:/semi;colon/ws", "E:/amp&ersand/ws", "E:/pipe|d/ws",
                    "E:/paren(s)/ws", "E:/back`tick/ws", "E:/redirect>out/ws", "E:/star*/ws",
                    "E:/caret^/ws", "E:/percent%PATH%/ws", "E:/uml\u00e4ut/ws", "E:/new\nline/ws"):
            with self.subTest(root=bad), unittest.mock.patch.object(state, "ROOT", Path(bad)):
                result = gate.run_gate(pipeline_with(self.echo_root()), "test", None)
                self.assertEqual((True, False, False, None),
                                 (result["has_gate"], result["configured"], result["passed"], result["exit"]))
                self.assertTrue(result["output"].startswith("refusing {root}"), result["output"])

    def test_a_root_inside_the_allowlist_is_replaced(self):
        for good in ("E:/Personal/Agenty", "/home/dev/work_1.2/ws-x", "C:/a/b-c_d.e"):
            with self.subTest(root=good), unittest.mock.patch.object(state, "ROOT", Path(good)):
                result = gate.run_gate(pipeline_with("echo {root}"), "test", None)
                self.assertNotIn("refusing", result["output"])
                self.assertEqual(Path(good).as_posix(), result["cmd"].split()[-1])

    def test_a_command_without_the_placeholder_is_not_subject_to_the_root_check(self):
        with unittest.mock.patch.object(state, "ROOT", self.root):
            self.run_row()
            self.assertTrue(self.run_gate(f'{PY} -c "print(1)"')["passed"])

    def test_an_unreplaced_double_brace_placeholder_still_refuses(self):
        out = self.run_gate(f'{PY} -c "print(1)" {{{{STACK}}}}')
        self.assertFalse(out["configured"])
        self.assertIn("placeholders", out["output"])

    def test_a_task_id_that_is_not_one_is_still_refused(self):
        out = self.run_gate(self.echo_root(), task="task-1; echo hi")
        self.assertFalse(out["configured"])
        self.assertIn("refusing task id", out["output"])


class StackGateCase(WorkspaceCase):

    def setUp(self):
        super().setUp()
        self.config(None, repos={"agentry": {"path": "src", "stack": "python", "stages": {
            "test": ["suite"], "review": []}}, "api": {"path": "api", "stages": {"test": ["api suite"]}}})
        (self.root / "api").mkdir()

    def make_tree(self, task: str = TASK, stage: str = "implement", **fields) -> dict:
        added = worktree.add(task, "feature", "agentry")
        self.assertTrue(added["ok"], added)
        self.run_row(task, stage, added["path"], **fields)
        return added

    def gate(self, stage: str = "test", task: str = TASK, repo: str = "agentry") -> tuple[int, str, list]:
        out = io.StringIO()
        argv = ["stack_gate.py", "--task", task, "--stage", stage, "--repo", repo]
        with unittest.mock.patch.object(sys, "argv", argv), \
                unittest.mock.patch.object(stack_gate, "run", return_value=0) as ran, \
                contextlib.redirect_stdout(out):
            code = stack_gate.main()
        return code, out.getvalue(), ran.call_args_list


class StackGateCwdTest(StackGateCase):

    def test_a_run_with_no_tree_runs_in_the_repo(self):
        self.run_row()
        code, _, calls = self.gate()
        self.assertEqual(0, code)
        self.assertEqual([unittest.mock.call(["suite"], self.root / "src")], calls)

    def test_a_usable_tree_is_where_the_commands_run(self):
        self.make_tree()
        _, _, calls = self.gate()
        self.assertEqual([unittest.mock.call(["suite"], self.root / "src-wt" / TASK)], calls)

    def test_a_missing_tree_fails_with_the_exact_message_and_runs_nothing(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        code, printed, calls = self.gate()
        self.assertEqual(1, code)
        self.assertEqual("agentry: test gate FAILED. task worktree MISSING; nothing was run.\n", printed)
        self.assertEqual([], calls)

    def test_an_invalid_recorded_path_fails_the_same_way(self):
        self.run_row()
        self.sql("UPDATE runs SET worktree = '../evil' WHERE task = ?", TASK)
        code, printed, calls = self.gate()
        self.assertEqual(1, code)
        self.assertEqual("agentry: test gate FAILED. task worktree INVALID; nothing was run.\n", printed)
        self.assertEqual([], calls)

    def test_a_stage_with_no_commands_is_still_an_explicit_pass(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        code, printed, calls = self.gate("review")
        self.assertEqual((0, "agentry: no review gate - explicit pass\n", []), (code, printed, calls))

    def test_another_repos_gate_is_not_moved_into_this_tasks_tree(self):
        self.make_tree()
        code, _, calls = self.gate(repo="api")
        self.assertEqual(0, code)
        self.assertEqual([unittest.mock.call(["api suite"], self.root / "api")], calls)

    def test_a_real_command_runs_in_the_tree(self):
        self.make_tree()
        self.config(None, repos={"agentry": {"path": "src", "stages": {"test": [
            f'{PY} -c "open(\'where.txt\', \'w\').write(\'x\')"']}}})
        out = io.StringIO()
        argv = ["stack_gate.py", "--task", TASK, "--stage", "test", "--repo", "agentry"]
        with unittest.mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            self.assertEqual(0, stack_gate.main())
        self.assertTrue((self.root / "src-wt" / TASK / "where.txt").is_file())
        self.assertFalse((self.src / "where.txt").exists())


class GitStateTest(WorkspaceCase):

    def test_auto_detect_skips_a_linked_worktree_and_lists_the_repo(self):
        self.config(None, repos=None)
        worktree.add(TASK, "feature")
        self.assertEqual([self.src], git_state.repos())

    def test_auto_detect_skips_a_root_that_is_itself_a_linked_worktree(self):
        self.config(None, repos=None)
        added = worktree.add(TASK, "feature")
        linked = Path(added["abs"])
        with unittest.mock.patch.object(state, "ROOT", linked):
            self.assertEqual([], git_state.repos())

    def test_auto_detect_still_lists_a_directory_with_a_git_directory(self):
        self.config(None, repos=None)
        (self.root / "api").mkdir()
        self.git(self.root / "api", "init", "-b", "main")
        self.assertEqual(["api", "src"], sorted(p.name for p in git_state.repos()))

    def test_auto_detect_still_lists_a_repo_whose_git_is_a_file_that_is_not_a_worktree_link(self):
        """A submodule checkout and a `git init --separate-git-dir` repo have a `.git` FILE
        too. Only a gitfile pointing into `<git dir>/worktrees/<name>` is a linked worktree."""
        self.config(None, repos=None)
        separate = self.root / "separate"
        self.git(self.src, "init", "--separate-git-dir", str(self.root / "separate-gitdir"),
                 str(separate))
        self.assertTrue((separate / ".git").is_file())
        sub = self.root / "group" / "sub"
        sub.mkdir(parents=True)
        (sub / ".git").write_text(f"gitdir: {self.src.as_posix()}/.git/modules/sub\n", encoding="utf-8")
        worktree.add(TASK, "feature")
        self.assertEqual(["separate", "src", "sub"], sorted(p.name for p in git_state.repos()))

    def test_a_git_file_without_a_gitdir_line_is_no_repo(self):
        self.config(None, repos=None)
        (self.root / "odd").mkdir()
        (self.root / "odd" / ".git").write_text("not a gitfile\n", encoding="utf-8")
        self.assertEqual(["src"], [p.name for p in git_state.repos()])

    def test_is_repo_root_reads_the_gitfile_target_not_the_directory_name(self):
        for target, expected in (("/x/.git/worktrees/task-0130", False),
                                 ("../main-repo/.git/worktrees/w", False),
                                 ("/x/.git/modules/sub", True),
                                 ("/elsewhere/separate-gitdir", True)):
            with self.subTest(gitdir=target):
                folder = self.root / "probe"
                folder.mkdir(exist_ok=True)
                (folder / ".git").write_text(f"gitdir: {target}\n", encoding="utf-8")
                self.assertEqual(expected, git_state.is_repo_root(folder))

    def test_configured_repos_are_listed_as_configured(self):
        self.assertEqual([self.root / "src"], git_state.repos())

    def test_a_tree_is_the_only_place_to_read_a_task_from(self):
        added = self.make_tree()
        self.assertEqual([Path(added["abs"])], git_state.work_trees_for_task(TASK))

    def test_a_run_with_no_tree_reads_its_repos_as_before(self):
        self.run_row()
        self.assertEqual(git_state.repos_for_task(TASK), git_state.work_trees_for_task(TASK))
        self.assertEqual([self.root / "src"], git_state.work_trees_for_task(TASK))

    def test_a_missing_or_malformed_tree_is_unknown_never_the_trunk(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        self.assertEqual([], git_state.work_trees_for_task(TASK))
        self.sql("UPDATE runs SET worktree = '../x' WHERE task = ?", TASK)
        self.assertEqual([], git_state.work_trees_for_task(TASK))

    def test_behind_trunk_counts_the_commits_the_branch_lacks(self):
        added = self.make_tree()
        tree = Path(added["abs"])
        self.assertEqual(0, git_state.behind_trunk(tree))
        self.commit(self.src, "a.txt", "one")
        self.commit(self.src, "b.txt", "two")
        self.assertEqual(2, git_state.behind_trunk(tree))
        self.assertEqual(0, git_state.behind_trunk(self.src))

    def test_a_synced_branch_is_fresh_again(self):
        added = self.make_tree()
        tree = Path(added["abs"])
        self.commit(self.src, "a.txt", "one")
        self.git(tree, "merge", "main", "-m", "sync")
        self.assertEqual(0, git_state.behind_trunk(tree))

    def test_work_on_the_branch_alone_is_not_behind(self):
        tree = Path(self.make_tree()["abs"])
        self.commit(tree, "mine.txt", "mine")
        self.assertEqual(0, git_state.behind_trunk(tree))

    def test_behind_trunk_takes_any_ref(self):
        tree = Path(self.make_tree()["abs"])
        self.commit(self.src, "a.txt", "one")
        self.assertEqual(1, git_state.behind_trunk(tree, BRANCH))
        self.assertEqual(0, git_state.behind_trunk(tree, "main"))

    def test_behind_trunk_cannot_tell_for_a_bad_ref_or_a_non_repo(self):
        tree = Path(self.make_tree()["abs"])
        self.assertIsNone(git_state.behind_trunk(tree, "no-such-ref"))
        elsewhere = self.root / "plain"
        elsewhere.mkdir()
        self.assertIsNone(git_state.behind_trunk(elsewhere))

    def test_the_timeout_defaults_to_the_git_timeout_and_can_be_given(self):
        seen = []
        real = git_state._git

        def spy(repo, *args, timeout=git_state.GIT_TIMEOUT):
            seen.append((args[0], timeout))
            return real(repo, *args, timeout=timeout)

        tree = Path(self.make_tree()["abs"])
        with unittest.mock.patch.object(git_state, "_git", side_effect=spy):
            git_state.behind_trunk(tree)
            git_state.behind_trunk(tree, timeout=7)
        counts = [t for cmd, t in seen if cmd == "rev-list"]
        self.assertEqual([git_state.GIT_TIMEOUT, 7], counts)

    def test_base_is_current_is_unchanged(self):
        tree = Path(self.make_tree()["abs"])
        self.assertIs(True, git_state.base_is_current(tree))
        self.commit(self.src, "a.txt", "one")
        self.assertIs(False, git_state.base_is_current(tree))


if __name__ == "__main__":
    unittest.main()
