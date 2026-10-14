"""Residuals of the task-0110 review, pinned once per spelling (task-0112).

Every denied spelling below was ALLOWED on the code before the fix. Items, as the task
numbers them:

  1. a substitution's closer was counted as a group's closer, so the group ended early
  2. a group as the sink of a pipe (`cat x | { echo; python - auto; }`)
  3. a redirect on a group's closer (`{ python - auto; } < x`, `done < x`)
  4. a newline right behind a pipe broke the chain
  5. a case pattern's `)` was counted as a group's closer
  6. a closer glued to a path read the push target `main)/foo`
  7. `flock f x` run directly, and the `-c` bodies of `flock` and `su`

The allowed spellings (reads, names, plain pipes) sit next to them: a fix that denies
everything is not a fix. After task-0116 the readonly and docs profiles refuse an
interpreter outright, so for them items 1-5 and 7 pin segmenter correctness; the dev
profile, which may run python, is where they still decide a verdict.
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

import pretool_gate
from test_pretool_gate_git import TempRepo, denial_reason, gate_state
from test_script_gate_residuals import READ_SCRIPTS, assert_script_allowed, assert_script_denied

WORK_BRANCH = "bugfix/task-9112"
TASK = "task-9112"
SUBST = pretool_gate.SUBST_CLOSE
PATTERN = pretool_gate.PATTERN_CLOSE
GROUP = pretool_gate.GROUP_CLOSE


class SubstitutionCloserDoesNotEndTheGroupTest(unittest.TestCase):
    """Item 1."""

    DENIED = (
        "{ cat {S}; echo $(echo a; echo b); echo z; } | python - {A}",
        "{ cat {S}; echo `echo a; echo b`; echo z; } | python - {A}",
        "{ cat {S}; echo $(echo a; echo b); } | python - {A}",
        "( cat {S}; echo $(echo a; echo b); echo z ) | python - {A}",
        "if true; then cat {S}; echo $(echo a; echo b); echo z; fi | python - {A}",
        "for i in 1; do cat {S}; echo $(echo a; echo b); echo z; done | python - {A}",
        "{ cat {S}; echo $(echo a; echo b) && echo z; } | python - {A}",
        "{ echo $(echo a; echo b); cat {S}; echo z; } | python - {A}",
    )
    ALLOWED = (
        "{ python {S} --show; echo $(echo a; echo b); echo z; } | cat",
        "{ cat {S}; echo $(echo a; echo b); echo z; } | cat",
        "{ cat {S}; echo $(echo a; echo b); echo z; }; python -c 'import sys'",
        "{ cat {S}; echo $(echo a; echo b); echo z; } && python -c 'import sys'",
    )

    def test_a_substitution_holding_a_separator_keeps_the_group_open(self):
        assert_script_denied(self, self.DENIED)

    def test_reading_around_such_a_group_stays_allowed(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_the_substitution_marker_closes_no_group(self):
        self.assertEqual((0, 0), pretool_gate.group_marks(["echo", SUBST]))
        self.assertEqual((0, 0), pretool_gate.group_marks(["echo", PATTERN]))
        self.assertEqual((0, 1), pretool_gate.group_marks(["echo", GROUP]))

    def test_the_group_spans_cover_every_member(self):
        pairs = pretool_gate.segments_with_separators(
            "{ cat x; echo $(echo a; echo b); echo z; } | python -")
        self.assertEqual([(0, 3)], pairs.groups)
        pairs = pretool_gate.segments_with_separators(
            "{ cat x; echo `echo a; echo b`; echo z; } | python -")
        self.assertEqual([(0, 3)], pairs.groups)

    def test_a_substitution_marker_never_reaches_a_segment(self):
        for command in ("echo $(echo a; echo b)", "echo `echo a; echo b`", "{ echo $(echo a; echo b); }"):
            with self.subTest(command=command):
                for _, seg in pretool_gate.segments_with_separators(command):
                    for marker in (SUBST, PATTERN, GROUP):
                        self.assertNotIn(marker, seg)

    def test_a_substitution_closer_is_padded_with_its_own_marker(self):
        padded = pretool_gate.pad_separators("echo $(echo a; echo b)")
        self.assertIn(SUBST, padded)
        self.assertNotIn(GROUP, padded)


class GroupAsPipeSinkTest(unittest.TestCase):
    """Item 2."""

    DENIED = (
        "cat {S} | { echo; python - {A}; }",
        "cat {S} | { python - {A}; }",
        "cat {S} | ( echo; python - {A} )",
        "cat {S} | (echo; python - {A})",
        "cat {S} |& { echo; python - {A}; }",
        "cat {S} | cat | { echo; python - {A}; }",
        "cat {S} | { echo; echo; python - {A}; }",
        "cat {S} | { python - {A}; echo; }",
        "cat {S} | if true; then python - {A}; fi",
        "cat {S} | while true; do python - {A}; done",
        "cat {S} | for i in 1; do python - {A}; done",
        "cat {S} | { { echo; python - {A}; }; }",
        "cat {S} | { echo; } | { python - {A}; }",
    )
    ALLOWED = (
        "cat {S} | { echo; cat; }",
        "cat {S} | { head -1; }",
        "{ echo; python {S} --show; } | cat",
        "echo x | { echo; python {S} --show; }",
        "cat {S} | cat; { echo; python -c 'import sys'; }",
        "cat {S}; { echo; python -c 'import sys'; }",
        "cat {S} && { echo; python -c 'import sys'; }",
    )

    def test_a_command_inside_a_group_behind_a_pipe_reads_the_pipe(self):
        assert_script_denied(self, self.DENIED)

    def test_reading_and_naming_around_a_group_sink_stays_allowed(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_the_pipe_sources_of_a_group_member_reach_past_the_group(self):
        pairs = pretool_gate.segments_with_separators("cat a | cat | { echo; python - ; }")
        self.assertEqual([0, 1], sorted(set(pretool_gate.pipe_sources(pairs, 3))))
        pairs = pretool_gate.segments_with_separators("cat a | { echo; python - ; }")
        self.assertEqual([0], sorted(set(pretool_gate.pipe_sources(pairs, 2))))

    def test_a_group_behind_a_plain_separator_feeds_nothing(self):
        for command in ("cat x/approve.py; { echo; python -; }", "cat x/approve.py && { echo; python -; }"):
            with self.subTest(command=command):
                self.assertNotIn("x/approve.py", pretool_gate.executed_names(command))

    def test_the_group_sink_names_the_piped_operand_as_executed(self):
        for command in ("cat x/approve.py | { echo; python -; }", "cat x/approve.py | ( echo; python - )"):
            with self.subTest(command=command):
                self.assertIn("x/approve.py", pretool_gate.executed_names(command))
                self.assertTrue(pretool_gate.runs_approve_script(command))


class GroupStdinTest(unittest.TestCase):
    """Item 3."""

    DENIED = (
        "{ python - {A}; } < {S}",
        "{ python - {A}; } <{S}",
        "{ echo; python - {A}; } < {S}",
        "{ python - {A}; echo; } < {S}",
        "{ python - {A}; } 2>&1 < {S}",
        "( python - {A} ) < {S}",
        "( python - {A}; echo ) < {S}",
        "( echo; python - {A} ) < {S}",
        "while true; do python - {A}; done < {S}",
        "until false; do python - {A}; done < {S}",
        "for i in 1; do python - {A}; done < {S}",
        "if true; then python - {A}; fi < {S}",
        "if true; then echo; python - {A}; fi < {S}",
        "{ { python - {A}; }; } < {S}",
        "{ python - {A}; } < {S} | cat",
        "{ python - {A}; } < {S} && echo done",
    )
    ALLOWED = (
        "{ python {S} --show; } < notes.txt",
        "{ cat; } < {S}",
        "{ echo; cat; } < {S}",
        "while read l; do echo \"$l\"; done < {S}",
        "for i in 1; do cat; done < {S}",
        "if true; then cat; fi < {S}",
        "( cat ) < {S}",
        "{ python -c 'import sys'; } 2>&1",
        "{ python {S} --show; } 2>&1",
    )

    def test_a_redirect_on_a_group_is_the_stdin_of_the_interpreter_inside(self):
        assert_script_denied(self, self.DENIED)

    def test_feeding_a_group_to_something_that_runs_nothing_stays_allowed(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_the_redirect_is_handed_to_the_interpreter_only(self):
        pairs = pretool_gate.segments_with_separators("{ echo; python - auto; } < x/approve.py")
        self.assertEqual([("", ["echo"]), (";", ["python", "-", "auto", "<", "x/approve.py"])], pairs)

    def test_a_group_redirect_is_not_handed_to_a_group_it_does_not_close(self):
        pairs = pretool_gate.segments_with_separators("{ python - ; }; python -c x < y")
        self.assertEqual([("", ["python", "-"]), (";", ["python", "-c", "x", "<", "y"])], pairs)

    def test_a_script_named_on_a_group_closer_is_executed(self):
        for command in ("{ python -; } < x/approve.py", "while true; do python -; done < x/approve.py",
                        "if true; then python -; fi < x/approve.py"):
            with self.subTest(command=command):
                self.assertIn("x/approve.py", pretool_gate.executed_names(command))
                self.assertTrue(pretool_gate.runs_approve_script(command))

    def test_the_redirect_tokens_behind_a_closer(self):
        self.assertEqual(["<", "f"], pretool_gate.closer_redirects(["}", "<", "f"]))
        self.assertEqual(["2>&1", "<", "f"], pretool_gate.closer_redirects(["done", "2>&1", "<", "f"]))
        self.assertEqual(["<", "f"], pretool_gate.closer_redirects(["python", "-", GROUP, "<", "f"]))
        self.assertEqual([], pretool_gate.closer_redirects(["python", "-"]))


class NewlineAfterPipeTest(unittest.TestCase):
    """Item 4."""

    DENIED = (
        "cat {S} |\npython - {A}",
        "cat {S} |\n  python - {A}",
        "cat {S} |\n\npython - {A}",
        "cat {S} |\n\t python - {A}",
        "cat {S} |&\npython - {A}",
        "cat {S} |\ncat |\npython - {A}",
        "cat {S} |\n{ echo; python - {A}; }",
        "{ cat {S}; echo; } |\npython - {A}",
        "cat {S} | \npython - {A}",
    )
    ALLOWED = (
        "cat {S} |\ncat",
        "cat {S} |\nhead -5",
        "cat {S};\npython -c 'import sys'",
        "cat {S}\npython -c 'import sys'",
        "cat {S} &&\npython -c 'import sys'",
        "cat {S} ||\npython -c 'import sys'",
        "cat {S} &\npython -c 'import sys'",
        "python {S} --show |\ncat",
    )

    def test_a_newline_behind_a_pipe_continues_the_pipeline(self):
        assert_script_denied(self, self.DENIED)

    def test_other_newlines_still_end_the_command(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_the_pipe_chain_survives_the_newline(self):
        self.assertEqual([("", ["cat", "x"]), ("|", ["python", "-"])],
                         pretool_gate.segments_with_separators("cat x |\npython -"))
        self.assertEqual([("", ["cat", "x"]), ("|", ["cat"]), ("|", ["python", "-"])],
                         pretool_gate.segments_with_separators("cat x |\ncat |\npython -"))

    def test_a_newline_behind_a_list_separator_is_a_separator_of_its_own(self):
        self.assertEqual([("", ["a"]), ("&&", ["b"])], pretool_gate.segments_with_separators("a &&\nb"))
        self.assertEqual([("", ["a"]), (";", ["b"])], pretool_gate.segments_with_separators("a\nb"))
        self.assertEqual([("", ["a"]), (";", ["b"])], pretool_gate.segments_with_separators("a;\nb"))
        self.assertEqual([("", ["a"]), (";", ["b"])], pretool_gate.segments_with_separators("a &\nb"))

    def test_a_newline_inside_quotes_is_untouched(self):
        self.assertEqual([("", ["echo", "a |\nb"])], pretool_gate.segments_with_separators('echo "a |\nb"'))

    def test_a_git_behind_a_pipe_and_a_newline_is_still_found(self):
        self.assertEqual([("push", ["origin", "main"])],
                         pretool_gate.git_invocations("echo x |\ngit push origin main"))


class CasePatternTest(unittest.TestCase):
    """Item 5."""

    DENIED = (
        "case x in a) cat {S};; esac | python - {A}",
        "case x in x) echo; cat {S};; esac | python - {A}",
        "case x in a) echo;; b) cat {S};; esac | python - {A}",
        "case x in a|b) cat {S};; esac | python - {A}",
        "case x in *) cat {S};; esac | python - {A}",
        "{ case x in a) echo;; esac; cat {S}; } | python - {A}",
        "{ cat {S}; case x in a) echo;; esac; } | python - {A}",
        "{ cat {S}; case x in a) echo;; esac; echo; } | python - {A}",
        "case x in a) echo;; esac; cat {S} | python - {A}",
        "case x in a) cat {S} | python - {A};; esac",
    )
    ALLOWED = (
        "case x in a) python {S} --show;; esac",
        "case x in a) python {S} --show;; esac | cat",
        "case x in a) cat {S};; esac | cat",
        "case x in a) cat {S};; esac; python -c 'import sys'",
        "case x in a) cat {S};; esac && python -c 'import sys'",
    )

    def test_a_case_statement_is_one_group_for_the_pipe_behind_it(self):
        assert_script_denied(self, self.DENIED)

    def test_reading_around_a_case_statement_stays_allowed(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_a_pattern_closer_does_not_end_the_group_around_the_case(self):
        pairs = pretool_gate.segments_with_separators(
            "{ cat x; case y in a) echo;; esac; echo z; } | python -")
        last = len(pairs) - 2
        self.assertEqual([(0, last)], [span for span in pairs.groups if span[0] == 0])

    def test_esac_closes_a_case_and_is_no_command(self):
        self.assertEqual([], pretool_gate.ungroup(["esac"]))
        self.assertEqual([], pretool_gate.ungroup(["esac", "2>&1"]))
        self.assertEqual(["esac", "x"], pretool_gate.ungroup(["esac", "x"]))
        self.assertEqual((1, 0), pretool_gate.group_marks(["case", "x", "in", "a"]))
        self.assertEqual((0, 1), pretool_gate.group_marks(["esac"]))

    def test_the_tokens_in_front_of_a_stray_paren_are_kept(self):
        # A `)` can be typed anywhere; it must not hide the git in front of it.
        self.assertIn(("push", ["origin", "main"]),
                      pretool_gate.git_invocations("git -C x) push origin main"))
        self.assertIn(("push", ["origin", "main"]),
                      pretool_gate.git_invocations("case x in a) git push origin main ;; esac"))


class GluedCloserPushTargetTest(unittest.TestCase):
    """Item 6: `$(...; git push origin main)/foo` read the target `main)/foo`."""

    TAILS = ("/foo", ".txt", "/", "/a/b", "\\\\x")
    SPELLINGS = (
        "echo $(echo x; git push origin {REF}){T}",
        "echo `echo x; git push origin {REF}`{T}",
        "x=$(echo x; git push origin {REF}){T}",
        "echo $(echo x; git push origin {REF}){T}; echo y",
        "cat <(echo x; git push origin {REF}){T}",
        "echo $(echo x && git push origin {REF}){T}",
        "echo $(echo x; (git push origin {REF})){T}",
        "echo $(echo x; git push origin HEAD:{REF}){T}",
        "echo $(echo x; git push origin +{REF}){T}",
    )

    def test_the_target_is_the_ref_without_the_closer_and_the_path_behind_it(self):
        for tail in self.TAILS:
            for template in self.SPELLINGS[:6]:
                command = template.replace("{REF}", "main").replace("{T}", tail)
                with self.subTest(command=command):
                    self.assertEqual(["main"], pretool_gate.push_target_branches(command, ""))

    def test_a_remote_and_a_double_dash_operand_are_cut_the_same_way(self):
        self.assertEqual("origin", pretool_gate.push_remote(["origin)/x", "main"]))
        self.assertEqual(["main"], pretool_gate.push_positionals(["--", "main)/x"]))
        self.assertEqual(["origin", "main"], pretool_gate.push_positionals(["origin", "main)/x"]))
        self.assertEqual(["origin"], pretool_gate.push_positionals(["origin", ")/x"]))

    def test_a_plain_ref_is_never_rewritten(self):
        for ref in ("main", "bugfix/task-9112", "refs/heads/main", "HEAD:main", "+main", "a.b-c_d"):
            with self.subTest(ref=ref):
                self.assertEqual(ref, pretool_gate.cut_closer(ref))
        self.assertEqual("main", pretool_gate.cut_closer("main)/foo"))
        self.assertEqual("main", pretool_gate.cut_closer("main`/foo"))
        self.assertEqual("", pretool_gate.cut_closer(")/foo"))

    def test_the_protected_branch_deny_fires_in_every_glued_spelling(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for name in ("main", "master", "staging", "production"):
                for tail in ("/foo", ".txt"):
                    for template in self.SPELLINGS:
                        command = template.replace("{REF}", name).replace("{T}", tail)
                        with self.subTest(command=command):
                            code, err = denial_reason(command, repo.path)
                            self.assertEqual(2, code, err)
                            self.assertIn(f"Push to protected branch '{name}'", err)

    def test_a_work_branch_push_in_a_glued_spelling_is_gated_by_its_approval_only(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for template in self.SPELLINGS[:6]:
                command = template.replace("{REF}", WORK_BRANCH).replace("{T}", "/foo")
                with self.subTest(command=command):
                    self.assertEqual(0, denial_reason(command, repo.path)[0])
        with TempRepo() as repo, gate_state(runs={TASK: {}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            command = f"echo $(echo x; git push origin {WORK_BRANCH})/foo"
            code, err = denial_reason(command, repo.path)
            self.assertEqual(2, code, err)
            self.assertIn(f"Push for {TASK} is not approved yet", err)

    def test_a_path_glued_to_a_closer_still_names_its_script(self):
        # The reason the closer stays in its word: `python $(pwd)/x/approve.py`.
        for command in ("python $(pwd)/x/approve.py auto", "python $(cd d; pwd)/x/approve.py auto",
                        "python `cd d; pwd`/x/approve.py auto"):
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.runs_approve_script(command))


class LockAndCommandOptionTest(unittest.TestCase):
    """Item 7."""

    DENIED = (
        "flock f {S} {A}",
        "flock -n f {S} {A}",
        "flock -w 5 f {S} {A}",
        "flock -x f {S} {A}",
        "flock 9 {S} {A}",
        "flock f python {S} {A}",
        "flock -w 5 f python {S} {A}",
        "flock -c 'python {S} {A}' f",
        "flock f -c 'python {S} {A}'",
        "flock -n f -c 'python {S} {A}'",
        "flock --command 'python {S} {A}' f",
        "flock --command='python {S} {A}' f",
        "su -c 'python {S} {A}'",
        "su root -c 'python {S} {A}'",
        "su -l root -c 'python {S} {A}'",
        "su - root -c 'python {S} {A}'",
        "su --command='python {S} {A}' root",
        "su -lc 'python {S} {A}'",
        "runuser -l root -c 'python {S} {A}'",
        "su -c \"cat {S} | python - {A}\"",
        "flock f -c 'cat {S} | python - {A}'",
        "sudo flock f python {S} {A}",
        "(flock f python {S} {A})",
        "cat {S} | flock f python - {A}",
    )
    ALLOWED = (
        "flock f python {S} --show",
        "flock -n f git log -p -- {S}",
        "flock f cat {S}",
        "flock -c 'cat {S}' f",
        "flock f -c 'git log -p -- {S}'",
        "su -c 'cat {S}'",
        "su root -c 'git show HEAD -- {S}'",
        "su -c 'python {S} --show'",
        "flock f ls",
    )

    def test_flock_and_su_run_what_they_are_handed(self):
        assert_script_denied(self, self.DENIED)

    def test_reading_and_naming_through_them_stays_allowed(self):
        assert_script_allowed(self, self.ALLOWED, READ_SCRIPTS)

    def test_the_lock_file_is_not_the_command(self):
        for tokens, want in ((["flock", "f", "x/approve.py"], "x/approve.py"),
                             (["flock", "-n", "f", "x/approve.py"], "x/approve.py"),
                             (["flock", "-w", "5", "f", "x/approve.py"], "x/approve.py"),
                             (["flock", "9", "x/approve.py"], "x/approve.py"),
                             (["flock", "-E", "3", "f", "x/approve.py"], "x/approve.py"),
                             (["sudo", "flock", "f", "x/approve.py"], "x/approve.py"),
                             (["flock", "f", "python", "x/approve.py"], "python")):
            with self.subTest(tokens=tokens):
                self.assertEqual(want, tokens[pretool_gate.argv0_index(tokens)])
        self.assertIsNone(pretool_gate.argv0_index(["flock", "f"]))

    def test_other_wrappers_keep_their_numeric_operand(self):
        for tokens in (["timeout", "30", "python"], ["nice", "-n", "10", "python"]):
            with self.subTest(tokens=tokens):
                self.assertEqual("python", tokens[pretool_gate.argv0_index(tokens)])

    def test_the_command_option_body_is_a_nested_command(self):
        for command, body in (("flock f -c 'a b'", "a b"), ("flock -c 'a b' f", "a b"),
                              ("su -c 'a b'", "a b"), ("su root -c 'a b'", "a b"),
                              ("su -lc 'a b'", "a b"), ("su --command='a b' root", "a b"),
                              ("flock --command 'a b' f", "a b"), ("runuser -l u -c 'a b'", "a b")):
            with self.subTest(command=command):
                self.assertEqual([body], pretool_gate.shell_c_bodies(command))
        for command in ("su root", "flock f ls", "flock f ls; echo -c x", "su root && echo -c x"):
            with self.subTest(command=command):
                self.assertEqual([], pretool_gate.shell_c_bodies(command))

    def test_a_git_push_inside_the_command_option_is_found(self):
        for command in ("su -c 'git push origin main'", "flock f -c 'git push origin main'",
                        "su root -c 'git commit -m x'"):
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.git_invokes(command, "push", "commit"))

    def test_the_protected_push_deny_fires_through_su_and_flock(self):
        with TempRepo() as repo, gate_state(runs={TASK: {"push_approved": 1}}):
            repo.commit()
            repo.checkout_new(WORK_BRANCH)
            for command in ("su -c 'git push origin main'", "flock f -c 'git push origin main'",
                            "su root -c \"git push origin main\"", "flock -n f git push origin main"):
                with self.subTest(command=command):
                    code, err = denial_reason(command, repo.path)
                    self.assertEqual(2, code, err)


if __name__ == "__main__":
    unittest.main()
