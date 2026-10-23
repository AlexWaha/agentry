"""`npm run build` in dashboard/ (FR-114, FR-115, NFR-1). Needs Node and installed dashboard packages.

The real build writes the committed plugin/tools/dashboard/dist, so these tests never run it there. They copy
the sources into a sandbox that has the same relative layout (dashboard/ next to plugin/tools/dashboard/),
link the installed node_modules into the copy, and build there. The copy's TypeScript build-info files are
redirected into the sandbox so the real project's incremental state is not touched.

On a machine without Node the whole module is skipped: end users never build (NFR-11). The dev gate
(`scripts/dashboard_gate.py`) is what makes a dev machine run it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import dashboard_budget as budget
import tmproot

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parents[2]
DASHBOARD = REPO / "dashboard"
MANIFEST_SCRIPT = REPO / "plugin" / "tools" / "dashboard" / "dist_manifest.py"
NPM = shutil.which("npm")
HAVE_NODE = NPM is not None and (DASHBOARD / "node_modules").is_dir()
BUILD_TIMEOUT = 300


def link_directory(link: Path, target: Path) -> None:
    """A directory junction on Windows (no privilege needed), a symlink elsewhere."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


@unittest.skipUnless(HAVE_NODE, "Node/npm or dashboard/node_modules is not available (dev machines only)")
class DistBuildTest(unittest.TestCase):
    def setUp(self):
        self.base = tmproot.sandbox(self, "dashbuild")
        self.dashboard = self.base / "dashboard"
        skip = shutil.ignore_patterns("node_modules", "coverage", ".tsbuild")
        shutil.copytree(DASHBOARD, self.dashboard, ignore=skip)
        for name in ("tsconfig.app.json", "tsconfig.node.json"):
            path = self.dashboard / name
            text = path.read_text(encoding="utf-8").replace("./node_modules/.tmp/", "./.tsbuild/")
            path.write_text(text, encoding="utf-8")
        tool_dir = self.base / "plugin" / "tools" / "dashboard"
        tool_dir.mkdir(parents=True)
        shutil.copy(MANIFEST_SCRIPT, tool_dir / "dist_manifest.py")
        self.dist = tool_dir / "dist"
        link = self.dashboard / "node_modules"
        link_directory(link, DASHBOARD / "node_modules")
        # Runs first on cleanup (registered after the sandbox's own): remove the link, never the target.
        self.addCleanup(os.rmdir, link)

    def build(self) -> subprocess.CompletedProcess[str]:
        # Node writes a compile cache under TMP/TEMP, which is the suite's run directory: switch it off so
        # nothing is left behind when the suite ends.
        env = {**os.environ, "NODE_DISABLE_COMPILE_CACHE": "1"}
        return subprocess.run([NPM, "run", "build"], cwd=self.dashboard, capture_output=True, text=True,
                              timeout=BUILD_TIMEOUT, check=False, env=env)

    def manifest_bytes(self) -> bytes:
        return (self.dist / "manifest.json").read_bytes()

    def check_manifest(self) -> subprocess.CompletedProcess[str]:
        script = self.base / "plugin" / "tools" / "dashboard" / "dist_manifest.py"
        return subprocess.run(
            [sys.executable, str(script), "--check"], capture_output=True, text=True, check=False
        )

    def test_build_succeeds_and_produces_index_hashed_assets_and_a_valid_manifest(self):
        done = self.build()

        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue((self.dist / "index.html").is_file())
        self.assertTrue(list((self.dist / "assets").glob("*.js")))
        self.assertTrue(list((self.dist / "assets").glob("*.css")))
        self.assertEqual(self.check_manifest().returncode, 0, self.check_manifest().stderr)
        self.assertEqual(list(self.dist.rglob("*.map")), [], "no source maps")

    def test_two_builds_of_the_same_source_give_a_byte_identical_manifest(self):
        self.assertEqual(self.build().returncode, 0)
        first = self.manifest_bytes()
        first_index = (self.dist / "index.html").read_bytes()

        self.assertEqual(self.build().returncode, 0)

        self.assertEqual(self.manifest_bytes(), first)
        self.assertEqual((self.dist / "index.html").read_bytes(), first_index)

    def test_a_source_change_changes_source_sha256(self):
        self.assertEqual(self.build().returncode, 0)
        before = json.loads(self.manifest_bytes())["source_sha256"]
        target = self.dashboard / "src" / "config" / "constants.ts"
        text = target.read_text(encoding="utf-8").replace("BOARD_COLUMN_CAP = 12", "BOARD_COLUMN_CAP = 13")
        target.write_text(text, encoding="utf-8")

        self.assertEqual(self.build().returncode, 0)

        self.assertNotEqual(json.loads(self.manifest_bytes())["source_sha256"], before)

    def test_manifest_holds_no_timestamp_or_absolute_path(self):
        self.assertEqual(self.build().returncode, 0)
        text = self.manifest_bytes().decode("utf-8")

        self.assertNotIn(str(self.base), text)
        self.assertNotIn(str(self.base).replace("\\", "/"), text)
        self.assertEqual(sorted(json.loads(text)), ["files", "source_sha256"])

    def test_a_type_error_fails_the_build_and_leaves_the_manifest_alone(self):
        self.assertEqual(self.build().returncode, 0)
        manifest = self.manifest_bytes()
        index = (self.dist / "index.html").read_bytes()
        broken = self.dashboard / "src" / "broken.ts"
        broken.write_text('export const broken: number = "not a number";\n', encoding="utf-8")

        done = self.build()

        self.assertNotEqual(done.returncode, 0)
        self.assertIn("broken.ts", done.stdout + done.stderr)
        self.assertEqual(self.manifest_bytes(), manifest, "manifest rewritten after a failed build")
        self.assertEqual((self.dist / "index.html").read_bytes(), index, "vite did not run after tsc failed")

    def test_the_javascript_stays_within_the_gzip_budgets(self):
        self.assertEqual(self.build().returncode, 0)

        rest, three = budget.gzip_totals(self.dist / "assets")

        self.assertLessEqual(rest, budget.JS_GZIP_BUDGET)
        self.assertGreater(three, 0)
        self.assertLessEqual(three, budget.GRAPH3D_GZIP_BUDGET)


if __name__ == "__main__":
    unittest.main()
