"""task-0134 part A: the commit approval is bound to the change set the User read.

Real git, real worktrees under the project's tmp/, the real advance.py, approve.py and the
real pretool_gate hook entry point; only the stage gate command and the approvals level are
stubbed. What is pinned:

- the binding (a SHA-256 of the task's change set against its merge-base) is recorded at
  the commit park, verified when approve.py records the approval and at the auto-level
  grant, and checked at the commit and at merge condition 3;
- any edit, add, delete or mode change moves it, a sync merge that does not overlap the
  task's files does not, and nothing it does touches the real index;
- a commit runs in the tree of the task whose approval it spends;
- every way of not being able to read it fails closed;
- the folded residuals of these files: the extended-length spelling of a trunk path, the
  placement misses (`env -C`, `make -C`, GIT_COMMON_DIR, GIT_CONFIG_COUNT) and the
  truncation marker of a tree task's diff card.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import approvals
import approve
import diff_checkpoint
import pretool_gate
import state
import tmproot  # arms the temp-root redirect before advance is imported
from test_worktree_core import BRANCH, OTHER, TASK
from test_worktree_gates import bash_payload, hook
from test_worktree_merge import MergeCase


class BindingCase(MergeCase):
    """A registered task in its tree (stage `implement`), with the helpers to take it to
    the commit park, approve it, and ask the hook about a commit or a merge."""

    def write(self, name: str, text: str = "work\n", tree: Path | None = None) -> None:
        path = (tree or self.tree) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def binding(self, tree: Path | None = None) -> str:
        return pretool_gate.tree_binding(tree or self.tree, snapshot=True)

    def park(self) -> dict:
        self.at("ready")
        out = self.advance()
        self.assertEqual("park", out["action"], out)
        return out

    def approve_cli(self, *argv: str) -> tuple[int, dict]:
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["approve.py", "--task", TASK, *argv]), \
                contextlib.redirect_stdout(out):
            code = approve.main()
        return code, json.loads(out.getvalue())

    def approve_commit(self) -> tuple[int, dict]:
        return self.approve_cli("--gate", "commit")

    def approved(self) -> None:
        """The User's approval, which must have been recorded."""
        code, out = self.approve_commit()
        self.assertEqual(0, code, out)

    def parked_and_approved(self, name: str = "feature.txt") -> str:
        """Uncommitted work in the tree, the commit park, the User's approval. Returns
        the binding the run holds."""
        self.write(name)
        self.park()
        code, out = self.approve_commit()
        self.assertEqual(0, code, out)
        return self.row()["approved_tree"]

    def commit_verdict(self, command: str | None = None, cwd: Path | None = None) -> tuple:
        command = command or f'git commit -m "[{TASK}] feat: work"'
        code, _, err = hook(pretool_gate, bash_payload(command, str(cwd or self.tree)))
        return code, err

    def merge_verdict(self, branch: str = BRANCH) -> tuple:
        command = f"git -C {self.src.as_posix()} merge --no-ff {branch}"
        code, _, err = hook(pretool_gate, bash_payload(command, str(self.root)))
        return code, err

    def commit_work(self, message: str | None = None) -> None:
        self.git(self.tree, "add", "-A")
        self.git(self.tree, "commit", "-m", message or f"[{TASK}] feat: work")

    def level(self, level: str) -> None:
        patcher = unittest.mock.patch.object(approvals, "read", lambda: level)
        patcher.start()
        self.addCleanup(patcher.stop)


