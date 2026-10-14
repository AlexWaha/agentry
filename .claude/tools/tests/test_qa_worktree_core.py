"""epic-0004 / task-0129 QA: adversarial probes of the core zone.

The dev modules (test_worktree_core, _registration, _gate_cwd, _merge) pin the contract.
This one attacks it, with real git repositories and real worktrees under the project's
tmp/ and no stand-ins for git:

  * a run row somebody tampered with (a path into .claude/tools, `..`, a drive, UNC, a
    sibling task's tree) must read INVALID everywhere and leave every target untouched;
  * an unreadable run store is INVALID, never LEGACY, in every reader that has to choose;
  * what `worktree.remove` does to files it does not own (a junction inside the tree);
  * spellings of a path inside a tree that the freezes depend on `owner_of` and
    `is_trunk_checkout` to fold;
  * registration around crashes: before the insert, after the insert, a real database
    refusal, a recovery by the manual command a refusal prints, racing callers;
  * the `worktree` column has no writer but create_run, and a task file cannot plant one;
  * the CLI as a real child process, and every module imported cold (import cycles).
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import ast
import contextlib
import ctypes
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
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
from test_worktree_gate_cwd import PY, StackGateCase, pipeline_with
from test_worktree_registration import PIPELINES, RegistrationCase

WINDOWS = os.name == "nt"
KEEP = "keep.txt"
FRESH = "task-0140"

TAMPERED = (
    ".claude/tools",
    "../outside-wt/task-0130",
    "src-wt/../.claude/tools",
    "src-wt/../../x",
    "src-wt/task-0130/..",
    "src-wt/task-0130/sub",
    "src-wt/task-0130/",
    "src-wt/./task-0130",
    "src-wt//task-0130",
    "/src-wt/task-0130",
    "C:/src-wt/task-0130",
    "C:/Windows/System32",
    "//server/share/src-wt/task-0130",
    "\\\\server\\share\\src-wt\\task-0130",
    "src-wt\\task-0130",
    "src-wt/task-0130:stream",
    "src-wt/task-0130 ",
    "src-wt/task-0131",
    "SRC-WT/TASK-0130",
    "src-wt",
    "-wt/task-0130",
    "tools-wt/task-0130",
    ".claude-wt/task-0130",
)


def junction(link: Path, target: Path) -> None:
    """A Windows directory junction `link` -> `target` (no privilege needed)."""
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"mklink /J failed: {done.stdout} {done.stderr}")


def short_path(path: Path) -> str:
    """The 8.3 spelling of an existing path."""
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)  # type: ignore[attr-defined]
    return buf.value


class TamperedRunRowTest(WorkspaceCase):
    """The run row is a database an agent's tooling can reach; its `worktree` value is
    only ever trusted after valid_path. Every reader must refuse a value that is not
    `<repo path>-wt/<this task>` and touch nothing it names."""

    def setUp(self):
        super().setUp()
        self.make_tree()
        self.real = self.root / "src-wt" / TASK
        self.sentinel_dir = self.src / ".claude" / "tools"
        self.sentinel_dir.mkdir(parents=True)
        (self.sentinel_dir / KEEP).write_text("keep", encoding="utf-8")
        self.marker = self.root / "ran.marker"

    def tamper(self, bad: str) -> None:
        self.sql("UPDATE runs SET worktree = ? WHERE task = ?", bad, TASK)

    def test_every_reader_calls_a_tampered_path_invalid_and_blocks(self):
        marker_cmd = f'{PY} -c "open(\'{self.marker.as_posix()}\', \'w\').write(\'x\')"'
        for bad in TAMPERED:
            with self.subTest(recorded=bad):
                self.tamper(bad)
                self.assertEqual(worktree.STATUS_INVALID, worktree.status(TASK))
                self.assertIsNone(worktree.work_dir(TASK))
                self.assertEqual(bad, worktree.recorded(TASK))
                self.assertFalse(worktree.valid_path(bad, TASK))
                self.assertEqual([], git_state.work_trees_for_task(TASK))
                self.assertTrue(worktree.dispatch_line(TASK).startswith(
                    f"Task worktree: INVALID for {TASK} - "))
                self.assertEqual(worktree.STATUS_INVALID, worktree.live()[0]["status"])
                result = gate.run_gate(pipeline_with(marker_cmd), "test", TASK)
                self.assertEqual((False, False, None), (result["configured"], result["passed"],
                                                        result["exit"]))
                self.assertTrue(result["output"].startswith("task worktree INVALID: "), result)
                self.assertFalse(self.marker.exists(), "a gate command ran")

    def test_a_tampered_row_owns_no_path_and_every_question_about_a_path_is_doubt(self):
        """Not even the path the row names is handed to its task, and no path is declared
        free either: with a run in flight whose tree cannot be located, "nobody" is not
        an answer the freezes may act on."""
        for bad in TAMPERED:
            with self.subTest(recorded=bad):
                self.tamper(bad)
                for path in (self.sentinel_dir / KEEP, self.real / "README.md", self.src / "README.md"):
                    with self.assertRaises(worktree.Doubt):
                        worktree.owner_of(path)

    def test_remove_and_repair_never_touch_what_a_tampered_row_names(self):
        for bad in TAMPERED:
            with self.subTest(recorded=bad):
                self.tamper(bad)
                removed = worktree.remove(TASK)
                self.assertEqual((False, True), (removed["removed"], removed["left"]), removed)
                self.assertFalse(worktree.repair(TASK)["ok"])
                self.assertTrue((self.sentinel_dir / KEEP).is_file())
                self.assertTrue((self.real / ".git").is_file(), "the real tree was touched")

    def test_create_run_refuses_every_tampered_value_and_inserts_nothing(self):
        for bad in TAMPERED:
            with self.subTest(recorded=bad):
                conn = state.connect()
                try:
                    with self.assertRaises(ValueError):
                        state.create_run(conn, FRESH, "feature", "implement", worktree=bad)
                finally:
                    conn.close()
                self.assertIsNone(self.row(FRESH))

    def test_the_sweep_ignores_a_tampered_row_instead_of_listing_its_target(self):
        self.tamper(".claude/tools")
        self.assertEqual([{"path": f"src-wt/{TASK}", "task": TASK, "why": "not recorded by its run"}],
                         worktree.orphans())


class UnreadableStoreTest(StackGateCase):
    """A run store that cannot be read is INVALID, never LEGACY: reading it as 'no tree
    recorded' would send every gate to the trunk checkout."""

    def setUp(self):
        super().setUp()
        self.make_tree()
        db = state.DB_PATH
        for suffix in ("", "-wal", "-shm", "-journal"):
            part = Path(f"{db}{suffix}")
            if part.exists():
                part.unlink()
        db.write_bytes(b"this is not a sqlite database " * 20)
        self.marker = self.root / "ran.marker"

    def test_the_status_is_invalid_and_every_chooser_fails_closed(self):
        self.assertEqual(worktree.STATUS_INVALID, worktree.status(TASK))
        self.assertEqual("", worktree.recorded(TASK))
        self.assertIsNone(worktree.work_dir(TASK))
        self.assertEqual([], git_state.work_trees_for_task(TASK))
        self.assertTrue(worktree.dispatch_line(TASK).startswith(f"Task worktree: INVALID for {TASK}"))
        with self.assertRaises(worktree.Doubt):
            worktree.owner_of(self.root / "src-wt" / TASK / "README.md")

    def test_the_exit_gate_runs_nothing_and_fails_for_a_retry(self):
        """The store, not the tree, is what could not be read: a failed gate (configured
        True) so advance.py retries, not the BLOCKED a MISSING or INVALID tree earns."""
        marker_cmd = f'{PY} -c "open(\'{self.marker.as_posix()}\', \'w\').write(\'x\')"'
        result = gate.run_gate(pipeline_with(marker_cmd), "test", TASK)
        self.assertEqual((True, False, None), (result["configured"], result["passed"], result["exit"]))
        self.assertIn("unreadable", result["output"])
        self.assertFalse(self.marker.exists())

    def test_the_stack_gate_runs_nothing(self):
        code, printed, calls = self.gate()
        self.assertEqual(1, code)
        self.assertEqual("agentry: test gate FAILED. task worktree INVALID; nothing was run.\n", printed)
        self.assertEqual([], calls)

    def test_nothing_is_removed_for_a_task_whose_record_cannot_be_read_and_it_says_so(self):
        out = worktree.remove(TASK)
        self.assertEqual((False, True), (out["removed"], out["left"]))
        self.assertIn("run store cannot be read", out["reason"])
        self.assertIn(f"worktree.py --remove --task {TASK}", out["reason"])
        self.assertTrue((self.root / "src-wt" / TASK / ".git").is_file())

    def test_readers_that_count_raise_instead_of_answering_empty(self):
        """[] and 0 would read as "nothing in flight, every slot free"."""
        for reader in (worktree.live, worktree.slots_used, worktree.orphans):
            with self.subTest(reader=reader.__name__), self.assertRaises(worktree.Doubt):
                reader()


