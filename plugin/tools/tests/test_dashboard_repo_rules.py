"""Repository rules of the dashboard SPA (FR-74, FR-75, FR-117, FR-118): attributes, ignores, pinned
libraries, folder layout and the gate wiring. No Node is needed: everything here reads files and asks git.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DASHBOARD = REPO / "dashboard"
SRC = DASHBOARD / "src"
STACK_MD = REPO / ".agentry" / "project" / "stack.md"
PIPELINE_JSON = REPO / ".agentry" / "pipeline.json"

# Libraries FR-74 names, as package names.
REQUIRED_DEPENDENCIES = {
    "react": "dependencies",
    "react-dom": "dependencies",
    "@tanstack/react-router": "dependencies",
    "@tanstack/react-query": "dependencies",
    "lucide-react": "dependencies",
    "class-variance-authority": "dependencies",
    "d3-force": "dependencies",
    "tailwindcss": "devDependencies",
    "vite": "devDependencies",
    "typescript-native": "devDependencies",
    "vitest": "devDependencies",
    "eslint": "devDependencies",
}

SHARED_COMPONENTS = (
    "StatusChip", "Stepper", "EventTimeline", "StatCard", "EmptyState", "DocsLink", "Logo", "Pager", "Tabs",
)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False)


class GitAttributesTest(unittest.TestCase):
    def attrs(self, path: str) -> dict[str, str]:
        done = git("check-attr", "binary", "linguist-generated", "--", path)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = {}
        for line in done.stdout.splitlines():
            _file, name, value = line.rsplit(": ", 2)
            out[name] = value
        return out

    def test_dist_files_are_binary_and_generated(self):
        for path in (
            "plugin/tools/dashboard/dist/index.html",
            "plugin/tools/dashboard/dist/manifest.json",
            "plugin/tools/dashboard/dist/assets/index-abc123.js",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.attrs(path), {"binary": "set", "linguist-generated": "set"})

    def test_other_dashboard_files_are_ordinary_text(self):
        for path in ("plugin/tools/dashboard/server.py", "dashboard/src/main.tsx", "dashboard/package.json"):
            with self.subTest(path=path):
                unset = {"binary": "unspecified", "linguist-generated": "unspecified"}
                self.assertEqual(self.attrs(path), unset)


class GitIgnoreTest(unittest.TestCase):
    def ignored(self, path: str) -> bool:
        return git("check-ignore", "-q", "--", path).returncode == 0

    def test_installed_packages_and_coverage_are_ignored(self):
        self.assertTrue(self.ignored("dashboard/node_modules/x"))
        self.assertTrue(self.ignored("dashboard/node_modules/.vite/deps/y.js"))
        self.assertTrue(self.ignored("dashboard/coverage/x"))

    def test_sources_lockfile_and_dist_are_not_ignored(self):
        for path in (
            "dashboard/package.json",
            "dashboard/package-lock.json",
            "dashboard/src/main.tsx",
            "dashboard/public/favicon.ico",
            "plugin/tools/dashboard/dist/index.html",
            "plugin/tools/dashboard/dist/assets/index-abc123.js",
        ):
            with self.subTest(path=path):
                self.assertFalse(self.ignored(path))


class PackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = json.loads((DASHBOARD / "package.json").read_text(encoding="utf-8"))
        cls.lock = json.loads((DASHBOARD / "package-lock.json").read_text(encoding="utf-8"))
        cls.stack = STACK_MD.read_text(encoding="utf-8")

    def test_pins_every_library_of_fr_74(self):
        for name, section in REQUIRED_DEPENDENCIES.items():
            with self.subTest(package=name):
                self.assertIn(name, self.package[section])

    def test_react_is_version_19_and_typescript_checks_in_strict_mode(self):
        self.assertTrue(self.lock["packages"]["node_modules/react"]["version"].startswith("19."))
        tsconfig = (DASHBOARD / "tsconfig.app.json").read_text(encoding="utf-8")
        self.assertRegex(tsconfig, r'"strict":\s*true')

    def test_scripts_are_the_documented_commands(self):
        scripts = self.package["scripts"]
        for name in ("typecheck", "test", "lint", "build"):
            self.assertIn(name, scripts)
        self.assertIn("vite build", scripts["build"])
        self.assertIn("dist_manifest.py --write", scripts["build"])
        self.assertLess(scripts["build"].index("tsc"), scripts["build"].index("vite build"))

    def test_stack_md_records_the_locked_version_of_every_direct_dependency(self):
        missing = []
        for section in ("dependencies", "devDependencies"):
            for name in self.package[section]:
                entry = self.lock["packages"].get(f"node_modules/{name}")
                self.assertIsNotNone(entry, f"{name} is not in the lock file")
                version = entry["version"]
                row = re.compile(rf"\|\s*`?{re.escape(name)}`?\s*\|\s*{re.escape(version)}\s*\|")
                if not row.search(self.stack):
                    missing.append(f"{name} {version}")
        self.assertEqual(missing, [], "stack.md must list these exact versions in its dashboard table")

    def test_runtime_adds_no_dependency(self):
        server = REPO / "plugin" / "tools" / "dashboard"
        self.assertFalse((server / "package.json").exists())
        self.assertFalse((REPO / "plugin" / "package.json").exists())


class LayoutTest(unittest.TestCase):
    def test_src_has_the_folders_fr_75_names(self):
        folders = (
            "app", "config", "i18n", "styles", "lib",
            "components/ui", "components/shared", "types", "test/fixtures",
        )
        for folder in folders:
            with self.subTest(folder=folder):
                self.assertTrue((SRC / folder).is_dir())

    def test_styles_hold_the_token_layers(self):
        self.assertTrue((SRC / "styles" / "tokens.css").is_file())
        self.assertTrue((SRC / "styles" / "theme.css").is_file())

    def test_lib_and_config_files_named_by_the_spec(self):
        for name in ("api-client.ts", "ws-client.ts", "format.ts", "markdown.tsx", "search-params.ts"):
            with self.subTest(file=name):
                self.assertTrue((SRC / "lib" / name).is_file())
        config_files = (
            "nav.ts", "routes.ts", "query-keys.ts", "docs-links.json",
            "stages.ts", "storage-keys.ts", "constants.ts",
        )
        for name in config_files:
            with self.subTest(file=name):
                self.assertTrue((SRC / "config" / name).is_file())

    def test_shared_components_include_pager_and_tabs(self):
        shared = SRC / "components" / "shared"
        for name in SHARED_COMPONENTS:
            with self.subTest(component=name):
                self.assertTrue((shared / f"{name}.tsx").is_file())

    def test_types_file_holds_the_ws_and_topic_unions(self):
        text = (SRC / "types" / "api.ts").read_text(encoding="utf-8")
        for name in ("Topic", "WsMessage", "Status", "Run", "TaskDetail", "Metrics", "Harness", "HandoffDoc"):
            with self.subTest(type=name):
                self.assertRegex(text, rf"export (?:interface|type) {name}\b")

    def test_every_feature_folder_registers_its_routes(self):
        features = SRC / "features"
        self.assertTrue(features.is_dir(), "dashboard/src/features is missing")
        for folder in sorted(p for p in features.iterdir() if p.is_dir()):
            with self.subTest(feature=folder.name):
                routes = folder / "routes.tsx"
                self.assertTrue(routes.is_file(), f"{folder.name} has no routes.tsx")
                text = routes.read_text(encoding="utf-8")
                self.assertRegex(text, r"export (?:function|const) createRoutes\b")


class GateWiringTest(unittest.TestCase):
    """The dashboard gate commands are wired for frontend tasks (FR-118): the build exit gates and the repo
    stage commands in pipeline.json call scripts/dashboard_gate.py."""

    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(PIPELINE_JSON.read_text(encoding="utf-8"))

    def exit_gate(self, stage: str) -> str:
        for entry in self.config["pipelines"]["build"]["stages"]:
            if entry.get("name") == stage:
                return entry["exit_gate"]["cmd"]
        self.fail(f"no {stage} stage")

    def test_exit_gates_run_the_dashboard_check_of_their_stage(self):
        for stage, check in (("implement", "typecheck"), ("test", "test"), ("review", "lint")):
            with self.subTest(stage=stage):
                self.assertIn(f"scripts/dashboard_gate.py {check}", self.exit_gate(stage))

    def test_repo_stage_commands_match_the_exit_gates(self):
        stages = self.config["repos"]["agentry"]["stages"]
        self.assertIn("python scripts/dashboard_gate.py typecheck", stages["implement"])
        self.assertIn("python scripts/dashboard_gate.py lint", stages["review"])
        self.assertEqual(stages["test"], [])


if __name__ == "__main__":
    unittest.main()
