"""Duplicating redirect spellings and a bare NUL for dev agents (task-0095).

Found by the reviewer of task-0074 (finding M2) and probed, not inferred.
`REDIR_RE` knew only `>` and `>>`, so `>&word`, `1>&word`, `>|word` and `<>word`
returned no target at all. In bash `>&word` with a non-numeric word is the
both-streams FILE redirect, not a descriptor duplication, so `echo x >&src/app.py`
let a read-only agent overwrite source, and `ls >&NUL` slipped past the
task-0074 fix. `handle_dev` never consulted the classifier, so a dev or qa agent
could still create the NUL entry Windows cannot delete.

The same shared function serves agent_gate (aliased) and the orchestrator gate,
so these tests drive every handler, not one.
"""

from __future__ import annotations

import io
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "pipeline"))

import agent_gate  # noqa: E402 - path set above
import pretool_gate  # noqa: E402

FOUR_OPERATORS = (">&", "1>&", ">|", "<>")
DUPLICATIONS = ("make 2>&1", "echo x >&2", "echo x 1>&2", "ls >&-", "ls 2>&-",
                "echo x >&2-", "echo x 2>&1 >&2", "ls >& 2")
CASES = ("NUL", "nul", "Nul")


def run(handler, command: str) -> tuple[int, str]:
    err = io.StringIO()
    with unittest.mock.patch("sys.stderr", err):
        code = handler("Bash", {"command": command})
    return code, err.getvalue()


class FourOperatorsAreFileWritesTest(unittest.TestCase):

    def test_the_classifier_returns_the_real_target_for_each_operator(self):
        for op in FOUR_OPERATORS:
            with self.subTest(op=op):
                self.assertEqual(f"{op}x", pretool_gate.redirect_write_target(f"echo hi {op}x"))

    def test_a_space_between_the_operator_and_the_target_is_still_a_write(self):
        self.assertEqual(">& x", pretool_gate.redirect_write_target("echo hi >& x"))

    def test_a_readonly_agent_is_denied_a_source_overwrite_through_each_operator(self):
        for op in FOUR_OPERATORS:
            with self.subTest(op=op):
                code, msg = run(agent_gate.handle_readonly, f"echo x {op}src/app.py")
                self.assertEqual(2, code, f"readonly was ALLOWED to write with {op!r}")
                self.assertIn("src/app.py", msg)

    def test_a_docs_agent_is_denied_a_source_overwrite_through_each_operator(self):
        for op in FOUR_OPERATORS:
            with self.subTest(op=op):
                code, _ = run(agent_gate.handle_docs, f"echo x {op}src/app.py")
                self.assertEqual(2, code, f"docs was ALLOWED to write with {op!r}")

    def test_the_operator_is_still_caught_inside_a_nested_shell(self):
        self.assertEqual(">&x", pretool_gate.redirect_write_target("bash -c 'echo hi >&x'"))

    def test_the_orchestrator_may_not_overwrite_source_through_an_ampersand_redirect(self):
        with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
            code, _ = run(lambda t, ti: pretool_gate.orch_check_bash(ti["command"]),
                          "echo x >&src/app.py")
        self.assertEqual(2, code)

    def test_the_orchestrator_may_still_write_bookkeeping_through_an_ampersand_redirect(self):
        with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
            code, _ = run(lambda t, ti: pretool_gate.orch_check_bash(ti["command"]),
                          "echo x >&docs/notes.md")
        self.assertEqual(0, code)


class TrueDescriptorDuplicationIsNotAWriteTest(unittest.TestCase):

    def test_the_classifier_reports_no_target(self):
        for command in DUPLICATIONS:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.redirect_write_target(command))

    def test_a_readonly_agent_is_not_denied_them(self):
        for command in DUPLICATIONS:
            with self.subTest(command=command):
                self.assertEqual(0, run(agent_gate.handle_readonly, command)[0])


class DescriptorLookingTargetOnAPlainRedirectIsAWriteTest(unittest.TestCase):
    """Only an `&` operator can duplicate a descriptor. After a plain `>`, `>>`,
    `2>` or `>|` the words `2`, `1`, `-` and `3` are file NAMES. The
    `op.endswith("&")` guard in redirect_write_fragments is the only thing that
    keeps them from being read as dups (task-0095 rework 2)."""

    PLAIN = ("echo x > 2", "echo x >> 1", "echo x 2> 1", "echo x > -", "echo x >| 3")

    def test_the_classifier_returns_a_target_for_each(self):
        for command in self.PLAIN:
            with self.subTest(command=command):
                self.assertNotEqual("", pretool_gate.redirect_write_target(command))

    def test_a_readonly_agent_is_denied_each(self):
        for command in self.PLAIN:
            with self.subTest(command=command):
                self.assertEqual(2, run(agent_gate.handle_readonly, command)[0],
                                 f"readonly was ALLOWED {command!r}")