class RecordingTest(BindingCase):

    def test_commit_approval_records_tree_hash(self):
        self.write("feature.txt")

        parked = self.park()
        at_park = self.row()["approved_tree"]
        code, out = self.approve_commit()

        self.assertEqual(0, code, out)
        row = self.row()
        self.assertEqual(1, row["commit_approved"])
        self.assertEqual(self.binding(), row["approved_tree"])
        self.assertEqual(at_park, row["approved_tree"])
        self.assertRegex(row["approved_tree"], r"^[0-9a-f]{64}$")
        self.assertIn(at_park[:12], parked["message"])
        self.assertIn(at_park[:12], out["message"])
        approved = [d for d in self.journal(kind="approved")]
        self.assertEqual([{"tree": at_park[:12]}], approved)

    def test_auto_grant_records_tree_hash(self):
        self.level(approvals.AUTO)
        self.write("feature.txt")
        self.at("ready")

        out = self.advance()

        self.assertEqual("park", out["action"], out)
        row = self.row()
        self.assertEqual(1, row["commit_approved"])
        self.assertEqual(self.binding(), row["approved_tree"])
        self.assertIn("auto-approved", out["message"])
        self.assertEqual([{"tree": row["approved_tree"][:12]}], self.journal(kind="approved"))

    def test_park_records_the_binding_the_diff_was_shown_for(self):
        self.write("feature.txt")

        self.park()

        self.assertEqual(0, self.row()["commit_approved"])
        self.assertEqual(self.binding(), self.row()["approved_tree"])

    def test_a_run_approved_with_no_park_takes_the_fresh_binding(self):
        self.write("feature.txt")
        self.at("ready")
        self.assertEqual("", self.row()["approved_tree"])

        code, out = self.approve_commit()

        self.assertEqual(0, code, out)
        self.assertEqual(self.binding(), self.row()["approved_tree"])

    def test_approve_refuses_when_the_tree_changed_after_the_diff_was_shown(self):
        self.write("feature.txt")
        self.park()
        self.write("late.txt", "sneaked in\n")

        code, out = self.approve_commit()

        self.assertEqual(2, code)
        self.assertIn("changed after the diff was shown", out["error"])
        self.assertIn("advance.py", out["error"])
        self.assertEqual(0, self.row()["commit_approved"])

    def test_advance_parks_a_changed_tree_again_and_the_new_approval_binds_it(self):
        self.write("feature.txt")
        self.park()
        first = self.row()["approved_tree"]
        self.write("late.txt", "added after the park\n")

        again = self.advance()

        self.assertEqual("park", again["action"], again)
        self.assertNotEqual(first, self.row()["approved_tree"])
        self.assertEqual(self.binding(), self.row()["approved_tree"])
        code, out = self.approve_commit()
        self.assertEqual(0, code, out)

    def test_a_legacy_run_is_not_bound(self):
        self.sql("UPDATE runs SET worktree = '' WHERE task = ?", TASK)
        self.write("feature.txt")
        self.at("ready")

        out = self.advance()
        code, approved = self.approve_commit()

        self.assertEqual("park", out["action"])
        self.assertEqual(0, code, approved)
        self.assertEqual("", self.row()["approved_tree"])
        self.assertEqual(1, self.row()["commit_approved"])

    def test_an_unreadable_tree_records_no_approval(self):
        self.write("feature.txt")
        self.park()
        err = pretool_gate.TreeBindingError("git exploded")

        with unittest.mock.patch.object(pretool_gate, "task_binding", side_effect=err):
            code, out = self.approve_commit()

        self.assertEqual(2, code)
        self.assertIn("cannot be read", out["error"])
        self.assertEqual(0, self.row()["commit_approved"])

    def test_the_auto_level_does_not_grant_what_it_cannot_bind(self):
        self.level(approvals.AUTO)
        self.write("feature.txt")
        self.at("ready")
        err = pretool_gate.TreeBindingError("git exploded")

        with unittest.mock.patch.object(pretool_gate, "task_binding", side_effect=err):
            out = self.advance()

        self.assertEqual("park", out["action"], out)
        row = self.row()
        self.assertEqual((0, "commit", ""), (row["commit_approved"], row["awaiting_human"],
                                             row["approved_tree"]))
        self.assertIn("NOT approved", out["message"])
        self.assertNotIn("auto-approved", out["message"])


