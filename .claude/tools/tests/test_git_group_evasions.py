"""Grouped and parenthesis-glued git against the commit and push gates (task-0109).

`git_invocations` is the one place every git gate asks "which subcommand runs?"
(commit approval, push approval, protected-branch push deny, the dev / readonly /
docs mutation checks). It scanned the flat shlex tokens, and `pad_separators` does
not pad parentheses, so `(git commit -m x)` tokenised as `(git` and no `git` token
existed at all. A trailing `)` stayed glued to the last word, so
`(cd d && git push origin main)` resolved the target `main)`, which is no
protected branch. The same glue hid `--waive)` from `agent_gate.runs_waive`.

These tests pin the repair once per spelling: the invocation is resolved with its
exact argv, the real gates deny it, read-only git in the same spellings stays
allowed, and quoted or substituted parentheses are not rewritten.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import pretool_gate
from test_pretool_gate_git import TempRepo, denial_reason, gate_state

WORK_BRANCH = "bugfix/task-9109"
TASK = "task-9109"

# @G@ is the plain git command the template wraps in shell syntax.
GROUPING = (
    "(@G@)",
    "( @G@ )",
    "((@G@))",
    "(@G@);",
    "(@G@)&",
    "(@G@) 2>&1",
    "(@G@) | cat",
    "(@G@) && echo done",
    "echo x && (@G@)",
    "echo x; (@G@)",
    "(echo x; @G@)",
    "(echo x && @G@)",
    "(cd d && @G@)",
    "(cd d && @G@) ; echo done",
    "! (@G@)",
    "{ @G@; }",
    "{ (@G@); }",
    "if true; then @G@; fi",
    "if true; then (@G@); fi",
    "if (@G@); then echo ok; fi",
    "for x in a; do @G@; done",
    "for x in a; do (@G@); done",
    "while true; do @G@; done",
    "until false; do (@G@); done",
    "if false; then echo no; elif true; then (@G@); fi",
    "(timeout 30 @G@)",
    "(env @G@)",
    "(FOO=1 @G@)",
)
# Same plain command behind a wrapper, no group: already seen before task-0109,
# pinned so the rework of git_invocations cannot lose them.
WRAPPED = (
    "timeout 30 @G@",
    "env @G@",
    "FOO=1 @G@",
    "sudo -u root @G@",
    "nice -n 10 @G@",
    "xargs @G@",
    "echo x && @G@",
    "@G@ && echo x",
    "@G@; echo x",
)

COMMIT = "git commit -m x"
PUSH_MAIN = "git push origin main"
PUSH_WORK = f"git push origin {WORK_BRANCH}"

READ_ONLY = (
    "git status",
    "git log --oneline",
    "git diff",
    "git branch --show-current",
    "git merge-base --is-ancestor origin/main HEAD",
)


def render(template: str, git: str) -> str:
    return template.replace("@G@", git)


def invocations(command: str, sub: str) -> list:
    return [args for name, args in pretool_gate.git_invocations(command) if name == sub]


def run_profile(name: str, command: str) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = getattr(agent_gate, name)("Bash", {"command": command}, cwd=".")
    return code, err.getvalue()


class GroupedGitIsResolvedTest(unittest.TestCase):

    def test_a_grouped_commit_is_seen_with_its_exact_argv(self):
        for template in GROUPING + WRAPPED:
            command = render(template, COMMIT)
            with self.subTest(command=command):
                self.assertIn(["-m", "x"], invocations(command, "commit"))
                self.assertTrue(pretool_gate.git_invokes(command, "commit"))

    def test_a_grouped_push_is_seen_without_the_closing_paren_in_its_target(self):
        for template in GROUPING + WRAPPED:
            command = render(template, PUSH_MAIN)
            with self.subTest(command=command):
                self.assertIn(["origin", "main"], invocations(command, "push"))
                self.assertEqual(["main"] * len(invocations(command, "push")),
                                 pretool_gate.push_target_branches(command, ""))

    def test_the_five_forms_of_the_task_are_resolved(self):
        for command, sub, args in (
                ("(git commit -m x)", "commit", ["-m", "x"]),
                ("(git push origin main)", "push", ["origin", "main"]),
                ("{ git commit -m x; }", "commit", ["-m", "x"]),
                ("if true; then git push; fi", "push", []),
                ("for x in a; do git commit -m x; done", "commit", ["-m", "x"])):
            with self.subTest(command=command):
                self.assertEqual([(sub, args)], pretool_gate.git_invocations(command))

    def test_a_global_option_inside_a_group_does_not_hide_the_subcommand(self):
        self.assertEqual([("push", ["origin", "main"])],
                         pretool_gate.git_invocations("(git -C d push origin main)"))
        self.assertEqual([("commit", ["-m", "x"])],
                         pretool_gate.git_invocations("( git -c user.name=x commit -m x )"))

    def test_two_grouped_calls_yield_two_entries(self):
        self.assertEqual(
            [("status", []), ("push", ["origin", "main"])],
            pretool_gate.git_invocations("(git status) && (git push origin main)"))
        self.assertEqual(
            [("status", []), ("commit", ["-m", "x"])],
            pretool_gate.git_invocations("(git status; git commit -m x)"))

    def test_read_only_git_in_a_group_is_resolved_and_is_not_a_commit_or_push(self):
        for git in READ_ONLY:
            for template in GROUPING:
                command = render(template, git)
                with self.subTest(command=command):
                    self.assertFalse(pretool_gate.git_invokes(command, "commit", "push"))

    def test_git_without_a_subcommand_in_a_group_is_resolved_not_unknown(self):
        self.assertEqual([], pretool_gate.git_invocations("(git --version)"))

    def test_an_unbalanced_quote_in_a_group_is_still_gated(self):
        self.assertTrue(pretool_gate.git_unparseable("(git commit -m 'x)"))


class GroupedGitIsGatedTest(unittest.TestCase):
    """The real gates, driven on a real repo, in every spelling."""

    def test_a_grouped_commit_is_denied_until_the_commit_is_approved(self):
        with TempRepo() as repo, gate_state(runs={TASK: {}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for template in GROUPING:
                command = render(template, COMMIT)
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn(f"Commit for {TASK} is not approved yet", err)

    def test_a_grouped_commit_is_allowed_once_the_commit_is_approved(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"commit_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for template in GROUPING:
                command = render(template, COMMIT)
                with self.subTest(command=command):
                    self.assertEqual(0, denial_reason(command, repo.path)[0])

    def test_a_grouped_push_to_a_protected_branch_is_denied(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for name in ("main", "master", "staging", "production"):
                for template in GROUPING:
                    command = render(template, f"git push origin {name}")
                    with self.subTest(command=command):
                        code, err = denial_reason(command, repo.path)
                        self.assertEqual(2, code, err)
                        self.assertIn(f"Push to protected branch '{name}'", err)

    def test_a_grouped_push_to_a_work_branch_needs_the_push_approval(self):
        with TempRepo() as repo, gate_state(runs={TASK: {}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for template in GROUPING:
                command = render(template, PUSH_WORK)
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn(f"Push for {TASK} is not approved yet", err)
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for template in GROUPING:
                command = render(template, PUSH_WORK)
                with self.subTest(command=command, approved=True):
                    self.assertEqual(0, denial_reason(command, repo.path)[0])

    def test_read_only_git_in_every_spelling_stays_allowed(self):
        with TempRepo() as repo, gate_state(runs={TASK: {}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for git in READ_ONLY:
                for template in GROUPING + WRAPPED:
                    command = render(template, git)
                    with self.subTest(command=command):
                        code, err = denial_reason(command, repo.path)
                        self.assertEqual(0, code, err)


class GroupedGitUnderAgentProfilesTest(unittest.TestCase):

    MUTATING = ("git commit -m x", "git push origin main", "git add -A")

    def test_readonly_and_docs_deny_a_grouped_mutating_git(self):
        for git in self.MUTATING:
            for template in GROUPING:
                command = render(template, git)
                for name in ("handle_readonly", "handle_docs"):
                    with self.subTest(profile=name, command=command):
                        code, err = run_profile(name, command)
                        self.assertEqual(2, code, err)

    def test_readonly_and_docs_allow_grouped_read_only_git(self):
        for git in READ_ONLY:
            for template in GROUPING:
                command = render(template, git)
                for name in ("handle_readonly", "handle_docs"):
                    with self.subTest(profile=name, command=command):
                        code, err = run_profile(name, command)
                        self.assertEqual(0, code, err)

    def test_a_grouped_force_push_is_still_a_force_push(self):
        self.assertTrue(agent_gate.is_force_push("(git push --force origin x)"))
        self.assertTrue(agent_gate.is_force_push("{ git push -f origin x; }"))
        self.assertFalse(agent_gate.is_force_push("(git push origin x)"))


class GroupParenthesesAreNotRewrittenElsewhereTest(unittest.TestCase):
    """Only parentheses that GROUP commands are padded. A substitution, a process
    substitution, an array, an escaped paren and anything quoted keep their text,
    because a consumer reads them as one word."""

    def test_a_group_is_padded_on_both_sides(self):
        close = pretool_gate.GROUP_CLOSE
        self.assertEqual(f"( git status {close}", pretool_gate.pad_separators("(git status)").strip())
        self.assertEqual(["(", "(", "git", "status", close, close],
                         [t for t in pretool_gate.pad_separators("((git status))").split(" ") if t])

    def test_substitutions_arrays_and_escapes_keep_their_text(self):
        for command in ("echo $(date)", "echo $((1+2))", "cat <(ls)", "tee >(cat)",
                        "a=(x y)", "a+=(x y)", r"find . \( -name x \)",
                        "echo $(echo (a))"):
            with self.subTest(command=command):
                self.assertEqual(command, pretool_gate.pad_separators(command))

    def test_quoted_parentheses_keep_their_text(self):
        for command in ('echo "(git commit)"', "echo '(git push)'", 'git commit -m "fix (x)"'):
            with self.subTest(command=command):
                self.assertEqual(command, pretool_gate.pad_separators(command))

    def test_a_quoted_group_text_is_no_git_call(self):
        self.assertEqual([], pretool_gate.git_invocations('echo "(git commit -m x)"'))
        self.assertEqual([("commit", ["-m", "fix (x)"])],
                         pretool_gate.git_invocations('git commit -m "fix (x)"'))

    def test_a_group_after_a_closed_substitution_is_padded_again(self):
        # The substitution's frame must be popped at its `)`, or every later group
        # in the command would count as inside it and stay glued.
        for command in ("echo $(date) && (git commit -m x)", "x=$(date); (git commit -m x)",
                        "cat <(ls) | (git commit -m x)", "a=(x y); (git commit -m x)"):
            with self.subTest(command=command):
                self.assertIn(("commit", ["-m", "x"]), pretool_gate.git_invocations(command))

    def test_a_quoted_argument_before_the_closing_paren_is_one_token(self):
        self.assertEqual([("commit", ["-m", "fix (x)"])],
                         pretool_gate.git_invocations('(git commit -m "fix (x)")'))
        self.assertEqual([("push", ["origin", "a b"])],
                         pretool_gate.git_invocations("(git push origin 'a b')"))

    def test_an_unmatched_closer_such_as_a_case_pattern_is_padded(self):
        close = pretool_gate.GROUP_CLOSE
        padded = [t for t in pretool_gate.pad_separators("a) b").split(" ") if t]
        self.assertEqual(["a", close, "b"], padded)
        self.assertIn(("push", ["origin", "main"]),
                      pretool_gate.git_invocations("case x in a) git push origin main ;; esac"))

    def test_a_substituted_git_is_still_found_by_the_nested_body(self):
        self.assertTrue(pretool_gate.git_invokes("echo $(git commit -m x)", "commit"))
        self.assertTrue(pretool_gate.git_invokes("x=$(git push origin main)", "push"))

    def test_segments_drop_group_syntax(self):
        self.assertEqual([("", ["git", "commit", "-m", "x"])],
                         pretool_gate.segments_with_separators("(git commit -m x)"))
        self.assertEqual([("", ["a"]), ("&&", ["b", "c"])],
                         pretool_gate.segments_with_separators("(a) && (b c)"))
        self.assertEqual([("", ["a"]), ("|", ["b", "x"])],
                         pretool_gate.segments_with_separators("(a) | (b x)"))


class ProcessSubstitutionTest(unittest.TestCase):
    """`<( ... )` and `>( ... )` run their body as a command, with the paren glued
    to the operator: the same blind spot as `(git commit -m x)`, one spelling over."""

    TEMPLATES = ("cat <(@G@)", "diff <(@G@) <(echo x)", "echo x > >(@G@)", "tee >(@G@) < x",
                 "cat <(cat <(@G@))")
    # An output form is also a file write, which readonly and docs deny on their own.
    INPUT_TEMPLATES = ("cat <(@G@)", "diff <(@G@) <(echo x)", "cat <(cat <(@G@))")

    def test_a_process_substituted_commit_or_push_is_seen(self):
        for template in self.TEMPLATES:
            with self.subTest(command=render(template, COMMIT)):
                self.assertTrue(pretool_gate.git_invokes(render(template, COMMIT), "commit"))
            with self.subTest(command=render(template, PUSH_MAIN)):
                self.assertTrue(pretool_gate.git_invokes(render(template, PUSH_MAIN), "push"))

    def test_the_gates_deny_a_process_substituted_commit_and_push(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for template in self.TEMPLATES:
                commit = render(template, COMMIT)
                push = render(template, PUSH_MAIN)
                with self.subTest(command=commit):
                    code, err = denial_reason(commit, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn(f"Commit for {TASK} is not approved yet", err)
                with self.subTest(command=push):
                    self.assertEqual(2, denial_reason(push, repo.path)[0])

    def test_readonly_and_docs_deny_a_process_substituted_mutation(self):
        for template in self.TEMPLATES:
            for git in ("git commit -m x", "git push origin main"):
                for name in ("handle_readonly", "handle_docs"):
                    command = render(template, git)
                    with self.subTest(profile=name, command=command):
                        self.assertEqual(2, run_profile(name, command)[0])

    def test_process_substituted_read_only_git_stays_allowed(self):
        for template in self.INPUT_TEMPLATES:
            for git in READ_ONLY:
                command = render(template, git)
                for name in ("handle_readonly", "handle_docs"):
                    with self.subTest(profile=name, command=command):
                        self.assertEqual(0, run_profile(name, command)[0])

    def test_quoted_and_arithmetic_lookalikes_are_not_process_substitution(self):
        for command in ('echo "<(git commit -m x)"', "echo '>(git push origin main)'",
                        "echo $((1>(2)))"):
            with self.subTest(command=command):
                self.assertEqual([], pretool_gate.substitution_bodies(command))
                self.assertFalse(pretool_gate.git_invokes(command, "commit", "push"))

    def test_a_process_substituted_waive_is_seen(self):
        self.assertTrue(agent_gate.runs_waive(
            f"cat <(python {GluedWaiveTest.HANDOFF} --waive)"))


class QuotedParenIsAnArgumentTest(unittest.TestCase):
    """A quoted `")"` is a word, not a group's closer. Dropping it shifted the operand
    of a git option that takes a value, so `git -C ")" push origin main` resolved to
    ('origin', ['main']) and the push and commit gates stopped firing."""

    def test_a_quoted_paren_operand_of_a_git_option_does_not_shift_the_subcommand(self):
        for command, sub, args in (
                ('git -C ")" push origin main', "push", ["origin", "main"]),
                ('git --git-dir ")" push origin main', "push", ["origin", "main"]),
                ('git --work-tree ")" commit -m x', "commit", ["-m", "x"]),
                ('git -c ")" commit -m x', "commit", ["-m", "x"]),
                ('(git -C ")" push origin main)', "push", ["origin", "main"]),
                ("git -C ')' push origin main", "push", ["origin", "main"])):
            with self.subTest(command=command):
                self.assertEqual([(sub, args)], pretool_gate.git_invocations(command))

    def test_the_gates_still_fire_for_a_quoted_paren_operand(self):
        self.assertTrue(pretool_gate.git_invokes('git -C ")" commit -m x', "commit"))
        self.assertEqual(["main"],
                         pretool_gate.push_target_branches('git -C ")" push origin main', ""))

    def test_a_quoted_paren_stays_in_the_args(self):
        self.assertEqual([("commit", ["-m", ")"])],
                         pretool_gate.git_invocations('git commit -m ")"'))
        self.assertEqual([("commit", ["-m", ")"])],
                         pretool_gate.git_invocations('(git commit -m ")")'))
        self.assertEqual([("push", ["origin", ")"])],
                         pretool_gate.git_invocations("git push origin ')'"))

    def test_a_forged_closer_marker_in_the_text_is_not_a_group_closer(self):
        marker = pretool_gate.GROUP_CLOSE
        self.assertEqual([("push", ["origin", "main"])],
                         pretool_gate.git_invocations(f'git -C "{marker}" push origin main'))

    def test_the_group_closer_marker_never_reaches_a_segment(self):
        for command in ("(git commit -m x)", "(a) && (b)", "case x in a) git status ;; esac"):
            with self.subTest(command=command):
                for _, seg in pretool_gate.segments_with_separators(command):
                    self.assertNotIn(pretool_gate.GROUP_CLOSE, seg)


class EscapesAndNewlinesTest(unittest.TestCase):
    """A paren is escaped only after an ODD run of backslashes, and a newline ends
    every open substitution frame."""

    HANDOFF = ".claude/tools/pipeline/handoff.py"

    def test_an_even_run_of_backslashes_does_not_escape_the_closing_paren(self):
        command = "echo $(echo a\\\\)\n(python " + self.HANDOFF + " --waive)"
        self.assertTrue(agent_gate.runs_waive(command))

    def test_a_later_group_is_padded_after_an_even_backslash_run(self):
        command = "cat <(echo a\\\\); (git push origin main)"
        self.assertIn(("push", ["origin", "main"]), pretool_gate.git_invocations(command))
        self.assertEqual(["main"], pretool_gate.push_target_branches(command, ""))

    def test_an_odd_run_of_backslashes_still_escapes_the_paren(self):
        for command in (r"find . \( -name x \)", r"echo a\\\)", r"echo \(a"):
            with self.subTest(command=command):
                self.assertEqual(command, pretool_gate.pad_separators(command))

    def test_a_newline_ends_an_unclosed_substitution_frame(self):
        command = "echo $(echo a\n(git commit -m x)"
        self.assertIn(("commit", ["-m", "x"]), pretool_gate.git_invocations(command))


class SyntaxThatIsNoGitStaysAllowedTest(unittest.TestCase):
    """Function definitions, arithmetic and multi-line groups meet the paren padding."""

    COMMANDS = (
        "f() { echo hi; }; f",
        "f() { git diff; }; f",
        "((x++))",
        "for ((i=0;i<2;i++)); do echo $i; done",
        "(\ngit diff\n)",
        "(\ngit status\ngit log\n)",
        "x=$((1+2)); echo $x",
    )

    def test_every_profile_allows_them(self):
        for command in self.COMMANDS:
            for name in ("handle_readonly", "handle_docs", "handle_dev"):
                with self.subTest(profile=name, command=command):
                    code, err = run_profile(name, command)
                    self.assertEqual(0, code, err)

    def test_the_main_thread_gate_allows_them(self):
        with TempRepo() as repo, gate_state(runs={TASK: {}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for command in self.COMMANDS:
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(0, code, err)


class GluedWaiveTest(unittest.TestCase):
    """`--waive)` was one token, so the exact-token compare in runs_waive missed it."""

    HANDOFF = ".claude/tools/pipeline/handoff.py"

    def test_a_glued_waive_closer_is_seen(self):
        for template in GROUPING:
            command = render(template, f"python {self.HANDOFF} --waive")
            with self.subTest(command=command):
                self.assertTrue(agent_gate.runs_waive(command))

    def test_the_spaced_spelling_is_still_seen(self):
        self.assertTrue(agent_gate.runs_waive(f"( python {self.HANDOFF} --waive )"))

    def test_a_glued_waive_with_a_value_is_seen(self):
        self.assertTrue(agent_gate.runs_waive(f"(python {self.HANDOFF} --waive --reason x)"))
        self.assertTrue(agent_gate.runs_waive(f"(timeout 30 python {self.HANDOFF} --task t --waive)"))

    def test_a_substituted_waive_is_still_seen(self):
        self.assertTrue(agent_gate.runs_waive(f"echo $(python {self.HANDOFF} --waive)"))

    def test_every_profile_denies_a_glued_waive(self):
        command = f"(python {self.HANDOFF} --waive)"
        for name in ("handle_dev", "handle_readonly", "handle_docs"):
            with self.subTest(profile=name):
                code, err = run_profile(name, command)
                self.assertEqual(2, code, err)
                self.assertIn("--waive is orchestrator-only", err)

    def test_checking_a_handoff_without_waive_stays_allowed(self):
        for template in GROUPING:
            command = render(template, f"python {self.HANDOFF} --check")
            with self.subTest(command=command):
                self.assertFalse(agent_gate.runs_waive(command))
        self.assertFalse(agent_gate.runs_waive(f"(git log -- {self.HANDOFF} --waive-not)"))
        self.assertFalse(agent_gate.runs_waive(f"(echo --waive); python {self.HANDOFF} --check"))


if __name__ == "__main__":
    unittest.main()
