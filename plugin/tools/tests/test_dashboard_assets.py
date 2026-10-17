"""The shipped dashboard build under plugin/tools/dashboard/dist (FR-86, FR-114 to FR-116, NFR-1).

No Node is needed: this reads the committed files. It is the check that a stale or hand-edited dist is a
red test, not a surprise at release time, and it serves the real dist to prove the favicon types.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
import unittest
from pathlib import Path

import tmproot

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
import dashboard_sandbox as sandbox  # noqa: E402

DASHBOARD_PY = TESTS_DIR.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD_PY))
import dist_manifest  # noqa: E402

DIST = DASHBOARD_PY / "dist"
JS_GZIP_BUDGET = 204_800
ICO_SIZES = [16, 32, 48]


def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


class ShippedDistTest(unittest.TestCase):
    def test_manifest_matches_the_dist_and_the_dashboard_source(self):
        self.assertEqual(dist_manifest.check(), [])

    def test_dist_holds_index_hashed_assets_and_manifest(self):
        self.assertTrue((DIST / "index.html").is_file())
        self.assertTrue((DIST / "manifest.json").is_file())
        scripts = sorted((DIST / "assets").glob("*.js"))
        styles = sorted((DIST / "assets").glob("*.css"))
        self.assertTrue(scripts and styles)
        for asset in scripts + styles:
            self.assertRegex(
                asset.name, r"^[\w-]+-[A-Za-z0-9_-]{8}\.(js|css)$", "asset names carry a content hash"
            )

    def test_manifest_has_no_timestamp_tool_version_or_absolute_path(self):
        text = (DIST / "manifest.json").read_text(encoding="utf-8")
        manifest = json.loads(text)

        self.assertEqual(sorted(manifest), ["files", "source_sha256"])
        self.assertRegex(manifest["source_sha256"], r"^[0-9a-f]{64}$")
        for rel, digest in manifest["files"].items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertNotRegex(rel, r"^(?:[A-Za-z]:|/)")
        self.assertNotRegex(text, r"[A-Za-z]:[\\/]")
        self.assertNotRegex(text, r"\b20\d\d-\d\d-\d\d")
        self.assertNotIn("version", text)

    def test_javascript_gzip_total_is_within_the_budget(self):
        total = sum(gzip_size(path) for path in (DIST / "assets").glob("*.js"))

        self.assertLessEqual(total, JS_GZIP_BUDGET, f"{total} bytes gzip, budget {JS_GZIP_BUDGET}")

    def test_index_html_has_a_script_src_in_head_and_nothing_inline_or_external(self):
        html = (DIST / "index.html").read_text(encoding="utf-8")
        head = html[html.index("<head"):html.index("</head>")]

        self.assertRegex(head, r"<script\b[^>]*\bsrc=")
        for tag in re.findall(r"<script\b[^>]*>", html):
            self.assertRegex(tag, r"\bsrc=")
        self.assertNotRegex(html, r"<script\b[^>]*>\s*[^<\s]")
        self.assertNotRegex(html, r"https?://")

    def test_index_html_links_both_favicons_and_the_theme_script(self):
        html = (DIST / "index.html").read_text(encoding="utf-8")

        self.assertIn('href="/favicon.svg"', html)
        self.assertIn('href="/favicon.ico"', html)
        self.assertIn('src="/theme-init.js"', html)
        self.assertTrue((DIST / "theme-init.js").is_file())

    def test_favicon_ico_holds_16_32_and_48_pixel_images(self):
        data = (DIST / "favicon.ico").read_bytes()
        count = int.from_bytes(data[4:6], "little")
        sizes = sorted(data[6 + index * 16] for index in range(count))

        self.assertEqual(int.from_bytes(data[2:4], "little"), 1, "type 1 is an icon")
        self.assertEqual(sizes, ICO_SIZES)

    def test_favicon_svg_is_the_logo(self):
        svg = (DIST / "favicon.svg").read_text(encoding="utf-8")

        self.assertIn("<svg", svg)
        self.assertIn("#E8590C", svg)

    def test_no_source_map_or_dev_file_ships(self):
        names = [path.name for path in DIST.rglob("*") if path.is_file()]

        self.assertEqual([name for name in names if name.endswith(".map")], [])


class ServedDistTest(unittest.TestCase):
    """The server serves the real dist with the explicit FR-61 types."""

    @classmethod
    def setUpClass(cls):
        root = tmproot.mkdtemp("dash_assets_")
        cls.addClassCleanup(tmproot.rmtree, root)
        project = sandbox.build_project(root / "project", "small")
        cls.server = sandbox.start_server(project, dist=DIST)
        cls.addClassCleanup(cls.server.stop)

    def type_of(self, path: str) -> tuple[int, str]:
        status, head, _body = self.server.get(path)
        return status, head.get("Content-Type", "")

    def test_favicons_and_theme_script_are_served_with_the_explicit_types(self):
        self.assertEqual(self.type_of("/favicon.svg"), (200, "image/svg+xml"))
        self.assertEqual(self.type_of("/favicon.ico"), (200, "image/x-icon"))
        self.assertEqual(self.type_of("/theme-init.js"), (200, "text/javascript; charset=utf-8"))

    def test_hashed_assets_are_served_with_their_types_and_immutable_caching(self):
        script = next((DIST / "assets").glob("*.js"))
        style = next((DIST / "assets").glob("*.css"))

        self.assertEqual(self.type_of(f"/assets/{script.name}"), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(self.type_of(f"/assets/{style.name}"), (200, "text/css; charset=utf-8"))
        _status, head, _body = self.server.get(f"/assets/{script.name}")
        self.assertIn("immutable", head.get("Cache-Control", ""))

    def test_served_bytes_equal_the_files_on_disk(self):
        for rel in ("favicon.ico", "favicon.svg", "theme-init.js", "index.html"):
            with self.subTest(file=rel):
                _status, _head, body = self.server.get("/" + rel)
                self.assertEqual(body, (DIST / rel).read_bytes())


if __name__ == "__main__":
    unittest.main()
