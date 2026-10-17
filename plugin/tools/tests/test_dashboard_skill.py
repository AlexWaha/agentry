"""task-0143 (FR-64, FR-71, FR-72): the `/agentry:dashboard` skill and its catalog entry.

Reads the real tree: the skill file, the catalog, and the script the skill runs.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN = TESTS_DIR.parents[1]
SKILL_DIR = PLUGIN / "skills" / "dashboard"
SKILL = SKILL_DIR / "SKILL.md"
CATALOG = PLUGIN / "skills" / "README.md"
SERVER = PLUGIN / "tools" / "dashboard" / "server.py"
LAUNCH_COMMAND = 'python "${CLAUDE_PLUGIN_ROOT}/tools/dashboard/server.py"'


def split_skill(text: str) -> tuple[dict[str, str], str]:
    """Frontmatter keys (single-line values) and the body of a SKILL.md."""
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", text, re.DOTALL)
    assert match, "SKILL.md has no frontmatter block"
    keys = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        keys[key.strip()] = value.strip()
    return keys, match.group(2)


class SkillFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")
        self.front, self.body = split_skill(self.text)

    def test_frontmatter_has_the_four_keys(self) -> None:
        self.assertEqual(self.front["name"], "dashboard")
        self.assertEqual(self.front["disable-model-invocation"], "true")
        self.assertEqual(self.front["allowed-tools"], "Bash")
        self.assertEqual(self.front["argument-hint"], "[stop]")

    def test_name_equals_directory_name(self) -> None:
        self.assertEqual(SKILL_DIR.name, self.front["name"])

    def test_description_has_a_use_when_trigger_and_fits_the_limit(self) -> None:
        description = self.front["description"]
        self.assertIn("Use when", description)
        self.assertLess(len(description), 1000)

    def test_body_is_under_200_lines(self) -> None:
        self.assertLess(len(self.text.splitlines()), 200)

    def test_body_runs_the_server_script_through_the_plugin_root(self) -> None:
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/tools/dashboard/server.py", self.body)
        self.assertIn(LAUNCH_COMMAND, self.body)

    def test_body_starts_the_server_in_the_background_and_reads_the_first_line(self) -> None:
        self.assertIn("run_in_background", self.body)
        self.assertIn("first line", self.body)

    def test_body_has_a_stop_branch_that_ends_the_background_task(self) -> None:
        self.assertRegex(self.body, r"(?m)^### Argument `stop`$")
        stop = self.body.split("### Argument `stop`", 1)[1]
        self.assertIn("background task", stop)

    def test_body_names_no_absolute_path_into_the_development_workspace(self) -> None:
        for needle in ("E:/Personal", "E:\\Personal", "Agenty", "C:/Users", "C:\\Users"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, self.text)

    def test_body_has_no_em_or_en_dash(self) -> None:
        self.assertNotIn("\u2014", self.text)
        self.assertNotIn("\u2013", self.text)


class ScriptTest(unittest.TestCase):
    def test_the_script_the_skill_names_exists(self) -> None:
        self.assertTrue(SERVER.is_file())

    def test_dashboard_python_sources_name_no_development_workspace_path(self) -> None:
        sources = sorted(SERVER.parent.glob("*.py"))
        self.assertIn(SERVER, sources)
        for source in sources:
            text = source.read_text(encoding="utf-8")
            for needle in ("E:/Personal", "E:\\Personal", "Agenty"):
                with self.subTest(file=source.name, needle=needle):
                    self.assertNotIn(needle, text)

    def test_help_exits_zero_without_a_project(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        result = subprocess.run([sys.executable, str(SERVER), "--help"], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--no-open", result.stdout)
        self.assertIn("--idle-timeout", result.stdout)


class CatalogTest(unittest.TestCase):
    def test_catalog_lists_dashboard_with_its_command(self) -> None:
        rows = [line for line in CATALOG.read_text(encoding="utf-8").splitlines()
                if line.startswith("| dashboard |")]
        self.assertEqual(len(rows), 1)
        self.assertIn("/agentry:dashboard", rows[0])

    def test_catalog_row_is_in_alphabetical_place(self) -> None:
        names = [line.split("|")[1].strip() for line in CATALOG.read_text(encoding="utf-8").splitlines()
                 if re.match(r"^\| [a-z][a-z-]* \|", line) and not line.startswith("| Skill |")]
        self.assertEqual(names, sorted(names))


if __name__ == "__main__":
    unittest.main()
