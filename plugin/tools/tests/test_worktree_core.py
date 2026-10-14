"""epic-0004 / task-0129: worktree.py, the single owner of task-worktree facts.

Everything here runs against a REAL throwaway git repository and real worktrees under
the project's tmp/ (a stand-in for git would share the assumptions under test). The
workspace is a sandbox root holding the repo `src`, so a task's tree lands in
`<root>/src-wt/<task>` exactly as in production, and no test touches the live repo.

`WorkspaceCase` is the harness the sibling worktree test modules import.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import time
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
import worktree

TASK = "task-0130"
OTHER = "task-0131"
BRANCH = f"feature/{TASK}"
SLEEP = "import time; time.sleep(60)"


class WorkspaceCase(unittest.TestCase):
    """A sandbox workspace: `src` is a git repo on `main` with one commit, a run store
    and the task folders live beside it, and `state` points at all of them."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "wt_").resolve()
        self.src = self.root / "src"
        self.src.mkdir()
        self.git(self.src, "init", "-b", "main")
        self.git(self.src, "config", "user.email", "test@example.com")
        self.git(self.src, "config", "user.name", "Test")
        (self.src / ".gitignore").write_text("/tmp/\n__pycache__/\n", encoding="utf-8")
        (self.src / "README.md").write_text("hello\n", encoding="utf-8")
        self.git(self.src, "add", ".")
        self.git(self.src, "commit", "-m", "first")

        self.dirs = {name: self.root / ".agentry" / "tasks" / name
                     for name in ("backlog", "active", "done")}
        for folder in self.dirs.values():
            folder.mkdir(parents=True)
        self.config_path = self.root / ".agentry" / "pipeline.json"
        self.config()
        for name, value in (("ROOT", self.root), ("STATE_DIR", self.root / ".agentry" / "state"),
                            ("DB_PATH", self.root / ".agentry" / "state" / "run.db"),
                            ("PIPELINE_PATH", self.config_path), ("TASK_DIRS", self.dirs)):
            patcher = unittest.mock.patch.object(state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # A child process (a gate command, a CLI run) takes its project root from the
        # environment, never from where its script sits, so the sandbox is named there too.
        env = unittest.mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    # --- fixtures ----------------------------------------------------------------

    def config(self, worktrees: object = "on", **extra) -> None:
        """Write pipeline.json. `worktrees`: "on" (enabled, 2 slots), "off" (the block
        with the flag false), None (no block at all), or any literal block."""
        block = {"on": {"enabled": True, "max_parallel": 2},
                 "off": {"enabled": False, "max_parallel": 2}}.get(worktrees, worktrees) \
            if isinstance(worktrees, str) else worktrees
        doc = {"main_branch": "main", "repos": {"agentry": {"path": "src"}}, **extra}
        if block is not None:
            doc["worktrees"] = block
        self.config_path.write_text(json.dumps(doc), encoding="utf-8")

    def git(self, repo: Path, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        self.assertEqual(0, done.returncode, f"git {' '.join(args)}: {done.stderr}")
        return (done.stdout or "").strip()

    def commit(self, repo: Path, name: str = "work.txt", message: str = "work") -> None:
        (repo / name).write_text(message + "\n", encoding="utf-8")
        self.git(repo, "add", name)
        self.git(repo, "commit", "-m", message)

    def run_row(self, task: str = TASK, stage: str = "implement", rel: str = "", **fields) -> dict:
        conn = state.connect()
        try:
            state.create_run(conn, task, "feature", stage, worktree=rel)
            if fields:
                state.set_fields(conn, task, **fields)
            return state.get_run(conn, task)
        finally:
            conn.close()

    def row(self, task: str = TASK) -> dict | None:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()

    def sql(self, statement: str, *params) -> None:
        conn = state.connect()
        try:
            conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def make_tree(self, task: str = TASK, stage: str = "implement", **fields) -> dict:
        """worktree.add() for real, then the run row that records it."""
        added = worktree.add(task, "feature")
        self.assertTrue(added["ok"], added)
        self.run_row(task, stage, added["path"], **fields)
        return added

    def holder(self, cwd: Path) -> subprocess.Popen:
        """A native child process whose cwd is `cwd`, killed when the test ends."""
        proc = subprocess.Popen([sys.executable, "-c", SLEEP], cwd=str(cwd))
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def cli(self, *argv: str) -> tuple[int, dict]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = worktree.main(list(argv))
        return code, json.loads(out.getvalue())


class ConfigTest(WorkspaceCase):

    def test_an_absent_block_is_off_with_one_slot(self):
        self.config(None)
        self.assertIs(False, worktree.enabled())
        self.assertEqual(1, worktree.max_parallel())

    def test_only_a_literal_true_enables(self):
        for value in (True,):
            self.config({"enabled": value})
            self.assertIs(True, worktree.enabled())
        for value in ("true", 1, "yes", None, [], False):
            with self.subTest(value=value):
                self.config({"enabled": value})
                self.assertIs(False, worktree.enabled())

    def test_a_block_that_is_not_an_object_is_off(self):
        for block in ("enabled", ["enabled"], 3):
            with self.subTest(block=block):
                self.config(block)
                self.assertIs(False, worktree.enabled())
                self.assertEqual(1, worktree.max_parallel())

    def test_max_parallel_reads_an_int_of_at_least_one(self):
        self.config({"max_parallel": 2})
        self.assertEqual(2, worktree.max_parallel())
        self.config({"max_parallel": 5})
        self.assertEqual(5, worktree.max_parallel())

    def test_a_junk_max_parallel_reads_as_one(self):
        for value in (0, -3, True, False, "3", 2.0, None, [2]):
            with self.subTest(value=value):
                self.config({"enabled": True, "max_parallel": value})
                self.assertEqual(1, worktree.max_parallel())

    def test_the_shipped_default_block_shape(self):
        self.config({"enabled": False, "max_parallel": 2})
        self.assertIs(False, worktree.enabled())
        self.assertEqual(2, worktree.max_parallel())


class PathShapeTest(WorkspaceCase):

    def test_a_tree_path_is_repo_path_plus_suffix_plus_task(self):
        self.assertTrue(worktree.valid_path("src-wt/task-0130"))
        self.assertTrue(worktree.valid_path("src-wt/task-0130", TASK))

    def test_the_last_segment_must_be_the_given_task(self):
        self.assertFalse(worktree.valid_path("src-wt/task-0130", OTHER))

    def test_every_other_shape_is_refused(self):
        for rel in ("", "src/task-0130", "src-wt/task-0130/sub", "src-wt/feature",
                    "../src-wt/task-0130", "src-wt/../task-0130", "./src-wt/task-0130",
                    "src-wt\\task-0130", "C:/src-wt/task-0130", "/src-wt/task-0130",
                    "other-wt/task-0130", "src-wt//task-0130", "src-wt/task-0130/",
                    "-wt/task-0130", "src-wt/task-01x0", ".agentry/src-wt/task-0130",
                    "src-wt/task-0130\n"):
            with self.subTest(rel=rel):
                self.assertFalse(worktree.valid_path(rel))

    def test_a_non_string_is_refused(self):
        for rel in (None, 5, ["src-wt/task-0130"], Path("src-wt/task-0130")):
            self.assertFalse(worktree.valid_path(rel))

    def test_the_repo_part_must_be_a_known_repo(self):
        self.assertFalse(worktree.valid_path("api-wt/task-0130"))
        self.config(None, repos={"agentry": {"path": "src"}, "api": {"path": "api"}})
        self.assertTrue(worktree.valid_path("api-wt/task-0130"))

    def test_a_nested_repo_path_keeps_its_directory(self):
        self.config(None, repos={"agentry": {"path": "code/src"}})
        self.assertTrue(worktree.valid_path("code/src-wt/task-0130"))
        self.assertFalse(worktree.valid_path("src-wt/task-0130"))

    def test_the_workspace_root_as_a_repo_has_no_tree_path(self):
        self.config(None, repos={"agentry": {"path": "."}})
        self.assertFalse(worktree.valid_path("-wt/task-0130"))
        self.assertIsNone(worktree.root_dir())

    def test_root_dir_is_the_sibling_directory_and_creates_nothing(self):
        self.assertEqual(self.root / "src-wt", worktree.root_dir())
        self.assertEqual(self.root / "src-wt", worktree.root_dir("agentry"))
        self.assertFalse((self.root / "src-wt").exists())

    def test_root_dir_is_none_for_an_unknown_key_or_several_repos(self):
        self.assertIsNone(worktree.root_dir("nope"))
        self.config(None, repos={"agentry": {"path": "src"}, "api": {"path": "api"}})
        self.assertIsNone(worktree.root_dir())
        self.assertEqual(self.root / "api-wt", worktree.root_dir("api"))

    def test_repo_dir_names_the_repo_of_a_tree(self):
        self.assertEqual(self.root / "src", worktree.repo_dir("src-wt/task-0130"))
        self.assertIsNone(worktree.repo_dir("elsewhere/task-0130"))

    def test_stale_message_text_is_exact(self):
        self.assertEqual("main moved 3 commits: `git merge main` in the worktree",
                         worktree.stale_message(3))


class AddTest(WorkspaceCase):

    def test_add_cuts_the_branch_and_the_tree_from_the_trunk(self):
        trunk = self.git(self.src, "rev-parse", "main")
        out = worktree.add(TASK, "feature")
        tree = self.root / "src-wt" / TASK
        self.assertEqual({"ok": True, "path": f"src-wt/{TASK}", "abs": tree.as_posix(),
                          "branch": BRANCH, "base": trunk, "created": True}, out)
        self.assertTrue((tree / ".git").is_file())
        self.assertTrue((tree / "README.md").is_file())
        self.assertEqual(BRANCH, self.git(tree, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual(trunk, self.git(tree, "rev-parse", "HEAD"))

    def test_the_tree_gets_its_own_tmp_directory(self):
        worktree.add(TASK, "feature")
        self.assertTrue((self.root / "src-wt" / TASK / "tmp").is_dir())

    def test_the_branch_does_not_track_the_trunk(self):
        worktree.add(TASK, "feature")
        done = subprocess.run(["git", "-C", str(self.src), "config", "--get",
                               f"branch.{BRANCH}.remote"], capture_output=True, text=True)
        self.assertNotEqual(0, done.returncode)

    def test_the_trunk_checkout_is_untouched(self):
        worktree.add(TASK, "feature")
        self.assertEqual("main", self.git(self.src, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual("", self.git(self.src, "status", "--porcelain"))

    def test_the_branch_is_named_after_the_type(self):
        out = worktree.add(TASK, "bugfix")
        self.assertEqual(f"bugfix/{TASK}", out["branch"])

    def test_reusing_exactly_the_same_tree_and_branch_is_idempotent(self):
        first = worktree.add(TASK, "feature")
        again = worktree.add(TASK, "feature")
        self.assertTrue(again["ok"])
        self.assertIs(False, again["created"])
        self.assertEqual(first["abs"], again["abs"])

    def test_the_same_tree_on_another_type_is_refused(self):
        worktree.add(TASK, "feature")
        out = worktree.add(TASK, "bugfix")
        self.assertFalse(out["ok"])
        self.assertIn("feature/task-0130", out["error"])
        self.assertIn("worktree list", out["manual"])

    def test_an_existing_branch_without_a_tree_is_refused_with_the_command(self):
        self.git(self.src, "branch", BRANCH)
        out = worktree.add(TASK, "feature")
        self.assertFalse(out["ok"])
        self.assertIn(f"worktree add {self.root.as_posix()}/src-wt/{TASK} {BRANCH}", out["manual"])
        self.assertFalse((self.root / "src-wt" / TASK).exists())

    def test_a_directory_that_is_not_a_worktree_is_refused_and_left_alone(self):
        stray = self.root / "src-wt" / TASK
        stray.mkdir(parents=True)
        (stray / "mine.txt").write_text("keep", encoding="utf-8")
        out = worktree.add(TASK, "feature")
        self.assertFalse(out["ok"])
        self.assertTrue((stray / "mine.txt").is_file())

    def test_a_task_that_names_a_repo_gets_that_repos_tree(self):
        other = self.root / "api"
        other.mkdir()
        self.git(other, "init", "-b", "main")
        self.git(other, "config", "user.email", "t@example.com")
        self.git(other, "config", "user.name", "T")
        self.commit(other, "a.txt", "api")
        self.config(None, repos={"agentry": {"path": "src"}, "api": {"path": "api"}})
        out = worktree.add(TASK, "feature", "api")
        self.assertTrue(out["ok"], out)
        self.assertEqual(f"api-wt/{TASK}", out["path"])

    def test_two_repos_and_no_repo_line_is_ambiguous(self):
        other = self.root / "api"
        other.mkdir()
        self.git(other, "init", "-b", "main")
        self.config(None, repos={"agentry": {"path": "src"}, "api": {"path": "api"}})
        out = worktree.add(TASK, "feature")
        self.assertFalse(out["ok"])
        self.assertIn("2 candidates", out["error"])
        self.assertFalse((self.root / "src-wt").exists())

    def test_a_repo_line_that_resolves_to_nothing_is_refused(self):
        out = worktree.add(TASK, "feature", "nowhere")
        self.assertFalse(out["ok"])
        self.assertIn("0 candidates", out["error"])

    def test_the_workspace_root_as_the_only_repo_is_refused(self):
        self.config(None, repos={"agentry": {"path": "."}})
        out = worktree.add(TASK, "feature")
        self.assertFalse(out["ok"])
        self.assertIn("workspace root", out["error"])

    def test_only_the_branch_types_the_gates_accept_are_allowed(self):
        accepted = ("feature", "bugfix", "hotfix", "enhancement", "techdebt", "fix", "refactor", "chore")
        for number, ok in enumerate(accepted, start=1):
            with self.subTest(task_type=ok):
                self.assertTrue(worktree.add(f"task-{number:04d}", ok)["ok"])
        for bad in ("epic", "Feature", "docs", "wip"):
            with self.subTest(task_type=bad):
                out = worktree.add("task-0999", bad)
                self.assertFalse(out["ok"])
                self.assertIn("not a branch type", out["error"])
        self.assertFalse((self.root / "src-wt" / "task-0999").exists())

    def test_a_type_that_cannot_name_a_branch_is_refused(self):
        for bad in ("", "../x", "-d", "a b", "x/y", "fe;ature", "1st", None):
            with self.subTest(task_type=bad):
                out = worktree.add(TASK, bad)
                self.assertFalse(out["ok"])
        self.assertFalse((self.root / "src-wt").exists())

    def test_a_bad_task_id_is_refused(self):
        for bad in ("", "task-", "task-1; rm", "../task-0130", "task-0130/x", None):
            with self.subTest(task=bad):
                self.assertFalse(worktree.add(bad, "feature")["ok"])

    def test_a_repo_with_no_commit_is_refused_not_raised(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.git(empty, "init", "-b", "main")
        self.config(None, repos={"agentry": {"path": "empty"}})
        out = worktree.add(TASK, "feature")
        self.assertFalse(out["ok"])
        self.assertIn("cannot resolve the trunk", out["error"])

    def test_the_trunk_ref_is_the_one_git_state_names(self):
        self.git(self.src, "checkout", "-b", "feature/other")
        self.commit(self.src, "elsewhere.txt", "not trunk")
        trunk = self.git(self.src, "rev-parse", "main")
        out = worktree.add(TASK, "feature")
        self.assertEqual(trunk, out["base"])
        self.assertFalse((self.root / "src-wt" / TASK / "elsewhere.txt").exists())

    def test_discard_removes_a_fresh_tree_and_its_empty_branch(self):
        out = worktree.add(TASK, "feature")
        worktree.discard(out)
        self.assertFalse((self.root / "src-wt" / TASK).exists())
        done = subprocess.run(["git", "-C", str(self.src), "rev-parse", "--verify", "--quiet",
                               f"refs/heads/{BRANCH}"], capture_output=True, text=True)
        self.assertNotEqual(0, done.returncode)
        self.assertTrue(worktree.add(TASK, "feature")["ok"])

    def test_discard_keeps_a_branch_that_already_carries_work(self):
        out = worktree.add(TASK, "feature")
        self.commit(self.root / "src-wt" / TASK)
        worktree.discard(out)
        self.assertEqual("work", self.git(self.src, "log", "-1", "--format=%s", BRANCH))

    def test_discard_ignores_a_reused_tree(self):
        worktree.add(TASK, "feature")
        again = worktree.add(TASK, "feature")
        worktree.discard(again)
        self.assertTrue((self.root / "src-wt" / TASK).is_dir())

    def test_a_failed_verification_removes_what_was_made(self):
        real = worktree._git

        def lying(repo, *args, **kwargs):
            if args[:2] == ("rev-parse", "HEAD"):
                return 0, "0" * 40
            return real(repo, *args, **kwargs)

        with unittest.mock.patch.object(worktree, "_git", side_effect=lying):
            out = worktree.add(TASK, "feature")
        self.assertFalse(out["ok"])
        self.assertIn("was removed", out["error"])
        self.assertFalse((self.root / "src-wt" / TASK).exists())


class StatusTest(WorkspaceCase):

    def test_no_run_is_legacy(self):
        self.assertEqual(worktree.STATUS_LEGACY, worktree.status(TASK))
        self.assertEqual("", worktree.recorded(TASK))
        self.assertIsNone(worktree.work_dir(TASK))

    def test_a_run_with_nothing_recorded_is_legacy(self):
        self.run_row()
        self.assertEqual(worktree.STATUS_LEGACY, worktree.status(TASK))
        self.assertIsNone(worktree.work_dir(TASK))

    def test_a_recorded_live_tree_is_ok_and_resolves(self):
        added = self.make_tree()
        self.assertEqual(worktree.STATUS_OK, worktree.status(TASK))
        self.assertEqual(f"src-wt/{TASK}", worktree.recorded(TASK))
        self.assertEqual(self.root / "src-wt" / TASK, worktree.work_dir(TASK))
        self.assertEqual(added["path"], self.row()["worktree"])

    def test_a_deleted_directory_is_missing(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        self.assertEqual(worktree.STATUS_MISSING, worktree.status(TASK))
        self.assertIsNone(worktree.work_dir(TASK))

    def test_a_tree_whose_git_link_dangles_is_missing(self):
        self.make_tree()
        for entry in (self.src / ".git" / "worktrees").iterdir():
            tmproot.rmtree(entry)
        self.assertEqual(worktree.STATUS_MISSING, worktree.status(TASK))

    def test_a_directory_without_a_git_file_is_missing(self):
        self.make_tree()
        (self.root / "src-wt" / TASK / ".git").unlink()
        self.assertEqual(worktree.STATUS_MISSING, worktree.status(TASK))

    def test_a_recorded_path_of_the_wrong_shape_is_invalid(self):
        self.run_row()
        for bad in ("../evil", "src-wt/task-0999", "src/task-0130", "C:/x/task-0130"):
            with self.subTest(recorded=bad):
                self.sql("UPDATE runs SET worktree = ? WHERE task = ?", bad, TASK)
                self.assertEqual(worktree.STATUS_INVALID, worktree.status(TASK))
                self.assertIsNone(worktree.work_dir(TASK))

    def test_an_unreadable_run_store_is_invalid_not_legacy(self):
        with unittest.mock.patch.object(state, "connect",
                                        side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(worktree.STATUS_INVALID, worktree.status(TASK))
            self.assertEqual("", worktree.recorded(TASK))
            self.assertIsNone(worktree.work_dir(TASK))
            for reader in (worktree.live, worktree.slots_used, worktree.orphans):
                with self.assertRaises(worktree.Doubt):
                    reader()
            with self.assertRaises(worktree.Doubt):
                worktree.owner_of(self.root / "src-wt" / TASK / "a.py")
            with self.assertRaises(worktree.Doubt):
                worktree.status(TASK, strict=True)

    def test_status_is_read_without_running_git(self):
        self.make_tree()
        with unittest.mock.patch.object(subprocess, "run", side_effect=AssertionError("git ran")), \
                unittest.mock.patch.object(subprocess, "Popen", side_effect=AssertionError("git ran")):
            self.assertEqual(worktree.STATUS_OK, worktree.status(TASK))
            self.assertIsNotNone(worktree.work_dir(TASK))
            self.assertEqual(TASK, worktree.owner_of(self.root / "src-wt" / TASK / "a.py"))
            self.assertTrue(worktree.is_trunk_checkout(self.src / "a.py"))
            self.assertIn(TASK, worktree.dispatch_line(TASK))
            self.assertEqual(1, worktree.slots_used())
            self.assertEqual([], worktree.orphans())


class OwnerAndTrunkTest(WorkspaceCase):

    def setUp(self):
        super().setUp()
        self.make_tree()
        self.tree = self.root / "src-wt" / TASK

    def test_a_file_inside_the_tree_belongs_to_its_task(self):
        self.assertEqual(TASK, worktree.owner_of(self.tree / "README.md"))
        self.assertEqual(TASK, worktree.owner_of(str(self.tree / "deep" / "er" / "file.py")))
        self.assertEqual(TASK, worktree.owner_of(self.tree))

    def test_a_relative_path_resolves_against_the_cwd(self):
        self.assertEqual(TASK, worktree.owner_of("README.md", cwd=str(self.tree)))
        self.assertEqual(TASK, worktree.owner_of("../task-0130/x.py", cwd=str(self.tree)))
        self.assertIsNone(worktree.owner_of("README.md", cwd=str(self.src)))

    def test_the_trunk_checkout_and_outside_paths_have_no_owner(self):
        self.assertIsNone(worktree.owner_of(self.src / "README.md"))
        self.assertIsNone(worktree.owner_of(self.root / "elsewhere" / "x.py"))
        self.assertIsNone(worktree.owner_of(self.root / "src-wt" / OTHER / "x.py"))

    def test_a_sibling_directory_with_the_same_prefix_is_not_inside(self):
        self.assertIsNone(worktree.owner_of(self.root / "src-wt" / "task-01301" / "x.py"))

    def test_dotdot_escapes_are_folded(self):
        self.assertIsNone(worktree.owner_of(self.tree / ".." / ".." / "src" / "README.md"))
        self.assertEqual(TASK, worktree.owner_of(self.src / ".." / "src-wt" / TASK / "x.py"))

    def test_a_stream_suffix_does_not_hide_the_tree(self):
        self.assertEqual(TASK, worktree.owner_of(f"{self.tree.as_posix()}::$INDEX_ALLOCATION/x"))

    @unittest.skipUnless(os.name == "nt", "case-insensitive file systems only")
    def test_case_does_not_hide_the_tree_on_windows(self):
        self.assertEqual(TASK, worktree.owner_of(str(self.tree).upper() + "\\README.md"))

    def test_an_unrecorded_directory_has_no_owner(self):
        stray = self.root / "src-wt" / "task-0999"
        stray.mkdir()
        self.assertIsNone(worktree.owner_of(stray / "x.py"))

    def test_a_unc_or_empty_path_is_doubt_not_no_one(self):
        for bad in ("", "   ", "//server/share/src-wt/task-0130/x.py"):
            with self.subTest(path=bad), self.assertRaises(worktree.Doubt):
                worktree.owner_of(bad)

    def test_the_trunk_checkout_on_main_is_trunk(self):
        self.assertTrue(worktree.is_trunk_checkout(self.src))
        self.assertTrue(worktree.is_trunk_checkout(self.src / "README.md"))
        self.assertTrue(worktree.is_trunk_checkout(self.src / "new" / "dir" / "f.py"))
        self.assertTrue(worktree.is_trunk_checkout("README.md", cwd=str(self.src)))

    def test_a_task_tree_is_not_the_trunk_checkout(self):
        self.assertFalse(worktree.is_trunk_checkout(self.tree / "README.md"))

    def test_paths_outside_every_repo_are_not_trunk(self):
        self.assertFalse(worktree.is_trunk_checkout(self.root / "docs" / "a.md"))
        self.assertFalse(worktree.is_trunk_checkout(self.root / ".agentry" / "tasks"))

    def test_a_repo_on_a_feature_branch_is_not_trunk(self):
        self.git(self.src, "checkout", "-b", "feature/task-0200")
        self.assertFalse(worktree.is_trunk_checkout(self.src / "README.md"))
        self.git(self.src, "checkout", "main")
        self.assertTrue(worktree.is_trunk_checkout(self.src / "README.md"))

    def test_a_detached_head_is_not_trunk(self):
        self.git(self.src, "checkout", "--detach")
        self.assertFalse(worktree.is_trunk_checkout(self.src / "README.md"))

    def test_the_main_branch_name_comes_from_the_config(self):
        self.git(self.src, "branch", "-m", "main", "trunk")
        self.assertFalse(worktree.is_trunk_checkout(self.src))
        self.config(main_branch="trunk")
        self.assertTrue(worktree.is_trunk_checkout(self.src))

    def test_is_trunk_checkout_does_not_depend_on_the_flag(self):
        self.config("off")
        self.assertTrue(worktree.is_trunk_checkout(self.src))
        self.config(None)
        self.assertTrue(worktree.is_trunk_checkout(self.src))


class LiveSlotsOrphansTest(WorkspaceCase):

    def test_live_lists_in_flight_runs_by_task_id(self):
        self.run_row(OTHER, stage="review", awaiting_human="")
        self.make_tree(TASK, stage="test")
        rows = worktree.live()
        self.assertEqual([TASK, OTHER], [r["task"] for r in rows])
        self.assertEqual({"task": TASK, "stage": "test", "stage_status": "in_progress",
                          "awaiting_human": "", "umbrella": False,
                          "worktree": f"src-wt/{TASK}", "status": "OK"}, rows[0])
        self.assertEqual({"worktree": "", "status": "LEGACY"},
                         {k: rows[1][k] for k in ("worktree", "status")})

    def test_done_paused_and_abandoned_runs_are_not_live(self):
        self.run_row("task-0201", stage="done")
        self.run_row("task-0202", stage="implement", stage_status=state.ST_PAUSED)
        self.run_row("task-0203", stage="done", stage_status=state.ST_ABANDONED)
        self.run_row("task-0204", stage="implement")
        self.assertEqual(["task-0204"], [r["task"] for r in worktree.live()])

    def test_a_missing_tree_shows_in_live_with_its_status(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        self.assertEqual("MISSING", worktree.live()[0]["status"])

    def test_slots_count_trees_not_runs(self):
        self.assertEqual(0, worktree.slots_used())
        self.run_row(OTHER)
        self.assertEqual(0, worktree.slots_used())
        self.make_tree()
        self.assertEqual(1, worktree.slots_used())

    def test_a_parked_and_a_blocked_run_keep_their_slot(self):
        self.make_tree(TASK, stage="ready", awaiting_human="commit")
        self.make_tree(OTHER, stage="test", stage_status=state.ST_BLOCKED)
        self.assertEqual(2, worktree.slots_used())

    def test_a_paused_run_gives_its_slot_up(self):
        self.make_tree(TASK, stage="test", stage_status=state.ST_PAUSED)
        self.assertEqual(0, worktree.slots_used())

    def test_a_done_run_gives_its_slot_up(self):
        self.make_tree(TASK, stage="done")
        self.assertEqual(0, worktree.slots_used())

    def test_an_umbrella_never_takes_a_slot(self):
        self.run_row(TASK)
        self.sql("UPDATE runs SET umbrella = 'true' WHERE task = ?", TASK)
        self.assertTrue(worktree.live()[0]["umbrella"])
        self.assertEqual(0, worktree.slots_used())

    def test_a_directory_without_a_run_is_an_orphan(self):
        (self.root / "src-wt" / "task-0999").mkdir(parents=True)
        self.assertEqual([{"path": "src-wt/task-0999", "task": "task-0999", "why": "no run"}],
                         worktree.orphans())

    def test_the_tree_of_a_done_or_abandoned_run_is_an_orphan(self):
        self.make_tree(TASK, stage="done")
        self.make_tree(OTHER, stage="done", stage_status=state.ST_ABANDONED)
        why = {o["task"]: o["why"] for o in worktree.orphans()}
        self.assertEqual({TASK: "run done", OTHER: "run abandoned"}, why)

    def test_the_tree_of_an_in_flight_or_paused_run_is_not_an_orphan(self):
        self.make_tree(TASK, stage="test")
        self.make_tree(OTHER, stage="test", stage_status=state.ST_PAUSED)
        self.assertEqual([], worktree.orphans())

    def test_orphans_are_sorted_ignore_other_names_and_remove_nothing(self):
        for name in ("task-0900", "task-0100", "notes", "task-x"):
            (self.root / "src-wt" / name).mkdir(parents=True, exist_ok=True)
        (self.root / "src-wt" / "file.txt").write_text("x", encoding="utf-8")
        self.assertEqual(["src-wt/task-0100", "src-wt/task-0900"],
                         [o["path"] for o in worktree.orphans()])
        self.assertTrue((self.root / "src-wt" / "task-0100").is_dir())

    def test_no_wt_directory_means_no_orphans(self):
        self.assertEqual([], worktree.orphans())


class DispatchLineTest(WorkspaceCase):

    def test_a_usable_tree_names_path_branch_and_the_rules(self):
        self.make_tree()
        tree = (self.root / "src-wt" / TASK).as_posix()
        self.assertEqual(
            f"Worktree: {tree} (branch {BRANCH}). Begin every Bash call with "
            f'cd "{tree}", give Edit and Write absolute paths under it, and do not '
            f"touch src/. Make the first line of the dispatch prompt exactly "
            f'"Task worktree: {TASK}" (alone on that line), or the write gate refuses '
            f"this agent's writes.", worktree.dispatch_line(TASK))

    def test_the_line_is_one_line_and_byte_stable(self):
        self.make_tree()
        first = worktree.dispatch_line(TASK)
        self.assertNotIn("\n", first)
        self.assertEqual(first, worktree.dispatch_line(TASK))

    def test_a_missing_or_invalid_tree_says_repair_and_do_not_edit(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        self.assertEqual(
            f"Task worktree: MISSING for {TASK} - run python \"$AGENTRY_ROOT/tools/pipeline/"
            f"worktree.py\" --repair --task {TASK}; do not edit.", worktree.dispatch_line(TASK))
        self.sql("UPDATE runs SET worktree = '../x' WHERE task = ?", TASK)
        self.assertIn("INVALID for", worktree.dispatch_line(TASK))

    def test_a_legacy_run_gets_no_line(self):
        self.run_row()
        self.assertEqual("", worktree.dispatch_line(TASK))
        self.assertEqual("", worktree.dispatch_line("task-0999"))

    def test_the_branch_follows_what_the_tree_has_checked_out(self):
        self.make_tree()
        self.git(self.root / "src-wt" / TASK, "checkout", "-b", "feature/task-0130-renamed")
        self.assertIn("(branch feature/task-0130-renamed)", worktree.dispatch_line(TASK))


class ResumeTextTest(WorkspaceCase):

    def resume(self) -> str:
        conn = state.connect()
        try:
            return state._resume_text(conn)
        finally:
            conn.close()

    def test_a_run_in_a_tree_carries_the_dispatch_line_in_the_resume_text(self):
        self.make_tree(TASK, stage="test")
        line = f"  - {TASK}: stage=test status=in_progress {worktree.dispatch_line(TASK)}"
        self.assertIn(line, self.resume().splitlines())

    def test_a_run_without_a_tree_reads_exactly_as_before(self):
        self.run_row(OTHER, stage="review", awaiting_human="commit")
        line = f"  - {OTHER}: stage=review status=in_progress awaiting_human=commit"
        self.assertIn(line, self.resume().splitlines())

    def test_a_broken_tree_says_repair_in_the_resume_text(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        self.assertIn(f"Task worktree: MISSING for {TASK}", self.resume())

    def test_a_dispatch_line_failure_cannot_break_the_resume_text(self):
        self.run_row()
        with unittest.mock.patch.object(worktree, "dispatch_line", side_effect=RuntimeError("x")):
            self.assertIn(f"  - {TASK}: stage=implement status=in_progress", self.resume())


class RemoveTest(WorkspaceCase):

    def test_a_clean_tree_is_removed_and_the_branch_and_row_survive(self):
        self.make_tree()
        out = worktree.remove(TASK)
        self.assertEqual({"task": TASK, "removed": True, "left": False, "reason": ""}, out)
        self.assertFalse((self.root / "src-wt" / TASK).exists())
        self.assertEqual("", self.git(self.src, "worktree", "prune"))
        self.assertTrue(self.git(self.src, "rev-parse", "--verify", f"refs/heads/{BRANCH}"))
        self.assertEqual(f"src-wt/{TASK}", self.row()["worktree"])

    def test_ignored_files_do_not_block_the_removal(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        (tree / "__pycache__").mkdir()
        (tree / "__pycache__" / "x.pyc").write_bytes(b"x")
        (tree / "tmp" / "scratch.log").write_text("log", encoding="utf-8")
        self.assertTrue(worktree.remove(TASK)["removed"])
        self.assertFalse(tree.exists())

    def test_a_caller_standing_inside_the_tree_is_moved_out_first(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(tree / "tmp")
        out = worktree.remove(TASK)
        self.assertTrue(out["removed"], out)
        self.assertFalse(tree.exists())
        self.assertEqual(str(self.root).lower(), os.getcwd().lower())

    def test_a_dirty_tree_is_left_with_its_work_and_not_retried(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        (tree / "README.md").write_text("edited\n", encoding="utf-8")
        with unittest.mock.patch.object(worktree.time, "sleep") as slept:
            out = worktree.remove(TASK)
        self.assertEqual((False, True), (out["removed"], out["left"]))
        self.assertIn("dirty", out["reason"])
        self.assertIn(f"worktree.py\" --remove --task {TASK}", out["reason"])
        self.assertEqual("edited\n", (tree / "README.md").read_text(encoding="utf-8"))
        slept.assert_not_called()

    def test_an_untracked_file_leaves_the_tree(self):
        self.make_tree()
        (self.root / "src-wt" / TASK / "notes.txt").write_text("n", encoding="utf-8")
        out = worktree.remove(TASK)
        self.assertTrue(out["left"])
        self.assertIn("dirty", out["reason"])

    def test_a_locked_tree_is_left(self):
        self.make_tree()
        self.git(self.src, "worktree", "lock", str(self.root / "src-wt" / TASK))
        out = worktree.remove(TASK)
        self.assertEqual((False, True), (out["removed"], out["left"]))
        self.assertTrue(out["reason"].startswith("locked"), out)
        self.assertIn(f"worktree unlock {(self.root / 'src-wt' / TASK).as_posix()}", out["reason"])
        self.assertIn(f"worktree.py\" --remove --task {TASK}", out["reason"])
        self.assertTrue((self.root / "src-wt" / TASK).is_dir())

    def test_removal_never_uses_force(self):
        self.make_tree()
        calls = []
        real = worktree._git

        def spy(repo, *args, **kwargs):
            calls.append(args)
            return real(repo, *args, **kwargs)

        with unittest.mock.patch.object(worktree, "_git", side_effect=spy):
            worktree.remove(TASK)
        self.assertTrue(calls)
        for args in calls:
            self.assertNotIn("--force", args)
            self.assertNotIn("-f", args)

    def test_a_legacy_run_or_no_run_has_nothing_to_remove(self):
        self.run_row()
        for task in (TASK, "task-0999"):
            self.assertEqual({"task": task, "removed": False, "left": False, "reason": ""},
                             worktree.remove(task))

    def test_a_tree_that_is_already_gone_is_nothing_to_remove_and_gets_pruned(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        out = worktree.remove(TASK)
        self.assertEqual((False, False, ""), (out["removed"], out["left"], out["reason"]))
        self.assertNotIn(TASK, self.git(self.src, "worktree", "list"))

    def test_a_recorded_path_of_the_wrong_shape_is_never_touched(self):
        outside = self.root / "keep"
        outside.mkdir()
        (outside / "f.txt").write_text("keep", encoding="utf-8")
        self.run_row()
        for bad in ("../keep", "keep", "src-wt/task-0999"):
            with self.subTest(recorded=bad):
                self.sql("UPDATE runs SET worktree = ? WHERE task = ?", bad, TASK)
                out = worktree.remove(TASK)
                self.assertTrue(out["left"])
                self.assertIn("not a valid worktree path", out["reason"])
        self.assertTrue((outside / "f.txt").is_file())

    def test_a_process_holding_the_tree_leaves_it_and_reports(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        self.holder(tree)
        with unittest.mock.patch.object(worktree.time, "sleep"):
            out = worktree.remove(TASK)
        self.assertEqual((False, True), (out["removed"], out["left"]))
        self.assertTrue(out["reason"].startswith("in use"), out)
        # git has already unregistered it, so --remove cannot finish the job: say what can.
        self.assertIn(f"rm -rf {tree.as_posix()}", out["reason"])
        self.assertIn("close whatever holds it", out["reason"])

    def test_a_holder_that_lets_go_during_the_call_is_cleaned_up_in_the_same_call(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        proc = self.holder(tree)
        letting_go = []

        def release(_seconds):
            if not letting_go:
                proc.kill()
                proc.wait()
                letting_go.append(True)

        with unittest.mock.patch.object(worktree.time, "sleep", side_effect=release):
            out = worktree.remove(TASK)
        self.assertEqual({"task": TASK, "removed": True, "left": False, "reason": ""}, out)
        self.assertFalse(tree.exists())
        self.assertNotIn(TASK, self.git(self.src, "worktree", "list"))
        self.assertTrue(self.git(self.src, "rev-parse", "--verify", f"refs/heads/{BRANCH}"))

    def test_a_leftover_from_an_earlier_interrupted_removal_is_reported_not_deleted(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        proc = self.holder(tree)
        with unittest.mock.patch.object(worktree.time, "sleep"):
            worktree.remove(TASK)
        proc.kill()
        proc.wait()
        self.assertTrue(tree.exists())
        out = worktree.remove(TASK)
        self.assertTrue(out["left"])
        self.assertIn("not registered", out["reason"])
        self.assertIn(f"rm -rf {tree.as_posix()}", out["reason"])
        self.assertTrue(tree.exists())

    def test_three_tries_one_pause_apart(self):
        self.make_tree()
        self.holder(self.root / "src-wt" / TASK)
        with unittest.mock.patch.object(worktree.time, "sleep") as slept:
            worktree.remove(TASK)
        self.assertEqual(worktree.REMOVE_TRIES - 1, slept.call_count)
        slept.assert_called_with(worktree.REMOVE_PAUSE)
        self.assertEqual((3, 1.0), (worktree.REMOVE_TRIES, worktree.REMOVE_PAUSE))

    def test_remove_never_raises(self):
        self.make_tree()
        with unittest.mock.patch.object(worktree, "_git", side_effect=RuntimeError("boom")):
            out = worktree.remove(TASK)
        self.assertEqual((False, True), (out["removed"], out["left"]))
        self.assertIn("boom", out["reason"])


class RepairTest(WorkspaceCase):

    def test_a_deleted_directory_is_re_added_on_its_branch(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        tmproot.rmtree(tree)
        self.assertEqual("MISSING", worktree.status(TASK))
        out = worktree.repair(TASK)
        self.assertTrue(out["ok"], out)
        self.assertEqual("OK", worktree.status(TASK))
        self.assertEqual(BRANCH, self.git(tree, "rev-parse", "--abbrev-ref", "HEAD"))

    def test_a_moved_workspace_is_re_linked(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        link = tree / ".git"
        # git marks the file hidden on Windows, and a hidden file cannot be rewritten, so it is
        # replaced; a scanner holding it open for a moment (indexer, antivirus) gets a few tries.
        for attempt in range(20):
            try:
                link.unlink()
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)
        link.write_text(f"gitdir: {self.src / '.git' / 'worktrees' / 'nowhere'}\n", encoding="utf-8")
        self.assertEqual("MISSING", worktree.status(TASK))
        out = worktree.repair(TASK)
        self.assertTrue(out["ok"], out)
        self.assertEqual("OK", worktree.status(TASK))

    def test_nothing_recorded_cannot_be_repaired(self):
        self.run_row()
        out = worktree.repair(TASK)
        self.assertFalse(out["ok"])
        self.assertIn("no valid recorded worktree", out["message"])

    def test_a_gone_directory_with_a_deleted_branch_fails_with_a_message(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        self.git(self.src, "worktree", "prune")
        self.git(self.src, "branch", "-D", BRANCH)
        out = worktree.repair(TASK)
        self.assertFalse(out["ok"])
        self.assertIn("MISSING", out["message"])


class CliTest(WorkspaceCase):

    def test_list_reports_flag_slots_and_trees(self):
        self.make_tree()
        code, out = self.cli("--list")
        self.assertEqual(0, code)
        self.assertEqual({"enabled": True, "max_parallel": 2, "trees": [
            {"task": TASK, "path": f"src-wt/{TASK}", "branch": BRANCH, "status": "OK",
             "stage": "implement"}]}, out)

    def test_list_with_nothing_in_flight(self):
        self.config(None)
        self.assertEqual((0, {"enabled": False, "max_parallel": 1, "trees": []}), self.cli("--list"))

    def test_sweep_reports_orphans_and_removes_nothing(self):
        (self.root / "src-wt" / "task-0999").mkdir(parents=True)
        code, out = self.cli("--sweep")
        self.assertEqual(0, code)
        self.assertEqual([{"path": "src-wt/task-0999", "task": "task-0999", "why": "no run"}],
                         out["orphans"])
        self.assertTrue((self.root / "src-wt" / "task-0999").is_dir())

    def test_remove_exits_zero_when_removed_and_one_when_left(self):
        self.make_tree()
        self.assertEqual(0, self.cli("--remove", "--task", TASK)[0])
        self.make_tree(OTHER)
        (self.root / "src-wt" / OTHER / "dirty.txt").write_text("d", encoding="utf-8")
        code, out = self.cli("--remove", "--task", OTHER)
        self.assertEqual(1, code)
        self.assertTrue(out["left"])

    def test_remove_with_nothing_recorded_exits_zero(self):
        self.run_row()
        self.assertEqual(0, self.cli("--remove", "--task", TASK)[0])

    def test_repair_exits_by_outcome(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        code, out = self.cli("--repair", "--task", TASK)
        self.assertEqual((0, True), (code, out["ok"]))
        self.run_row(OTHER)
        self.assertEqual(1, self.cli("--repair", "--task", OTHER)[0])

    def test_a_task_id_that_is_not_one_is_refused(self):
        for bad in ("", "../x", "task-1; rm -rf /"):
            with self.subTest(task=bad):
                self.assertEqual(2, self.cli("--remove", "--task", bad)[0])
        self.assertEqual(2, self.cli("--repair")[0])


class ReposAutoDetectTest(WorkspaceCase):

    def test_a_linked_worktree_is_not_taken_for_a_repo(self):
        self.config(None, repos=None)
        worktree.add(TASK, "feature")
        names = [p.name for p in git_state.repos()]
        self.assertEqual(["src"], names)


def link_dir(link: Path, target: Path) -> None:
    """A directory link `link` -> `target`: a junction on Windows (no privilege needed),
    a symlink elsewhere."""
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise AssertionError(f"mklink /J failed: {done.stdout} {done.stderr}")
    else:
        os.symlink(target, link, target_is_directory=True)


def break_store() -> None:
    """Replace the run store with bytes that are not a database."""
    db = state.DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        part = Path(f"{db}{suffix}")
        if part.exists():
            part.unlink()
    db.write_bytes(b"this is not a sqlite database " * 20)


class DoubtTest(WorkspaceCase):
    """One doubt signal: a reader that cannot tell raises worktree.Doubt, so no caller can
    take "nobody owns it" or "no slot is used" for an answer."""

    def setUp(self):
        super().setUp()
        self.make_tree()
        self.inside = self.root / "src-wt" / TASK / "a.py"

    def test_a_real_broken_store_makes_every_reader_raise(self):
        break_store()
        for reader in (worktree.live, worktree.slots_used, worktree.orphans):
            with self.subTest(reader=reader.__name__), self.assertRaises(worktree.Doubt):
                reader()
        with self.assertRaises(worktree.Doubt):
            worktree.owner_of(self.inside)
        with self.assertRaises(worktree.Doubt):
            worktree.status(TASK, strict=True)
        self.assertEqual(worktree.STATUS_INVALID, worktree.status(TASK))

    def test_a_store_that_fails_once_is_read_again(self):
        calls = []
        real = state.connect

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("busy for a moment")
            return real()

        with unittest.mock.patch.object(state, "connect", side_effect=flaky):
            self.assertEqual(TASK, worktree.owner_of(self.inside))
        self.assertEqual(2, len(calls))

    def test_a_store_that_fails_twice_is_unreadable_after_exactly_two_tries(self):
        with unittest.mock.patch.object(state, "connect",
                                        side_effect=sqlite3.OperationalError("locked")) as tried:
            with self.assertRaises(worktree.Doubt):
                worktree.slots_used()
        self.assertEqual(2, tried.call_count)

    def test_an_in_flight_run_with_an_invalid_path_makes_owner_of_doubt_everywhere(self):
        self.run_row(OTHER)
        self.sql("UPDATE runs SET worktree = ? WHERE task = ?", "../evil", OTHER)
        for path in (self.inside, self.src / "README.md", self.root / "elsewhere" / "x.py"):
            with self.subTest(path=str(path)), self.assertRaises(worktree.Doubt) as raised:
                worktree.owner_of(path)
            self.assertIn(OTHER, str(raised.exception))

    def test_the_run_is_still_listed_and_still_holds_its_slot_when_its_path_is_invalid(self):
        self.sql("UPDATE runs SET worktree = ? WHERE task = ?", "../evil", TASK)
        self.assertEqual("INVALID", worktree.live()[0]["status"])
        self.assertEqual(1, worktree.slots_used())

    def test_a_done_runs_invalid_path_and_leftover_tree_are_not_read(self):
        self.make_tree(OTHER, stage="done")
        self.assertIsNone(worktree.owner_of(self.root / "src-wt" / OTHER / "x.py"))
        self.sql("UPDATE runs SET worktree = ? WHERE task = ?", "../evil", OTHER)
        self.assertEqual(TASK, worktree.owner_of(self.inside))
        self.assertIsNone(worktree.owner_of(self.src / "README.md"))

    def test_a_paused_runs_tree_is_still_its_own(self):
        self.make_tree(OTHER, stage="test", stage_status=state.ST_PAUSED)
        self.assertEqual(OTHER, worktree.owner_of(self.root / "src-wt" / OTHER / "x.py"))

    def test_a_stranded_run_without_a_stage_still_owns_its_tree(self):
        self.sql("UPDATE runs SET stage = NULL WHERE task = ?", TASK)
        self.assertEqual(TASK, worktree.owner_of(self.inside))

    def test_the_repo_list_is_read_once_per_call_not_once_per_run(self):
        self.make_tree(OTHER)
        self.make_tree("task-0132")
        real = git_state.repos
        calls = []

        def spy():
            calls.append(1)
            return real()

        with unittest.mock.patch.object(git_state, "repos", side_effect=spy):
            self.assertEqual(TASK, worktree.owner_of(self.inside))
        self.assertEqual(1, len(calls))
        calls.clear()
        with unittest.mock.patch.object(git_state, "repos", side_effect=spy):
            self.assertEqual(3, len(worktree.live()))
        self.assertEqual(1, len(calls))

    def test_the_cli_says_it_cannot_tell_and_exits_one(self):
        break_store()
        for flag in ("--list", "--sweep"):
            with self.subTest(flag=flag):
                code, out = self.cli(flag)
                self.assertEqual(1, code)
                self.assertIn("cannot tell", out["error"])

    def test_the_listing_does_not_read_the_git_file_of_an_invalid_path(self):
        self.sql("UPDATE runs SET worktree = ? WHERE task = ?", "plugin/tools", TASK)
        code, out = self.cli("--list")
        self.assertEqual(0, code)
        self.assertEqual([{"task": TASK, "path": "plugin/tools", "branch": "", "status": "INVALID",
                           "stage": "implement"}], out["trees"])


class TrustedTreeTest(WorkspaceCase):
    """OK means the recorded path is the real directory: not a link, nothing linked on the
    way. Anything else is somewhere else, and nothing is deleted through it."""

    def test_a_tree_that_is_a_link_is_invalid_and_nothing_is_touched_through_it(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        moved = self.root / "moved-tree"
        tree.rename(moved)
        link_dir(tree, moved)
        self.assertEqual(worktree.STATUS_INVALID, worktree.status(TASK))
        self.assertIsNone(worktree.work_dir(TASK))
        self.assertEqual([], git_state.work_trees_for_task(TASK))
        self.assertEqual("INVALID", worktree.live()[0]["status"])
        self.assertTrue(worktree.dispatch_line(TASK).startswith(f"Task worktree: INVALID for {TASK}"))
        out = worktree.remove(TASK)
        self.assertEqual((False, True), (out["removed"], out["left"]))
        self.assertIn("not a plain directory", out["reason"])
        self.assertFalse(worktree.repair(TASK)["ok"])
        self.assertTrue((moved / ".git").is_file())
        self.assertTrue((moved / "README.md").is_file())

    def test_a_link_on_the_way_to_the_tree_is_invalid_too(self):
        self.make_tree()
        wt = self.root / "src-wt"
        elsewhere = self.root / "elsewhere-wt"
        wt.rename(elsewhere)
        link_dir(wt, elsewhere)
        self.assertEqual(worktree.STATUS_INVALID, worktree.status(TASK))
        out = worktree.remove(TASK)
        self.assertEqual((False, True), (out["removed"], out["left"]))
        self.assertTrue((elsewhere / TASK / ".git").is_file())
        self.assertTrue((elsewhere / TASK / "README.md").is_file())

    def test_a_link_swapped_in_during_the_removal_is_not_deleted_through(self):
        """The check is repeated right before the direct delete, not only at the start."""
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        holder = self.holder(tree)
        moved = self.root / "moved-tree"
        swapped = []

        def swap(_seconds):
            if not swapped:
                holder.kill()
                holder.wait()
                tree.rename(moved)
                link_dir(tree, moved)
                (moved / "precious.txt").write_text("keep", encoding="utf-8")
                swapped.append(True)

        with unittest.mock.patch.object(worktree.time, "sleep", side_effect=swap):
            out = worktree.remove(TASK)
        self.assertTrue(out["left"], out)
        self.assertIn("stopped being a plain directory", out["reason"])
        self.assertEqual("keep", (moved / "precious.txt").read_text(encoding="utf-8"))


class DiscardTest(WorkspaceCase):

    def test_a_fresh_tree_nobody_recorded_is_dropped_with_its_branch(self):
        added = worktree.add(TASK, "feature")
        worktree.discard(added)
        self.assertFalse((self.root / "src-wt" / TASK).exists())
        self.assertEqual("", self.git(self.src, "branch", "--list", BRANCH))

    def test_a_tree_another_registration_recorded_is_not_dropped(self):
        added = worktree.add(TASK, "feature")
        self.run_row(TASK, rel=added["path"])
        worktree.discard(added)
        self.assertTrue((self.root / "src-wt" / TASK / ".git").is_file())
        self.assertEqual(BRANCH, self.git(self.src, "branch", "--list", BRANCH).lstrip("+* ").strip())
        self.assertEqual("OK", worktree.status(TASK))

    def test_an_unreadable_store_proves_nothing_so_the_tree_stays(self):
        added = worktree.add(TASK, "feature")
        break_store()
        worktree.discard(added)
        self.assertTrue((self.root / "src-wt" / TASK / ".git").is_file())

    def test_a_reused_tree_is_never_discarded(self):
        worktree.add(TASK, "feature")
        again = worktree.add(TASK, "feature")
        self.assertFalse(again["created"])
        worktree.discard(again)
        self.assertTrue((self.root / "src-wt" / TASK / ".git").is_file())


class NoBarePruneTest(WorkspaceCase):
    """`git worktree prune` drops the registration of EVERY tree whose directory is missing
    at that moment - one on an unplugged drive, one being moved. Only the task's own stale
    entry may go."""

    def two_gone(self) -> None:
        self.make_tree(TASK)
        self.make_tree(OTHER)
        tmproot.rmtree(self.root / "src-wt" / TASK)
        tmproot.rmtree(self.root / "src-wt" / OTHER)

    def admin(self, task: str) -> Path:
        return self.src / ".git" / "worktrees" / task

    def spy(self):
        calls = []
        real = worktree._git

        def record(repo, *args, **kwargs):
            calls.append(args)
            return real(repo, *args, **kwargs)

        return calls, unittest.mock.patch.object(worktree, "_git", side_effect=record)

    def test_removing_a_gone_tree_drops_its_entry_and_only_its_entry(self):
        self.two_gone()
        calls, patched = self.spy()
        with patched:
            out = worktree.remove(TASK)
        self.assertEqual((False, False, ""), (out["removed"], out["left"], out["reason"]))
        self.assertFalse(self.admin(TASK).exists())
        self.assertTrue(self.admin(OTHER).is_dir(), "a sibling's registration was dropped")
        self.assertNotIn(("worktree", "prune"), [c[:2] for c in calls])

    def test_repairing_a_gone_tree_keeps_a_siblings_entry(self):
        self.two_gone()
        calls, patched = self.spy()
        with patched:
            out = worktree.repair(TASK)
        self.assertTrue(out["ok"], out)
        self.assertTrue(self.admin(OTHER).is_dir(), "a sibling's registration was dropped")
        self.assertNotIn(("worktree", "prune"), [c[:2] for c in calls])

    def test_discarding_a_fresh_tree_issues_no_prune(self):
        added = worktree.add(TASK, "feature")
        calls, patched = self.spy()
        with patched:
            worktree.discard(added)
        self.assertNotIn(("worktree", "prune"), [c[:2] for c in calls])

    def test_a_cleanup_that_succeeds_issues_no_prune(self):
        self.make_tree()
        calls, patched = self.spy()
        with patched:
            self.assertTrue(worktree.remove(TASK)["removed"])
        self.assertNotIn(("worktree", "prune"), [c[:2] for c in calls])


class TaskIdAsciiTest(WorkspaceCase):

    def test_only_ascii_digits_make_a_task_id(self):
        arabic = "task-" + "".join(chr(0x660 + n) for n in range(4))  # Arabic-indic digits
        self.assertTrue(worktree.TASK_RE.fullmatch("task-0130"))
        self.assertIsNone(worktree.TASK_RE.fullmatch(arabic))
        self.assertFalse(worktree.valid_path(f"src-wt/{arabic}"))
        out = worktree.add(arabic, "feature")
        self.assertFalse(out["ok"])
        self.assertFalse((self.root / "src-wt").exists())
        self.assertEqual(2, self.cli("--remove", "--task", arabic)[0])


class SpacedRootTest(WorkspaceCase):
    """A workspace path with a space: the manual commands a refusal prints must still run
    when pasted into a shell."""

    def setUp(self):
        real = tmproot.sandbox
        with unittest.mock.patch.object(tmproot, "sandbox",
                                        lambda case, prefix: real(case, "wt spaced_")):
            super().setUp()

    def test_the_printed_manual_commands_survive_the_space(self):
        self.assertIn(" ", str(self.root))
        self.git(self.src, "branch", BRANCH)
        out = worktree.add(TASK, "feature")
        self.assertFalse(out["ok"])
        argv = shlex.split(out["manual"])
        self.assertEqual("git", argv[0])
        self.assertIn((self.root / "src-wt" / TASK).as_posix(), argv)
        done = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(worktree.add(TASK, "feature")["ok"])

    def test_a_gone_tree_manual_names_only_that_tree(self):
        worktree.add(TASK, "feature")
        tmproot.rmtree(self.root / "src-wt" / TASK)
        out = worktree.add(TASK, "feature")
        self.assertIn("worktree remove", out["manual"])
        self.assertNotIn("prune", out["manual"])
        subprocess.run(shlex.split(out["manual"]), check=True, capture_output=True)
        self.assertEqual(1, len(self.git(self.src, "worktree", "list").splitlines()))


if __name__ == "__main__":
    unittest.main()
