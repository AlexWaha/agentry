"""Static proof that the dashboard server only reads (spec-0002 FR-8, FR-71, NFR-2, NFR-4).

An AST scan of every module in plugin/tools/dashboard/ (dist_manifest.py is exempt from the
write rules because its --write half is the build step) fails on: a call to a harness function
outside the allowlist, a sqlite3.connect outside ro_connect, any file-writing API, a non-stdlib
import, a network-client import, and an absolute path into the development workspace. Each rule
is also shown to bite by running the scanner over a small injected fixture.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
DASHBOARD_DIR = TOOLS_DIR / "dashboard"

# The harness folders: a module whose name is a file stem in one of them is "the harness".
HARNESS_MODULES = frozenset(
    p.stem for folder in ("pipeline", "memory", "hooks") for p in (TOOLS_DIR / folder).glob("*.py"))
SIBLINGS = frozenset(p.stem for p in DASHBOARD_DIR.glob("*.py")) | HARNESS_MODULES | {"paths"}

# FR-8 (a): the only harness functions a dashboard module may call. Attribute reads such as
# approvals.LEVELS, main_thread_rules.RULES, events.KINDS or state.ST_PAUSED are not calls.
ALLOWED_CALLS = {
    "state": {"load_pipeline", "stages", "stage_names", "checkpoint_stages", "lanes",
              "is_abandoned", "covering_umbrella"},
    "events": {"task_report", "summary"},
    "memory": {"fts_query", "fts_terms", "query"},
    "inject": {"active_task_text", "render"},
    "handoff": {"owed", "validate_handoff", "chain_docs", "covered_required", "handoff_path"},
    "update": {"unstamped_done_tasks", "stamp_path"},
    "mode": {"modes", "default"},
    "approvals": {"never_granted"},
    "supervisor": {"pid_alive"},
}
BANNED_IMPORTS = {"urllib", "http.client", "ftplib", "smtplib", "requests", "tempfile", "shutil"}
WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch", "unlink", "rmdir", "rename",
                 "symlink_to", "hardlink_to", "chmod"}
WRITE_FUNCTIONS = {"os": {"remove", "unlink", "replace", "rename", "mkdir", "makedirs", "rmdir",
                          "removedirs", "write", "truncate", "chmod", "symlink", "link"},
                   "shutil": None, "tempfile": None}
WRITE_MODE_CHARS = set("wax+")
WORKSPACE_MARKERS = ("E:/Personal", "E:\\Personal", "Agenty")


def dashboard_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(DASHBOARD_DIR.glob("*.py"))}


def _mode_of(call: ast.Call) -> str | None:
    """The mode literal of an open() call, if it has one: the second argument of the builtin,
    the first of Path.open. A string that cannot be a mode (webbrowser.open's URL) is None."""
    position = 0 if isinstance(call.func, ast.Attribute) else 1
    arg = call.args[position] if len(call.args) > position else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            arg = keyword.value
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    return arg.value if len(arg.value) <= 4 and set(arg.value) <= set("rwxabt+U") else None


def imported_modules(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
            out += [f"{node.module}.{alias.name}" for alias in node.names]
    return out


def scan_source(name: str, source: str, check_writes: bool = True) -> list[str]:
    """Violations of the read-only rules in one module's source."""
    tree = ast.parse(source)
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in HARNESS_MODULES:
            problems.append(f"{name}:{node.lineno} imports names out of harness module {node.module}")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module, attr = func.value.id, func.attr
            if module in HARNESS_MODULES and attr not in ALLOWED_CALLS.get(module, set()):
                problems.append(f"{name}:{node.lineno} calls {module}.{attr}, not on the allowlist")
            if module == "dist_manifest" and attr != "check":
                problems.append(f"{name}:{node.lineno} calls dist_manifest.{attr}")
            if check_writes and module in WRITE_FUNCTIONS and (
                    WRITE_FUNCTIONS[module] is None or attr in WRITE_FUNCTIONS[module]):
                problems.append(f"{name}:{node.lineno} file-writing call {module}.{attr}")
        if check_writes and isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
            problems.append(f"{name}:{node.lineno} file-writing method .{func.attr}()")
        opens = (isinstance(func, ast.Name) and func.id == "open") or (
            isinstance(func, ast.Attribute) and func.attr == "open")
        if check_writes and opens:
            mode = _mode_of(node)
            if mode is not None and WRITE_MODE_CHARS & set(mode):
                problems.append(f"{name}:{node.lineno} open() in write mode {mode!r}")
    for scope, line in connect_calls(tree):
        if (name, scope) != ("stores.py", "ro_connect"):
            problems.append(f"{name}:{line} sqlite3.connect outside ro_connect (in {scope})")
    for module in imported_modules(tree):
        top = module.split(".")[0]
        if module in BANNED_IMPORTS or top in BANNED_IMPORTS:
            problems.append(f"{name} imports {module}")
        elif top not in sys.stdlib_module_names and top not in SIBLINGS:
            problems.append(f"{name} imports {module}, which is neither stdlib nor a sibling module")
    return problems


def connect_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(enclosing function, line) of every sqlite3.connect call."""
    found = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "connect" and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "sqlite3"):
                found.append((scope, child.lineno))
            visit(child, inner)

    visit(tree, "<module>")
    return found


class ShippedModulesTest(unittest.TestCase):

    def test_the_scan_has_modules_to_scan(self):
        names = set(dashboard_sources())
        self.assertTrue({"server.py", "stores.py", "views_pipeline.py", "views_memory.py",
                         "views_harness.py", "dist_manifest.py"} <= names, names)

    def test_every_module_passes_the_rules(self):
        problems = []
        for name, source in dashboard_sources().items():
            problems += scan_source(name, source, check_writes=name != "dist_manifest.py")
        self.assertEqual([], problems)

    def test_exactly_one_sqlite_connect_and_it_is_ro_connect(self):
        # FR-1: this holds for dist_manifest.py too (it has none) and for the realtime modules.
        found = []
        for name, source in dashboard_sources().items():
            found += [(name, scope, line) for scope, line in connect_calls(ast.parse(source))]
        self.assertEqual(1, len(found), found)
        self.assertEqual(("stores.py", "ro_connect"), found[0][:2])

    def test_the_connect_uses_read_only_uri_mode(self):
        source = dashboard_sources()["stores.py"]
        self.assertIn('"?mode=ro"', source)
        self.assertIn("uri=True", source)
        self.assertIn("timeout=1", source)

    def test_only_check_of_dist_manifest_is_reachable_from_the_server_modules(self):
        for name, source in dashboard_sources().items():
            if name == "dist_manifest.py":
                continue
            with self.subTest(module=name):
                self.assertNotIn("dist_manifest.write", source)
                self.assertNotIn("dist_manifest.main", source)

    def test_the_write_half_of_dist_manifest_is_reached_from_its_own_main_only(self):
        tree = ast.parse(dashboard_sources()["dist_manifest.py"])
        callers = set()
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
                if isinstance(call.func, ast.Name) and call.func.id == "write":
                    callers.add(function.name)
        self.assertEqual({"main"}, callers)

    def test_no_module_names_the_development_workspace(self):
        hits = [(name, marker) for name, source in dashboard_sources().items()
                for marker in WORKSPACE_MARKERS if marker in source]
        self.assertEqual([], hits)

    def test_the_server_compares_the_token_in_constant_time(self):
        source = dashboard_sources()["server.py"]
        self.assertIn("hmac.compare_digest", source)
        self.assertNotIn("== ctx.token", source)

    def test_no_debug_output_calls_remain(self):
        for name, source in dashboard_sources().items():
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    with self.subTest(module=name, line=node.lineno):
                        self.assertNotIn(node.func.id, {"print", "breakpoint", "pprint"})


class InjectedFixturesTest(unittest.TestCase):
    """The scanner must fail on each forbidden construct, or the shipped-code pass proves nothing."""

    def assertFlagged(self, source: str, needle: str) -> None:
        problems = scan_source("fixture.py", source)
        self.assertTrue(any(needle in p for p in problems), problems)

    def test_a_clean_fixture_passes(self):
        source = "import state\nimport stores\nstate.stages({})\nstores.ro_connect(1)\n"
        self.assertEqual([], scan_source("fixture.py", source))

    def test_a_call_to_a_harness_function_off_the_allowlist(self):
        self.assertFlagged("import state\nstate.connect()\n", "state.connect")
        self.assertFlagged("import handoff\nhandoff.uncovered_done_tasks()\n", "handoff.uncovered_done_tasks")
        self.assertFlagged("import events\nevents.append('gate', 'task-1')\n", "events.append")
        self.assertFlagged("import update\nupdate.stamp('task-1', False)\n", "update.stamp")

    def test_importing_names_out_of_a_harness_module(self):
        self.assertFlagged("from state import connect\n", "imports names out of harness module")

    def test_a_second_sqlite_connect(self):
        source = "import sqlite3\ndef other():\n    return sqlite3.connect('x.db')\n"
        self.assertFlagged(source, "sqlite3.connect outside ro_connect")

    def test_the_connect_scan_locates_the_enclosing_function(self):
        source = "import sqlite3\ndef ro_connect(p):\n    return sqlite3.connect(p)\nsqlite3.connect(1)\n"
        tree = ast.parse(source)
        self.assertEqual(["ro_connect", "<module>"], [scope for scope, _ in connect_calls(tree)])

    def test_path_write_text_and_friends(self):
        self.assertFlagged("from pathlib import Path\nPath('x').write_text('y')\n", ".write_text()")
        self.assertFlagged("from pathlib import Path\nPath('x').write_bytes(b'y')\n", ".write_bytes()")
        self.assertFlagged("from pathlib import Path\nPath('x').mkdir()\n", ".mkdir()")
        self.assertFlagged("import os\nos.replace('a', 'b')\n", "os.replace")
        self.assertFlagged("import os\nos.remove('a')\n", "os.remove")
        self.assertFlagged("import shutil\nshutil.copy('a', 'b')\n", "imports shutil")

    def test_open_in_a_write_mode_but_not_in_a_read_mode(self):
        self.assertFlagged("open('f', 'w')\n", "write mode")
        self.assertFlagged("open('f', mode='a')\n", "write mode")
        self.assertFlagged("from pathlib import Path\nPath('f').open('r+')\n", "write mode")
        self.assertEqual([], scan_source("fixture.py", "open('f')\nopen('f', 'rb')\n"))
        self.assertEqual([], scan_source("fixture.py", "import webbrowser\nwebbrowser.open('http://x')\n"))

    def test_a_third_party_import(self):
        self.assertFlagged("import requests\n", "requests")
        self.assertFlagged("import yaml\n", "neither stdlib nor a sibling")
        self.assertFlagged("from flask import Flask\n", "neither stdlib nor a sibling")

    def test_network_client_imports(self):
        for line in ("import urllib.request", "from urllib.parse import quote", "import http.client",
                     "from http import client", "import ftplib", "import smtplib"):
            with self.subTest(line=line):
                self.assertTrue(scan_source("fixture.py", line + "\n"), line)

    def test_http_server_is_allowed(self):
        self.assertEqual([], scan_source("fixture.py", "from http.server import BaseHTTPRequestHandler\n"))

    def test_a_write_to_a_dist_manifest_function_other_than_check(self):
        self.assertFlagged("import dist_manifest\ndist_manifest.write()\n", "dist_manifest.write")
        self.assertEqual([], scan_source("fixture.py", "import dist_manifest\ndist_manifest.check()\n"))


if __name__ == "__main__":
    unittest.main()
