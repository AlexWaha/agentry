"""A push ref that holds a shell closer fails closed (task-0120 FX-4).

task-0112 item 6 cut every push token at its first `)` or backtick, so that the
unquoted closer of `echo $(echo x; git push origin main)/foo` stopped reading as the
ref `main)/foo`. Git ref names may hold `)`, though: with a branch named `x)y`,
`git push origin 'x)y:main'` was cut to `x`, allowed without a trunk marker, and git
published `main`. The leak guard read the cut ref as well.

pad_separators now marks the closer of a substitution (GLUED_CLOSE); only a marked
closer is cut. Any other `)`, backtick or `$(` in a remote or refspec is refused.
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

import pretool_gate
from test_pretool_gate_git import TempRepo, denial_reason, gate_state

WORK_BRANCH = "bugfix/task-9120"
TASK = "task-9120"

# Each of these names a real ref (git allows `)` and a backtick in a branch name)
# and real git publishes the part behind the colon.
QUOTED_CLOSER_PUSHES = (
    "git push origin 'x)y:main'",
    "git push origin 'x)y':main",
    "git push origin x\\)y:main",
    "git push origin 'x)y:refs/heads/main'",
    'git push origin "x)y:main"',
    "git push origin 'main:x)y'",
    "git push origin 'a)b:feature/task-9120'",
    "git push origin 'x`y:main'",
    "git push origin x\\`y:main",
    'git push origin "$(echo main)"',
    "git push origin 'x$(y:main'",
    "git push 'o)rigin' main",
    "git push origin -- 'x)y:main'",
    "git push origin 'x)y:main' && echo done",
    "echo $(echo x; git push origin 'x)y:main')/foo",
    "echo $(echo x; git push origin 'x)y:main')",
)

GLUED_CLOSER_PUSHES = (
    "echo $(echo x; git push origin main)/foo",
    "echo `echo x; git push origin main`/foo",
    "x=$(echo x; git push origin main).txt",
    "echo $(echo x && git push origin main)/foo",
    "echo $(echo x; (git push origin main))/foo",
)


class QuotedCloserFailsClosedTest(unittest.TestCase):
    def test_a_quoted_or_escaped_closer_makes_the_push_unresolvable(self):
        for command in QUOTED_CLOSER_PUSHES:
            with self.subTest(command=command):
                self.assertIsNone(pretool_gate.push_remote_pairs(command, "main", []))
                self.assertIsNone(pretool_gate.push_target_branches(command, "main", []))

    def test_the_cut_ref_is_never_read(self):
        # Before the fix this resolved to [('origin', 'x', 'x')] - source AND
        # destination lost.
        command = "git push origin 'a)b:feature/task-9120'"
        self.assertIsNone(pretool_gate.push_remote_pairs(command, "main", []))
        self.assertEqual("a)b:feature/task-9120",
                         pretool_gate.push_ref_with_closer(["origin", "a)b:feature/task-9120"]))

    def test_check_push_target_denies_with_a_message_naming_the_ref(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for command in QUOTED_CLOSER_PUSHES:
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn("holds a shell closer", err)

    def test_an_approved_trunk_push_does_not_cover_a_ref_with_a_closer(self):
        with unittest.mock.patch.object(pretool_gate, "trunk_push_approved",
                                        return_value=True) as approved:
            for command in QUOTED_CLOSER_PUSHES:
                with self.subTest(command=command):
                    verdict, spend = pretool_gate.check_push_target(command, "main", [], "")
                    self.assertEqual((2, False), (verdict, spend))
            approved.assert_not_called()

    def test_a_plain_ref_and_a_plain_quoted_ref_are_not_touched(self):
        for command in ("git push origin 'feature/task-9120'", "git push origin bugfix/task-9120",
                        "git push origin 'HEAD:feature/task-9120'", 'git push -u origin "a.b-c_d"'):
            with self.subTest(command=command):
                self.assertIsNotNone(pretool_gate.push_remote_pairs(command, "main", []))
                self.assertIsNone(pretool_gate.push_ref_with_closer(
                    [t for sub, a in pretool_gate.git_invocations(command) if sub == "push"
                     for t in a]))


class SubstitutionCloserIsStillCutTest(unittest.TestCase):
    """The case 0112 fixed must keep resolving to the real ref."""

    def test_an_unquoted_closer_glued_to_a_path_is_cut_to_the_real_ref(self):
        for command in GLUED_CLOSER_PUSHES:
            with self.subTest(command=command):
                self.assertEqual(["main"], pretool_gate.push_target_branches(command, "", []))

    def test_the_quoted_and_the_unquoted_closer_differ_only_in_the_marker(self):
        quoted = pretool_gate.command_segments("git push origin 'main)/foo'")
        glued = pretool_gate.command_segments("echo $(echo x; git push origin main)/foo")
        self.assertIn("main)/foo", quoted[0])
        self.assertFalse(any(pretool_gate.GROUP_CLOSE_MARK in tok for tok in quoted[0]))
        self.assertTrue(any(pretool_gate.GLUED_CLOSE + ")/foo" in tok
                            for seg in glued for tok in seg))

    def test_a_quoted_ref_that_looks_like_a_glued_closer_is_refused(self):
        command = "git push origin 'main)/foo'"
        self.assertIsNone(pretool_gate.push_target_branches(command, "", []))

    def test_a_marker_cannot_be_typed_into_a_ref(self):
        marker = pretool_gate.GLUED_CLOSE
        for command in (f"git push origin 'x{marker})y:main'", f'git push origin "x{marker})y:main"'):
            with self.subTest(command=command):
                self.assertNotIn(marker, pretool_gate.pad_separators(command))
                self.assertIsNone(pretool_gate.push_target_branches(command, "", []))

    def test_a_quoted_closer_in_front_of_a_real_one_is_still_refused(self):
        command = "echo $(echo x; git push origin 'x)y:main')/foo"
        self.assertIsNone(pretool_gate.push_target_branches(command, "", []))

    def test_the_protected_branch_deny_still_fires_for_the_glued_spelling(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for command in GLUED_CLOSER_PUSHES:
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn("Push to protected branch 'main'", err)


class SubstitutionWithoutASeparatorTest(unittest.TestCase):
    """task-0120 SEC-B M1b: every unquoted substitution closer is marked, not only the
    ones behind a separator, so a work-branch push in `$( git push origin <branch>)` is
    no longer refused as a branch name that holds a closer."""

    WORK_PUSHES = (
        f"echo $( git push origin {WORK_BRANCH})",
        f"x=$( git push -u origin {WORK_BRANCH})",
        f"echo ` git push origin {WORK_BRANCH}`",
        f"echo $( git push origin {WORK_BRANCH})/foo",
        f"echo $( git push origin {WORK_BRANCH} )",
        f"echo $( git push origin HEAD:{WORK_BRANCH})",
    )
    TRUNK_PUSHES = ("echo $( git push origin main)", "echo ` git push origin main`",
                    "echo $( git push origin main)/foo", "echo $( git push origin HEAD:main)")
    QUOTED_IN_SUBSTITUTION = (
        "echo $( git push origin 'x)y:main')",
        "echo $( git push origin 'x)y:" + WORK_BRANCH + "')",
        'echo $( git push origin "x)y:main")',
        "echo ` git push origin 'x`y:main'`",
        "echo $( git push origin x\\)y:main)",
    )

    def test_a_work_branch_push_is_allowed_subject_to_the_normal_checks(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for command in self.WORK_PUSHES:
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(0, code, err)

    def test_the_normal_checks_still_apply_to_it(self):
        with TempRepo() as repo, gate_state(runs={TASK: {}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            code, err = denial_reason(f"echo $( git push origin {WORK_BRANCH})", repo.path)
        self.assertEqual(2, code)
        self.assertIn(f"Push for {TASK} is not approved yet", err)
        self.assertNotIn("holds a shell closer", err)

    def test_a_trunk_push_in_a_substitution_is_the_trunk_not_a_closer_ref(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for command in self.TRUNK_PUSHES:
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn("Push to protected branch 'main'", err)

    def test_a_quoted_or_escaped_closer_in_a_substitution_stays_refused(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for command in self.QUOTED_IN_SUBSTITUTION:
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn("holds a shell closer", err)


class LeakGuardAndTrunkApprovalSeeTheRealRefTest(unittest.TestCase):
    def test_the_leak_guard_receives_the_uncut_source_of_a_glued_push(self):
        command = f"echo $(echo x; git push origin {WORK_BRANCH}:{WORK_BRANCH})/foo"
        with unittest.mock.patch.object(pretool_gate, "push_leak_reason",
                                        return_value="") as leak:
            verdict, _ = pretool_gate.check_push_target(command, WORK_BRANCH, [WORK_BRANCH], "")
        self.assertEqual(0, verdict)
        self.assertEqual([WORK_BRANCH], leak.call_args.args[2])

    def test_the_leak_guard_never_runs_on_a_cut_ref(self):
        command = "git push origin 'x)y:feature/task-9120'"
        with unittest.mock.patch.object(pretool_gate, "push_leak_reason",
                                        return_value="") as leak:
            verdict, _ = pretool_gate.check_push_target(command, "main", ["x"], "")
        self.assertEqual(2, verdict)
        leak.assert_not_called()

    def test_the_trunk_approval_is_asked_about_the_uncut_trunk_ref(self):
        command = "echo $(echo x; git push origin main)/foo"
        with unittest.mock.patch.object(pretool_gate, "trunk_push_approved",
                                        return_value=True) as approved, \
                unittest.mock.patch.object(pretool_gate, "push_leak_reason", return_value=""):
            verdict, spend = pretool_gate.check_push_target(command, "main", ["main"], "")
        self.assertEqual((0, True), (verdict, spend))
        approved.assert_called_once_with("main", "origin")


if __name__ == "__main__":
    unittest.main()
