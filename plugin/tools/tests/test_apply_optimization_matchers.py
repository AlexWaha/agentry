"""apply_optimization.py must generate hook matchers that cover every file-writing tool
(task-0096 put MultiEdit and NotebookEdit in the matchers), or a new adopter starts with
them outside every write gate."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

SETUP_DIR = Path(__file__).resolve().parents[1] / "setup"
TESTS_DIR = Path(__file__).resolve().parent
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

    def test_every_generated_matcher_covers_all_four_write_tools(self):
        # The shipped agents no longer carry matchers (task-0038: agent_gate is registered once,
        # plugin-wide; test_gate_path_parity pins that registration). What this tool writes for
        # an adopter must still put every file-writing tool inside the gate.
        for profile in ("dev", "docs", "readonly"):
            for matcher in MATCHER_RE.findall(apply_optimization.hooks_yaml(profile, Path("."))):
                for tool in WRITE_TOOLS.split("|"):
                    with self.subTest(profile=profile, matcher=matcher, tool=tool):
                        self.assertRegex(tool, f"^(?:{matcher})$")

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