class DuplicationBeforeACloserIsNotAWriteTest(unittest.TestCase):
    """`)` or a backtick closing a command substitution is glued to the target
    class, so `$(ls 2>&1)` read the target `1)`, missed DUP_TARGET_RE and became a
    file write: readonly and docs were denied it, and so was the main thread's
    `out=$(cmd 2>&1)`, the form BARE_NUL_NOTE recommends."""

    CLOSED = ("out=$(ls 2>&1)", "(ls 2>&1)", "(ls >&2)", "echo `ls 2>&1`",
              "echo `ls >&2`", "echo $(ls >&2)", "out=$(ls 2>&-)")
    STILL_FILES = ("echo $(ls >&x)", "echo $(ls >&src/app.py)", "(ls >&2-x)",
                   "echo $(ls >&$fd)", "echo `ls >&x`")

    def test_the_classifier_reports_no_target_before_a_closer(self):
        for command in self.CLOSED:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.redirect_write_target(command))

    def test_readonly_docs_and_dev_agents_are_not_denied_them(self):
        for handler in (agent_gate.handle_readonly, agent_gate.handle_docs,
                        agent_gate.handle_dev):
            for command in self.CLOSED:
                with self.subTest(handler=handler.__name__, command=command):
                    self.assertEqual(0, run(handler, command)[0])

    def test_the_orchestrator_may_capture_with_a_substitution(self):
        with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
            code, _ = run(lambda t, ti: pretool_gate.orch_check_bash(ti["command"]),
                          "out=$(cmd 2>&1)")
        self.assertEqual(0, code)

    def test_a_non_descriptor_word_before_a_closer_is_still_a_file(self):
        for command in self.STILL_FILES:
            with self.subTest(command=command):
                self.assertNotEqual("", pretool_gate.redirect_write_target(command))

    def test_a_readonly_agent_is_still_denied_a_source_overwrite_before_a_closer(self):
        self.assertEqual(2, run(agent_gate.handle_readonly, "echo $(ls >&src/app.py)")[0])


class BareNulIsDeniedForDevAgentsTest(unittest.TestCase):

    def assert_denied_as_a_real_file(self, handler, command: str) -> None:
        code, msg = run(handler, command)
        self.assertEqual(2, code, f"a redirect to a bare NUL was ALLOWED: {command!r}")
        self.assertIn("real file", msg)
        self.assertNotIn("discard", msg)

    def test_a_dev_agent_is_denied_a_bare_nul_through_every_operator_and_case(self):
        for name in CASES:
            for op in (">", "2>", ">>", "&>", ">&", "1>&", ">|", "<>"):
                with self.subTest(command=f"ls {op}{name}"):
                    self.assert_denied_as_a_real_file(agent_gate.handle_dev, f"ls {op}{name}")

    def test_a_dev_agent_is_denied_a_bare_nul_that_follows_a_legitimate_write(self):
        self.assert_denied_as_a_real_file(agent_gate.handle_dev, "ls > out.txt 2>NUL")

    def test_a_dev_agent_is_denied_a_bare_nul_in_a_quoted_target_and_a_nested_shell(self):
        for command in ('ls > "NUL"', "bash -c 'ls 2>NUL'", "echo $(ls 2>nul)"):
            with self.subTest(command=command):
                self.assert_denied_as_a_real_file(agent_gate.handle_dev, command)

    def test_a_readonly_agent_is_denied_the_ampersand_spelling_with_the_real_file_note(self):
        for name in CASES:
            with self.subTest(name=name):
                self.assert_denied_as_a_real_file(agent_gate.handle_readonly, f"ls >&{name}")
                self.assert_denied_as_a_real_file(agent_gate.handle_docs, f"ls >|{name}")

    def test_the_orchestrator_gate_names_the_real_file_for_the_ampersand_spelling(self):
        with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
            code, msg = run(lambda t, ti: pretool_gate.orch_check_bash(ti["command"]),
                            "ls >&NUL")
        self.assertEqual(2, code)
        self.assertIn("real file", msg)


def main_thread(command: str) -> tuple[int, str]:
    with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
        return run(lambda t, ti: pretool_gate.handle_bash(ti["command"], "", orch=True), command)


