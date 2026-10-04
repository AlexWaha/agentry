"""A redirect glued to a word is still a redirect (task-0102).

`REDIR_RE` carried a lookbehind that refused a word character before the
operator, so `echo hi>src/app.py`, `cat a.txt>f`, `python gen.py>f`, `echo x>>f`,
`ls 2>&1>f` and (after mask_quoted turns the closing quote into `Q`)
`echo "hi">f` all returned no write target. A readonly or docs agent could
overwrite any file with them, and a glued path-qualified NUL (`ls>./NUL`) slipped
past every NUL deny. `orch_check_bash` also read only the FIRST write fragment,
so `echo x > docs/a.md; echo y > src/app.py` was allowed for the main thread.

The lookbehind still guards `->`, `-->` and the second `>` of `>>` / `<>`, so the
arrows, descriptor duplications and quoted text below must stay no-write.
"""

from __future__ import annotations

import io
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "pipeline"))

import agent_gate  # noqa: E402 - path set above
import pretool_gate  # noqa: E402

# glued command -> the fragment the classifier must report
GLUED = {
    "echo hi>src/app.py": ">src/app.py",
    "cat a.txt>f": ">f",
    "python gen.py>f": ">f",
    "echo x>>f": ">>f",
    "ls 2>&1>f": ">f",
    'echo "hi">f': ">f",
}

# The lookbehind task-0095 shipped, verbatim apart from the operator group.
PRE_FIX_REDIR_RE = re.compile(
    rf"(?<![-\w<>])(?P<op>{pretool_gate._REDIR_OP})\s*(?P<t>[^\s;|&<>]+)")

# Text that merely contains `>` or an arrow, or duplicates a descriptor.
NO_WRITE = (
    "echo a->b", "echo a-->b", "git log --graph --format=%h->%s", "make 2>&1", "echo x>&2",
    "ls 2>&1", "echo x 1>&2", "ls >&-", "echo 'k => v'", 'echo "k => v"', "echo 'x>y'",
    'echo "x>y"', "echo '->' 'a>b'", "git commit -m 'merge a>b into c'",
    'git commit -m "fix a->b => c"', 'python -c "print(1>0)"', "out=$(ls 2>&1)",
)

ORCH_TWO_FRAGMENTS = (
    "echo x > docs/a.md; echo y > src/app.py",
    "echo x>docs/a.md; echo y>src/app.py",
    "echo x > docs/a.md && echo y>>src/app.py",
    "echo x > docs/a.md\necho y > src/app.py",
    "echo x > docs/a.md && bash -c 'echo y > src/app.py'",
)


def run(handler, command: str) -> tuple[int, str]:
    err = io.StringIO()
    with unittest.mock.patch("sys.stderr", err):
        code = handler("Bash", {"command": command})
    return code, err.getvalue()


def main_thread(command: str) -> tuple[int, str]:
    with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
        return run(lambda t, ti: pretool_gate.handle_bash(ti["command"], "", orch=True), command)


def orchestrator_bash(command: str) -> tuple[int, str]:
    with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True):
        return run(lambda t, ti: pretool_gate.orch_check_bash(ti["command"]), command)


class PreFixLookbehindFoundNothingTest(unittest.TestCase):
    """Documents the defect on the old pattern, so the fix is not a rewrite of
    the question: the pre-fix lookbehind finds no redirect in any glued form, and
    the current REDIR_RE finds exactly the real one."""

    def test_the_pre_fix_pattern_matches_none_of_the_glued_forms(self):
        for command in GLUED:
            with self.subTest(command=command):
                masked = pretool_gate.mask_quoted(pretool_gate.strip_heredocs(command))
                found = [m.group("op") + m.group("t") for m in PRE_FIX_REDIR_RE.finditer(masked)]
                # `ls 2>&1>f` keeps its descriptor dup `2>&1`; no FILE target is found.
                self.assertEqual([], [f for f in found if not f.startswith("2>&")],
                                 f"the pre-fix pattern already saw {command!r}")

    def test_the_pre_fix_first_fragment_rule_let_a_second_redirect_through(self):
        # redirect_write_target() is the first fragment, which is all the old
        # orch_check_bash read: it is allowed, and only the second one is not.
        command = ORCH_TWO_FRAGMENTS[0]
        targets = [pretool_gate.redirect_target(f)
                   for f in pretool_gate.redirect_write_fragments(command)]
        self.assertEqual(["docs/a.md", "src/app.py"], targets)
        self.assertEqual(f"> {targets[0]}", pretool_gate.redirect_write_target(command))
        self.assertTrue(pretool_gate.orch_allowed_path(targets[0]))
        self.assertFalse(pretool_gate.orch_allowed_path(targets[1]))


