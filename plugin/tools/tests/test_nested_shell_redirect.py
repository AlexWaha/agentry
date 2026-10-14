"""The redirect gate sees a write hidden inside a nested shell command
(`bash -c "echo x > src/app.py"`), in both pretool_gate and the readonly profile.
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


class NestedShellRedirectTest(unittest.TestCase):
    """mask_quoted() blanks a quoted span, and a quoted span can be a whole
    nested command: `bash -c "echo x > src/app.py"` was scanned as the empty
    string, so the redirect gate saw nothing and both the readonly profile and
    orchestrator_gate allowed a file write. The body is now unwrapped and
    scanned recursively.

    Diffed in BOTH directions per the comment in pretool_gate.py: the refusals
    below are new, and the allowances below must stay allowances."""

    WRAPPED = (
        'bash -c "echo pwned > src/app.py"',
        "sh -c 'echo pwned > src/app.py'",
        'bash -c "echo pwned >> src/app.py"',
        'zsh -c "cat x > src/app.py"',
        'bash -lc "echo pwned > src/app.py"',
        'bash -c "bash -c \'echo pwned > src/app.py\'"',
        'bash -c "ls > NUL"',                       # a bare NUL is a real file here
    )
    STILL_ALLOWED = (
        'python -c "print(2 > 1)"',                 # quoted operator, not a redirect
        "git log --oneline -5",
        'bash -c "pytest -k order"',                # nested, but read-only
        "bash -c 'cat src/app.py'",
        'bash -c "ls 2>&1"',                        # descriptor dup
        'grep -n "a > b" src/app.py',
    )

    def test_a_wrapped_redirect_is_seen(self):
        for command in self.WRAPPED:
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.redirect_write_target(command),
                                "nested redirect went undetected")

    def test_a_direct_redirect_is_still_seen(self):
        self.assertEqual("> src/app.py",
                         pretool_gate.redirect_write_target("echo pwned > src/app.py"))

    def test_read_only_commands_are_still_allowed(self):
        for command in self.STILL_ALLOWED:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.redirect_write_target(command))

    def test_the_readonly_profile_denies_both_spellings(self):
        for command in ("echo pwned > src/app.py", *self.WRAPPED):
            with self.subTest(command=command):
                self.assertEqual(2, agent_gate.handle_readonly(
                    "Bash", {"command": command}))

    def test_a_script_path_is_not_read_as_a_c_body(self):
        # `bash deploy.sh -c` has no -c BODY; the positional argument ends the scan.
        self.assertEqual([], pretool_gate.shell_c_bodies("bash deploy.sh -c"))
        self.assertEqual(["echo hi"], pretool_gate.shell_c_bodies('bash -c "echo hi"'))

    def test_recursion_is_bounded(self):
        # A self-referential body must not recurse forever.
        deep = 'bash -c "' * 8 + "echo x" + '"' * 8
        self.assertEqual("", pretool_gate.redirect_write_target(deep))


if __name__ == "__main__":
    unittest.main()
