"""Agent frontmatter invariants: 31 files, every field present, every value legal.

FR-56 to FR-59 of spec-0001. The counts are the point: a field dropped from one
file out of 31 is invisible in review and silently changes how that agent runs.
Every accepted-value set below was read out of the Claude Code binary
(2.1.270), not assumed: `color` from the subagent palette map, `effort` from
the effort enum, `cacheTtl` from the experimental schema ("5m" or "1h").
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[2]
ROOT = CLAUDE_DIR.parent
AGENTS_DIR = CLAUDE_DIR / "agents"
PLAN = ROOT / ".agentry" / "plans" / "2026-09-12-harness-overhaul-plan.md"
ROSTER = CLAUDE_DIR / "CLAUDE.md"
APPLY_OPTIMIZATION = CLAUDE_DIR / "tools" / "setup" / "apply_optimization.py"

AGENT_COUNT = 31

# Claude Code subagent color palette.
COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
MODELS = {"fable", "opus", "sonnet"}
# rules/communication.md: an agent that inherits a prompting session mode routes
# its permission prompts through the main conversation, where they read as the
# orchestrator interrupting the CEO. Recorded incident, so the value is pinned.
PERMISSION_MODE = "bypassPermissions"
# NFR-5 forbids em dash and en dash everywhere. Built from codepoints, never
# written literally, so this file passes the same gate it pins.
FORBIDDEN_DASHES = (chr(0x2014), chr(0x2013))

# FR-57: cacheTtl on exactly these eight and no others.
CACHE_TTL_AGENTS = {
    "senior-backend-dev", "senior-frontend-dev", "qa-engineer", "devops-engineer",
    "data-engineer", "rapid-prototyper", "reviewer", "security-engineer",
}

# Every authority below is PARSED, never transcribed. A transcription passes
# once the authority changes and the agent files do not, which is the exact
# drift FR-56 and FR-58 exist to catch: they say "matches the plan's Phase 5
# table" and "matches the MAX_TURNS map", not "matches this copy of them".
# Each parse asserts its own yield, so a format change fails loudly here
# instead of silently yielding an empty map that nothing can contradict.
#
# The roster and MAX_TURNS authorities are tracked files, so they ship with the
# template and a missing one is a real failure worth erupting on at import. The
# plan is project work product and gitignored, so it does NOT ship: parsing it
# at import time turns every fresh clone into a suite-level ERROR. It is parsed
# lazily inside the one test that needs it, which skips when the file is absent.


def _phase_5() -> dict[str, tuple[str, str]]:
    """FR-56: the Model and Effort columns of the plan's Phase 5 table."""
    text = PLAN.read_text(encoding="utf-8")
    section = re.search(r"^## Phase 5\b.*?(?=^## )", text, re.MULTILINE | re.DOTALL)
    assert section, f"no Phase 5 section in {PLAN}"
    rows = re.findall(
        r"^\| *([a-z][a-z0-9-]+) *\| *(\w+) *\| *(\w+) *\|$", section.group(0), re.MULTILINE
    )
    table = {name: (model, effort) for name, model, effort in rows}
    assert len(table) == AGENT_COUNT, f"Phase 5 table parsed {len(table)} rows: {sorted(table)}"
    return table


def _departments() -> dict[str, set[str]]:
    """FR-59: department membership, from the team roster in .claude/CLAUDE.md."""
    text = ROSTER.read_text(encoding="utf-8")
    chunks = re.split(r"\*\*(Technical|Support|Business|Content & Growth) department", text)
    depts = {}
    for i in range(1, len(chunks), 2):
        members = set(re.findall(r"^\| *([a-z][a-z0-9-]+) *\| *(?:dev|readonly|docs) *\|",
                                 chunks[i + 1], re.MULTILINE))
        assert members, f"roster section {chunks[i]!r} parsed no agents"
        depts[chunks[i]] = members
    assert len(depts) == 4, f"roster parsed {len(depts)} departments: {sorted(depts)}"
    return depts