class RemoveSafetyTest(WorkspaceCase):

    @unittest.skipUnless(WINDOWS, "a directory junction is a Windows feature")
    def test_a_junction_inside_an_ignored_directory_never_costs_its_target_its_files(self):
        """Measured, git 2.51 for Windows: `git worktree remove` of a CLEAN tree follows a
        junction that sits in an ignored directory (a pnpm node_modules, a mklink /J
        shortcut) and deletes the files of the directory it points at."""
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        outside = self.root / "outside"
        outside.mkdir()
        (outside / KEEP).write_text("keep", encoding="utf-8")
        junction(tree / "tmp" / "link", outside)
        out = worktree.remove(TASK)
        self.assertTrue((outside / KEEP).is_file(), f"remove() deleted the junction's target: {out}")

    def test_a_leftover_found_by_a_later_call_keeps_whatever_was_put_in_it(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        holder = self.holder(tree)
        with unittest.mock.patch.object(worktree.time, "sleep"):
            self.assertTrue(worktree.remove(TASK)["left"])
        holder.kill()
        holder.wait()
        tree.mkdir(exist_ok=True)
        (tree / "precious.txt").write_text("unregistered edit", encoding="utf-8")
        out = worktree.remove(TASK)
        self.assertTrue(out["left"], out)
        self.assertEqual("unregistered edit", (tree / "precious.txt").read_text(encoding="utf-8"))

    def test_removing_one_task_leaves_a_sibling_tree_and_the_trunk_alone(self):
        self.make_tree()
        self.make_tree(OTHER)
        self.assertTrue(worktree.remove(TASK)["removed"])
        sibling = self.root / "src-wt" / OTHER
        self.assertTrue((sibling / ".git").is_file())
        self.assertEqual("OK", worktree.status(OTHER))
        self.assertTrue((self.src / "README.md").is_file())
        self.assertIn(OTHER, self.git(self.src, "worktree", "list"))

    def test_a_tree_with_committed_work_loses_nothing_but_its_directory(self):
        self.make_tree()
        tree = self.root / "src-wt" / TASK
        self.commit(tree, "work.txt", "committed work")
        tip = self.git(self.src, "rev-parse", BRANCH)
        self.assertTrue(worktree.remove(TASK)["removed"])
        self.assertEqual(tip, self.git(self.src, "rev-parse", BRANCH))
        self.assertEqual("committed work", self.git(self.src, "show", f"{BRANCH}:work.txt"))


class PathSpellingTest(WorkspaceCase):
    """Every freeze asks owner_of / is_trunk_checkout where a path really lands, so the
    spellings Windows folds must fold here too."""

    def setUp(self):
        super().setUp()
        self.make_tree()
        self.make_tree(OTHER)
        self.tree = self.root / "src-wt" / TASK
        self.drive = self.root.as_posix()

    def test_spellings_that_land_in_the_tree_belong_to_it(self):
        spellings = {
            "dot segment": f"{self.drive}/src-wt/{TASK}/./new.py",
            "dotdot through a sibling": f"{self.drive}/src-wt/{OTHER}/../{TASK}/new.py",
            "trailing slash": f"{self.drive}/src-wt/{TASK}/",
            "stream suffix": f"{self.drive}/src-wt/{TASK}/new.py::$DATA",
        }
        if WINDOWS:
            spellings.update({
                "upper case": f"{self.drive}/SRC-WT/{TASK.upper()}/new.py",
                "backslashes": str(self.tree) + "\\new.py",
                "trailing dot on the directory": f"{self.drive}/src-wt/{TASK}./new.py",
                "8.3 short name": short_path(self.tree).replace("\\", "/") + "/new.py",
                "msys drive": "/" + self.drive[0].lower() + self.drive[2:] + f"/src-wt/{TASK}/new.py",
            })
        for label, path in spellings.items():
            with self.subTest(spelling=label):
                self.assertEqual(TASK, worktree.owner_of(path))

    def test_a_sibling_with_the_same_prefix_is_not_the_tree(self):
        (self.root / "src-wt" / f"{TASK}0").mkdir()
        self.assertIsNone(worktree.owner_of(self.root / "src-wt" / f"{TASK}0" / "x.py"))

    def test_a_relative_path_resolves_against_the_cwd_given_not_the_process(self):
        self.assertEqual(OTHER, worktree.owner_of("new.py", cwd=str(self.root / "src-wt" / OTHER)))
        self.assertIsNone(worktree.owner_of("new.py", cwd=str(self.src)))

    @unittest.skipUnless(WINDOWS, "a directory junction is a Windows feature")
    def test_a_path_through_a_junction_belongs_to_where_it_lands(self):
        """An editable tree must not be a doorway into a frozen one."""
        junction(self.root / "src-wt" / OTHER / "tmp" / "door", self.tree)
        landing = self.root / "src-wt" / OTHER / "tmp" / "door" / "new.py"
        self.assertEqual(TASK, worktree.owner_of(landing))

    @unittest.skipUnless(WINDOWS, "a directory junction is a Windows feature")
    def test_a_junction_from_outside_into_the_trunk_is_still_the_trunk(self):
        junction(self.root / "alias", self.src)
        self.assertTrue(worktree.is_trunk_checkout(self.root / "alias" / "new.py"))

    @unittest.skipUnless(WINDOWS, "extended-length paths are a Windows feature")
    def test_extended_length_spellings_still_land_in_the_tree(self):
        """`\\\\?\\E:\\...` addresses the same directory with no normalisation, so a file
        written through it lands in the tree. owner_of read it as UNC ('any doubt') and
        answered None, which the review freeze reads as 'not this task's tree'."""
        win = str(self.tree)
        for label, path in {"win32 file namespace": "\\\\?\\" + win + "\\new.py",
                            "win32 device namespace": "\\\\.\\" + win + "\\new.py",
                            "forward slashes": "//?/" + self.tree.as_posix() + "/new.py"}.items():
            with self.subTest(spelling=label):
                self.assertEqual(TASK, worktree.owner_of(path))

    @unittest.skipUnless(WINDOWS, "extended-length paths are a Windows feature")
    def test_extended_length_spellings_still_land_in_the_trunk(self):
        for label, path in {"win32 file namespace": "\\\\?\\" + str(self.src) + "\\new.py",
                            "forward slashes": "//?/" + self.src.as_posix() + "/new.py"}.items():
            with self.subTest(spelling=label):
                self.assertTrue(worktree.is_trunk_checkout(path))

    def test_a_real_network_path_stays_in_doubt(self):
        for path in ("//server/share/src-wt/task-0130/x.py", "\\\\?\\UNC\\server\\share\\x.py"):
            with self.subTest(path=path):
                with self.assertRaises(worktree.Doubt):
                    worktree.owner_of(path)
                self.assertFalse(worktree.is_trunk_checkout(path))


class LinkedWorktreeDetectTest(WorkspaceCase):
    """Auto-detect lists a directory only when its `.git` is a directory, at the levels it
    looks (a child, a grandchild; the root is covered in test_worktree_gate_cwd)."""

    def test_a_linked_worktree_is_never_listed_whether_a_child_or_a_grandchild(self):
        self.config_path.write_text(json.dumps({"main_branch": "main"}), encoding="utf-8")
        self.git(self.src, "worktree", "add", "-b", "as-child", str(self.root / "linked"))
        (self.root / "group").mkdir()
        self.git(self.src, "worktree", "add", "-b", "as-grandchild", str(self.root / "group" / "linked"))
        self.assertTrue((self.root / "linked" / ".git").is_file())
        self.assertTrue((self.root / "group" / "linked" / ".git").is_file())
        self.assertEqual([self.src], git_state.repos())


class ReadersStartNoProcessTest(WorkspaceCase):
    """Hooks call the readers under a time budget, so none of them may start git."""

    def test_no_reader_spawns_a_process_for_a_live_a_missing_or_a_legacy_run(self):
        self.make_tree()
        self.make_tree(OTHER)
        tmproot.rmtree(self.root / "src-wt" / OTHER)
        self.run_row("task-0132")
        spawn = unittest.mock.patch("subprocess.Popen", side_effect=AssertionError("a process was started"))
        with spawn:
            for task in (TASK, OTHER, "task-0132", "task-0199"):
                worktree.status(task)
                worktree.recorded(task)
                worktree.work_dir(task)
                worktree.dispatch_line(task)
                git_state.work_trees_for_task(task)
            worktree.enabled()
            worktree.max_parallel()
            worktree.root_dir()
            worktree.valid_path(f"src-wt/{TASK}")
            worktree.owner_of(self.root / "src-wt" / TASK / "x.py")
            worktree.is_trunk_checkout(self.src / "x.py")
            worktree.live()
            worktree.slots_used()
            worktree.orphans()
            worktree.stale_message(3)
            git_state.repos()


class RegistrationProbeTest(RegistrationCase):

    def refused_leaves_nothing(self, out: dict) -> None:
        self.assertEqual("refused", out["action"], out)
        self.assertIsNone(self.row())
        self.assertEqual("backlog", state.task_dir(TASK))
        self.assertFalse((self.root / "src-wt").exists())
        self.assertFalse(self.branch_exists())

    def test_a_type_the_gates_do_not_know_is_refused_and_creates_nothing(self):
        for bad in ("docs", "epic", "Feature", "", "feature/x", "feature;rm", "-b", "feature "):
            with self.subTest(type=bad):
                self.task_file()
                self.refused_leaves_nothing(self.advance(TASK, f"--type={bad}"))

    def test_every_branch_type_the_gates_accept_gets_a_tree(self):
        for kind in pretool_types():
            with self.subTest(type=kind):
                task = f"task-01{pretool_types().index(kind) + 40}"
                self.task_file(task)
                out = self.advance(task, "--type", kind)
                self.assertEqual("started", out["action"], out)
                branch = self.git(self.tree(task), "rev-parse", "--abbrev-ref", "HEAD")
                self.assertEqual(f"{kind}/{task}", branch)

    def test_a_bugfix_that_skips_the_plan_still_starts_in_its_tree(self):
        self.config("on", **PIPELINES)
        self.task_file()
        out = self.advance(TASK, "--type", "bugfix")
        self.assertEqual("started", out["action"])
        self.assertEqual(f"src-wt/{TASK}", self.row()["worktree"])
        self.assertEqual(f"bugfix/{TASK}", self.git(self.tree(), "rev-parse", "--abbrev-ref", "HEAD"))

    def test_an_upper_case_umbrella_flag_still_exempts_the_task(self):
        self.task_file(head=f"epic: epic-0004\numbrella: TRUE\nsubtasks: [{OTHER}]")
        out = self.advance()
        self.assertEqual("started", out["action"])
        self.assertEqual(("", "true"), (self.row()["worktree"], self.row()["umbrella"]))
        self.assertFalse((self.root / "src-wt").exists())

    def test_a_declared_repo_that_is_the_workspace_itself_is_refused(self):
        for declared in (".", "./"):
            with self.subTest(repo=declared):
                self.task_file(head=f"repo: {declared}")
                out = self.advance()
                self.assertEqual("refused", out["action"], out)
                self.assertIsNone(self.row())
                self.assertFalse((self.root / "src-wt").exists())

    def test_a_planted_worktree_line_in_the_task_file_is_never_read(self):
        for mode in ("off", "on"):
            with self.subTest(flag=mode):
                self.config(mode, **PIPELINES)
                task = "task-0150" if mode == "off" else "task-0151"
                self.task_file(task, head="worktree: .claude/tools")
                self.assertEqual("started", self.advance(task)["action"])
                recorded = self.row(task)["worktree"]
                self.assertEqual("" if mode == "off" else f"src-wt/{task}", recorded)

    def test_a_crash_between_the_add_and_the_insert_registers_on_the_next_run(self):
        pre = worktree.add(TASK, "feature")
        self.commit(self.tree(), "half.txt", "work done before the crash")
        head = self.git(self.tree(), "rev-parse", "HEAD")
        self.task_file()
        out = self.advance()
        self.assertEqual("started", out["action"], out)
        self.assertEqual(pre["path"], self.row()["worktree"])
        self.assertEqual(head, self.git(self.tree(), "rev-parse", "HEAD"))
        self.assertTrue((self.tree() / "half.txt").is_file())
        self.assertEqual([{"type": "feature", "worktree": pre["path"]}], self.journal())
        self.assertTrue(out["message"].endswith(worktree.dispatch_line(TASK)))
        self.assertEqual(2, len(self.git(self.src, "worktree", "list").splitlines()))

    def test_the_manual_command_a_refusal_prints_is_one_that_works(self):
        self.git(self.src, "branch", BRANCH)
        self.task_file()
        out = self.advance()
        self.assertEqual("refused", out["action"])
        manual = out["message"].split("Run by hand: ", 1)[1]
        done = subprocess.run(shlex.split(manual), capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("started", self.advance()["action"])
        self.assertEqual(f"src-wt/{TASK}", self.row()["worktree"])

    def test_the_remove_command_then_the_add_command_recover_a_hand_deleted_tree(self):
        worktree.add(TASK, "feature")
        tmproot.rmtree(self.tree())
        self.task_file()
        first = self.advance()
        self.assertIn("worktree remove", first["message"])
        self.assertNotIn("prune", first["message"])
        subprocess.run(shlex.split(first["message"].split("Run by hand: ", 1)[1]), check=True,
                       capture_output=True)
        second = self.advance()
        self.assertEqual("refused", second["action"])
        subprocess.run(shlex.split(second["message"].split("Run by hand: ", 1)[1]), check=True,
                       capture_output=True)
        self.assertEqual("started", self.advance()["action"])

    def test_a_refusal_by_the_database_itself_removes_the_tree_and_the_branch(self):
        self.task_file()
        conn = state.connect()
        conn.execute("CREATE TRIGGER refuse_runs BEFORE INSERT ON runs "
                     "BEGIN SELECT RAISE(ABORT, 'store is closed'); END")
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.DatabaseError):
            self.advance()
        self.assertFalse(self.tree().exists())
        self.assertFalse(self.branch_exists())
        self.assertEqual("backlog", state.task_dir(TASK))
        self.assertEqual(1, len(self.git(self.src, "worktree", "list").splitlines()))

    def test_a_crash_after_the_insert_keeps_the_tree_and_the_next_run_goes_on(self):
        self.task_file()
        with unittest.mock.patch.object(state, "move_task", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.advance()
        self.assertEqual(f"src-wt/{TASK}", self.row()["worktree"])
        self.assertTrue((self.tree() / ".git").is_file())
        again = self.advance()
        self.assertNotEqual("started", again["action"], again)
        self.assertEqual(f"src-wt/{TASK}", self.row()["worktree"])
        self.assertEqual(2, len(self.git(self.src, "worktree", "list").splitlines()))

    def test_two_tasks_added_at_once_get_two_trees(self):
        results: dict[str, dict] = {}

        def add(task: str) -> None:
            results[task] = worktree.add(task, "feature")

        threads = [threading.Thread(target=add, args=(t,)) for t in (TASK, OTHER)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([True, True], [results[t]["ok"] for t in (TASK, OTHER)], results)
        self.assertEqual(3, len(self.git(self.src, "worktree", "list").splitlines()))

    def test_one_task_added_by_three_callers_at_once_gets_exactly_one_tree(self):
        results: list[dict] = []

        def add() -> None:
            results.append(worktree.add(TASK, "feature"))

        threads = [threading.Thread(target=add) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(3, len(results))
        self.assertEqual(1, len([r for r in results if r["ok"] and r["created"]]), results)
        self.assertTrue((self.tree() / ".git").is_file())
        self.assertEqual(2, len(self.git(self.src, "worktree", "list").splitlines()))


def pretool_types() -> list[str]:
    import pretool_gate
    return list(pretool_gate.BRANCH_TYPES)


class FrozenColumnTest(WorkspaceCase):

    def tool_sources(self) -> list[Path]:
        return [p for p in sorted(TESTS_DIR.parent.rglob("*.py"))
                if "tests" not in p.relative_to(TESTS_DIR.parent).parts]

    def test_the_column_is_not_a_frozen_field_and_never_comes_from_the_task_file(self):
        self.assertNotIn("worktree", state.FROZEN_FIELDS)
        (self.dirs["backlog"] / f"{TASK}.md").write_text(
            "---\nid: 1\nworktree: src-wt/task-0130\n---\n", encoding="utf-8")
        self.assertNotIn("worktree", state.frozen_frontmatter(TASK))

    def test_no_script_writes_the_column_except_create_run(self):
        for path in self.tool_sources():
            source = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                for node in ast.walk(ast.parse(source)):
                    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "set_fields":
                        self.assertNotIn("worktree", [k.arg for k in node.keywords], path.name)
                self.assertIsNone(re.search(r"UPDATE\s+runs\s+SET[^\"']*worktree", source), path.name)
        inserts = [p.name for p in self.tool_sources()
                   if "INSERT INTO runs" in p.read_text(encoding="utf-8")]
        self.assertEqual(["state.py"], inserts)


class CliProcessTest(WorkspaceCase):
    """The CLI as a real child process, run from a copy of the scripts inside the
    sandbox workspace (state.ROOT follows the script's location)."""

    def setUp(self):
        super().setUp()
        self.scripts = self.root / ".claude" / "tools" / "pipeline"
        shutil.copytree(PIPELINE_DIR, self.scripts, ignore=shutil.ignore_patterns("__pycache__"))

    def run_cli(self, *argv: str) -> tuple[int, dict | str, str]:
        done = subprocess.run([sys.executable, str(self.scripts / "worktree.py"), *argv],
                              capture_output=True, text=True, cwd=str(self.root), timeout=120)
        try:
            return done.returncode, json.loads(done.stdout), done.stderr
        except ValueError:
            return done.returncode, done.stdout, done.stderr

    def test_list_reports_the_flag_the_slots_and_a_live_tree(self):
        code, doc, _ = self.run_cli("--list")
        self.assertEqual((0, {"enabled": True, "max_parallel": 2, "trees": []}), (code, doc))
        self.make_tree()
        code, doc, _ = self.run_cli("--list")
        self.assertEqual(0, code)
        self.assertEqual([{"task": TASK, "path": f"src-wt/{TASK}", "branch": BRANCH, "status": "OK",
                           "stage": "implement"}], doc["trees"])

    def test_sweep_reports_an_orphan_and_removes_nothing(self):
        self.assertTrue(worktree.add(TASK, "feature")["ok"])
        code, doc, _ = self.run_cli("--sweep")
        self.assertEqual(0, code)
        self.assertEqual([{"path": f"src-wt/{TASK}", "task": TASK, "why": "no run"}], doc["orphans"])
        self.assertTrue((self.root / "src-wt" / TASK / ".git").is_file())

    def test_remove_takes_the_tree_and_keeps_the_branch(self):
        self.make_tree()
        code, doc, _ = self.run_cli("--remove", "--task", TASK)
        self.assertEqual(0, code)
        self.assertEqual({"task": TASK, "removed": True, "left": False, "reason": ""}, doc)
        self.assertFalse((self.root / "src-wt" / TASK).exists())
        self.assertEqual(BRANCH, self.git(self.src, "branch", "--list", BRANCH).lstrip("* ").strip())

    def test_remove_exits_one_when_a_dirty_tree_had_to_stay(self):
        self.make_tree()
        (self.root / "src-wt" / TASK / "notes.txt").write_text("mine", encoding="utf-8")
        code, doc, _ = self.run_cli("--remove", "--task", TASK)
        self.assertEqual(1, code)
        self.assertTrue(doc["left"])
        self.assertTrue((self.root / "src-wt" / TASK / "notes.txt").is_file())

    def test_repair_brings_back_a_deleted_directory(self):
        self.make_tree()
        tmproot.rmtree(self.root / "src-wt" / TASK)
        code, doc, _ = self.run_cli("--repair", "--task", TASK)
        self.assertEqual((0, True), (code, doc["ok"]), doc)
        self.assertEqual("OK", worktree.status(TASK))

    def test_a_missing_or_malformed_task_is_a_usage_error(self):
        for argv in (("--remove",), ("--repair",), ("--remove", "--task", "nonsense"),
                     ("--repair", "--task", "../x")):
            with self.subTest(argv=argv):
                code, doc, _ = self.run_cli(*argv)
                self.assertEqual(2, code)
                self.assertIn("error", doc)

    def test_two_actions_at_once_are_refused_by_the_parser(self):
        code, _, err = self.run_cli("--list", "--sweep")
        self.assertEqual(2, code)
        self.assertIn("not allowed with", err)
        self.assertEqual(2, self.run_cli()[0])

    def test_every_module_imports_cold_in_a_fresh_interpreter(self):
        modules = ("worktree", "state", "git_state", "gate", "stack_gate", "advance", "approve",
                   "stop_gate", "supervisor", "diff_checkpoint", "pretool_gate", "agent_gate")
        for name in modules:
            with self.subTest(first_import=name):
                done = subprocess.run(
                    [sys.executable, "-c",
                     f"import sys; sys.path.insert(0, {str(self.scripts)!r}); import {name}"],
                    capture_output=True, text=True, cwd=str(self.root), timeout=120)
                self.assertEqual(0, done.returncode, done.stderr)


class AdvanceBlockedByATreeTest(RegistrationCase):
    """A recorded tree that is gone blocks the run the way an unconfigured gate does: the
    command never runs and the retry budget is not spent."""

    def test_a_missing_tree_blocks_the_run_without_spending_a_retry(self):
        self.config("on", pipelines={"build": {"editing_stages": ["implement"], "stages": [
            {"name": "implement", "owner": "dev", "exit_gate": {"cmd": self.marker_command()}},
            {"name": "done", "owner": "user", "terminal": True}]}})
        self.task_file()
        self.assertEqual("started", self.advance()["action"])
        tmproot.rmtree(self.tree())
        out = self.advance()
        self.assertEqual("blocked", out["action"], out)
        self.assertIn(f"worktree.py --repair --task {TASK}", out["message"])
        self.assertEqual((state.ST_BLOCKED, 0), (self.row()["stage_status"], self.row()["retries"]))
        self.assertFalse((self.root / "ran.marker").exists())

    def marker_command(self) -> str:
        return f'{PY} -c "open(\'{(self.root / "ran.marker").as_posix()}\', \'w\').write(\'x\')"'


class StackGateSpellingTest(StackGateCase):

    def test_a_repo_path_spelled_with_a_dot_segment_still_runs_in_the_tree(self):
        self.config(None, repos={"agentry": {"path": "./src", "stages": {"test": ["suite"]}}})
        self.make_tree()
        out = io.StringIO()
        argv = ["stack_gate.py", "--task", TASK, "--stage", "test", "--repo", "agentry"]
        with unittest.mock.patch.object(sys, "argv", argv), \
                unittest.mock.patch.object(stack_gate, "run", return_value=0) as ran, \
                contextlib.redirect_stdout(out):
            self.assertEqual(0, stack_gate.main())
        self.assertEqual([unittest.mock.call(["suite"], self.root / "src-wt" / TASK)], ran.call_args_list)


class RootPlaceholderProbeTest(WorkspaceCase):

    def test_root_fills_every_occurrence_after_task(self):
        cmd = PY + ' -c "import sys; print(sys.argv[1:])" {root} {task} {root}'
        out = gate.run_gate(pipeline_with(cmd), "test", TASK)
        self.assertTrue(out["passed"], out)
        root = self.root.as_posix()
        self.assertEqual(str([root, TASK, root]), out["output"].strip())

    def test_a_root_with_whitespace_or_a_quote_refuses_before_any_lookup(self):
        lookup = unittest.mock.patch.object(worktree, "status", side_effect=AssertionError("looked up"))
        for odd in ("E:/a b/ws", "E:/a" + chr(9) + "b/ws", "E:/it's/ws", 'E:/say"hi"/ws'):
            with self.subTest(root=odd):
                with unittest.mock.patch.object(state, "ROOT", Path(odd)), lookup:
                    out = gate.run_gate(pipeline_with(PY + " -c pass {root}/x"), "test", TASK)
                self.assertEqual((True, False, False, None), (out["has_gate"], out["configured"],
                                                              out["passed"], out["exit"]))
                self.assertTrue(out["output"].startswith("refusing {root}: "), out)


if __name__ == "__main__":
    unittest.main()