class BindingValueTest(BindingCase):
    """What moves the binding and what does not."""

    def test_an_untracked_file_moves_it(self):
        before = self.binding()
        self.write("new.txt")
        self.assertNotEqual(before, self.binding())

    def test_an_edit_to_a_tracked_file_moves_it(self):
        before = self.binding()
        self.write("README.md", "hello\nand more\n")
        self.assertNotEqual(before, self.binding())

    def test_a_deleted_file_moves_it(self):
        before = self.binding()
        (self.tree / "README.md").unlink()
        self.assertNotEqual(before, self.binding())

    def test_a_mode_change_moves_it(self):
        before = self.binding()
        self.git(self.tree, "update-index", "--chmod=+x", "README.md")
        self.assertNotEqual(before, self.binding())

    def test_reverting_to_the_base_gives_the_empty_change_set_again(self):
        empty = self.binding()
        self.write("README.md", "changed\n")
        self.write("new.txt")
        self.assertNotEqual(empty, self.binding())

        self.git(self.tree, "checkout", "--", "README.md")
        (self.tree / "new.txt").unlink()

        self.assertEqual(empty, self.binding())
        self.assertEqual(hashlib.sha256(b"").hexdigest(), empty)
        self.assertEqual(pretool_gate.EMPTY_BINDING, empty)

    def test_an_ignored_file_does_not_move_it(self):
        before = self.binding()
        self.write("tmp/scratch.txt")
        self.write("__pycache__/x.pyc")
        self.assertEqual(before, self.binding())

    def test_the_committed_tip_has_the_binding_of_the_snapshot_it_was_made_from(self):
        self.write("feature.txt")
        snapshot = self.binding()

        self.commit_work()

        self.assertEqual(snapshot, pretool_gate.tree_binding(self.tree, tip=BRANCH))
        self.assertEqual(snapshot, self.binding())

    def test_a_sync_merge_that_does_not_overlap_the_task_keeps_it(self):
        self.write("feature.txt")
        self.commit_work()
        before = pretool_gate.tree_binding(self.tree, tip=BRANCH)
        self.commit(self.src, "elsewhere.txt", "someone else merged")

        self.git(self.tree, "merge", "main", "-m", "sync")

        self.assertEqual(before, pretool_gate.tree_binding(self.tree, tip=BRANCH))
        self.assertEqual(before, self.binding())

    def test_a_sync_merge_that_overlaps_the_task_moves_it(self):
        (self.src / "shared.txt").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
        self.git(self.src, "add", "shared.txt")
        self.git(self.src, "commit", "-m", "shared file")
        self.git(self.tree, "merge", "main", "-m", "get the shared file")
        self.write("shared.txt", "ONE\ntwo\nthree\nfour\nfive\n")
        self.commit_work()
        before = pretool_gate.tree_binding(self.tree, tip=BRANCH)
        (self.src / "shared.txt").write_text("one\ntwo\nthree\nfour\nFIVE\n", encoding="utf-8")
        self.git(self.src, "add", "shared.txt")
        self.git(self.src, "commit", "-m", "elsewhere edits the same file")

        self.git(self.tree, "merge", "main", "-m", "sync")

        self.assertNotEqual(before, pretool_gate.tree_binding(self.tree, tip=BRANCH))

    def test_the_index_copy_keeps_the_mtime_of_the_real_index(self):
        # Racy git: an edit of the same size made in the same timestamp tick as the index
        # write looks unchanged to the stat cache. git re-reads such an entry because the
        # index file is not older than it; a copy stamped "now" would trust the cache and
        # miss the edit (seen as a flake: an approved file edited right after a merge).
        self.write("feature.txt")
        index = Path(self.git(self.tree, "rev-parse", "--path-format=absolute",
                              "--git-path", "index"))
        stamps = []
        real = pretool_gate.git_run

        def spy(argv, repo, env=None):
            if env and "GIT_INDEX_FILE" in env and "add" in argv:
                stamps.append((os.stat(env["GIT_INDEX_FILE"]).st_mtime_ns,
                               os.stat(index).st_mtime_ns))
            return real(argv, repo) if env is None else real(argv, repo, env=env)

        with unittest.mock.patch.object(pretool_gate, "git_run", spy):
            self.binding()

        self.assertEqual(1, len(stamps))
        self.assertEqual(stamps[0][1], stamps[0][0])

    def test_computing_it_leaves_the_real_index_and_the_work_tree_alone(self):
        self.write("feature.txt")
        self.git(self.tree, "add", "README.md")
        index = Path(self.git(self.tree, "rev-parse", "--path-format=absolute",
                              "--git-path", "index"))
        before = (index.read_bytes(), self.git(self.tree, "status", "--porcelain=v2"),
                  self.git(self.src, "for-each-ref"))
        leftovers = set(os.listdir(tmproot.RUN_ROOT))

        self.binding()

        self.assertEqual(before, (index.read_bytes(), self.git(self.tree, "status", "--porcelain=v2"),
                                  self.git(self.src, "for-each-ref")))
        self.assertEqual([], [n for n in set(os.listdir(tmproot.RUN_ROOT)) - leftovers
                              if n.startswith("binding-index-")])

    def test_a_ref_that_does_not_resolve_is_an_error_not_a_value(self):
        with self.assertRaises(pretool_gate.TreeBindingError):
            pretool_gate.tree_binding(self.tree, tip="refs/heads/no-such-branch")

    def test_a_directory_that_is_not_a_repository_is_an_error(self):
        with self.assertRaises(pretool_gate.TreeBindingError):
            pretool_gate.tree_binding(self.root / ".agentry", snapshot=True)


