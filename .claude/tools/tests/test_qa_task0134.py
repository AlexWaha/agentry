"""QA additions for task-0134 (tree-bound approvals, confinement to the task worktree).

What the two feature files do not pin, found while verifying the acceptance criteria:

- the binding is the same value from the working tree and from the committed tip under
  `core.autocrlf` (this is Windows, where it is on by default) and for names with spaces
  and non-ASCII characters;
- two snapshots taken at once do not disturb each other;
- `git -C <tree> commit` run from a directory that is not the tree is bound to the tree
  it names;
- `approved_tree` reaches a run store created before the task and is not a frozen field;
- a directory junction inside a task's own tree that leads into another tree is judged by
  where it lands.

Real git, real worktrees under the project's tmp/, the real hook entry points.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import pretool_gate
import state
import tmproot
from test_agent_gate_worktree_confinement import ConfinementCase
from test_approve_tree_binding import BindingCase
from test_worktree_core import BRANCH, OTHER, TASK

SPACED = "dir with space/file name.txt"
ACCENTED = "caf" + chr(0xE9) + "/na" + chr(0xEF) + "ve " + chr(0x444) + chr(0x43B) + ".txt"


class BindingAcrossPlatformsTest(BindingCase):

    def test_crlf_work_binds_like_the_commit_made_from_it_under_autocrlf(self):
        self.git(self.tree, "config", "core.autocrlf", "true")
        (self.tree / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
        snapshot = self.binding()

        self.commit_work()

        self.assertEqual(snapshot, pretool_gate.tree_binding(self.tree, tip=BRANCH))
        self.assertEqual(snapshot, self.binding())

    def test_a_change_of_line_ending_only_moves_the_binding_with_autocrlf_off(self):
        self.git(self.tree, "config", "core.autocrlf", "false")
        (self.tree / "eol.txt").write_bytes(b"one\ntwo\n")
        lf = self.binding()
        (self.tree / "eol.txt").write_bytes(b"one\r\ntwo\r\n")

        self.assertNotEqual(lf, self.binding())

    def test_names_with_spaces_and_non_ascii_characters_agree_between_work_tree_and_tip(self):
        self.git(self.tree, "config", "core.quotepath", "false")
        empty = self.binding()
        for name in (SPACED, ACCENTED):
            self.write(name, "content\n")
        snapshot = self.binding()

        self.commit_work()

        self.assertNotEqual(empty, snapshot)
        self.assertEqual(snapshot, pretool_gate.tree_binding(self.tree, tip=BRANCH))

    def test_a_rename_moves_the_binding_it_is_not_read_as_the_same_file(self):
        self.write("old-name.txt", "same bytes\n")
        self.commit_work()
        before = pretool_gate.tree_binding(self.tree, tip=BRANCH)

        self.git(self.tree, "mv", "old-name.txt", "new-name.txt")

        self.assertNotEqual(before, self.binding())

    def test_snapshots_taken_at_once_never_disagree_and_leave_no_index_copy(self):
        # Two git processes writing the same new loose object can collide on Windows
        # ("unable to write file ... Permission denied"). That surfaces as a
        # TreeBindingError, which every caller reads as a refusal, so the invariant is:
        # no snapshot ever returns a DIFFERENT value, and none leaves a scratch index.
        self.write("feature.txt")
        self.write("more.txt", "more\n")
        expected = self.binding()
        results, errors = [], []

        def take():
            try:
                results.append(self.binding())
            except pretool_gate.TreeBindingError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=take) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        self.assertEqual(4, len(results) + len(errors))
        self.assertEqual([], [r for r in results if r != expected])
        self.assertEqual([], [n for n in os.listdir(tmproot.RUN_ROOT)
                              if n.startswith("binding-index-")])


class CommitFromElsewhereTest(BindingCase):
    """`git -C <tree> commit` is how a dispatch line tells an agent to work."""

    def dash_c_commit(self, tree: Path) -> str:
        return f'git -C "{tree.as_posix()}" commit -m "[{TASK}] feat: work"'

    def test_dash_c_commit_of_the_approved_tree_from_the_workspace_root_passes(self):
        self.parked_and_approved()

        code, err = self.commit_verdict(self.dash_c_commit(self.tree), cwd=self.root)

        self.assertEqual(0, code, err)

    def test_dash_c_commit_of_a_tree_that_changed_after_the_approval_is_denied(self):
        self.parked_and_approved()
        self.write("sneaked.txt", "added after the diff was read\n")

        code, err = self.commit_verdict(self.dash_c_commit(self.tree), cwd=self.root)

        self.assertEqual(2, code)
        self.assertIn("the tree changed after the CEO approved it", err)


class RunStoreTest(BindingCase):

    def test_approved_tree_is_not_a_frozen_task_file_field(self):
        self.assertNotIn("approved_tree", state.FROZEN_FIELDS)

    def test_a_run_store_that_predates_the_column_gains_it_on_the_next_connect(self):
        conn = state.connect()
        try:
            conn.execute("ALTER TABLE runs DROP COLUMN approved_tree")
            conn.commit()
        finally:
            conn.close()

        conn = state.connect()
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(runs)")]
            run = state.get_run(conn, TASK)
        finally:
            conn.close()

        self.assertIn("approved_tree", columns)
        self.assertEqual("", run["approved_tree"])


@unittest.skipUnless(os.name == "nt", "directory junctions are a Windows mechanism")
class JunctionEscapeTest(ConfinementCase):

    def test_a_junction_in_the_own_tree_that_leads_into_another_tree_is_judged_where_it_lands(self):
        import _winapi

        link = self.tree / "shortcut"
        _winapi.CreateJunction(str(self.other_tree), str(link))
        self.addCleanup(os.rmdir, str(link))  # the junction only, never what it points at

        self.assertDenied(self.write(link / "planted.txt"), OTHER)
        self.assertDenied(self.bash(f'echo hi > "{link.as_posix()}/planted.txt"'), OTHER)
        self.assertFalse((self.other_tree / "planted.txt").exists())


if __name__ == "__main__":
    unittest.main()
