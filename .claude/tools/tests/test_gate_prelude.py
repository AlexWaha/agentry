"""One git-abbreviation helper and one shared Bash prelude (task-0125).

Both gates abbreviate git long options the same way, so the rule lives in
pretool_gate and agent_gate binds it. The Bash refusals every profile shares
(approve.py, handoff --waive, a state setter, a protected-state write, a memory
target) live in agent_gate.bash_prelude, which handle_dev, handle_readonly and
handle_docs all run before anything profile-specific.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import ast
import contextlib
import io
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import agent_gate
import pretool_gate
import state

CWD = str(state.ROOT)

APPROVE = "python .claude/tools/pipeline/approve.py --task task-0001 --gate plan"
WAIVE = "python .claude/tools/pipeline/handoff.py --waive task-0001 --reason x"
SETTER = "python .claude/tools/pipeline/approvals.py --set auto"
PROTECTED = "echo x > .agentry/state/approvals"
MEMORY = "python .claude/tools/memory/memory.py --export --out /etc/x.json"

DEV = "Dev agent"
READONLY = "Read-only agent"
DOCS = "Docs agent"
PROFILE_OF = {DEV: "dev", READONLY: "readonly", DOCS: "docs"}

DEV_APPROVE_TEXT = ("approve.py is orchestrator-only. Recording CEO approval from a dev agent "
                    "is self-approval - report readiness to the orchestrator instead.\n")
DEV_WAIVE_TEXT = ("handoff.py --waive is orchestrator-only - it records a CEO waiver. "
                  "Report the handoff problem to the orchestrator instead.\n")
RESTRICTED_APPROVE_TEXT = "approve.py is orchestrator-only.\n"
RESTRICTED_WAIVE_TEXT = "handoff.py --waive is orchestrator-only - it records a CEO waiver.\n"


def stderr_of(call, *args) -> tuple:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = call(*args)
    return code, err.getvalue()


def prelude(who: str, command: str) -> tuple:
    return stderr_of(agent_gate.bash_prelude, who, command, CWD)


def profile_bash(who: str, command: str) -> tuple:
    """The whole profile handler for one Bash call, as the hook reaches it."""
    ti = {"command": command}
    if who == DEV:
        return stderr_of(agent_gate.handle_dev, "Bash", ti, CWD)
    handler = agent_gate.handle_readonly if who == READONLY else agent_gate.handle_docs
    return stderr_of(handler, "Bash", ti, CWD)


class AbbreviationHelperTest(unittest.TestCase):
    def test_both_gates_use_the_same_function_object(self):
        self.assertIs(pretool_gate.is_long_option_prefix, agent_gate.is_long_option_prefix)

    def test_agent_gate_defines_no_abbreviation_body_of_its_own(self):
        tree = ast.parse((PIPELINE_DIR / "agent_gate.py").read_text(encoding="utf-8"))
        defined = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        self.assertNotIn("is_long_option_prefix", defined)

    def test_prefix_grid(self):
        grid = (
            ("--target", "--target-directory", True),
            ("--t", "--target-directory", True),
            ("--target-directory", "--target-directory", True),
            ("--", "--target-directory", False),
            ("-t", "--target-directory", False),
            ("", "--target-directory", False),
            ("--targets", "--target-directory", False),
            ("--suf", "--suffix", True),
            ("--suffixes", "--suffix", False),
        )
        for name, option, expected in grid:
            with self.subTest(name=name, option=option):
                self.assertEqual(expected, pretool_gate.is_long_option_prefix(name, option))

    def test_push_option_in_still_resolves_through_the_helper(self):
        fanout = pretool_gate.PUSH_FANOUT_LONG
        self.assertEqual("", pretool_gate.push_option_in("--", fanout))
        self.assertEqual("", pretool_gate.push_option_in("-a", fanout))
        self.assertEqual("--all", pretool_gate.push_option_in("--al", fanout))
        self.assertEqual("--all", pretool_gate.push_option_in("--all", fanout))
        self.assertEqual("--mirror", pretool_gate.push_option_in("--mir=x", fanout))
        self.assertEqual("", pretool_gate.push_option_in("--zzz", fanout))
        self.assertEqual("", pretool_gate.push_option_in("main", fanout))


class PreludeTextTest(unittest.TestCase):
    """Each of the five checks, for each profile, with the exact single-cause text."""

    def test_approve_script_text_per_profile(self):
        self.assertEqual((2, DEV_APPROVE_TEXT), prelude(DEV, APPROVE))
        self.assertEqual((2, RESTRICTED_APPROVE_TEXT), prelude(READONLY, APPROVE))
        self.assertEqual((2, RESTRICTED_APPROVE_TEXT), prelude(DOCS, APPROVE))

    def test_waive_text_per_profile(self):
        self.assertEqual((2, DEV_WAIVE_TEXT), prelude(DEV, WAIVE))
        self.assertEqual((2, RESTRICTED_WAIVE_TEXT), prelude(READONLY, WAIVE))
        self.assertEqual((2, RESTRICTED_WAIVE_TEXT), prelude(DOCS, WAIVE))

    def test_state_setter_text_is_the_same_for_every_profile(self):
        expected = agent_gate.STATE_SETTER_MSG.format(script="approvals.py") + "\n"
        for who in PROFILE_OF:
            with self.subTest(who=who):
                self.assertEqual((2, expected), prelude(who, SETTER))

    def test_protected_state_text_names_the_profile(self):
        for who in PROFILE_OF:
            with self.subTest(who=who):
                expected = agent_gate.PROTECTED_STATE_MSG.format(
                    who=who, path=".agentry/state/approvals") + "\n"
                self.assertEqual((2, expected), prelude(who, PROTECTED))

    def test_memory_target_text_names_the_profile(self):
        for who in PROFILE_OF:
            with self.subTest(who=who):
                expected = agent_gate.MEMORY_TARGET_MSG.format(
                    who=who, option="--out", value="/etc/x.json") + "\n"
                self.assertEqual((2, expected), prelude(who, MEMORY))

    def test_a_harmless_command_passes_the_prelude(self):
        for who in PROFILE_OF:
            with self.subTest(who=who):
                self.assertEqual((0, ""), prelude(who, "git status"))

    def test_checks_run_in_the_documented_order(self):
        # approve.py wins over every later check in the same command
        both = f"{APPROVE} && {SETTER} && {PROTECTED} && {MEMORY}"
        self.assertEqual((2, DEV_APPROVE_TEXT), prelude(DEV, both))
        # a state setter wins over a protected write and a memory target
        later = f"{PROTECTED} && {MEMORY} && {SETTER}"
        self.assertEqual(agent_gate.STATE_SETTER_MSG.format(script="approvals.py") + "\n",
                         prelude(DEV, later)[1])


class EveryProfileRunsThePreludeTest(unittest.TestCase):
    def test_each_profile_handler_denies_each_prelude_cause_with_the_prelude_text(self):
        for who in PROFILE_OF:
            for label, command in (("approve", APPROVE), ("waive", WAIVE), ("setter", SETTER),
                                   ("protected", PROTECTED), ("memory", MEMORY)):
                with self.subTest(who=who, check=label):
                    self.assertEqual(prelude(who, command), profile_bash(who, command))

    def test_restricted_profiles_call_the_shared_prelude(self):
        for profile in ("readonly", "docs"):
            with self.subTest(profile=profile), \
                    unittest.mock.patch.object(agent_gate, "bash_prelude",
                                               return_value=2) as shared:
                code = agent_gate.lexical_bash_checks(profile, "git status", CWD)
            self.assertEqual(2, code)
            shared.assert_called_once()

    def test_dev_calls_the_shared_prelude_with_its_own_label(self):
        with unittest.mock.patch.object(agent_gate, "bash_prelude", return_value=2) as shared:
            code = agent_gate.handle_dev("Bash", {"command": "git status"}, CWD)
        self.assertEqual(2, code)
        shared.assert_called_once_with(DEV, "git status", CWD)


class DevOrderTest(unittest.TestCase):
    """handle_dev: prelude first, then the git target, the zone, force push, the ref write,
    the unnarrowed test, and the orchestrator's Bash gate last."""

    def test_the_prelude_runs_before_the_git_target_and_the_zone_checks(self):
        boom = AssertionError("a later dev check ran before the shared prelude")
        with unittest.mock.patch.object(agent_gate, "git_target_deny", side_effect=boom), \
                unittest.mock.patch.object(agent_gate, "zone_denial", side_effect=boom):
            code, text = stderr_of(agent_gate.handle_dev, "Bash", {"command": APPROVE}, CWD)
        self.assertEqual((2, DEV_APPROVE_TEXT), (code, text))

    def test_an_allowed_command_walks_the_chain_in_order(self):
        calls = []

        def record(name, result):
            def fake(*args, **kwargs):
                calls.append(name)
                return result
            return fake

        with unittest.mock.patch.object(agent_gate, "bash_prelude", record("prelude", 0)), \
                unittest.mock.patch.object(agent_gate, "git_target_deny", record("git", 0)), \
                unittest.mock.patch.object(agent_gate, "zone_denial", record("zone", "")), \
                unittest.mock.patch.object(agent_gate, "is_force_push", record("force", False)), \
                unittest.mock.patch.object(agent_gate, "dev_ref_write", record("ref", "")), \
                unittest.mock.patch.object(agent_gate, "unnarrowed_test_cmd",
                                           record("test", "")), \
                unittest.mock.patch.object(pretool_gate, "handle_bash",
                                           record("orchestrator", 0)):
            code = agent_gate.handle_dev("Bash", {"command": "git status"}, CWD)
        self.assertEqual(0, code)
        self.assertEqual(["prelude", "git", "zone", "force", "ref", "test", "orchestrator"],
                         calls)


if __name__ == "__main__":
    unittest.main()
