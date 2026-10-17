"""The paged routes stay fast on a large project (spec-0002 NFR-14).

A synthetic sandbox of 10000 events, 5000 memory rows, 1000 task files and 500 handoff documents; the
first request to each paged route, with a mid-list offset and a filter, answers in under a second -
including the first request after the server starts, when nothing is cached yet.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _dir in (TOOLS_DIR / "memory", TOOLS_DIR / "pipeline", TOOLS_DIR / "dashboard", TESTS_DIR):
    sys.path.insert(0, str(_dir))

import dashboard_sandbox as sb
import tmproot

LIMIT_SECONDS = 1.0


class SyntheticSandboxTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tmproot.mkdtemp("dash_perf_")
        cls.addClassCleanup(tmproot.rmtree, cls.root)
        sb.build_project(cls.root, "synthetic")
        cls.server = sb.start_server(cls.root)
        cls.addClassCleanup(cls.server.stop)

    def timed(self, path: str) -> tuple[dict, float]:
        started = time.perf_counter()
        status, _, body = self.server.get(path)
        elapsed = time.perf_counter() - started
        self.assertEqual(200, status, body[:300])
        return json.loads(body), elapsed

    def assertFast(self, path: str, minimum_total: int = 1) -> dict:
        first, first_seconds = self.timed(path)
        second, second_seconds = self.timed(path)
        self.assertLess(first_seconds, LIMIT_SECONDS, f"first request to {path}")
        self.assertLess(second_seconds, LIMIT_SECONDS, f"repeat request to {path}")
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["meta"]["total"], minimum_total, path)
        return first

    # These run in file order on one server, so the very first request of each route is cold.

    def test_a_01_tasks_with_a_mid_list_offset_and_a_filter(self):
        body = self.assertFast("/api/v1/tasks?offset=100&limit=25&type=feature", 300)
        self.assertEqual(25, len(body["data"]))
        self.assertEqual({"feature"}, {t["type"] for t in body["data"]})

    def test_a_02_events_with_a_mid_list_offset_and_filters(self):
        body = self.assertFast("/api/v1/events?offset=500&limit=25&kind=gate&result=fail", 700)
        self.assertEqual(25, len(body["data"]))
        self.assertEqual({"fail"}, {e["outcome"] for e in body["data"]})

    def test_a_03_memory_with_a_mid_list_offset_and_a_filter(self):
        body = self.assertFast("/api/v1/memory?offset=1000&limit=20&agent=qa-engineer", 2000)
        self.assertEqual(20, len(body["data"]))

    def test_a_04_a_ranked_memory_search_with_a_mid_list_offset(self):
        body = self.assertFast("/api/v1/memory?q=needleword&offset=300&limit=20", 500)
        self.assertEqual(20, len(body["data"]))

    def test_a_05_handoffs_with_a_mid_list_offset(self):
        body = self.assertFast("/api/v1/handoffs?offset=250&limit=25", 500)
        self.assertEqual(25, len(body["data"]))

    def test_the_dataset_has_the_documented_size(self):
        tasks = self.timed("/api/v1/tasks?include_superseded=true&limit=1")[0]
        self.assertEqual(1000, tasks["meta"]["total"])
        self.assertGreaterEqual(self.timed("/api/v1/events?limit=1")[0]["meta"]["total"], 10000)
        self.assertEqual(500, self.timed("/api/v1/handoffs?limit=1")[0]["meta"]["total"])
        stats = self.timed("/api/v1/memory/stats")[0]["data"]["counts"]
        self.assertEqual(5000, sum(stats.values()))

    def test_a_deep_page_costs_no_more_than_the_first(self):
        _, first = self.timed("/api/v1/events?limit=500&offset=0")
        _, last = self.timed("/api/v1/events?limit=500&offset=9500")
        self.assertLess(last, LIMIT_SECONDS)
        self.assertLess(first, LIMIT_SECONDS)


if __name__ == "__main__":
    unittest.main()
