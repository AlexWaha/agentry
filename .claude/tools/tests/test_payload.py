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

import contextlib
import io
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

    def test_an_agent_that_omits_claude_md_is_not_charged_for_it(self):
        # task-0106. The three CLAUDE.md-borne rows are in the context of an agent
        # that loads CLAUDE.md and of no other; counting them for an omitting agent
        # would report the saving as nothing.
        build_tree(self.root, rules={"a.md": "a" * 80}, excludes=[], imports=["a.md"],
                   hooks=[], agent_body="---\nname: probe\nomitClaudeMd: true\n---\nbody\n")
        rows = {r.label: r.chars for r in payload.Harness(self.root).rows("probe")}
        self.assertEqual(rows["CLAUDE.md files and core rules"], 0)
        self.assertNotIn("root CLAUDE.md", rows)
        self.assertFalse([label for label in rows if label.startswith("core rules")])
        self.assertIn("agent file (probe.md)", rows)

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


def run_main(*argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = payload.main(list(argv))
    assert code == 0
    return out.getvalue()


class PrintedFiguresTest(unittest.TestCase):
    """Every token figure that reaches the output goes through the divisor.

    Fixture sizes in characters, none divisible by four so a wrong divisor or a
    missing floor shows: root CLAUDE.md 5, .claude/CLAUDE.md 20, one rule file
    100, the agent file 20, no skills, no hooks. Sum 145, which is 36 tokens.
    """

    def setUp(self):
        self.root = tmproot.sandbox(self, "payload-figures")
        build_tree(self.root, rules={"a.md": "a" * 100}, excludes=[],
                   imports=["a.md"], hooks=[])

    def test_chars_to_tokens_floors_by_the_constant(self):
        self.assertEqual(payload.chars_to_tokens(145), 36)
        self.assertEqual(payload.chars_to_tokens(3), 0)
        self.assertEqual(payload.chars_to_tokens(4), 1)

    def test_row_tokens_use_the_conversion(self):
        self.assertEqual(payload.Row("x", 7, payload.COMPUTED).tokens, 1)
        self.assertEqual(payload.Row("x", 8, payload.COMPUTED).tokens, 2)

    def test_json_row_and_total_figures(self):
        data = json.loads(run_main("--root", str(self.root), "--json"))
        probe = data["agents"]["probe"]
        self.assertEqual([r["chars"] for r in probe["paths"][:4]], [5, 20, 100, 20])
        self.assertEqual([r["tokens"] for r in probe["paths"][:4]], [1, 5, 25, 5])
        self.assertEqual(probe["total_chars"], 145)
        self.assertEqual(probe["total_tokens"], 36)

    def test_text_breakdown_total_and_roster_lines(self):
        text = run_main("--root", str(self.root), "--agent", "probe")
        self.assertRegex(text, r"FLOOR \(sum of rows above\)\s+145\s+36\n")
        self.assertRegex(text, r"probe\s+unknown\s+145\s+36\n")

    def test_the_divisor_constant_drives_every_printed_figure(self):
        original = payload.TOKEN_CHARS
        payload.TOKEN_CHARS = 10
        self.addCleanup(setattr, payload, "TOKEN_CHARS", original)
        data = json.loads(run_main("--root", str(self.root), "--json"))
        self.assertEqual(data["agents"]["probe"]["total_tokens"], 14)
        text = run_main("--root", str(self.root), "--agent", "probe")
        self.assertRegex(text, r"FLOOR \(sum of rows above\)\s+145\s+14\n")


class SettingsLayerDisclosureTest(unittest.TestCase):
    """The figures cover the project settings layer only and must say so."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "payload-layers")

    def test_text_output_names_the_layer_read_and_the_ones_not(self):
        build_tree(self.root, rules={"a.md": "a"}, excludes=[], imports=["a.md"], hooks=[])
        text = run_main("--root", str(self.root))
        self.assertIn("settings layers read: .claude/settings.json.", text)
        self.assertIn("NOT read: user (~/.claude/settings.json)", text)
        self.assertIn("plugin-provided hooks", text)
        self.assertIn("FLOOR, not a total", text)

    def test_no_line_calls_the_figure_a_total_without_the_floor_label(self):
        build_tree(self.root, rules={"a.md": "a"}, excludes=[], imports=["a.md"], hooks=[])
        text = run_main("--root", str(self.root), "--all")
        self.assertNotIn("TOTAL", text)
        self.assertIn("tokens (floor)", text)

    def test_json_output_carries_the_layers_and_the_floor_flag(self):
        build_tree(self.root, rules={"a.md": "a"}, excludes=[], imports=["a.md"], hooks=[])
        data = json.loads(run_main("--root", str(self.root), "--json"))
        self.assertIs(data["is_floor"], True)
        self.assertEqual(data["layers_read"], [".claude/settings.json"])
        self.assertIn("plugin-provided hooks", data["layers_not_read"])

    def test_a_tree_without_a_settings_file_reads_no_layer(self):
        build_tree(self.root, rules={"a.md": "a"}, excludes=[], imports=["a.md"], hooks=[])
        (self.root / ".claude" / "settings.json").unlink()
        harness = payload.Harness(self.root)
        self.assertEqual(harness.layers_read(), [])
        self.assertIn("none (no project settings file)", harness.layers_warning())


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
