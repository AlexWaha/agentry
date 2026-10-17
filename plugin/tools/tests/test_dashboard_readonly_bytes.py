"""task-0141: the byte-identity proof that the dashboard writes nothing (FR-9, FR-47).

A real server runs on a sandbox project built with the real harness writers. The
sandbox is hashed (SQLite -wal and -shm sidecars excluded: a read-only reader of
a WAL store may create them), then all 15 routes are driven once, one WebSocket
session is held open for three watcher ticks, and the sandbox is hashed again.
The two hash sets must be identical, files and folders alike.

The proof is only worth something if the comparison can fail, so a second set of
tests runs the same comparison against a fixture server with a deliberately
writing route and expects it to report the write.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import sqlite3
import sys
import threading
import time
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR / "dashboard"))
sys.path.insert(0, str(TESTS_DIR))

import dashboard_sandbox as sandbox
import test_dashboard_ws_session as kit
import tmproot

ROUTES = (
    "/api/v1/status",
    "/api/v1/runs",
    "/api/v1/tasks",
    "/api/v1/tasks/task-0104",
    "/api/v1/epics",
    "/api/v1/events",
    "/api/v1/metrics",
    "/api/v1/memory",
    "/api/v1/memory/stats",
    "/api/v1/graphs/memory",
    "/api/v1/codegraph",
    "/api/v1/codegraph/root/graph",
    "/api/v1/harness",
    "/api/v1/handoffs",
    "/api/v1/handoffs/task-0106",
)
WATCHER_TICKS = 3


def changes(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    """What differs between two hash_tree results, empty lists dropped."""
    found = {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
    }
    return {kind: paths for kind, paths in found.items() if paths}


class RealServerTest(unittest.TestCase):
    """FR-9: 15 routes, one WebSocket session and three ticks leave every byte alone."""

    @classmethod
    def setUpClass(cls):
        root = tmproot.mkdtemp("ro-bytes")
        cls.addClassCleanup(tmproot.rmtree, root)
        cls.project = sandbox.build_project(root)
        edges = [("a.py", "b.py", 3), ("b.py", "c.py", 1)]
        sandbox.make_codegraph(cls.project, ["a.py", "b.py", "c.py"], edges)

    def drive_routes(self, server) -> None:
        for route in ROUTES:
            with self.subTest(route=route):
                status, _, _ = server.get(route)
                self.assertEqual(status, 200, f"{route} should answer 200 on the sandbox")

    def drive_websocket(self, server) -> None:
        client = kit.RawClient(server.port, path=f"/ws?token={server.token}")
        try:
            self.assertEqual(client.status, 101)
            self.assertEqual(client.message()["type"], "hello")
            client.send_json({"type": "command", "v": 1, "payload": {"action": "advance"}})
            self.assertEqual(client.message()["payload"]["code"], "read_only")
            client.send_json({"type": "x"})
            self.assertEqual(client.message()["type"], "error")
            time.sleep(WATCHER_TICKS + 0.6)
            client.send(kit.client_frame(0x8, b"\x03\xe8"))
            self.assertEqual(client.close_code(), 1000)
        finally:
            client.close()

    def test_sandbox_hash_is_identical_after_all_routes_one_websocket_session_and_three_ticks(self):
        before = sandbox.hash_tree(self.project)
        with sandbox.start_server(self.project) as server:
            self.drive_routes(server)
            self.drive_websocket(server)
            self.drive_routes(server)
        after = sandbox.hash_tree(self.project)
        self.assertEqual(changes(before, after), {})
        self.assertEqual(before, after)

    def test_the_sandbox_has_stores_for_the_proof_to_protect(self):
        files = sandbox.hash_tree(self.project)
        for needed in (".agentry/state/events.db", ".agentry/state/run.db", ".agentry/memory/memory.db",
                       ".agentry/pipeline.json", ".codegraph/codegraph.db"):
            self.assertIn(needed, files)


class WritingFixtureHandler(BaseHTTPRequestHandler):
    """The deliberately writing route: GET /<action> changes the project on disk."""

    project: Path

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        state = self.project / ".agentry" / "state"
        action = self.path.strip("/")
        if action == "create":
            (state / "leak.txt").write_text("written by a route", encoding="utf-8")
        elif action == "modify":
            with closing(sqlite3.connect(state / "events.db")) as conn:
                conn.execute("INSERT INTO events (task, kind) VALUES ('task-0001', 'gate')")
                conn.commit()
        elif action == "remove":
            (self.project / ".agentry" / "tasks" / "done" / "task-0001.md").unlink()
        elif action == "mkdir":
            (self.project / ".agentry" / "cache").mkdir()
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class ComparisonCanFailTest(unittest.TestCase):
    """The same before/after comparison, against a server that does write."""

    def setUp(self):
        self.project = kit.make_project(tmproot.sandbox(self, "ro-writer"))
        (self.project / ".agentry" / "tasks" / "done" / "task-0001.md").write_text("done", encoding="utf-8")
        handler = type("Handler", (WritingFixtureHandler,), {"project": self.project})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5.0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def drive(self, action: str) -> dict[str, list[str]]:
        import http.client

        before = sandbox.hash_tree(self.project)
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        try:
            conn.request("GET", "/" + action)
            self.assertEqual(conn.getresponse().status, 200)
        finally:
            conn.close()
        return changes(before, sandbox.hash_tree(self.project))

    def test_a_route_that_does_nothing_reports_no_change(self):
        self.assertEqual(self.drive("noop"), {})

    def test_a_route_that_creates_a_file_is_reported_as_added(self):
        self.assertEqual(self.drive("create"), {"added": [".agentry/state/leak.txt"]})

    def test_a_route_that_writes_a_store_is_reported_as_changed(self):
        self.assertEqual(self.drive("modify"), {"changed": [".agentry/state/events.db"]})

    def test_a_route_that_deletes_a_file_is_reported_as_removed(self):
        self.assertEqual(self.drive("remove"), {"removed": [".agentry/tasks/done/task-0001.md"]})

    def test_a_route_that_makes_a_folder_is_reported_as_added(self):
        self.assertEqual(self.drive("mkdir"), {"added": [".agentry/cache"]})

    def test_wal_sidecars_alone_do_not_count_as_a_change(self):
        before = sandbox.hash_tree(self.project)
        state = self.project / ".agentry" / "state"
        (state / "events.db-wal").write_bytes(b"sidecar")
        (state / "events.db-shm").write_bytes(b"sidecar")
        self.assertEqual(changes(before, sandbox.hash_tree(self.project)), {})


if __name__ == "__main__":
    unittest.main()