class CommitCheckTest(BindingCase):

    def test_commit_denied_when_tree_differs_from_approved(self):
        self.parked_and_approved()
        self.write("sneaked.txt", "not in the diff the User read\n")

        code, err = self.commit_verdict()

        self.assertEqual(2, code)
        self.assertIn("the tree changed after the User approved it", err)
        self.assertIn('advance.py" --task ' + TASK, err)

    def test_an_edit_to_an_approved_file_is_denied_too(self):
        self.parked_and_approved("feature.txt")
        self.write("feature.txt", "rewritten after approval\n")

        code, err = self.commit_verdict()

        self.assertEqual(2, code)
        self.assertIn("changed after the User approved", err)

    def test_commit_and_merge_pass_when_tree_matches(self):
        self.parked_and_approved()

        code, err = self.commit_verdict()
        self.assertEqual(0, code, err)
        self.commit_work()
        merge_code, merge_err = self.merge_verdict()

        self.assertEqual(0, merge_code, merge_err)

    def test_an_approval_with_no_binding_cannot_commit(self):
        self.write("feature.txt")
        self.at("ready", commit_approved=1)

        code, err = self.commit_verdict()

        self.assertEqual(2, code)
        self.assertIn("carries no tree binding", err)

    def test_a_commit_tagged_for_this_task_from_another_tree_is_refused(self):
        # OTHER declares (branch: in its task file, frozen at registration) that it rides
        # on this task's branch, so the tag rule accepts its approval; the placement rule
        # is what stops a commit made in TASK's tree from spending it.
        self.task_file(OTHER, head=f"branch: {BRANCH}")
        self.advance(OTHER)
        self.sql("UPDATE runs SET stage = 'ready', commit_approved = 1, approved_tree = ? "
                 "WHERE task = ?", "x" * 64, OTHER)
        self.write("feature.txt")

        code, err = self.commit_verdict(f'git commit -m "[{OTHER}] feat: borrowed"')

        self.assertEqual(2, code)
        self.assertIn(f"Commit for {OTHER} refused", err)
        self.assertIn(f"the worktree of {TASK}", err)

    def test_a_legacy_run_is_untouched(self):
        self.sql("UPDATE runs SET worktree = '', commit_approved = 1 WHERE task = ?", TASK)
        self.write("feature.txt")

        code, err = self.commit_verdict()

        self.assertEqual(0, code, err)

    def test_an_unreadable_tree_fails_closed(self):
        self.parked_and_approved()
        err = pretool_gate.TreeBindingError("git exploded")

        with unittest.mock.patch.object(pretool_gate, "task_binding", side_effect=err):
            code, stderr = self.commit_verdict()

        self.assertEqual(2, code)
        self.assertIn("cannot be read", stderr)

    def test_an_unexpected_failure_reading_the_tree_fails_closed(self):
        self.parked_and_approved()

        with unittest.mock.patch.object(pretool_gate, "task_binding", side_effect=RuntimeError("x")):
            code, stderr = self.commit_verdict()

        self.assertEqual(2, code)
        self.assertIn("cannot be read", stderr)

    def test_a_git_call_that_fails_denies_the_commit(self):
        self.parked_and_approved()
        real = pretool_gate.git_run

        def broken(argv, repo, *rest, **kw):
            if "diff-tree" in argv:
                raise OSError("no git")
            return real(argv, repo, *rest, **kw)

        with unittest.mock.patch.object(pretool_gate, "git_run", broken):
            code, stderr = self.commit_verdict()

        self.assertEqual(2, code)
        self.assertIn("cannot be read", stderr)

    def test_an_expired_push_budget_denies_rather_than_allows(self):
        self.parked_and_approved()
        real = pretool_gate.git_run

        def expire(argv, repo, *rest, **kw):
            if "write-tree" in argv:
                raise pretool_gate.PushBudgetExpired()
            return real(argv, repo, *rest, **kw)

        with unittest.mock.patch.object(pretool_gate, "git_run", expire):
            code, stderr = self.commit_verdict()

        self.assertEqual(2, code)
        self.assertIn("time budget", stderr)


