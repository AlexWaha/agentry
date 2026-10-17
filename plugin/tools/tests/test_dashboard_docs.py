"""task-0143 (FR-73, FR-119): the dashboard user page, its links, the changelog entry and the
release runbook step.

Reads the real tree: the page, its index rows, the changelog entry and the runbook step must
all exist for these tests to pass.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCS = REPO / "docs"
PAGE = DOCS / "dashboard.md"
DOCS_INDEX = DOCS / "README.md"
COMMANDS = DOCS / "commands.md"
ROOT_README = REPO / "README.md"
CHANGELOG = REPO / "CHANGELOG.md"
ORCHESTRATION = REPO / ".claude" / "rules" / "orchestration.md"

REQUIRED_SECTIONS = ("start", "stop", "security model", "ssh", "troubleshooting")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def headings(text: str) -> list[str]:
    return [m.group(1).strip().lower() for m in re.finditer(r"(?m)^#{2,3} (.+)$", text)]


def section(text: str, title: str) -> str:
    """The body of the `## <title>` section (case-insensitive prefix match), to the next `## `."""
    match = re.search(rf"(?ims)^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", text)
    return match.group(1) if match else ""


class PageTest(unittest.TestCase):
    def test_page_exists(self) -> None:
        self.assertTrue(PAGE.is_file(), "docs/dashboard.md is missing")

    def test_page_has_every_required_section(self) -> None:
        found = headings(read(PAGE))
        for needed in REQUIRED_SECTIONS:
            with self.subTest(section=needed):
                self.assertTrue(any(needed in h for h in found), f"no heading mentions {needed!r}: {found}")

    def test_page_has_a_table_of_contents(self) -> None:
        self.assertRegex(read(PAGE), r"(?i)table of contents")

    def test_page_records_the_measured_stop_behaviour(self) -> None:
        text = read(PAGE).lower()
        for needle in ("background", "session", "idle", "ctrl+c"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_every_relative_link_resolves(self) -> None:
        text = read(PAGE)
        links = [m.group(1) for m in LINK.finditer(text)]
        self.assertTrue(links, "the page links to nothing")
        for target in links:
            if re.match(r"^(https?:|mailto:|#)", target):
                continue
            path = target.split("#", 1)[0]
            with self.subTest(link=target):
                self.assertTrue((PAGE.parent / path).resolve().exists(), f"{target} does not resolve")

    def test_page_names_no_path_into_the_development_workspace(self) -> None:
        text = read(PAGE)
        for needle in ("E:/Personal", "E:\\Personal", "C:/Users", "C:\\Users"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text)


class IndexLinkTest(unittest.TestCase):
    def test_docs_index_links_the_page_directly_after_commands(self) -> None:
        rows = [line for line in read(DOCS_INDEX).splitlines() if line.startswith("|")]
        commands = next(i for i, line in enumerate(rows) if "(commands.md)" in line)
        self.assertIn("(dashboard.md)", rows[commands + 1])

    def test_commands_page_links_the_page(self) -> None:
        text = read(COMMANDS)
        self.assertIn("dashboard.md", text)
        self.assertIn("/agentry:dashboard", text)

    def test_root_readme_links_the_page_in_the_docs_table(self) -> None:
        rows = [line for line in read(ROOT_README).splitlines() if line.startswith("|")]
        commands = next(i for i, line in enumerate(rows) if "(docs/commands.md)" in line)
        self.assertIn("(docs/dashboard.md)", rows[commands + 1])


class ChangelogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(CHANGELOG)
        self.release = section(self.text, "[0.2.0]")

    def test_release_added_lists_the_dashboard_and_the_extending_page(self) -> None:
        added = self.release.split("### Added", 1)[1].split("### ", 1)[0]
        self.assertIn("/agentry:dashboard", added)
        self.assertIn("docs/dashboard.md", added)
        bullets = ["- " + b for b in added.split("\n- ")]
        extending = [b for b in bullets if "docs/extending.md" in b]
        self.assertEqual(len(extending), 1)
        self.assertIn("(task-0148)", extending[0])


class RunbookTest(unittest.TestCase):
    def setUp(self) -> None:
        text = read(ORCHESTRATION)
        self.runbook = text.split("### Deploying a harness change", 1)[1].split("\n## ", 1)[0]

    def test_clean_clone_build_step_is_named(self) -> None:
        self.assertIn("npm ci && npm run build", self.runbook)
        self.assertIn("clean clone", self.runbook)

    def test_dist_must_show_nothing_in_porcelain_status(self) -> None:
        self.assertIn("git status --porcelain plugin/tools/dashboard/dist", self.runbook)
        self.assertRegex(self.runbook, r"must print\s+nothing")

    def test_the_dist_check_comes_before_the_version_bump_and_the_tag(self) -> None:
        check = self.runbook.index("git status --porcelain plugin/tools/dashboard/dist")
        bump = self.runbook.index("bump `version`")
        tag = self.runbook.index("git tag vX.Y.Z")
        self.assertLess(check, bump)
        self.assertLess(bump, tag)


class DashTest(unittest.TestCase):
    def test_written_files_have_no_em_or_en_dash(self) -> None:
        for path in (PAGE, DOCS_INDEX, COMMANDS, ROOT_README, CHANGELOG):
            if not path.is_file():
                continue
            with self.subTest(file=path.name):
                text = read(path)
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)


if __name__ == "__main__":
    unittest.main()
