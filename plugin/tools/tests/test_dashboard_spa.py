"""SPA fallback (FR-60, FR-78): every route of the app, loaded directly (a browser refresh), gets index.html.

The routes are read from dashboard/src/config/routes.ts, so a route added there without server support
fails here. Static files need no token (FR-56), so the requests below send none.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import tmproot

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
import dashboard_sandbox as sandbox  # noqa: E402

REPO = TESTS_DIR.parents[2]
DIST = TESTS_DIR.parent / "dashboard" / "dist"
ROUTES_TS = REPO / "dashboard" / "src" / "config" / "routes.ts"

NO_TOKEN = {"Authorization": None}
EXPECTED_ROUTE_COUNT = 12


def route_patterns() -> list[str]:
    """The twelve route patterns declared in ROUTES, in order."""
    text = ROUTES_TS.read_text(encoding="utf-8")
    block = text[text.index("export const ROUTES"):text.index("} as const")]
    return re.findall(r':\s*"(/[^"]*)"', block)


def concrete(pattern: str) -> str:
    return re.sub(r"\$\w+", "task-0001", pattern)


class SpaFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = tmproot.mkdtemp("dash_spa_")
        cls.addClassCleanup(tmproot.rmtree, root)
        cls.server = sandbox.start_server(sandbox.build_project(root / "project", "small"), dist=DIST)
        cls.addClassCleanup(cls.server.stop)
        cls.index = (DIST / "index.html").read_bytes()

    def test_there_are_twelve_routes(self):
        self.assertEqual(len(route_patterns()), EXPECTED_ROUTE_COUNT)

    def test_every_app_route_returns_index_html(self):
        for pattern in route_patterns():
            path = concrete(pattern)
            with self.subTest(path=path):
                status, head, body = self.server.get(path, NO_TOKEN)
                self.assertEqual(status, 200)
                self.assertEqual(head.get("Content-Type"), "text/html; charset=utf-8")
                self.assertEqual(body, self.index)

    def test_deep_and_unknown_paths_without_an_extension_also_fall_back(self):
        paths = ("/nope", "/tasks/task-0001/extra", "/handoffs/task-0001?tab=x", "/memory?tab=stats&page=2")
        for path in paths:
            with self.subTest(path=path):
                status, _head, body = self.server.get(path, NO_TOKEN)
                self.assertEqual(status, 200)
                self.assertEqual(body, self.index)

    def test_index_html_is_never_cached(self):
        _status, head, _body = self.server.get("/board", NO_TOKEN)

        self.assertEqual(head.get("Cache-Control"), "no-store")

    def test_a_missing_file_with_an_extension_is_a_404_not_the_app(self):
        status, _head, body = self.server.get("/assets/missing-abc123.js", NO_TOKEN)

        self.assertEqual(status, 404)
        self.assertNotEqual(body, self.index)


if __name__ == "__main__":
    unittest.main()