class MergeCheckTest(BindingCase):

    def test_merge_condition3_denied_when_tree_differs_from_approved(self):
        self.parked_and_approved()
        self.commit_work()
        self.write("forged.txt", "pushed onto the branch after the approval\n")
        self.commit_work("[task-0130] feat: forged")

        code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("condition 3 of 3 failed", err)
        self.assertIn("holds a change set the User did not approve", err)

    def test_an_approved_branch_missing_part_of_the_work_is_denied_at_the_merge(self):
        self.write("one.txt")
        self.write("two.txt")
        self.park()
        self.approved()
        self.git(self.tree, "add", "one.txt")
        self.git(self.tree, "commit", "-m", f"[{TASK}] feat: half")

        code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("did not approve", err)

    def test_a_merge_whose_approval_carries_no_binding_is_denied(self):
        self.write("feature.txt")
        self.commit_work()
        self.at("ready", commit_approved=1)

        code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("carries no tree binding", err)

    def test_a_sync_merge_keeps_the_approval_valid_for_the_merge(self):
        self.parked_and_approved()
        self.commit_work()
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.git(self.tree, "merge", "main", "-m", "sync")

        code, err = self.merge_verdict()

        self.assertEqual(0, code, err)

    def test_an_overlapping_sync_is_denied_at_the_merge(self):
        (self.src / "shared.txt").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
        self.git(self.src, "add", "shared.txt")
        self.git(self.src, "commit", "-m", "shared file")
        self.git(self.tree, "merge", "main", "-m", "get the shared file")
        self.write("shared.txt", "ONE\ntwo\nthree\nfour\nfive\n")
        self.park()
        self.approved()
        self.commit_work()
        (self.src / "shared.txt").write_text("one\ntwo\nthree\nfour\nFIVE\n", encoding="utf-8")
        self.git(self.src, "add", "shared.txt")
        self.git(self.src, "commit", "-m", "elsewhere edits the same file")
        self.git(self.tree, "merge", "main", "-m", "sync")

        code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("did not approve", err)

    def test_the_voided_approval_is_parked_again_by_advance(self):
        (self.src / "shared.txt").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
        self.git(self.src, "add", "shared.txt")
        self.git(self.src, "commit", "-m", "shared file")
        self.git(self.tree, "merge", "main", "-m", "get the shared file")
        self.write("shared.txt", "ONE\ntwo\nthree\nfour\nfive\n")
        self.park()
        self.approved()
        self.commit_work()
        (self.src / "shared.txt").write_text("one\ntwo\nthree\nfour\nFIVE\n", encoding="utf-8")
        self.git(self.src, "add", "shared.txt")
        self.git(self.src, "commit", "-m", "elsewhere edits the same file")
        self.git(self.tree, "merge", "main", "-m", "sync")
        self.at("ready")

        out = self.advance()

        self.assertEqual("park", out["action"], out)
        row = self.row()
        self.assertEqual((0, "commit"), (row["commit_approved"], row["awaiting_human"]))
        self.assertEqual(self.binding(), row["approved_tree"])
        code, approved = self.approve_commit()
        self.assertEqual(0, code, approved)
        self.assertEqual(0, self.merge_verdict()[0])

    def test_a_sync_merge_that_does_not_overlap_does_not_void_the_approval(self):
        self.parked_and_approved()
        self.commit_work()
        stored = self.row()["approved_tree"]
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.git(self.tree, "merge", "main", "-m", "sync")
        self.at("ready")

        out = self.advance()

        self.assertNotEqual("stale", out["action"])
        row = self.row()
        self.assertEqual((1, stored), (row["commit_approved"], row["approved_tree"]))

    def test_the_solo_flow_commit_merge_then_advance_closes_the_task_without_a_new_park(self):
        # The park message tells the orchestrator to commit, merge and THEN re-run
        # advance.py, which still stands at the commit stage: the trunk carries the work
        # by then, so the empty change set must not read as a tree that drifted.
        stored = self.parked_and_approved()
        self.commit_work()
        self.merge_into_trunk()

        out = self.advance()

        self.assertEqual("done", out["action"], out)
        row = self.row()
        self.assertEqual((1, stored), (row["commit_approved"], row["approved_tree"]))
        self.assertEqual("done", state.task_dir(TASK))

    def test_an_unreadable_branch_fails_closed(self):
        self.parked_and_approved()
        self.commit_work()

        with unittest.mock.patch.object(pretool_gate, "tree_binding",
                                        side_effect=pretool_gate.TreeBindingError("x")):
            code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("condition 3 of 3 failed", err)
        self.assertIn("could not be read", err)

    def test_condition3_runs_before_freshness(self):
        self.parked_and_approved()
        self.commit_work()
        self.write("forged.txt")
        self.commit_work("[task-0130] feat: forged")
        self.commit(self.src, "elsewhere.txt", "trunk moved too")

        code, err = self.merge_verdict()

        self.assertEqual(2, code)
        self.assertIn("condition 3 of 3 failed", err)
        self.assertNotIn("condition 4", err)

    def test_a_legacy_task_is_not_bound_at_the_merge(self):
        self.git(self.src, "checkout", "-q", "-b", "feature/task-9399")
        self.commit(self.src, "legacy.txt", "legacy work")
        self.git(self.src, "checkout", "-q", "main")
        self.sql("INSERT INTO runs (task, type, stage, stage_status, commit_approved, worktree) "
                 "VALUES ('task-9399', 'feature', 'ready', 'in_progress', 1, '')")

        code, err = self.merge_verdict("feature/task-9399")

        self.assertEqual(0, code, err)


