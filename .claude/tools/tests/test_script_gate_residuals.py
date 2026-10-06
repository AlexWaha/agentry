"""Residuals of the task-0100 and task-0109 reviews, pinned once per spelling (task-0110).

Each class below is one item of the task and every denied spelling in it was ALLOWED
on the code before the fix:

  1. a group's closer followed by a redirect (`{ cat x; } 2>&1 | python -`)
  2. a multi-command group as the source of a pipe (`{ cat x; echo; } | python -`)
  3. GNU abbreviations of `cp --target-directory` (`--target=`, `--target-dir=`)
  4. GNU option permutation, an option behind the destination of `cp`
  5. wrappers outside the closed list (`ionice`, `taskset`, `flock`)
  6. a git behind a separator inside a substitution (`$(echo x; git push origin main)`)
  7. a control character that stands in for the group-closer marker, glued into a word
  8. a lone CR inside a word, which shlex splits on and bash does not

The allowed spellings (reads, names, plain pipes) are pinned next to them: a fix that
denies everything is not a fix.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

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
from test_script_gate_evasions import PROFILES, SCRIPTS, render, run_profile

WORK_BRANCH = "bugfix/task-9110"
TASK = "task-9110"
US = pretool_gate.GROUP_CLOSE_MARK


def assert_script_denied(case: unittest.TestCase, templates: tuple, profiles: tuple = PROFILES):
    for script, (_, phrase) in SCRIPTS.items():
        for template in templates:
            command = render(template, script)
            for name in profiles:
                with case.subTest(script=script, profile=name, command=command):
                    code, err = run_profile(name, command)
                    case.assertEqual(2, code, err)
                    case.assertIn(phrase, err)


def assert_script_allowed(case: unittest.TestCase, templates: tuple, scripts: tuple):
    for script in scripts:
        for template in templates:
            command = render(template, script)
            for name in PROFILES:
                with case.subTest(script=script, profile=name, command=command):
                    code, err = run_profile(name, command)
                    case.assertEqual(0, code, err)


READ_SCRIPTS = ("approvals.py", "mode.py")  # approve.py has no read form


class CloserWithRedirectTest(unittest.TestCase):
    """Item 1."""

    DENIED = (
        "{ cat {S}; } 2>&1 | python - {A}",
        "{ cat {S}; } 2>&1 |& python - {A}",
        "{ cat {S}; } 2>&1 | cat | python - {A}",
        "{ cat {S}; } >&2 | python - {A}",
        "{ cat {S}; } 2>&1 | timeout 30 python - {A}",
        "if true; then cat {S}; fi 2>&1 | python - {A}",
        "for i in 1; do cat {S}; done 2>&1 | python - {A}",
        "( cat {S} ) 2>&1 | python - {A}",
    )
    ALLOWED = (
        "{ python {S} --show; } 2>&1 | cat",
        "{ python {S} --show; } 2>&1",
        "{ python {S} --show; } >&2 | cat",
        "if true; then python {S} --show; fi 2>&1 | cat",
        "for i in 1; do python {S} --show; done 2>&1 | head -5",
        "{ cat {S}; } 2>&1 | cat",
        "{ cat {S}; } 2>&1 | head -5",
    )

    def test_a_group_closer_with_a_redirect_does_not_break_the_pipe_chain(self):
        assert_script_denied(self, self.DENIED)

    def test_reading_through_a_redirected_group_stays_allowed(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_a_closer_followed_only_by_redirects_is_no_segment(self):
        for tokens in (["}", "2>&1"], ["fi", ">", "x"], ["done", ">&2"], ["}", "<", "f"]):
            with self.subTest(tokens=tokens):
                self.assertEqual([], pretool_gate.ungroup(tokens))
        for tokens in (["}", "x"], ["done", "x", "2>&1"], ["fi", "-v"]):
            with self.subTest(tokens=tokens):
                self.assertEqual(tokens, pretool_gate.ungroup(tokens))

    def test_the_pipe_source_survives_a_redirected_closer(self):
        self.assertEqual(
            [("", ["cat", "x/approve.py"]), ("|", ["python", "-"])],
            pretool_gate.segments_with_separators("{ cat x/approve.py; } 2>&1 | python -"))


class MultiCommandGroupPipeSourceTest(unittest.TestCase):
    """Item 2."""

    DENIED = (
        "{ cat {S}; echo; } | python - {A}",
        "{ echo; cat {S}; } | python - {A}",
        "{ cat {S}; echo; } |& python - {A}",
        "{ cat {S}; echo; } 2>&1 | python - {A}",
        "{ cat {S}; echo; } | cat | python - {A}",
        "{ cat {S}; echo; } | timeout 30 python - {A}",
        "{ cat {S} && echo; } | python - {A}",
        "{ cat {S} || echo; } | python - {A}",
        "{ cat {S}\necho; } | python - {A}",
        "{ cat {S}; echo; echo; } | python - {A}",
        "{ { cat {S}; echo; }; echo; } | python - {A}",
        "{ echo; { cat {S}; echo; }; } | python - {A}",
        "{ { cat {S}; }; echo; } | python - {A}",
        "( cat {S}; echo ) | python - {A}",
        "( cat {S}; echo; ) | python - {A}",
        "(cat {S}; echo) | python - {A}",
        "( ( cat {S}; echo ); echo ) | python - {A}",
        "if true; then cat {S}; echo; fi | python - {A}",
        "if true; then echo; cat {S}; fi | python - {A}",
        "for i in 1; do cat {S}; echo; done | python - {A}",
        "while true; do cat {S}; echo; done | python - {A}",
        "until false; do cat {S}; echo; done | python - {A}",
        "echo x | { cat {S}; echo; } | python - {A}",
        "cat x | { head -1; cat {S}; } | python - {A}",
    )
    ALLOWED = (
        "{ python {S} --show; echo; } 2>&1 | cat",
        "{ python {S} --show; echo; } | cat",
        "( python {S} --show; echo ) | cat",
        "{ python {S} --show; echo; } | python -c 'import sys'",
        "{ python {S} --show; echo; } |& python -m json.tool",
        "if true; then python {S} --show; echo; fi | cat",
        "for i in 1; do python {S} --show; echo; done | head -5",
        "{ cat {S}; echo; } | cat",
        "{ cat {S}; echo; } 2>&1 | head -5",
        "( cat {S}; echo ) | cat | cat",
        "{ cat {S}; echo; } && python -c 'import sys'",
        "{ cat {S}; echo; }; python -c 'import sys'",
        "{ cat {S}; echo; } || python -c 'import sys'",
        "{ cat {S}; echo; }\npython -c 'import sys'",
        "echo x | { cat; echo; } | python -c 'import sys'",
        "python -c 'import sys' | { cat {S}; echo; }",
    )

    def test_every_command_of_a_group_feeding_an_interpreter_is_a_pipe_source(self):
        assert_script_denied(self, self.DENIED)

    def test_reading_and_naming_around_a_multi_command_group_stays_allowed(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_the_piped_operand_of_every_group_member_counts_as_executed(self):
        for command in ("{ cat x/approve.py; echo; } | python -",
                        "{ echo; cat x/approve.py; } | python -",
                        "( cat x/approve.py; echo ) | python -",
                        "if true; then cat x/approve.py; echo; fi | python -",
                        "for i in 1; do cat x/approve.py; echo; done | python -"):
            with self.subTest(command=command):
                self.assertIn("x/approve.py", pretool_gate.executed_names(command))
                self.assertTrue(pretool_gate.runs_approve_script(command))

    def test_a_group_followed_by_a_plain_separator_feeds_nothing(self):
        for command in ("{ cat x/approve.py; echo; }; python -",
                        "{ cat x/approve.py; echo; } && python -",
                        "( cat x/approve.py; echo ); python -"):
            with self.subTest(command=command):
                self.assertNotIn("x/approve.py", pretool_gate.executed_names(command))

    def test_pipe_sources_names_each_member_of_the_group_and_the_chain_behind_it(self):
        pairs = pretool_gate.segments_with_separators("cat a | { head -1; echo; } | python -")
        self.assertEqual([0, 1, 2], sorted(pretool_gate.pipe_sources(pairs, 3)))
        pairs = pretool_gate.segments_with_separators("{ cat a; echo; } | python -")
        self.assertEqual([0, 1], sorted(pretool_gate.pipe_sources(pairs, 2)))
        pairs = pretool_gate.segments_with_separators("{ cat a; echo; }; python -")
        self.assertEqual([], pretool_gate.pipe_sources(pairs, 2))

    def test_the_pairs_stay_a_plain_list_of_separator_and_tokens(self):
        pairs = pretool_gate.segments_with_separators("{ cat x; echo; } | python -")
        self.assertEqual([("", ["cat", "x"]), (";", ["echo"]), ("|", ["python", "-"])], pairs)
        self.assertEqual([(0, 1)], pairs.groups)

    def test_a_single_command_group_records_no_span(self):
        for command in ("{ cat x; } | python -", "(cat x) | python -", "if true; then cat x; fi"):
            with self.subTest(command=command):
                spans = pretool_gate.segments_with_separators(command).groups
                self.assertTrue(all(first < last for first, last in spans), spans)


class CopyTargetAbbreviationTest(unittest.TestCase):
    """Item 3."""

    DENIED = (
        "cp --target=.agentry/state x",
        "cp --target-dir=.agentry/state x",
        "cp --target-d=.agentry/state x",
        "cp --t=.agentry/state x",
        "cp --target .agentry/state x",
        "cp --target-dir .agentry/state x",
        "cp --target-directory .agentry/state x",
        "cp -v --target=.agentry/state tmp/x tmp/y",
        "cp tmp/x --target=.agentry/state",
        "bash -c 'cp --target=.agentry/state x'",
    )
    ALLOWED = (
        "cp --target=tmp .agentry/state/approvals",
        "cp --target-dir=tmp .agentry/state/approvals .agentry/state/mode",
        "cp --target tmp .agentry/state/approvals",
        "cp --target-directory=tmp .agentry/state/approvals",
        "cp --no-target-directory .agentry/state/approvals tmp/copy",
        "cp -t tmp .agentry/state/approvals",
    )

    def test_an_abbreviated_target_directory_option_is_the_destination(self):
        for command in self.DENIED:
            with self.subTest(command=command):
                code, err = run_profile("handle_dev", command)
                self.assertEqual(2, code, err)
                self.assertIn("orchestrator", err)

    def test_copying_the_state_out_through_an_abbreviation_is_allowed(self):
        for command in self.ALLOWED:
            with self.subTest(command=command):
                code, err = run_profile("handle_dev", command)
                self.assertEqual(0, code, err)

    def test_target_directory_reads_every_unambiguous_prefix(self):
        for option in ("--t", "--ta", "--target", "--target-", "--target-dir", "--target-directory"):
            with self.subTest(option=option):
                self.assertEqual(["d"], agent_gate.target_directory([f"{option}=d", "x"]))
                self.assertEqual(["d"], agent_gate.target_directory([option, "d", "x"]))
        for option in ("--no-target-directory", "--tar-get=d", "--verbose", "--targets=d"):
            with self.subTest(option=option):
                self.assertEqual([], agent_gate.target_directory([option, "d", "x"]))


class CopyOptionPermutationTest(unittest.TestCase):
    """Item 4."""

    DENIED = (
        "cp tmp/x .agentry/state/approvals -v",
        "cp tmp/x .agentry/state/approvals --verbose",
        "cp tmp/x .agentry/state/approvals -f -v",
        "cp tmp/x .agentry/state/approvals -S .bak",
        "cp tmp/x .agentry/state/approvals -S.bak",
        "cp tmp/x .agentry/state/approvals -vS .bak",
        "cp tmp/x .agentry/state/approvals --suffix .bak",
        "cp tmp/x .agentry/state/approvals --suffix=.bak",
        "cp tmp/x .agentry/state/approvals --suf .bak",
        "cp tmp/x .agentry/state/approvals --sparse never",
        "cp tmp/x .agentry/state/approvals --no-preserve mode",
        "cp tmp/x .agentry/state/approvals --backup",
        "cp tmp/x .agentry/state/approvals --backup=numbered",
        "cp -v tmp/x .agentry/state/approvals",
        "cp tmp/x -v .agentry/state/approvals",
        "cp -S .bak tmp/x .agentry/state/approvals",
        "cp -- tmp/x .agentry/state/approvals",
        "cp tmp/x .agentry/state/approvals -v 2>&1",
    )
    ALLOWED = (
        "cp .agentry/state/approvals tmp/copy -v",
        "cp .agentry/state/approvals tmp/copy --verbose",
        "cp .agentry/state/approvals tmp/copy -S .bak",
        "cp .agentry/state/approvals tmp/copy --suffix=.bak",
        "cp .agentry/state/approvals -v tmp/copy",
        "cp .agentry/state/approvals -S .bak tmp/copy",
        "cp -v .agentry/state/approvals tmp/copy",
        "cp -S .bak .agentry/state/approvals tmp/copy",
        "cp -- .agentry/state/approvals tmp/copy",
        "cp -t tmp .agentry/state/approvals",
        "cp .agentry/state/approvals .agentry/state/mode tmp -t tmp",
    )

    def test_an_option_behind_the_destination_is_not_the_destination(self):
        for command in self.DENIED:
            with self.subTest(command=command):
                code, err = run_profile("handle_dev", command)
                self.assertEqual(2, code, err)
                self.assertIn("orchestrator", err)

    def test_the_state_as_a_source_stays_allowed_in_every_option_order(self):
        for command in self.ALLOWED:
            with self.subTest(command=command):
                code, err = run_profile("handle_dev", command)
                self.assertEqual(0, code, err)

    def test_the_destination_is_the_last_operand_that_is_no_option_or_option_value(self):
        for args, want in ((["a", "b", "-v"], ["b"]),
                           (["a", "b", "-S", ".bak"], ["b"]),
                           (["a", "b", "--suffix", ".bak"], ["b"]),
                           (["a", "b", "--suffix=.bak"], ["b"]),
                           (["-S", ".bak", "a", "b"], ["b"]),
                           (["-vS", ".bak", "a", "b"], ["b"]),
                           (["-S.bak", "a", "b"], ["b"]),
                           (["--", "a", "-b"], ["-b"]),
                           (["a"], ["a"]),
                           ([], [])):
            with self.subTest(args=args):
                self.assertEqual(want, agent_gate.destination_operand(args))


class OpenWrapperListTest(unittest.TestCase):
    """Item 5."""

    DENIED = (
        "ionice -c3 python {S} {A}",
        "ionice -c 3 python {S} {A}",
        "ionice -c2 -n7 python {S} {A}",
        "ionice -c3 {S} {A}",
        "taskset -c 0 python {S} {A}",
        "taskset 0x1 python {S} {A}",
        "taskset -c 0 {S} {A}",
        "flock f python {S} {A}",
        "flock -n f python {S} {A}",
        "flock -w 5 f python {S} {A}",
        "chrt -f 10 python {S} {A}",
        "numactl --cpunodebind=0 python {S} {A}",
        "doas python {S} {A}",
        "unbuffer python {S} {A}",
        "ionice -c3 timeout 30 python {S} {A}",
        "(ionice -c3 python {S} {A})",
        "cat {S} | ionice -c3 python - {A}",
        "cat {S} | flock f python - {A}",
    )
    ALLOWED = (
        "ionice -c3 python {S} --show",
        "taskset -c 0 python {S} --show",
        "flock f python {S} --show",
        "flock -n f python {S}",
        "ionice -c3 git show HEAD -- {S}",
        "taskset -c 0 cat {S}",
        "flock f git log -p -- {S}",
        "ionice -c3 python {S} --show | cat",
    )

    def test_a_wrapper_outside_the_old_list_does_not_hide_the_script(self):
        assert_script_denied(self, self.DENIED)

    def test_reading_and_naming_behind_those_wrappers_stays_allowed(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_the_wrapper_is_not_the_command_that_runs(self):
        for command in ("ionice -c3 python s.py a", "taskset -c 0 python s.py a",
                        "flock f python s.py a", "chrt -f 10 python s.py a"):
            with self.subTest(command=command):
                self.assertEqual(["python", "s.py"], pretool_gate.exec_names_of(command.split())[-2:])


class SubstitutionSeparatorTest(unittest.TestCase):
    """Item 6."""

    PLAIN = (
        "echo $(echo x; @G@)",
        "echo $(echo x && @G@)",
        "echo $(true || @G@)",
        "echo $(echo x | @G@)",
        "echo `echo x; @G@`",
        "echo `echo x && @G@`",
        "cat <(echo x; @G@)",
        "cat >(echo x; @G@)",
        "x=$(echo x; @G@)",
        "echo $(echo x; @G@) && echo y",
        "echo $(echo $(echo x; @G@))",
        "echo $( echo x; @G@ )",
        "echo $(echo x; (@G@))",
        "echo $(echo x; { @G@; })",
        "(echo $(echo x; @G@))",
        "{ echo $(echo x; @G@); }",
        "echo $(echo x; @G@)>f",
        "echo $(echo x; @G@) 2>&1",
        "cat <(echo x; @G@) | cat",
    )

    def test_a_push_behind_a_separator_in_a_substitution_resolves_without_the_closer(self):
        for template in self.PLAIN:
            command = template.replace("@G@", "git push origin main")
            with self.subTest(command=command):
                targets = pretool_gate.push_target_branches(command, "")
                self.assertIn("main", targets or [])
                self.assertEqual(["main"] * len(targets), targets)

    def test_a_commit_behind_a_separator_in_a_substitution_keeps_its_last_argument(self):
        for template in self.PLAIN:
            command = template.replace("@G@", 'git commit -m "x"')
            with self.subTest(command=command):
                self.assertIn(("commit", ["-m", "x"]), pretool_gate.git_invocations(command))

    def test_the_protected_branch_push_deny_fires_in_every_spelling(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for name in ("main", "master", "staging", "production"):
                for template in self.PLAIN:
                    command = template.replace("@G@", f"git push origin {name}")
                    with self.subTest(command=command):
                        code, err = denial_reason(command, repo.path)
                        self.assertEqual(2, code, err)
                        self.assertIn(f"Push to protected branch '{name}'", err)

    def test_a_work_branch_push_in_a_substitution_is_gated_by_its_approval_only(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for template in self.PLAIN:
                command = template.replace("@G@", f"git push origin {WORK_BRANCH}")
                with self.subTest(command=command):
                    self.assertEqual(0, denial_reason(command, repo.path)[0])
        with TempRepo() as repo, gate_state(runs={TASK: {}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for template in self.PLAIN:
                command = template.replace("@G@", f"git push origin {WORK_BRANCH}")
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn(f"Push for {TASK} is not approved yet", err)

    def test_read_only_git_in_a_substitution_is_neither_a_push_nor_a_commit(self):
        for git in ("git status", "git log --oneline", "git branch --show-current"):
            for template in self.PLAIN:
                command = template.replace("@G@", git)
                with self.subTest(command=command):
                    self.assertFalse(pretool_gate.git_invokes(command, "commit", "push"))

    def test_a_substitution_closer_ending_a_word_is_its_own_token(self):
        for command in ("echo $(echo x; git push origin main)", "echo `echo x; git push origin main`",
                        "cat <(echo x; git push origin main)"):
            with self.subTest(command=command):
                self.assertEqual([("push", ["origin", "main"])], pretool_gate.git_invocations(command))

    def test_a_substitution_closer_glued_to_a_path_stays_part_of_the_word(self):
        # `python $(pwd)/x/approve.py` names its script in ONE word; splitting the
        # closer off would leave `$(pwd` as the script operand and hide approve.py.
        for command in ("python $(pwd)/x/approve.py auto", "python `pwd`/x/approve.py auto",
                        "python $(pwd).claude/approve.py auto",
                        "python $(cd d; pwd)/x/approve.py auto", "python `cd d; pwd`/x/approve.py auto",
                        "python $(cd d && pwd).claude/approve.py auto"):
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.runs_approve_script(command))

    def test_a_quoted_or_escaped_paren_is_never_a_closer(self):
        self.assertEqual([("push", ["origin", "main"])],
                         pretool_gate.git_invocations('git -C ")" push origin main'))
        self.assertEqual([("commit", ["-m", "fix (x)"])],
                         pretool_gate.git_invocations('echo $(echo x; git commit -m "fix (x)")'))


class ControlCharacterTest(unittest.TestCase):
    """Items 7 and 8: a control character is a word character to bash, so it must
    never split a word, stand in for a marker, or vanish and swallow its neighbour."""

    # The characters shlex and bash disagree on (CR) and the one the group closer
    # marker borrows (US), plus the rest of C0 and DEL for generality.
    CONTROLS = ("\x1f", "\r", "\x01", "\x07", "\x0b", "\x0c", "\x1b", "\x7f")
    IN_A_WORD = ("python -X a{C}b {S} {A}", "python -W a{C}b {S} {A}")
    AS_A_WORD = ("python -X {C} {S} {A}", "python -W {C} {S} {A}")

    @staticmethod
    def spell(template: str, char: str) -> str:
        return template.replace("{C}", char)

    def test_a_control_character_inside_a_word_does_not_hide_the_script(self):
        for char in self.CONTROLS:
            assert_script_denied(self, tuple(self.spell(t, char) for t in self.IN_A_WORD))

    def test_a_control_character_as_a_whole_word_does_not_hide_the_script(self):
        for char in self.CONTROLS:
            assert_script_denied(self, tuple(self.spell(t, char) for t in self.AS_A_WORD))

    def test_the_group_closer_marker_cannot_be_forged_in_or_out_of_quotes(self):
        for command in (f"python -X {US}) x/approve.py auto", f'python -X "{US})" x/approve.py auto',
                        f"python -X '{US})' x/approve.py auto", f"python -X a{US}) x/approve.py auto"):
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.runs_approve_script(command))
        for command in (f'git -C "{US})" push origin main', f"git -C {US}) push origin main",
                        f"git -C '{US})' push origin main"):
            with self.subTest(command=command):
                self.assertEqual([("push", ["origin", "main"])], pretool_gate.git_invocations(command))

    def test_a_control_character_inside_a_git_option_value_does_not_hide_the_subcommand(self):
        for char in self.CONTROLS:
            for git, sub, args in (("git -c user.name=a{C}b commit -m x", "commit", ["-m", "x"]),
                                   ("git -C a{C}b push origin main", "push", ["origin", "main"]),
                                   ("git -c {C} commit -m x", "commit", ["-m", "x"]),
                                   ("git -C {C} push origin main", "push", ["origin", "main"])):
                command = self.spell(git, char)
                with self.subTest(command=command):
                    self.assertEqual([(sub, args)], pretool_gate.git_invocations(command))

    def test_the_commit_and_push_gates_fire_on_a_control_character_in_a_word(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for char in ("\x1f", "\r"):
                command = f"git -c user.name=a{char}b commit -m x"
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn(f"Commit for {TASK} is not approved yet", err)
                command = f"git -C a{char}b push origin main"
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)
                    self.assertIn("Push to protected branch 'main'", err)

    def test_a_carriage_return_that_ends_a_word_or_a_line_is_a_line_ending_not_a_word(self):
        # CRLF scripts: the CR belongs to no word, so the command is seen as written.
        for command, sub, args in (("git status\r\ngit push origin main\r\n", "push", ["origin", "main"]),
                                   ("git push origin main\r", "push", ["origin", "main"]),
                                   ("git push\r origin main", "push", ["origin", "main"]),
                                   ("git commit -m x\r\n", "commit", ["-m", "x"]),
                                   ("git commit\r\n-m x", "commit", [])):
            with self.subTest(command=command):
                self.assertIn((sub, args), pretool_gate.git_invocations(command))
        assert_script_denied(self, ("python {S} {A}\r\n", "python {S}\r {A}",
                                    "{ cat {S}; echo; } | python - {A}\r\n"))

    def test_a_control_character_inside_quotes_leaves_the_word_whole(self):
        for char in ("\x01", "\x1f", "\r", "\t"):
            with self.subTest(char=repr(char)):
                found = pretool_gate.git_invocations(f'git commit -m "a{char}b"')
                self.assertEqual(["commit"], [sub for sub, _ in found])
                self.assertEqual(2, len(found[0][1]))

    def test_tab_and_newline_keep_their_meaning(self):
        self.assertEqual([("push", ["origin", "main"])],
                         pretool_gate.git_invocations("git\tpush\torigin\tmain"))
        self.assertEqual([("status", []), ("push", ["origin", "main"])],
                         pretool_gate.git_invocations("git status\ngit push origin main"))

    def test_a_plain_command_is_not_rewritten(self):
        for command in ("git push origin main", "python x/approve.py auto", "echo 'a b' | cat"):
            with self.subTest(command=command):
                self.assertEqual(command, pretool_gate.normalise_controls(command))


if __name__ == "__main__":
    unittest.main()
