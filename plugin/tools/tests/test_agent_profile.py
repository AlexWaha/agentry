"""The gate profile of a subagent comes from its own definition (task-0038, S4).

agent_gate and dangerous_patterns are registered once, plugin-wide, so they fire for
the main thread, for every roster agent and for built-in or foreign agents. The
profile is therefore looked up from `<project>/.claude/agents/<agent_type>.md`
(`profile:` and `allow_scripts:` keys): no identity is the main thread, an identity
that does not resolve is readonly (decision D3), and an explicit `--profile` still
wins so a hook line from before the plugin layout keeps working.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import agent_profile
import state
import tmproot

AGENTS_DIR = state.ROOT / ".claude" / "agents"
ROOT_CLAUDE_MD = str(state.ROOT / "CLAUDE.md")


def definition_text(profile: str, extra: str = "") -> str:
    return f"---\nname: probe\nmodel: sonnet\nprofile: {profile}\n{extra}---\n\n# Probe\n"


class DefinitionTest(unittest.TestCase):
    """agent_profile.definition and resolve, against a throwaway project root."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "agent_profile_")
        self.agents = self.root / ".claude" / "agents"
        self.agents.mkdir(parents=True)

    def write(self, name: str, text: str) -> None:
        (self.agents / f"{name}.md").write_text(text, encoding="utf-8")

    def test_each_profile_resolves_from_its_own_key(self):
        for profile in ("dev", "docs", "readonly"):
            with self.subTest(profile=profile):
                self.write("probe", definition_text(profile))
                self.assertEqual((profile, ()), agent_profile.definition("probe", self.root))

    def test_allow_scripts_is_read_as_a_flow_list(self):
        self.write("probe", definition_text("readonly", "allow_scripts: [k6, 'lighthouse', npx]\n"))
        self.assertEqual(("readonly", ("k6", "lighthouse", "npx")),
                         agent_profile.definition("probe", self.root))

    def test_an_allow_script_that_is_not_a_plain_program_name_is_dropped(self):
        self.write("probe", definition_text("readonly", "allow_scripts: [npx, ../evil, 'a b', ]\n"))
        self.assertEqual(("readonly", ("npx",)), agent_profile.definition("probe", self.root))

    def test_a_block_style_allow_scripts_list_grants_nothing(self):
        # Only the flow form is read; anything else fails closed to no extra script.
        self.write("probe", definition_text("readonly", "allow_scripts:\n  - npx\n"))
        self.assertEqual(("readonly", ()), agent_profile.definition("probe", self.root))

    def test_a_profile_in_the_body_does_not_count(self):
        self.write("probe", "---\nname: probe\n---\n\nprofile: dev\n")
        self.assertIsNone(agent_profile.definition("probe", self.root))

    def test_an_unknown_missing_or_malformed_definition_is_none(self):
        self.write("badprofile", definition_text("admin"))
        self.write("nofront", "# no frontmatter\nprofile: dev\n")
        self.write("noprofile", "---\nname: x\n---\n")
        for name in ("badprofile", "nofront", "noprofile", "absent"):
            with self.subTest(name=name):
                self.assertIsNone(agent_profile.definition(name, self.root))
        self.assertIsNone(agent_profile.definition("probe", None))

    def test_a_name_that_is_not_a_plain_stem_never_reaches_the_filesystem(self):
        self.write("probe", definition_text("dev"))
        (self.root / "evil.md").write_text(definition_text("dev"), encoding="utf-8")
        for name in ("", "../../evil", "..\\..\\evil", "probe:pagent", "a/b", ".hidden",
                     "probe\x00", "probe\n", "probe::$DATA", " probe"):
            with self.subTest(name=name):
                self.assertIsNone(agent_profile.definition(name, self.root))

    def test_resolve_falls_back_to_readonly_for_every_unresolved_caller(self):
        self.write("probe", definition_text("dev"))
        for agent_type in ("general-purpose", "Explore", "plugin:probe", "missing", ""):
            with self.subTest(agent_type=agent_type):
                payload = {"agent_type": agent_type, "agent_id": "a1"}
                self.assertEqual(("readonly", ()), agent_profile.resolve(payload, self.root))
        self.assertEqual(("readonly", ()), agent_profile.resolve({"agent_id": "a1"}, self.root))
        self.assertEqual(("dev", ()), agent_profile.resolve({"agent_type": "probe"}, self.root))

    def test_resolve_never_raises(self):
        for root in (None, "", 7, [1]):
            with self.subTest(root=root):
                self.assertEqual(("readonly", ()),
                                 agent_profile.resolve({"agent_type": "probe"}, root))