class ClearingTest(BindingCase):
    """The binding goes wherever the commit approval goes."""

    def test_reject_clears_it(self):
        self.parked_and_approved()

        code, _ = self.approve_cli("--reject", "--reason", "not like this")

        self.assertEqual(0, code)
        row = self.row()
        self.assertEqual((0, ""), (row["commit_approved"], row["approved_tree"]))

    def test_abandon_clears_it(self):
        self.parked_and_approved()

        code, _ = self.approve_cli("--abandon", "--reason", "dropped")

        self.assertEqual(0, code)
        self.assertEqual("", self.row()["approved_tree"])

    def test_resume_of_a_force_majeure_approval_clears_it(self):
        self.write("feature.txt")
        self.commit_work()
        self.at("implement")
        self.approved()
        self.assertTrue(self.row()["approved_tree"])
        code, _ = self.approve_cli("--pause", "User force majeure")
        self.assertEqual(0, code)

        code, out = self.approve_cli("--resume")

        self.assertEqual(0, code, out)
        row = self.row()
        self.assertEqual((0, ""), (row["commit_approved"], row["approved_tree"]))

    def test_the_stale_send_back_keeps_it(self):
        self.passing_gate()
        stored = self.parked_and_approved()
        self.commit_work()
        self.commit(self.src, "elsewhere.txt", "someone else merged")
        self.at("review")

        out = self.advance()

        self.assertEqual("stale", out["action"], out)
        row = self.row()
        self.assertEqual((1, stored), (row["commit_approved"], row["approved_tree"]))