class GluedNulRedirectIsDeniedInEveryProfileTest(unittest.TestCase):
    """REDIR_RE refused a word glued to the operator, so `ls>NUL` was never a
    fragment and slipped past every NUL deny. The CEO rule is NUL in no profile.
    The general glued-redirect classifier (`echo hi>src/app.py`) and the glued
    path-qualified NUL are exercised in test_glued_redirects (task-0102)."""

    GLUED = ("ls>NUL", "x>nul", "cmd>NUL 2>&1", "ls>&NUL", "ls>|NUL", "ls 2>&1>NUL",
             "ls&>NUL", "ls>>NUL", "ls>\"NUL\"", "bash -c 'ls>NUL'", "echo $(ls>NUL)",
             "ls 2>&1 && ls>NUL")

    def assert_denied_as_a_real_file(self, run_it, command: str) -> None:
        code, msg = run_it(command)
        self.assertEqual(2, code, f"a glued redirect to NUL was ALLOWED: {command!r}")
        self.assertIn("real file", msg)
        self.assertNotIn("discard", msg)

    def test_every_profile_denies_every_glued_spelling(self):
        runners = {
            "dev": lambda c: run(agent_gate.handle_dev, c),
            "readonly": lambda c: run(agent_gate.handle_readonly, c),
            "docs": lambda c: run(agent_gate.handle_docs, c),
            "main thread": main_thread,
        }
        for profile, run_it in runners.items():
            for command in self.GLUED:
                with self.subTest(profile=profile, command=command):
                    self.assert_denied_as_a_real_file(run_it, command)

    def test_names_that_only_contain_nul_are_not_a_redirect_to_it(self):
        for command in ("echo x>nullable.py", "cat null>x", "echo x>NULL", "echo x>nul.txt",
                        "echo x>./NUL", "echo x>NUL.bak", "echo x>annul", "ls -> nul"):
            with self.subTest(command=command):
                self.assertFalse(pretool_gate.redirects_to_bare_nul(command))

    def test_a_sentence_or_heredoc_about_nul_is_not_a_redirect(self):
        for command in ('memory.py --fix "never write 2>NUL"', "echo 'ls>NUL is a real file'",
                        "cat <<EOF\nls>NUL\nEOF"):
            with self.subTest(command=command):
                self.assertFalse(pretool_gate.redirects_to_bare_nul(command))
                self.assertEqual(0, run(agent_gate.handle_dev, command)[0])

    def test_a_dev_agent_may_still_use_glued_writes_and_duplications(self):
        for command in ("echo x>out.txt", "make 2>&1", "echo x>&2"):
            with self.subTest(command=command):
                self.assertEqual(0, run(agent_gate.handle_dev, command)[0])


class PathQualifiedNulIsDeniedForDevAgentsTest(unittest.TestCase):
    """The undeletable entry is the NAME, so a directory prefix changes nothing."""

    QUALIFIED = ("ls > ./NUL", "ls > src/NUL", "ls > /tmp/NUL", "ls 2> sub/dir/nul",
                 "ls >> a\\b\\NUL", "ls >&./NUL", "ls > '/tmp/Nul'", "echo $(ls 2>./NUL)")
    OTHER_NAMES = ("./NUL.bak", "src/nul.txt", "/tmp/nullable", "./NULL", "a/b/annul")

    def test_a_dev_agent_is_denied_the_nul_name_under_any_directory(self):
        for command in self.QUALIFIED:
            with self.subTest(command=command):
                code, msg = run(agent_gate.handle_dev, command)
                self.assertEqual(2, code, f"a path-qualified NUL was ALLOWED: {command!r}")
                self.assertIn("real file", msg)

    def test_a_dev_agent_may_write_other_names_under_a_directory(self):
        for name in self.OTHER_NAMES:
            with self.subTest(name=name):
                self.assertEqual(0, run(agent_gate.handle_dev, f"echo x > {name}")[0])


class LegitimateDevWritesStayAllowedTest(unittest.TestCase):

    def test_a_dev_agent_may_write_a_normal_file(self):
        for command in ("echo x > out.txt", "echo x >> out.txt", "echo x >&out.txt",
                        "echo x >|out.txt", "echo x > out.txt 2>&1"):
            with self.subTest(command=command):
                self.assertEqual(0, run(agent_gate.handle_dev, command)[0])

    def test_a_dev_agent_may_write_names_that_merely_contain_nul(self):
        for name in ("nullable.py", "annul.txt", "nul.txt", "NULL", "./NUL.bak"):
            for op in (">", ">&", ">|"):
                with self.subTest(command=f"echo x {op}{name}"):
                    self.assertEqual(0, run(agent_gate.handle_dev, f"echo x {op}{name}")[0])

    def test_a_dev_agent_may_duplicate_descriptors(self):
        for command in DUPLICATIONS:
            with self.subTest(command=command):
                self.assertEqual(0, run(agent_gate.handle_dev, command)[0])


if __name__ == "__main__":
    unittest.main()
