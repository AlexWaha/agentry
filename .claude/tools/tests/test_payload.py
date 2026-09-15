"""The payload measurement has to be wrong-proof about what counts as loaded.

FR-35 of spec-0001, contract C-11. The script produces the number Phase 1 is
judged on, so the failure that matters is not a crash - it is a plausible number
that is quietly too small. Two such near-misses are pinned here because both
were live during development:

1. Reading the always-loaded rules off the `@rules/` import list instead of the
   .claude/rules/ walk. Measured on the pre-Phase-1 tree at 623d4e1: 20 rule
   files, an empty claudeMdExcludes, and ZERO `@rules/` lines, so the import
   reading scored the baseline's 213462 characters as nothing.
2. Counting a hook's raw stdout instead of the additionalContext inside its
   envelope. Plain stdout is discarded on SubagentStart (task-0081), so text
   outside the envelope reaches no agent and is worth zero tokens, however many
   bytes the hook printed.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TESTS_DIR))

import payload  # noqa: E402 - path set above
import tmproot  # noqa: E402 - same


def build_tree(root: Path, *, rules: dict[str, str], excludes: list[str],
               imports: list[str], hooks: list[dict], agent: str = "probe",
               agent_body: str = "") -> None:
    """A minimal harness tree: the four files the measurement reads."""
    claude = root / ".claude"
    (claude / "rules").mkdir(parents=True, exist_ok=True)
    (claude / "agents").mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text("root\n", encoding="utf-8")
    for name, text in rules.items():
        (claude / "rules" / name).write_text(text, encoding="utf-8")
    import_lines = "".join(f"@rules/{name}\n" for name in imports)
    (claude / "CLAUDE.md").write_text(f"# core\n\n{import_lines}", encoding="utf-8")
    (claude / "settings.json").write_text(json.dumps({
        "claudeMdExcludes": [f"**/.claude/rules/{name}" for name in excludes],
        "hooks": {"SubagentStart": hooks},
    }), encoding="utf-8")
    (claude / "agents" / f"{agent}.md").write_text(agent_body or "---\nname: probe\n---\n",
                                                   encoding="utf-8")


def echo_hook(root: Path, name: str, body: str) -> Path:
    """A throwaway hook script that prints `body` verbatim on stdout."""
    path = root / f"{name}.py"
    path.write_text(f"import sys\nsys.stdin.read()\nsys.stdout.write({body!r})\n",
                    encoding="utf-8")
    return path


class TokenArithmeticTest(unittest.TestCase):
    def test_divisor_is_four_and_declared(self):
        # The DoD forbids moving this to reach the target, so it is pinned.
        self.assertEqual(payload.TOKEN_CHARS, 4)

    def test_tokens_is_floor_division_by_the_constant(self):
        self.assertEqual(payload.tokens("a" * 4001), 1000)
        self.assertEqual(payload.tokens("abc"), 0)


class CoreRulesTest(unittest.TestCase):
    """Always-loaded rules come from the walk, never from the import list."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "payload-core")

    def test_walk_minus_excludes_is_the_set(self):
        build_tree(self.root,
                   rules={"kept.md": "k" * 100, "dropped.md": "d" * 100},
                   excludes=["dropped.md"], imports=["kept.md"], hooks=[])
        names = [p.name for p in payload.Harness(self.root).core_rules()]
        self.assertEqual(names, ["kept.md"])

    def test_a_tree_with_no_import_lines_still_counts_its_rules(self):
        # The pre-Phase-1 shape. An import-list reading returns nothing here.
        build_tree(self.root, rules={"a.md": "a" * 80, "b.md": "b" * 40},
                   excludes=[], imports=[], hooks=[])
        harness = payload.Harness(self.root)
        self.assertEqual(sorted(p.name for p in harness.core_rules()), ["a.md", "b.md"])
        rows = {r.label: r.chars for r in harness.rows("probe")}
        self.assertIn("core rules (always loaded, 2 files)", rows)
        self.assertEqual(rows["core rules (always loaded, 2 files)"], 120)

    def test_disagreement_between_the_two_channels_is_reported(self):
        build_tree(self.root, rules={"a.md": "a", "b.md": "b"},
                   excludes=[], imports=["a.md"], hooks=[])
        warning = payload.Harness(self.root).check_rule_channels()
        self.assertIn("walked but not imported: b.md", warning)

    def test_agreement_is_silent(self):
        build_tree(self.root, rules={"a.md": "a", "b.md": "b"},
                   excludes=["b.md"], imports=["a.md"], hooks=[])
        self.assertEqual(payload.Harness(self.root).check_rule_channels(), "")


class HookSelectionTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "payload-hooks")

    def _tree(self, hooks):
        build_tree(self.root, rules={"a.md": "a"}, excludes=[], imports=["a.md"],
                   hooks=hooks)

    def test_an_entry_without_a_matcher_fires_for_every_agent(self):
        self._tree([{"hooks": [{"type": "command", "command": "python x.py"}]}])
        harness = payload.Harness(self.root)
        self.assertEqual(len(harness.hook_entries("probe")), 1)
        self.assertEqual(len(harness.hook_entries("anyone-at-all")), 1)

    def test_a_matcher_selects_only_the_named_agents(self):
        self._tree([{"matcher": "reviewer|architect",
                     "hooks": [{"type": "command", "command": "python x.py"}]}])
        harness = payload.Harness(self.root)
        self.assertEqual(len(harness.hook_entries("reviewer")), 1)
        self.assertEqual(harness.hook_entries("qa-engineer"), [])
        # search would match this name through the `reviewer` alternative.
        self.assertEqual(harness.hook_entries("reviewer-lite"), [])


class HookOutputTest(unittest.TestCase):
    """What a hook delivers, not what it prints."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "payload-stdout")

    def _measure(self, body):
        script = echo_hook(self.root, "probe_hook", body)
        build_tree(self.root, rules={"a.md": "a"}, excludes=[], imports=["a.md"],
                   hooks=[{"hooks": [{"type": "command",
                                      "command": f'python "{script.as_posix()}"'}]}])
        harness = payload.Harness(self.root)
        cmd = harness.hook_entries("probe")[0][0]
        return harness.run_hook(cmd, "probe")

    def test_the_envelope_body_is_what_counts(self):
        block, note = self._measure(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SubagentStart", "additionalContext": "x" * 250}}))
        self.assertEqual(note, "")
        self.assertEqual(len(block), 250)

    def test_plain_stdout_delivers_nothing_and_says_so(self):
        block, note = self._measure("y" * 5000)
        self.assertEqual(block, "")
        self.assertIn("delivers nothing", note)


class FrontmatterListTest(unittest.TestCase):
    def test_block_and_flow_forms_both_parse(self):
        block = "name: probe\nskills:\n  - write-tests\n  - bug-fix\ntools: Read\n"
        self.assertEqual(payload.list_key(block, "skills"), ["write-tests", "bug-fix"])
        flow = "name: probe\nskills: [write-tests, bug-fix]\n"
        self.assertEqual(payload.list_key(flow, "skills"), ["write-tests", "bug-fix"])
        self.assertEqual(payload.list_key("name: probe\n", "skills"), [])


class RealTreeTest(unittest.TestCase):
    """The shipped harness, so the script cannot pass on fixtures alone."""

    def setUp(self):
        self.harness = payload.Harness(payload.DEFAULT_ROOT)

    def test_every_agent_resolves_to_a_known_profile(self):
        names = self.harness.agent_names()
        self.assertEqual(len(names), 31)
        profiles = {n: self.harness.profile(n) for n in names}
        unknown = [n for n, p in profiles.items() if p not in ("dev", "readonly", "docs")]
        self.assertEqual(unknown, [], f"agents with no agent_gate profile: {unknown}")
        self.assertEqual(set(profiles.values()), {"dev", "readonly", "docs"})

    def test_the_two_rule_channels_agree_on_the_shipped_tree(self):
        self.assertEqual(self.harness.check_rule_channels(), "")


if __name__ == "__main__":
    unittest.main()