class SubagentDetectionTest(unittest.TestCase):
    def test_the_main_thread_has_no_identity(self):
        for payload in ({}, {"tool_name": "Bash"}, {"agent_type": "", "agent_id": "  "},
                        {"transcript_path": "C:/Users/x/.claude/projects/p/abc.jsonl"}):
            with self.subTest(payload=payload):
                self.assertFalse(agent_profile.is_subagent(payload))

    def test_any_identity_key_or_a_subagent_transcript_marks_a_subagent(self):
        for payload in ({"agent_type": "reviewer"}, {"subagent_type": "x"}, {"agent_name": "x"},
                        {"agent_id": "a1"},
                        {"transcript_path": "C:\\p\\subagents\\agent-a1.jsonl"},
                        {"transcript_path": "/p/agent-a1.jsonl"}):
            with self.subTest(payload=payload):
                self.assertTrue(agent_profile.is_subagent(payload))


def run_gate(payload: dict, *args: str) -> subprocess.CompletedProcess:
    """The real agent_gate hook on a payload, exactly as Claude Code feeds it."""
    payload = {"cwd": str(state.ROOT), **payload}
    return subprocess.run(
        [sys.executable, str(PIPELINE_DIR / "agent_gate.py"), *args],
        input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=30)


def stderr_of(proc: subprocess.CompletedProcess) -> str:
    return proc.stderr.decode("utf-8", "replace")


