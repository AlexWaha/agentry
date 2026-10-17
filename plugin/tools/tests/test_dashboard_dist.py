"""dist_manifest: integrity check of the committed SPA build, without Node (spec-0002 FR-115, FR-116, NFR-11).

Each case builds a small dist next to a small `dashboard/` source tree in a sandbox, writes the manifest
the way `npm run build` does, then damages one thing and expects check() to name it.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import unittest
import unittest.mock
from contextlib import redirect_stderr
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = TESTS_DIR.parent / "dashboard"
for _dir in (DASHBOARD_DIR, TESTS_DIR):
    sys.path.insert(0, str(_dir))

import dashboard_sandbox as sb
import dist_manifest
import tmproot


class DistCase(unittest.TestCase):

    def setUp(self):
        self.base = tmproot.sandbox(self, "dash_dist_")
        self.dist = sb.fixture_dist(self.base / "dist")
        self.source = self.base / "dashboard"
        (self.source / "src" / "lib").mkdir(parents=True)
        (self.source / "package.json").write_text('{"name": "dashboard"}\n', encoding="utf-8")
        (self.source / "src" / "main.tsx").write_text("export const x = 1;\n", encoding="utf-8")
        (self.source / "src" / "lib" / "format.ts").write_text("export const f = 2;\n", encoding="utf-8")
        for ignored in ("node_modules/react", "coverage", "dist"):
            (self.source / ignored).mkdir(parents=True)
            (self.source / ignored / "noise.js").write_text("ignored\n", encoding="utf-8")
        self.rebuild()

    def rebuild(self) -> None:
        """What `npm run build` ends with."""
        dist_manifest.write(self.dist, self.source)

    def check(self, source: bool = True) -> list[str]:
        return dist_manifest.check(self.dist, self.source if source else None)

    def index(self, text: str) -> None:
        (self.dist / "index.html").write_text(text, encoding="utf-8")
        self.rebuild()  # the manifest matches, so only the index rules can complain

    def assertProblem(self, needle: str, problems: list[str]) -> None:
        self.assertTrue(any(needle in p for p in problems), problems)


class CheckTest(DistCase):

    def test_a_clean_dist_has_no_problems(self):
        self.assertEqual([], self.check())

    def test_a_modified_dist_file_is_named(self):
        (self.dist / "assets" / "app-abc123.js").write_text("console.log('tampered');\n", encoding="utf-8")
        problems = self.check()
        self.assertEqual(1, len(problems), problems)
        self.assertProblem("modified", problems)
        self.assertProblem("assets/app-abc123.js", problems)

    def test_an_added_file_is_named(self):
        (self.dist / "assets" / "extra.js").write_text("x\n", encoding="utf-8")
        problems = self.check()
        self.assertEqual(1, len(problems), problems)
        self.assertProblem("not in the manifest", problems)
        self.assertProblem("assets/extra.js", problems)

    def test_a_removed_file_is_named(self):
        (self.dist / "assets" / "app-abc123.css").unlink()
        problems = self.check()
        self.assertEqual(1, len(problems), problems)
        self.assertProblem("missing from dist", problems)
        self.assertProblem("assets/app-abc123.css", problems)

    def test_an_inline_script_in_the_index_is_a_problem(self):
        self.index('<!doctype html><html><head><script>alert(1)</script></head><body></body></html>')
        problems = self.check()
        self.assertEqual(1, len(problems), problems)
        self.assertProblem("inline <script>", problems)

    def test_an_inline_script_is_found_whatever_its_spelling(self):
        for tag in ('<SCRIPT>x</SCRIPT>', '<script type="module">x</script>', '<script\n>x</script>'):
            with self.subTest(tag=tag):
                self.index(f"<html><head>{tag}</head></html>")
                self.assertProblem("inline <script>", self.check())

    def test_scripts_with_a_src_are_fine(self):
        self.index('<html><head><script src="/a.js"></script>'
                   '<script type="module" crossorigin src="/assets/b.js"></script></head></html>')
        self.assertEqual([], self.check())

    def test_an_external_url_in_the_index_is_a_problem(self):
        for html in ('<link rel="stylesheet" href="https://cdn.example/x.css">',
                     '<script src="http://cdn.example/x.js"></script>',
                     '<script src="//cdn.example/x.js"></script>',
                     '<img src=\'//cdn.example/x.png\'>'):
            with self.subTest(html=html):
                self.index(f"<html><head>{html}</head></html>")
                self.assertProblem("external URL", self.check())

    def test_relative_urls_are_not_external(self):
        self.index('<html><head><link rel="icon" href="/favicon.svg">'
                   '<link href="assets/x.css" rel="stylesheet"></head></html>')
        self.assertEqual([], self.check())

    def test_a_changed_source_file_is_a_problem(self):
        (self.source / "src" / "main.tsx").write_text("export const x = 2;\n", encoding="utf-8")
        problems = self.check()
        self.assertEqual(1, len(problems), problems)
        self.assertProblem("source", problems)

    def test_an_added_or_removed_source_file_is_a_problem(self):
        added = self.source / "src" / "new.ts"
        added.write_text("export {};\n", encoding="utf-8")
        self.assertProblem("source", self.check())
        added.unlink()
        self.assertEqual([], self.check())
        (self.source / "src" / "lib" / "format.ts").unlink()
        self.assertProblem("source", self.check())

    def test_a_renamed_source_file_is_a_problem(self):
        (self.source / "src" / "lib" / "format.ts").rename(self.source / "src" / "lib" / "fmt.ts")
        self.assertProblem("source", self.check())

    def test_the_source_check_is_skipped_without_the_dashboard_folder(self):
        shutil.rmtree(self.source)
        self.assertEqual([], dist_manifest.check(self.dist, self.source))
        self.assertEqual([], dist_manifest.check(self.dist, None))

    def test_build_output_and_dependencies_are_not_source(self):
        for noise in ("node_modules/react/noise.js", "coverage/noise.js", "dist/noise.js"):
            (self.source / noise).write_text("changed\n", encoding="utf-8")
        (self.source / "tsconfig.tsbuildinfo").write_text("{}", encoding="utf-8")
        self.assertEqual([], self.check())

    def test_line_endings_do_not_change_the_source_hash(self):
        path = self.source / "src" / "main.tsx"
        path.write_bytes(b"export const x = 1;\n")
        before = dist_manifest.source_sha256(self.source)
        path.write_bytes(b"export const x = 1;\r\n")
        self.assertEqual(before, dist_manifest.source_sha256(self.source))

    def test_a_missing_or_broken_manifest_is_a_problem(self):
        manifest = self.dist / "manifest.json"
        manifest.write_text("{not json", encoding="utf-8")
        self.assertProblem("manifest.json", self.check())
        manifest.write_text('{"files": []}', encoding="utf-8")
        self.assertProblem("manifest.json", self.check())
        manifest.unlink()
        self.assertProblem("manifest.json", self.check())

    def test_a_missing_dist_folder_is_a_problem(self):
        shutil.rmtree(self.dist)
        self.assertProblem("dist", self.check())

    def test_every_problem_is_reported_not_just_the_first(self):
        (self.dist / "assets" / "app-abc123.js").write_text("x\n", encoding="utf-8")
        (self.dist / "assets" / "extra.js").write_text("x\n", encoding="utf-8")
        (self.dist / "assets" / "note.txt").unlink()
        (self.source / "package.json").write_text("{}\n", encoding="utf-8")
        self.assertEqual(4, len(self.check()))


class ManifestTest(DistCase):

    def manifest_bytes(self) -> bytes:
        return (self.dist / "manifest.json").read_bytes()

    def test_building_twice_gives_byte_identical_manifests(self):
        first = self.manifest_bytes()
        self.rebuild()
        self.assertEqual(first, self.manifest_bytes())

    def test_the_manifest_has_the_two_keys_and_hashes_every_file_but_itself(self):
        manifest = json.loads(self.manifest_bytes())
        self.assertEqual({"source_sha256", "files"}, set(manifest))
        self.assertRegex(manifest["source_sha256"], r"^[0-9a-f]{64}$")
        on_disk = sorted(p.relative_to(self.dist).as_posix() for p in self.dist.rglob("*")
                         if p.is_file() and p.name != "manifest.json")
        self.assertEqual(on_disk, list(manifest["files"]))
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", h) for h in manifest["files"].values()))

    def test_changing_a_source_file_changes_the_source_hash(self):
        before = json.loads(self.manifest_bytes())["source_sha256"]
        (self.source / "src" / "main.tsx").write_text("export const x = 3;\n", encoding="utf-8")
        self.rebuild()
        self.assertNotEqual(before, json.loads(self.manifest_bytes())["source_sha256"])

    def test_no_timestamp_tool_version_or_absolute_path_is_recorded(self):
        text = self.manifest_bytes().decode("utf-8")
        self.assertNotRegex(text, r"\d{4}-\d{2}-\d{2}")
        self.assertNotRegex(text, r"\d{2}:\d{2}:\d{2}")
        self.assertNotIn(str(self.base), text)
        self.assertNotIn(self.base.name, text)
        self.assertNotRegex(text, r"[A-Za-z]:[\\/]")
        self.assertNotIn("version", text.lower())

    def test_the_manifest_is_written_with_unix_line_endings(self):
        self.assertNotIn(b"\r", self.manifest_bytes())
        self.assertTrue(self.manifest_bytes().endswith(b"\n"))

    def test_a_fixture_dist_without_a_source_records_an_empty_source_hash(self):
        other = sb.fixture_dist(self.base / "dist2")
        manifest = json.loads((other / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("", manifest["source_sha256"])
        self.assertEqual([], dist_manifest.check(other, None))


class CommandLineTest(DistCase):

    def run_main(self, *argv: str) -> tuple[int, str]:
        err = io.StringIO()
        with unittest.mock.patch.object(dist_manifest, "DIST_DIR", self.dist), \
                unittest.mock.patch.object(dist_manifest, "SOURCE_DIR", self.source), redirect_stderr(err):
            return dist_manifest.main(list(argv)), err.getvalue()

    def test_write_then_check(self):
        (self.dist / "manifest.json").unlink()
        self.assertEqual((0, ""), self.run_main("--write"))
        self.assertEqual((0, ""), self.run_main("--check"))

    def test_check_prints_the_problems_and_exits_1(self):
        (self.dist / "assets" / "app-abc123.js").write_text("x\n", encoding="utf-8")
        code, err = self.run_main("--check")
        self.assertEqual(1, code)
        self.assertIn("modified", err)

    def test_write_without_a_dist_folder_is_an_error(self):
        shutil.rmtree(self.dist)
        code, err = self.run_main("--write")
        self.assertEqual(1, code)
        self.assertIn("vite build", err)


class NoNodeTest(unittest.TestCase):
    """NFR-11: an end user has no Node; the server must start and serve with it off PATH."""

    def test_the_server_starts_and_serves_with_node_removed_from_path(self):
        root = tmproot.sandbox(self, "dash_nonode_")
        sb.build_project(root)
        python_dir = str(Path(sys.executable).parent)
        self.assertIsNone(shutil.which("node", path=python_dir))
        with sb.start_server(root, env_extra={"PATH": python_dir}) as srv:
            self.assertEqual(200, srv.get("/", {"Authorization": None})[0])
            status, _, body = srv.get("/api/v1/status")
            self.assertEqual(200, status)
            self.assertEqual(root.name, json.loads(body)["data"]["project"])
            self.assertEqual("", srv.stderr_text())

    def test_the_check_itself_needs_no_node(self):
        base = tmproot.sandbox(self, "dash_nonode_check_")
        dist = sb.fixture_dist(base / "dist")
        with unittest.mock.patch.dict("os.environ", {"PATH": str(Path(sys.executable).parent)}):
            self.assertEqual([], dist_manifest.check(dist, None))


if __name__ == "__main__":
    unittest.main()
