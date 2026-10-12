"""apply_optimization.py must generate the same hook matchers task-0096 put in the
agent files and settings.json, or a new adopter starts with MultiEdit and
NotebookEdit outside every write gate."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

SETUP_DIR = Path(__file__).resolve().parents[1] / "setup"
TESTS_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SETUP_DIR))
sys.path.insert(0, str(TESTS_DIR))

import apply_optimization
import tmproot

WRITE_TOOLS = "Edit|Write|MultiEdit|NotebookEdit"
MATCHER_RE = re.compile(r'matcher: "([^"]+)"')


class GeneratedMatcherTest(unittest.TestCase):

    def test_the_dev_profile_gets_the_full_tool_list_on_both_events(self):
        matchers = MATCHER_RE.findall(apply_optimization.hooks_yaml("dev", Path(".")))
        self.assertEqual([f"Bash|{WRITE_TOOLS}", WRITE_TOOLS], matchers)

    def test_other_profiles_get_the_full_pre_tool_list(self):
        for profile in ("docs", "readonly"):
            with self.subTest(profile=profile):
                matchers = MATCHER_RE.findall(apply_optimization.hooks_yaml(profile, Path(".")))
                self.assertEqual([f"Bash|{WRITE_TOOLS}"], matchers)

    def test_the_generated_matchers_equal_the_ones_the_shipped_agents_carry(self):
        shipped = set()
        for path in (CLAUDE_DIR / "agents").glob("*.md"):
            shipped.update(MATCHER_RE.findall(path.read_text(encoding="utf-8")))
        generated = set()
        for profile in ("dev", "docs", "readonly"):
            generated.update(MATCHER_RE.findall(apply_optimization.hooks_yaml(profile, Path("."))))
        self.assertTrue(generated <= shipped, generated - shipped)

    def test_merge_settings_registers_the_pre_tool_gate_on_the_full_tool_list(self):
        dst = tmproot.sandbox(self, "apply_matchers_") / ".claude"
        dst.mkdir()
        report: dict = {}
        apply_optimization.merge_settings(dst, dst.parent, report)
        settings = json.loads((dst / "settings.json").read_text(encoding="utf-8"))
        groups = settings["hooks"]["PreToolUse"]
        gate = [g for g in groups
                if any("pretool_gate.py" in h["command"] for h in g["hooks"])]
        self.assertEqual([f"Bash|{WRITE_TOOLS}"], [g["matcher"] for g in gate])


if __name__ == "__main__":
    unittest.main()