class AgentGateLookupTest(unittest.TestCase):
    """agent_gate.py with no --profile, against the shipped agent definitions."""

    def test_the_main_thread_is_allowed_whatever_it_runs(self):
        # pretool_gate binds the main thread; this hook must not (no identity, no profile).
        for tool, ti in (("Bash", {"command": "rm -rf docs"}),
                         ("Write", {"file_path": ".agentry/state/approvals", "content": "auto"})):
            with self.subTest(tool=tool):
                proc = run_gate({"tool_name": tool, "tool_input": ti})
                self.assertEqual(0, proc.returncode, stderr_of(proc))

    def test_a_docs_agent_is_held_to_the_docs_profile(self):
        ok = run_gate({"tool_name": "Write", "agent_type": "technical-writer", "agent_id": "a1",
                       "tool_input": {"file_path": ROOT_CLAUDE_MD}})
        self.assertEqual(0, ok.returncode, stderr_of(ok))
        denied = run_gate({"tool_name": "Write", "agent_type": "technical-writer", "agent_id": "a1",
                           "tool_input": {"file_path": "app/main.py"}})
        self.assertEqual(2, denied.returncode)
        self.assertIn("Docs agent", stderr_of(denied))

    def test_a_readonly_agent_may_not_write(self):
        proc = run_gate({"tool_name": "Write", "agent_type": "reviewer", "agent_id": "a1",
                         "tool_input": {"file_path": ROOT_CLAUDE_MD}})
        self.assertEqual(2, proc.returncode)
        self.assertIn("read-only", stderr_of(proc))

    def test_a_dev_agent_is_held_to_the_dev_profile(self):
        # Only the dev profile refuses web tools; a readonly fallback would allow and log.
        proc = run_gate({"tool_name": "WebSearch", "agent_type": "senior-backend-dev", "agent_id": "a1",
                         "tool_input": {"query": "x"}})
        self.assertEqual(2, proc.returncode)
        self.assertIn("dev agents", stderr_of(proc))

    def test_an_agent_with_no_definition_is_readonly(self):
        # D3: built-ins and foreign plugin agents lose write access.
        for agent_type in ("general-purpose", "Explore", "probe:pagent", "no-such-agent"):
            with self.subTest(agent_type=agent_type):
                proc = run_gate({"tool_name": "Write", "agent_type": agent_type, "agent_id": "a1",
                                 "tool_input": {"file_path": "docs/x.md"}})
                self.assertEqual(2, proc.returncode, stderr_of(proc))
                bash = run_gate({"tool_name": "Bash", "agent_type": agent_type, "agent_id": "a1",
                                 "tool_input": {"command": "rm docs/x.md"}})
                self.assertEqual(2, bash.returncode, stderr_of(bash))

    def test_an_identity_with_only_an_agent_id_is_readonly(self):
        proc = run_gate({"tool_name": "Bash", "agent_id": "a1", "tool_input": {"command": "rm x"}})
        self.assertEqual(2, proc.returncode)

    def test_allow_scripts_come_from_the_definition(self):
        command = {"command": "npx --version"}
        measuring = run_gate({"tool_name": "Bash", "agent_type": "performance-benchmarker",
                              "agent_id": "a1", "tool_input": command})
        self.assertEqual(0, measuring.returncode, stderr_of(measuring))
        other = run_gate({"tool_name": "Bash", "agent_type": "reviewer", "agent_id": "a1",
                          "tool_input": command})
        self.assertEqual(2, other.returncode)
        self.assertIn(agent_gate.ALLOWLIST_PHRASE, stderr_of(other))

    def test_an_explicit_profile_wins_over_the_lookup(self):
        # Back-compat: a hook line that still names --profile governs every caller,
        # the main thread included, exactly as before the plugin layout.
        main = run_gate({"tool_name": "Write", "tool_input": {"file_path": "docs/x.md"}},
                        "--profile", "readonly")
        self.assertEqual(2, main.returncode)
        forced = run_gate({"tool_name": "Write", "agent_type": "technical-writer", "agent_id": "a1",
                           "tool_input": {"file_path": "docs/x.md"}}, "--profile", "readonly")
        self.assertEqual(2, forced.returncode)

    def test_every_shipped_agent_resolves_to_a_profile_and_the_roster_agrees(self):
        roster = re.findall(r"^\| *([a-z][a-z0-9-]+) *\| *(dev|readonly|docs) *\|",
                            (state.ROOT / ".claude" / "CLAUDE.md").read_text(encoding="utf-8"),
                            re.MULTILINE)
        self.assertEqual(31, len(roster))
        for name, profile in roster:
            with self.subTest(agent=name):
                self.assertEqual(profile, agent_profile.definition(name, state.ROOT)[0])


class DangerousPatternsScopeTest(unittest.TestCase):
    """The plugin-wide PostToolUse hook speaks to dev agents only."""

    def setUp(self):
        self.sample = tmproot.sandbox(self, "dangerous_patterns_") / "sample.py"
        self.sample.write_text("breakpoint()\n", encoding="utf-8")

    def run_hook(self, **identity) -> str:
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.sample)}, **identity}
        proc = subprocess.run([sys.executable, str(HOOKS_DIR / "dangerous_patterns.py")],
                              input=json.dumps(payload).encode("utf-8"), capture_output=True,
                              timeout=30)
        self.assertEqual(0, proc.returncode)
        return proc.stdout.decode("utf-8", "replace")

    def test_a_dev_agent_gets_the_warning(self):
        self.assertIn("breakpoint", self.run_hook(agent_type="senior-backend-dev", agent_id="a1"))

    def test_the_main_thread_stays_silent(self):
        self.assertEqual("", self.run_hook())

    def test_docs_readonly_and_unresolved_agents_stay_silent(self):
        for agent_type in ("technical-writer", "reviewer", "general-purpose", "probe:pagent"):
            with self.subTest(agent_type=agent_type):
                self.assertEqual("", self.run_hook(agent_type=agent_type, agent_id="a1"))


if __name__ == "__main__":
    unittest.main()