def _max_turns() -> dict[str, int]:
    """FR-58: the MAX_TURNS map, from apply_optimization.py itself."""
    match = re.search(r"^MAX_TURNS = (\{.*\})$", APPLY_OPTIMIZATION.read_text(encoding="utf-8"),
                      re.MULTILINE)
    assert match, f"no MAX_TURNS map in {APPLY_OPTIMIZATION}"
    turns = ast.literal_eval(match.group(1))
    assert set(turns) == {"dev", "readonly", "docs"}, f"unexpected profiles: {sorted(turns)}"
    return turns


DEPARTMENTS = _departments()
MAX_TURNS = _max_turns()


def _scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}: *(\S+)$", text, re.MULTILINE)
    return match.group(1) if match else None


def _agents() -> dict[str, str]:
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(AGENTS_DIR.glob("*.md"))
    }


class AgentFrontmatterTest(unittest.TestCase):

    def setUp(self) -> None:
        self.agents = _agents()

    def test_agent_count_is_31(self):
        self.assertEqual(len(self.agents), AGENT_COUNT, sorted(self.agents))

    def test_departments_cover_every_agent_exactly_once(self):
        covered: set[str] = set()
        for members in DEPARTMENTS.values():
            self.assertFalse(covered & members, "agent in two departments")
            covered |= members
        self.assertEqual(covered, set(self.agents))

    def test_every_field_present_on_every_agent(self):
        for key in ("model", "color", "effort", "maxTurns", "permissionMode"):
            present = [n for n, t in self.agents.items() if _scalar(t, key)]
            with self.subTest(field=key):
                self.assertEqual(
                    len(present), AGENT_COUNT,
                    f"{key}: {len(present)}/{AGENT_COUNT}, missing "
                    f"{sorted(set(self.agents) - set(present))}",
                )

    def test_permission_mode_is_bypass_permissions_on_every_agent(self):
        for name, text in self.agents.items():
            with self.subTest(agent=name):
                self.assertEqual(_scalar(text, "permissionMode"), PERMISSION_MODE)

    @unittest.skipUnless(PLAN.exists(), "Phase 5 plan is project work product, gitignored")
    def test_model_and_effort_match_the_plan(self):
        phase_5 = _phase_5()
        for name, text in self.agents.items():
            with self.subTest(agent=name):
                model, effort = _scalar(text, "model"), _scalar(text, "effort")
                self.assertIn(model, MODELS)
                self.assertIn(effort, EFFORTS)
                self.assertEqual((model, effort), phase_5[name])

    def test_no_pinned_model_identifiers(self):
        for name, text in self.agents.items():
            with self.subTest(agent=name):
                self.assertNotIn("claude-opus-5", text)
                self.assertNotIn("claude-sonnet-5", text)

    def test_color_is_one_per_department_and_four_distinct(self):
        by_dept = {}
        for dept, members in DEPARTMENTS.items():
            colors = {_scalar(self.agents[n], "color") for n in members}
            self.assertEqual(len(colors), 1, f"{dept} is not one color: {colors}")
            color = colors.pop()
            self.assertIn(color, COLORS, f"{color} is not a Claude Code agent color")
            by_dept[dept] = color
        self.assertEqual(len(set(by_dept.values())), 4, by_dept)

    def test_max_turns_matches_the_gate_profile(self):
        for name, text in self.agents.items():
            with self.subTest(agent=name):
                profile = re.search(r"--profile (\w+)", text)
                self.assertIsNotNone(profile, "no agent_gate profile")
                self.assertEqual(
                    _scalar(text, "maxTurns"), str(MAX_TURNS[profile.group(1)]),
                )

    def test_cache_ttl_is_on_exactly_the_eight_named_agents(self):
        present = {n for n, t in self.agents.items() if "cacheTtl" in t}
        self.assertEqual(present, CACHE_TTL_AGENTS)
        for name in present:
            with self.subTest(agent=name):
                self.assertRegex(
                    self.agents[name], r"(?m)^experimental:\n  cacheTtl: 1h$",
                )

    def test_no_em_or_en_dash(self):
        for name, text in self.agents.items():
            for dash in FORBIDDEN_DASHES:
                with self.subTest(agent=name, dash=hex(ord(dash))):
                    self.assertNotIn(dash, text)


if __name__ == "__main__":
    unittest.main()
