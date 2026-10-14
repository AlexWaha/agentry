"""The `verify` skill is a thin entry point that delegates to `health-check`.

Claude Code 2.1.286 runs a project skill named `verify` right before a commit.
The gate itself stays in `health-check`; these tests pin that `verify` exists,
carries no checks of its own (so nothing can drift), and that every skill an
agent preloads still resolves to a directory. They read the real tree.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

import payload  # noqa: E402 - path set above

CLAUDE_DIR = TOOLS_DIR.parent
SKILLS_DIR = CLAUDE_DIR / "skills"
AGENTS_DIR = CLAUDE_DIR / "agents"
VERIFY = SKILLS_DIR / "verify" / "SKILL.md"
HEALTH_CHECK = SKILLS_DIR / "health-check" / "SKILL.md"
README = SKILLS_DIR / "README.md"

# Any of these in verify's body would be a second copy of the gate.
GATE_COMMANDS = ("{{TEST_CMD}}", "{{LINT_CMD}}", "{{FORMAT_CMD}}", "unittest", "ruff")


def split_skill(text: str) -> tuple[str, str]:
    """Frontmatter and body of a SKILL.md."""
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, re.DOTALL)
    assert match, "SKILL.md has no frontmatter block"
    return match.group(1), match.group(2)


class VerifySkillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = VERIFY.read_text(encoding="utf-8")
        self.front, self.body = split_skill(self.text)

    def test_file_starts_with_frontmatter_fence(self) -> None:
        self.assertEqual(self.text.splitlines()[0], "---")

    def test_name_equals_directory_name(self) -> None:
        self.assertRegex(self.front, r"(?m)^name:[ \t]*verify[ \t]*$")
        self.assertEqual(VERIFY.parent.name, "verify")

    def test_model_invocation_stays_enabled(self) -> None:
        self.assertNotIn("disable-model-invocation", self.front)

    def test_description_is_under_the_catalog_limit(self) -> None:
        match = re.search(r"(?m)^description:[ \t]*(.+)$", self.front)
        self.assertIsNotNone(match)
        self.assertLess(len(match.group(1)), 1000)

    def test_body_is_under_200_lines(self) -> None:
        self.assertLess(len(self.text.splitlines()), 200)

    def test_body_delegates_to_health_check(self) -> None:
        self.assertIn("health-check", self.body)

    def test_body_holds_no_gate_command(self) -> None:
        for token in GATE_COMMANDS:
            with self.subTest(token=token):
                self.assertNotIn(token, self.body)


class CatalogTest(unittest.TestCase):
    def test_readme_lists_verify_with_the_commit_trigger(self) -> None:
        rows = [
            line
            for line in README.read_text(encoding="utf-8").splitlines()
            if line.startswith("| verify |")
        ]
        self.assertEqual(len(rows), 1)
        self.assertIn("commit", rows[0])

    def test_readme_counts_match_the_directories(self) -> None:
        text = README.read_text(encoding="utf-8")
        directories = [p for p in SKILLS_DIR.iterdir() if p.is_dir()]
        pipeline_set = [p for p in directories if p.name.startswith("pipeline") or p.name == "swarm"]
        self.assertIn(f"listing shows {len(directories)} directories", text)
        catalogued = len(directories) - len(pipeline_set)
        self.assertTrue(text.startswith(f"# Skills Catalog\n\n{catalogued} skills,"))

    def test_every_preloaded_skill_has_a_directory(self) -> None:
        agents = sorted(AGENTS_DIR.glob("*.md"))
        self.assertTrue(agents)
        preloaded: set[str] = set()
        for agent in agents:
            front = payload.frontmatter(agent.read_text(encoding="utf-8"))
            preloaded.update(payload.list_key(front, "skills"))
        self.assertIn("health-check", preloaded)
        missing = sorted(n for n in preloaded if not (SKILLS_DIR / n / "SKILL.md").is_file())
        self.assertEqual(missing, [])


class HealthCheckTest(unittest.TestCase):
    def test_test_step_forbids_piping_the_suite(self) -> None:
        text = HEALTH_CHECK.read_text(encoding="utf-8")
        step = next(line for line in text.splitlines() if line.startswith("4. **Run tests**"))
        self.assertIn("unpiped", step)


if __name__ == "__main__":
    unittest.main()