class ResidualsTest(BindingCase):

    @unittest.skipUnless(os.name == "nt", "the extended-length prefix is a Windows spelling")
    def test_the_extended_length_spelling_of_a_trunk_path_is_still_the_trunk(self):
        plain = str(self.src / "app.py")
        extended = "\\\\?\\" + plain

        self.assertTrue(pretool_gate.in_trunk_checkout(plain))
        self.assertTrue(pretool_gate.in_trunk_checkout(extended))
        self.assertTrue(pretool_gate.in_trunk_checkout("//?/" + plain.replace("\\", "/")))
        self.assertTrue(pretool_gate.in_trunk_checkout(extended, strict=True))

    @unittest.skipUnless(os.name == "nt", "the extended-length prefix is a Windows spelling")
    def test_a_real_network_path_in_extended_form_is_still_outside(self):
        self.assertFalse(pretool_gate.in_trunk_checkout("\\\\?\\UNC\\host\\share\\x.py"))
        self.assertFalse(pretool_gate.in_trunk_checkout("\\\\host\\share\\x.py"))

    @unittest.skipUnless(os.name == "nt", "the extended-length prefix is a Windows spelling")
    def test_a_tree_path_in_extended_form_is_not_the_trunk(self):
        self.assertFalse(pretool_gate.in_trunk_checkout("\\\\?\\" + str(self.tree / "app.py")))

    def test_the_agentry_bookkeeping_of_an_extended_path_is_never_frozen(self):
        if os.name != "nt":
            self.skipTest("Windows spelling")
        self.assertFalse(pretool_gate.in_trunk_checkout(
            "\\\\?\\" + str(self.src / ".agentry" / "x.md")))

    def placement(self, command: str) -> tuple:
        code, _, err = hook(pretool_gate, bash_payload(command, str(self.tree)))
        return code, err

    def test_env_chdir_leaves_a_commit_with_no_place_the_gate_can_read(self):
        for command in (f'env -C {self.tree.as_posix()} git commit -m "[{TASK}] x"',
                        f'env --chdir={self.tree.as_posix()} git commit -m "[{TASK}] x"',
                        f'env -i -C {self.tree.as_posix()} git commit -m "[{TASK}] x"'):
            with self.subTest(command=command):
                code, err = self.placement(command)
                self.assertEqual(2, code, err)
                self.assertIn("where the text does not say", err)

    def test_make_chdir_leaves_a_merge_with_no_place_the_gate_can_read(self):
        for command in (f"make -C {self.src.as_posix()} git merge {BRANCH}",
                        f"make --directory={self.src.as_posix()} git merge {BRANCH}"):
            with self.subTest(command=command):
                code, err = self.placement(command)
                self.assertEqual(2, code, err)
                self.assertIn("where the text does not say", err)

    def test_git_dir_redirects_by_environment_are_refused(self):
        for variable in ("GIT_COMMON_DIR=/elsewhere", "GIT_CONFIG_COUNT=1",
                         "GIT_DIR=/elsewhere", "GIT_WORK_TREE=/elsewhere"):
            with self.subTest(variable=variable):
                code, err = self.placement(f'{variable} git commit -m "[{TASK}] x"')
                self.assertEqual(2, code, err)
                self.assertIn(variable.split("=")[0], err)

    def test_env_without_a_chdir_option_is_placed_as_usual(self):
        self.write("feature.txt")
        self.park()
        self.approved()

        code, err = self.placement(f'env GIT_AUTHOR_NAME=x git commit -m "[{TASK}] x"')

        self.assertEqual(0, code, err)

    def test_the_git_option_dash_c_after_env_is_still_gits(self):
        self.write("feature.txt")
        self.park()
        self.approved()

        code, err = self.placement(f'env GIT_AUTHOR_NAME=x git -C {self.tree.as_posix()} '
                                   f'commit -m "[{TASK}] x"')

        self.assertEqual(0, code, err)

    def test_the_diff_card_of_a_tree_task_points_at_the_tree_not_at_slash_diff(self):
        self.write("big.txt", "".join(f"line {n} of a long uncommitted file\n" for n in range(400)))
        self.git(self.tree, "add", "big.txt")

        diff = diff_checkpoint.capped_diff(TASK)

        self.assertTrue(diff.endswith(
            f'... (truncated, run git -C "{self.tree.as_posix()}" diff HEAD for the full diff)'),
            diff[-200:])
        self.assertNotIn(diff_checkpoint.TRUNCATED, diff)
        self.assertLessEqual(len(diff), diff_checkpoint.PREVIEW_MAX_CHARS)

    def test_a_legacy_task_keeps_the_slash_diff_marker(self):
        self.sql("UPDATE runs SET worktree = '' WHERE task = ?", TASK)
        (self.src / "big.txt").write_text(
            "".join(f"line {n} of a long uncommitted file\n" for n in range(400)), encoding="utf-8")
        self.git(self.src, "add", "big.txt")

        diff = diff_checkpoint.capped_diff(TASK)

        self.assertTrue(diff.endswith(diff_checkpoint.TRUNCATED), diff[-200:])

    def test_a_clean_tree_with_commits_shows_what_the_branch_holds_beyond_the_trunk(self):
        self.write("feature.txt", "committed work\n")
        self.commit_work()

        diff = diff_checkpoint.capped_diff(TASK)

        self.assertIn("+committed work", diff)
        self.assertIn("feature.txt", diff)


if __name__ == "__main__":
    unittest.main()
