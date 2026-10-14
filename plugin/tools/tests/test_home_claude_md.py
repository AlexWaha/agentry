"""task-0135: pretool_gate.under_home_claude lets the user's own global
~/.claude/CLAUDE.md be edited, and nothing else under ~/.claude/ except plans/.

The home directory is a temp dir patched in as Path.home(), so the real
~/.claude is never touched.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import pretool_gate as pg
import tmproot


class HomeClaudeMdTest(unittest.TestCase):
    def setUp(self):
        self.home = tmproot.sandbox(self, "home-claude-md-").resolve()
        self.hc = self.home / ".claude"
        (self.hc / "plans").mkdir(parents=True)
        (self.hc / "agents").mkdir()
        (self.hc / "projects").mkdir()
        patcher = unittest.mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def denied(self, rel: str) -> bool:
        return pg.under_home_claude(str(self.hc / rel))

    def test_global_claude_md_is_allowed(self):
        self.assertFalse(self.denied("CLAUDE.md"))

    def test_global_claude_md_is_allowed_when_it_does_not_exist_yet(self):
        self.assertFalse((self.hc / "CLAUDE.md").exists())
        self.assertFalse(self.denied("CLAUDE.md"))

    def test_plans_exception_is_kept(self):
        self.assertFalse(self.denied("plans/some-plan.md"))

    def test_neighbours_under_home_claude_stay_denied(self):
        for rel in ("settings.json", "settings.local.json", "projects/x/memory/MEMORY.md",
                    "agents/a.md", "skills/s/SKILL.md", "CLAUDE.md.bak", "CLAUDE.mdx"):
            with self.subTest(rel=rel):
                self.assertTrue(self.denied(rel))

    def test_claude_md_in_a_subdirectory_stays_denied(self):
        for rel in ("agents/CLAUDE.md", "projects/CLAUDE.md", "plans/../agents/CLAUDE.md"):
            with self.subTest(rel=rel):
                self.assertTrue(self.denied(rel))

    def test_dotdot_spelling_that_resolves_to_the_file_is_allowed(self):
        self.assertFalse(pg.under_home_claude(str(self.hc / "agents" / ".." / "CLAUDE.md")))

    def test_dotdot_spelling_that_resolves_elsewhere_stays_denied(self):
        self.assertTrue(pg.under_home_claude(str(self.hc / "plans" / ".." / "settings.json")))
        self.assertTrue(pg.under_home_claude(str(self.hc / "CLAUDE.md" / ".." / "settings.json")))

    def test_a_path_outside_home_claude_is_not_affected(self):
        self.assertFalse(pg.under_home_claude(str(self.home / "CLAUDE.md")))
        self.assertFalse(pg.under_home_claude(str(self.home / "work" / ".claude" / "settings.json")))

    @unittest.skipUnless(os.name == "nt", "case-insensitive file names are a Windows rule")
    def test_case_variant_is_allowed_on_windows(self):
        self.assertFalse(pg.under_home_claude(str(self.hc / "claude.md")))
        self.assertFalse(pg.under_home_claude(str(self.hc / "CLAUDE.MD")))
        self.assertFalse(pg.under_home_claude(str(self.home / ".CLAUDE" / "CLAUDE.md")))

    @unittest.skipIf(os.name == "nt", "case-sensitive file names off Windows")
    def test_case_variant_stays_denied_off_windows(self):
        self.assertTrue(self.denied("claude.md"))

    def test_symlink_named_claude_md_pointing_elsewhere_stays_denied(self):
        target = self.hc / "settings.json"
        target.write_text("{}", encoding="utf-8")
        link = self.hc / "CLAUDE.md"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available on this host")
        self.assertTrue(pg.under_home_claude(str(link)))

    def test_handle_edit_allows_the_file_and_denies_settings(self):
        with unittest.mock.patch.object(pg, "deny", side_effect=lambda m: 2), \
                unittest.mock.patch.object(pg, "orch_check_edit", return_value=0):
            self.assertEqual(pg.handle_edit(str(self.hc / "settings.json"), orch=True), 2)
            self.assertNotEqual(
                pg.handle_edit(str(self.hc / "CLAUDE.md"), "plain text", orch=True),
                2,
            )


if __name__ == "__main__":
    unittest.main()