class GluedRedirectYieldsItsRealTargetTest(unittest.TestCase):

    def test_the_classifier_reports_the_real_fragment_for_each_glued_form(self):
        for command, expected in GLUED.items():
            with self.subTest(command=command):
                self.assertEqual(expected, pretool_gate.redirect_write_target(command))

    def test_a_glued_form_is_still_found_inside_a_nested_shell(self):
        self.assertEqual(">src/app.py",
                         pretool_gate.redirect_write_target("bash -c 'echo hi>src/app.py'"))

    def test_a_glued_write_after_a_dup_is_found_but_the_dup_is_not_a_fragment(self):
        self.assertEqual([">f"], list(pretool_gate.redirect_write_fragments("ls 2>&1>f")))

    def test_a_readonly_agent_is_denied_each_glued_form(self):
        for command in GLUED:
            with self.subTest(command=command):
                code, _ = run(agent_gate.handle_readonly, command)
                self.assertEqual(2, code, f"readonly was ALLOWED {command!r}")

    def test_a_docs_agent_is_denied_each_glued_form(self):
        for command in GLUED:
            with self.subTest(command=command):
                code, _ = run(agent_gate.handle_docs, command)
                self.assertEqual(2, code, f"docs was ALLOWED {command!r}")

    def test_the_denial_names_the_glued_target(self):
        code, msg = run(agent_gate.handle_readonly, "echo hi>src/app.py")
        self.assertEqual(2, code)
        self.assertIn("src/app.py", msg)

    def test_a_dev_agent_may_still_use_a_glued_write(self):
        for command in GLUED:
            with self.subTest(command=command):
                self.assertEqual(0, run(agent_gate.handle_dev, command)[0])


class ArrowsAndDuplicationsStayNoWriteTest(unittest.TestCase):

    def test_the_classifier_reports_no_target_for_each(self):
        for command in NO_WRITE:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.redirect_write_target(command))

    def test_a_readonly_agent_is_not_denied_the_non_git_ones(self):
        for command in NO_WRITE:
            if command.startswith("git commit"):
                continue
            with self.subTest(command=command):
                self.assertEqual(0, run(agent_gate.handle_readonly, command)[0])

    def test_the_orchestrator_is_not_denied_any_of_them(self):
        for command in NO_WRITE:
            with self.subTest(command=command):
                self.assertEqual(0, orchestrator_bash(command)[0])


class OrchestratorChecksEveryWriteFragmentTest(unittest.TestCase):

    def test_a_disallowed_target_after_an_allowed_one_is_denied(self):
        for command in ORCH_TWO_FRAGMENTS:
            with self.subTest(command=command):
                code, msg = orchestrator_bash(command)
                self.assertEqual(2, code, f"the orchestrator was ALLOWED {command!r}")
                self.assertIn("src/app.py", msg)

    def test_the_same_commands_are_denied_through_the_full_main_thread_gate(self):
        for command in ORCH_TWO_FRAGMENTS:
            with self.subTest(command=command):
                self.assertEqual(2, main_thread(command)[0])

    def test_a_glued_write_outside_the_allowlist_is_denied_on_its_own(self):
        self.assertEqual(2, orchestrator_bash("echo hi>src/app.py")[0])

    def test_every_fragment_inside_the_allowlist_is_still_allowed(self):
        for command in ("echo x > docs/a.md; echo y > .agentry/b.md",
                        "echo x>docs/a.md && echo y>>.claude/c.md",
                        "echo x > docs/a.md 2>&1"):
            with self.subTest(command=command):
                self.assertEqual(0, orchestrator_bash(command)[0])


class GluedPathQualifiedNulIsDeniedInEveryProfileTest(unittest.TestCase):
    """The undeletable entry is the NAME, so `ls>./NUL` creates it like `ls>NUL`.
    Left open by task-0095, whose NUL scan declined path-qualified glued targets."""

    QUALIFIED = ("ls>./NUL", "ls>/tmp/NUL", "ls>a/b/nul", "ls>>sub/NUL", "ls 2>&1>./NUL",
                 'ls>"./NUL"', "bash -c 'ls>a/b/nul'", "echo $(ls>/tmp/Nul)")
    OTHER_NAMES = ("echo x>./NUL.bak", "echo x>src/nul.txt", "echo x>a/nullable",
                   "echo x>/tmp/NULL", "echo x>a/annul")

    def test_every_profile_denies_each_glued_path_qualified_nul(self):
        runners = {
            "dev": lambda c: run(agent_gate.handle_dev, c),
            "readonly": lambda c: run(agent_gate.handle_readonly, c),
            "docs": lambda c: run(agent_gate.handle_docs, c),
            "main thread": main_thread,
        }
        for profile, run_it in runners.items():
            for command in self.QUALIFIED:
                with self.subTest(profile=profile, command=command):
                    code, _ = run_it(command)
                    self.assertEqual(2, code, f"{profile} was ALLOWED {command!r}")

    def test_a_dev_agent_is_told_why_with_the_real_file_note(self):
        for command in self.QUALIFIED:
            with self.subTest(command=command):
                code, msg = run(agent_gate.handle_dev, command)
                self.assertEqual(2, code)
                self.assertIn("real file", msg)

    def test_a_dev_agent_may_write_other_names_glued(self):
        for command in self.OTHER_NAMES:
            with self.subTest(command=command):
                self.assertEqual(0, run(agent_gate.handle_dev, command)[0])


if __name__ == "__main__":
    unittest.main()
