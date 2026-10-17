"""Docs-links test (FR-113): every "Learn more" target of the dashboard points at something that exists.

`dashboard/src/config/docs-links.json` maps each page id to a documentation file and a heading anchor. The
page ids are the keys of `ROUTES` in `dashboard/src/config/routes.ts`. The check, in one function:

  * every page id has an entry;
  * every entry names a Markdown file directly under `docs/` that exists;
  * every non-empty anchor equals the GitHub slug of a heading in that file.

The shipped repository must pass; each kind of break is injected into a throwaway copy and must be reported.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
import tmproot  # noqa: E402

REPO = TESTS_DIR.parents[2]
DASHBOARD_CONFIG = REPO / "dashboard" / "src" / "config"
LINKS_FILE = DASHBOARD_CONFIG / "docs-links.json"
ROUTES_FILE = DASHBOARD_CONFIG / "routes.ts"
DOCS = REPO / "docs"

ROUTE_KEY = re.compile(r"^  (\w+):\s*\"", re.MULTILINE)
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
HTML_TAG = re.compile(r"<[^>]+>")
SLUG_DROP = re.compile(r"[^\w\- ]", re.UNICODE)
DOCS_PATH = re.compile(r"^docs/[\w-]+\.md$")


def route_ids(routes_ts: str) -> list[str]:
    """The page ids: the keys of the `ROUTES` object."""
    body = routes_ts.split("export const ROUTES = {", 1)[1].split("} as const", 1)[0]
    return ROUTE_KEY.findall(body)


def slug(heading: str) -> str:
    """The GitHub anchor of a heading: markup removed, lower case, punctuation dropped, spaces to hyphens."""
    text = MD_LINK.sub(r"\1", heading)
    text = HTML_TAG.sub("", text)
    text = text.replace("`", "").replace("*", "")
    return SLUG_DROP.sub("", text.lower()).replace(" ", "-")


def anchors(markdown: str) -> set[str]:
    """The anchors of a document; a repeated heading gets `-1`, `-2` as on GitHub. Fenced code is skipped."""
    seen: dict[str, int] = {}
    found: set[str] = set()
    fenced = False
    for line in markdown.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(1))
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def problems(links: dict, ids: list[str], root: Path) -> list[str]:
    """Every defect of the docs-links table, as one line each; an empty list means the table is sound."""
    out = [f"page '{page}' has no entry" for page in ids if page not in links]
    for page, entry in sorted(links.items()):
        path, anchor = entry.get("path", ""), entry.get("anchor", "")
        if not DOCS_PATH.match(path):
            out.append(f"'{page}': path '{path}' is not a Markdown file directly under docs/")
            continue
        target = root / path
        if not target.is_file():
            out.append(f"'{page}': file '{path}' does not exist")
        elif anchor and anchor not in anchors(target.read_text(encoding="utf-8")):
            out.append(f"'{page}': anchor '#{anchor}' is not a heading of '{path}'")
    return out


class SlugTest(unittest.TestCase):
    def test_slug_follows_the_github_rules(self):
        self.assertEqual(slug("How a checkpoint works"), "how-a-checkpoint-works")
        self.assertEqual(slug("Handoff and memory between tasks"), "handoff-and-memory-between-tasks")
        self.assertEqual(slug("`advance.py` and the *gate*"), "advancepy-and-the-gate")
        self.assertEqual(slug("Plan -> approve (v2)"), "plan---approve-v2")
        self.assertEqual(slug("[Docs](https://example.com) page"), "docs-page")

    def test_a_repeated_heading_gets_a_numbered_anchor_and_fenced_code_is_skipped(self):
        text = "# Title\n## Setup\n```\n# not a heading\n```\n## Setup\n"

        self.assertEqual(anchors(text), {"title", "setup", "setup-1"})

    def test_route_ids_are_read_from_the_routes_object(self):
        text = 'export const ROUTES = {\n  overview: "/",\n  task: "/tasks/$taskId",\n} as const;\n'

        self.assertEqual(route_ids(text), ["overview", "task"])


class ShippedRepositoryTest(unittest.TestCase):
    def test_the_shipped_table_has_no_defect(self):
        links = json.loads(LINKS_FILE.read_text(encoding="utf-8"))
        ids = route_ids(ROUTES_FILE.read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(ids), 12)
        self.assertEqual(problems(links, ids, REPO), [])


class InjectedDefectTest(unittest.TestCase):
    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_docs_links_")
        (self.root / "docs").mkdir()
        page = self.root / "docs" / "pipeline.md"
        page.write_text("# Pipeline\n## The build pipeline\n", encoding="utf-8")
        self.ids = ["overview", "board"]
        self.links = {
            "overview": {"path": "docs/pipeline.md", "anchor": ""},
            "board": {"path": "docs/pipeline.md", "anchor": "the-build-pipeline"},
        }

    def test_a_sound_table_passes(self):
        self.assertEqual(problems(self.links, self.ids, self.root), [])

    def test_an_entry_pointing_at_a_missing_file_is_reported(self):
        self.links["board"]["path"] = "docs/gone.md"

        found = problems(self.links, self.ids, self.root)

        self.assertEqual(found, ["'board': file 'docs/gone.md' does not exist"])

    def test_an_entry_pointing_at_a_missing_anchor_is_reported(self):
        self.links["board"]["anchor"] = "no-such-heading"

        self.assertEqual(
            problems(self.links, self.ids, self.root),
            ["'board': anchor '#no-such-heading' is not a heading of 'docs/pipeline.md'"],
        )

    def test_a_route_without_an_entry_is_reported(self):
        del self.links["board"]

        self.assertEqual(problems(self.links, self.ids, self.root), ["page 'board' has no entry"])

    def test_a_path_outside_docs_is_reported(self):
        self.links["overview"]["path"] = "README.md"

        found = problems(self.links, self.ids, self.root)

        self.assertIn("is not a Markdown file directly under docs/", found[0])


if __name__ == "__main__":
    unittest.main()
