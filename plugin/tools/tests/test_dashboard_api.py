"""The 15 /api/v1 routes of the dashboard server on sandbox projects (spec-0002 FR-11 to FR-38, FR-123).

Each class builds a project with the real harness writers (dashboard_sandbox), starts server.py as a
child process on it and asks it over HTTP, the way the SPA does.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _dir in (TOOLS_DIR / "memory", TOOLS_DIR / "pipeline", TOOLS_DIR / "dashboard", TESTS_DIR):
    sys.path.insert(0, str(_dir))

import dashboard_sandbox as sb
import events
import memory
import stores
import tmproot
import views_harness
import views_memory
import views_pipeline

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ROUTES = ("/api/v1/status", "/api/v1/runs", "/api/v1/tasks", "/api/v1/tasks/task-0106",
          "/api/v1/epics", "/api/v1/events", "/api/v1/metrics", "/api/v1/memory",
          "/api/v1/memory/stats", "/api/v1/graphs/memory", "/api/v1/codegraph",
          "/api/v1/harness", "/api/v1/handoffs", "/api/v1/handoffs/task-0106")
PAGED = ("/api/v1/tasks", "/api/v1/events", "/api/v1/memory", "/api/v1/handoffs")
ALL_15 = (*ROUTES, "/api/v1/codegraph/root/graph")


def event_rows(count: int, *, base_minute: int = 0) -> list[dict]:
    """`count` journal rows over three tasks, gates passing and failing, one per minute."""
    rows = []
    for i in range(count):
        minute = base_minute + i
        kind = "gate" if i % 2 == 0 else "transition"
        rows.append({"kind": kind, "task": ("task-0136", "task-0137", "task-0042")[i % 3],
                     "at": f"2026-10-0{minute // 1440 + 1}T{minute % 1440 // 60:02d}:{minute % 60:02d}:00Z",
                     "stage": ("implement", "test")[i % 2], "pipeline": "build",
                     "outcome": ("fail" if i % 4 == 0 else "pass") if kind == "gate" else "",
                     "detail": {"reason": f"n {i}", "duration_ms": 100 + i}})
    return rows


class ServerCase(unittest.TestCase):
    """One project and one running server for all the tests of a class."""

    server: sb.RunningServer
    root: Path

    @classmethod
    def build(cls, root: Path) -> None:
        raise NotImplementedError

    @classmethod
    def setUpClass(cls):
        cls.root = tmproot.mkdtemp("dash_api_")
        cls.addClassCleanup(tmproot.rmtree, cls.root)
        cls.build(cls.root)
        cls.server = sb.start_server(cls.root)
        cls.addClassCleanup(cls.server.stop)

    def api(self, path: str, headers: dict | None = None):
        status, head, body = self.server.get(path, headers)
        return status, head, json.loads(body.decode("utf-8"))

    def data(self, path: str):
        status, _, body = self.api(path)
        self.assertEqual(200, status, body)
        return body

    def assertError(self, path: str, status: int, code: str, field: str | None = None):
        got, head, body = self.api(path)
        self.assertEqual(status, got, (path, body))
        self.assertEqual(code, body["code"])
        self.assertIn("message", body)
        if field is not None:
            self.assertIn(field, body["errors"], (path, body))
        return head, body


def run_readers(project: Path) -> dict:
    """What the Stop hook's two debt readers say about a COPY of `project`."""
    copy = tmproot.mkdtemp("dash_readers_")
    try:
        shutil.copytree(project, copy / "p")
        paths = [str(TOOLS_DIR / "memory"), str(TOOLS_DIR / "pipeline")]
        code = (f"import json, sys\nsys.path[:0] = {paths!r}\nimport handoff, update\n"
                "print(json.dumps({'handoffs': handoff.uncovered_done_tasks(), "
                "'memory': update.unstamped_done_tasks()}))\n")
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(copy / "p")}
        env.pop("PIPELINE_LANE", None)
        done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                              timeout=120, check=True)
        return json.loads(done.stdout)
    finally:
        tmproot.rmtree(copy)


# --------------------------------------------------------------------------- small project

class SmallProjectTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.build_project(root)

    # --- B. conventions -------------------------------------------------------------

    def test_every_route_answers_200_with_the_envelope(self):
        for path in ALL_15[:-1]:
            with self.subTest(path=path):
                status, head, body = self.api(path)
                self.assertEqual(200, status)
                self.assertIn("data", body)
                self.assertTrue(head["Content-Type"].startswith("application/json"))
                if path in PAGED:
                    self.assertEqual({"total", "limit", "offset"} <= set(body["meta"]), True)
                else:
                    self.assertTrue(body.keys() <= {"data", "meta"})

    def test_a_missing_codegraph_index_is_a_404_on_the_graph_route(self):
        self.assertError("/api/v1/codegraph/root/graph", 404, "not_found")

    def test_every_verb_but_get_is_405_read_only_with_an_allow_header(self):
        for path in ALL_15:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(path=path, method=method):
                    status, head, body = self.server.request(method, path, body=b"{}")
                    self.assertEqual(405, status)
                    self.assertEqual("GET", head["Allow"])
                    self.assertEqual("read_only", json.loads(body)["code"])

    def test_options_and_head_are_405_as_well(self):
        for method in ("OPTIONS", "HEAD"):
            for path in ("/api/v1/status", "/"):
                with self.subTest(method=method, path=path):
                    status, head, _ = self.server.request(method, path)
                    self.assertEqual(405, status)
                    self.assertEqual("GET", head["Allow"])

    def test_unknown_routes_are_404_not_found(self):
        for path in ("/api/v1/nope", "/api/v2/status", "/api/v1/status/", "/api", "/api/",
                     "/api/v1/tasks/task-0106/extra", "/api/v1/codegraph/root"):
            with self.subTest(path=path):
                self.assertError(path, 404, "not_found")

    def test_well_formed_ids_that_do_not_exist_are_404(self):
        self.assertError("/api/v1/tasks/task-9999", 404, "not_found")
        self.assertError("/api/v1/handoffs/task-9999", 404, "not_found")

    def test_malformed_ids_are_422_naming_the_field(self):
        for path in ("/api/v1/tasks/nope", "/api/v1/tasks/task-1", "/api/v1/handoffs/..%2Fx",
                     "/api/v1/handoffs/task-0106x"):
            with self.subTest(path=path):
                self.assertError(path, 422, "validation_failed", "id")

    def test_every_checked_parameter_is_422_with_its_own_name(self):
        cases = {
            "/api/v1/runs": ["lane=a%20b"],
            "/api/v1/tasks": ["folder=bogus", "type=has%20space", "type=" + "x" * 33, "limit=0",
                              "limit=501", "limit=abc", "offset=-1", "offset=abc",
                              "include_superseded=maybe"],
            "/api/v1/events": ["kind=bogus", "result=maybe", "task=" + "1" * 33, "task=has%20space",
                               "limit=0", "limit=501", "offset=-1", "offset=abc"],
            "/api/v1/metrics": ["days=0", "days=91", "days=abc", "since=yesterday", "since=2026-02-30"],
            "/api/v1/memory": ["kind=bogus", "retired=bogus", "limit=0", "limit=201", "offset=-1"],
            "/api/v1/handoffs": ["limit=0", "limit=201", "offset=abc"],
            "/api/v1/codegraph/root/graph": ["level=bogus", "limit=9", "limit=401"],
        }
        for path, queries in cases.items():
            for query in queries:
                with self.subTest(path=path, query=query):
                    field = re.split(r"=", query)[0]
                    self.assertError(f"{path}?{query}", 422, "validation_failed", field)

    def test_since_and_days_together_are_422(self):
        _, body = self.assertError("/api/v1/metrics?since=2026-10-01&days=7", 422,
                                   "validation_failed", "since")
        self.assertIn("days", body["errors"])

    def test_an_unknown_codegraph_key_is_422(self):
        self.assertError("/api/v1/codegraph/nokey/graph", 422, "validation_failed", "key")

    def test_unknown_query_parameters_are_ignored(self):
        self.assertEqual(200, self.api("/api/v1/status?whatever=1")[0])

    def test_every_timestamp_field_is_iso_utc_with_z_and_no_text_field_is_null(self):
        nullable = {"needs_you", "supervisor", "run", "handoff", "memory_stamped_at", "lead_time_s",
                    "timeout"}
        stamps = {"at", "since", "created", "updated", "completed", "seen", "last_sync", "stamped_at"}
        seen = set()

        def walk(value, key=""):
            if isinstance(value, dict):
                for k, v in value.items():
                    walk(v, k)
            elif isinstance(value, list):
                for item in value:
                    walk(item, key)
            elif value is None:
                self.assertIn(key, nullable, f"null in {key}")
            elif key in stamps and value != "":
                seen.add(key)
                self.assertRegex(value, TIMESTAMP, key)

        for path in ALL_15[:-1]:
            walk(self.data(path)["data"])
        self.assertTrue({"at", "since", "created", "updated", "seen"} <= seen, seen)

    # --- C. status and runs -----------------------------------------------------------

    def test_status_reports_counts_lanes_and_the_fixed_flags(self):
        data = self.data("/api/v1/status")["data"]
        self.assertEqual(1, data["protocol"])
        self.assertIs(True, data["read_only"])
        self.assertEqual(self.root.name, data["project"])
        self.assertEqual("0.2.0", data["plugin_version"])
        self.assertEqual({"backlog": 3, "active": 2, "done": 4, "hidden_superseded": 1}, data["counts"])
        self.assertEqual(["default", "fix", "ghost"], [lane["name"] for lane in data["lanes"]])
        self.assertEqual({"name": "default", "repo": "agentry", "mode": "build", "approvals": "manual",
                          "swarm": False}, data["lanes"][0])
        self.assertEqual("", data["lanes"][1]["repo"])
        self.assertEqual({"mode": "solo", "push_needs_approval": True}, data["workflow"])
        self.assertEqual({"enabled": False, "max_parallel": 2}, data["worktrees"])
        self.assertEqual({"state": "stopped", "runs": 1}, data["supervisor"])

    def test_debt_is_what_the_stop_hook_readers_return(self):
        data = self.data("/api/v1/status")["data"]
        expected = run_readers(self.root)
        self.assertEqual(expected["handoffs"], data["debt"]["handoffs"])
        self.assertEqual(expected["memory"], data["debt"]["memory"])
        # task-0109 is abandoned: nothing merged, so no handoff is owed for it
        self.assertEqual(["task-0107", "task-0108"], [d["task"] for d in data["debt"]["handoffs"]])

    def test_runs_of_every_lane_newest_first_then_by_task(self):
        runs = self.data("/api/v1/runs")["data"]
        self.assertEqual({"task-0102": "fix", "task-0104": "default", "task-0105": "default",
                          "task-0106": "default", "task-0108": "default", "task-0109": "default"},
                         {r["task"]: r["lane"] for r in runs})
        key = [(-datetime.fromisoformat(r["updated"].replace("Z", "+00:00")).timestamp(), r["task"])
               for r in runs]
        self.assertEqual(sorted(key), key)

    def test_the_lane_filter(self):
        runs = self.data("/api/v1/runs?lane=fix")["data"]
        self.assertEqual(["task-0102"], [r["task"] for r in runs])
        self.assertEqual([], self.data("/api/v1/runs?lane=nope")["data"])

    def test_needs_you_kinds(self):
        runs = {r["task"]: r for r in self.data("/api/v1/runs")["data"]}
        self.assertEqual("commit", runs["task-0105"]["needs_you"]["kind"])
        self.assertEqual("Read the diff and approve the commit", runs["task-0105"]["needs_you"]["reason"])
        self.assertRegex(runs["task-0105"]["needs_you"]["since"], TIMESTAMP)
        self.assertEqual("blocked", runs["task-0102"]["needs_you"]["kind"])
        self.assertIsNone(runs["task-0108"]["needs_you"])          # paused
        self.assertEqual("paused", runs["task-0108"]["stage_status"])
        self.assertIsNone(runs["task-0104"]["needs_you"])          # in progress
        self.assertIsNone(runs["task-0106"]["needs_you"])          # done

    def test_supervised_runs_carry_the_registry_entry(self):
        runs = {r["task"]: r for r in self.data("/api/v1/runs")["data"]}
        seen = datetime.fromtimestamp(1792000000.0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual({"label": "HEALTHY", "seen": seen}, runs["task-0104"]["supervisor"])
        self.assertIsNone(runs["task-0105"]["supervisor"])

    def test_stages_follow_the_pipeline_with_skips_and_gate_counts(self):
        runs = {r["task"]: r for r in self.data("/api/v1/runs")["data"]}
        bugfix = {s["name"]: s for s in runs["task-0102"]["stages"]}
        self.assertEqual("skipped", bugfix["plan"]["state"])
        self.assertEqual("skipped", bugfix["plan-approval"]["state"])
        self.assertTrue(bugfix["plan-approval"]["human"])
        self.assertTrue(bugfix["ready"]["human"])
        self.assertFalse(bugfix["implement"]["human"])
        for task, run in runs.items():
            with self.subTest(task=task):
                states = [s["state"] for s in run["stages"]]
                self.assertEqual(0 if run["stage"] == "done" else 1, states.count("now"))
        feature = {s["name"]: s for s in runs["task-0104"]["stages"]}
        self.assertEqual(["done", "done", "now", "next", "next", "next", "next"],
                         [s["state"] for s in runs["task-0104"]["stages"]])
        self.assertEqual({"pass": 0, "fail": 1}, {k: feature["implement"][k] for k in ("pass", "fail")})
        zeta = {s["name"]: s for s in runs["task-0106"]["stages"]}
        self.assertEqual((1, 0), (zeta["implement"]["pass"], zeta["implement"]["fail"]))
        self.assertEqual((1, 1), (zeta["test"]["pass"], zeta["test"]["fail"]))

    def test_a_finished_run_has_no_current_stage_and_its_done_stage_is_done(self):
        runs = {r["task"]: r for r in self.data("/api/v1/runs")["data"]}
        finished = runs["task-0106"]
        self.assertEqual("done", finished["stage"])
        self.assertEqual({"done"}, {s["state"] for s in finished["stages"]})
        detail = self.data("/api/v1/tasks/task-0106")["data"]
        self.assertEqual({"done"}, {s["state"] for s in detail["stages"]})

    def test_an_abandoned_run_is_finished_not_in_progress(self):
        runs = {r["task"]: r for r in self.data("/api/v1/runs")["data"]}
        abandoned = runs["task-0109"]
        self.assertEqual(("done", "abandoned"), (abandoned["stage"], abandoned["stage_status"]))
        self.assertEqual(0, [s["state"] for s in abandoned["stages"]].count("now"))
        self.assertEqual("done", abandoned["stages"][-1]["state"])
        self.assertIsNone(abandoned["needs_you"])

    def test_a_run_parked_at_done_for_the_merge_is_still_in_progress(self):
        pipeline = stores.read_pipeline(self.root)
        stages = views_pipeline._stages(pipeline, "build", "feature", "done", "task-0199", {}, "merge")
        self.assertEqual(1, [s["state"] for s in stages].count("now"))
        self.assertEqual(("done", "now"), (stages[-2]["state"], stages[-1]["state"]))
        row = {"task": "task-0199", "stage_status": "in_progress", "awaiting_human": "merge", "updated": ""}
        self.assertEqual("merge", views_pipeline._needs_you(row, {})["kind"])

    def test_every_stage_but_the_final_one_has_exactly_one_current_stage_in_both_pipelines(self):
        pipeline = stores.read_pipeline(self.root)
        for which in ("build", "plan"):
            names = [s["name"] for s in views_pipeline.state.stages(pipeline, which)]
            for current in names:
                with self.subTest(pipeline=which, stage=current):
                    stages = views_pipeline._stages(pipeline, which, "", current, "task-0199", {})
                    now = [s["state"] for s in stages].count("now")
                    self.assertEqual(0 if current == names[-1] else 1, now)

    def test_runs_carry_the_title_of_their_task(self):
        runs = {r["task"]: r for r in self.data("/api/v1/runs")["data"]}
        self.assertEqual("Delta in flight", runs["task-0104"]["title"])
        self.assertEqual("epic-0001", runs["task-0104"]["epic"])

    # --- C. tasks ----------------------------------------------------------------------

    def ids(self, query: str) -> list[str]:
        return [t["id"] for t in self.data(f"/api/v1/tasks{query}")["data"]]

    def test_the_backlog_hides_the_superseded_task_and_counts_it(self):
        body = self.data("/api/v1/tasks?folder=backlog")
        self.assertEqual(["task-0101", "task-0102"], [t["id"] for t in body["data"]])
        self.assertEqual(2, body["meta"]["total"])
        self.assertEqual(1, body["meta"]["hidden_superseded"])
        shown = self.data("/api/v1/tasks?folder=backlog&include_superseded=true")
        self.assertEqual(["task-0101", "task-0102", "task-0103"], [t["id"] for t in shown["data"]])
        self.assertEqual((3, 0), (shown["meta"]["total"], shown["meta"]["hidden_superseded"]))

    def test_order_is_ascending_for_backlog_and_active_and_descending_otherwise(self):
        self.assertEqual(["task-0104", "task-0105"], self.ids("?folder=active"))
        self.assertEqual(["task-0109", "task-0108", "task-0107", "task-0106"], self.ids("?folder=done"))
        self.assertEqual(["task-0109", "task-0108", "task-0107", "task-0106", "task-0105", "task-0104",
                          "task-0102", "task-0101"], self.ids(""))

    def test_filters_by_epic_type_and_text_narrow_the_total(self):
        self.assertEqual(["task-0106", "task-0104", "task-0101"], self.ids("?epic=epic-0001"))
        self.assertEqual(["task-0107", "task-0102"], self.ids("?type=bugfix"))
        self.assertEqual(["task-0106"], self.ids("?q=ZETA"))
        self.assertEqual(["task-0106"], self.ids("?q=0106"))
        self.assertEqual(["task-0108", "task-0106", "task-0104", "task-0101"], self.ids("?type=feature"))
        body = self.data("/api/v1/tasks?epic=epic-0002&type=bugfix")
        self.assertEqual((["task-0107", "task-0102"], 2),
                         ([t["id"] for t in body["data"]], body["meta"]["total"]))
        self.assertEqual([], self.ids("?q=no-such-task"))

    def test_facets_ignore_the_folder_and_type_filters_respectively(self):
        meta = self.data("/api/v1/tasks?folder=done&type=bugfix")["meta"]
        self.assertEqual({"backlog": 1, "active": 0, "done": 1}, meta["facets"]["folders"])
        self.assertEqual(["bugfix", "feature", "refactor"], meta["facets"]["types"])
        meta = self.data("/api/v1/tasks?epic=epic-0001")["meta"]
        self.assertEqual({"backlog": 1, "active": 1, "done": 1}, meta["facets"]["folders"])
        self.assertEqual(["feature"], meta["facets"]["types"])
        self.assertEqual({"backlog": 2, "active": 2, "done": 4},
                         self.data("/api/v1/tasks")["meta"]["facets"]["folders"])

    def test_task_summaries_carry_refs_for_their_dependencies(self):
        task = next(t for t in self.data("/api/v1/tasks?folder=active")["data"] if t["id"] == "task-0104")
        self.assertEqual("high", task["priority"])
        self.assertEqual([{"id": "task-0106", "title": "Zeta done", "folder": "done"}], task["depends_on"])
        self.assertEqual("2026-10-01T00:00:00Z", task["created"])

    # --- C. task detail -----------------------------------------------------------------

    def test_a_task_with_a_run_events_handoff_and_stamp(self):
        task = self.data("/api/v1/tasks/task-0106")["data"]
        self.assertEqual("Zeta done", task["title"])
        self.assertEqual("done", task["folder"])
        self.assertEqual("harness", task["phase"])
        self.assertEqual("agentry", task["repo"])
        self.assertFalse(task["umbrella"])
        self.assertIn("## Description", task["body"])
        self.assertFalse(task["body_truncated"])
        self.assertEqual("task-0106", task["run"]["task"])
        self.assertEqual(task["run"]["stages"], task["stages"])
        self.assertEqual((3, 1, 0), (task["gate_runs"], task["gate_failures"], task["rejections"]))
        ids = [e["id"] for e in task["events"]]
        self.assertEqual(sorted(ids), ids)
        self.assertTrue({"registered", "gate", "merged"} <= {e["kind"] for e in task["events"]})
        self.assertFalse(task["events_truncated"])
        self.assertEqual({"valid": True, "problems": []}, task["handoff"])
        self.assertEqual("2026-10-15T09:30:00Z", task["memory_stamped_at"])
        self.assertEqual([{"id": "task-0104", "title": "Delta in flight", "folder": "active"}],
                         task["dependents"])

    def test_a_done_task_without_a_run_has_run_null_and_an_invalid_handoff(self):
        task = self.data("/api/v1/tasks/task-0107")["data"]
        self.assertIsNone(task["run"])
        self.assertEqual([], task["stages"])
        self.assertEqual(1, task["rejections"])
        self.assertFalse(task["handoff"]["valid"])
        self.assertIn("missing section '## Gotchas and lessons'", task["handoff"]["problems"])
        self.assertIsNone(task["memory_stamped_at"])
        self.assertEqual([e["kind"] for e in task["events"]], ["rejected"])

    def test_a_backlog_task_without_history(self):
        task = self.data("/api/v1/tasks/task-0101")["data"]
        self.assertEqual(("backlog", [], None, None), (task["folder"], task["events"], task["handoff"],
                                                       task["memory_stamped_at"]))

    def test_the_detail_run_is_found_in_any_lane(self):
        task = self.data("/api/v1/tasks/task-0102")["data"]
        self.assertEqual("fix", task["run"]["lane"])

    # --- C. epics -----------------------------------------------------------------------

    def test_epics_exclude_thread_documents_and_resolve_task_folders(self):
        epics = self.data("/api/v1/epics")["data"]
        self.assertEqual(["epic-0001", "epic-0002"], [e["id"] for e in epics])
        self.assertEqual(("First epic", "planned"), (epics[0]["title"], epics[0]["status"]))
        self.assertEqual([("task-0101", "backlog"), ("task-0103", "backlog"), ("task-0104", "active"),
                          ("task-0106", "done")], [(t["id"], t["folder"]) for t in epics[0]["tasks"]])

    # --- E. harness on the sandbox, handoffs -------------------------------------------

    def test_a_sandbox_without_a_claude_folder_has_an_empty_inventory(self):
        data = self.data("/api/v1/harness")["data"]
        self.assertEqual(([], []), (data["agents"], data["rules"]))
        self.assertEqual("directory", data["plugin"]["install"])

    def test_handoffs_are_listed_newest_first_with_validity(self):
        body = self.data("/api/v1/handoffs")
        self.assertEqual([("task-0107", False), ("task-0106", True)],
                         [(h["task"], h["valid"]) for h in body["data"]])
        self.assertEqual({"total": 2, "limit": 25, "offset": 0}, body["meta"])
        first = body["data"][0]
        self.assertEqual(("A handoff", "senior-backend-dev", "2026-10-15"),
                         (first["title"], first["author"], first["date"]))
        self.assertGreater(first["bytes"], 500)

    # --- A. read-only: the stores can be renamed after use ---------------------------------

    def test_no_store_stays_open_after_a_request(self):
        state_dir = self.root / ".agentry" / "state"
        for path in ALL_15[:-1]:
            self.api(path)
        for store in (state_dir / "events.db", state_dir / "run.db", state_dir / "run.fix.db",
                      self.root / ".agentry" / "memory" / "memory.db"):
            with self.subTest(store=store.name):
                moved = store.with_name(store.name + ".moved")
                os.replace(store, moved)
                os.replace(moved, store)

    def test_a_fetched_event_equals_the_row_the_watcher_pushes(self):
        import watch
        db = self.root / ".agentry" / "state" / "events.db"
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute("SELECT id, at, lane, task, pipeline, kind, stage, from_stage, outcome, "
                                "via, detail FROM events ORDER BY id").fetchall()
        finally:
            conn.close()
        fetched = {e["id"]: e for e in self.data("/api/v1/events?limit=500")["data"]}
        self.assertEqual(len(rows), len(fetched))
        for row in rows:
            with self.subTest(id=row[0]):
                self.assertEqual(watch._event(row), fetched[row[0]])


# --------------------------------------------------------------------------- events and paging

class EventsAndPagingTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        sb.seed(root, {"events": event_rows(120)})
        for i in range(30):
            sb.write_task(root, ("backlog", "active", "done")[i % 3], f"task-{200 + i:04d}",
                          title=f"Paging task {i}", type=("feature", "bugfix")[i % 2])
        for i in range(60):
            sb.write_handoff(root, f"task-{1000 + i}", valid=i % 4 != 0,
                             date=f"2026-0{i // 28 + 1}-{i % 28 + 1:02d}")
        sb.seed(root, {"lessons": [
            {"signature": f"paging-lesson-{i:02d}", "trigger": "t", "what": "paging words", "why": "w",
             "fix": "f", "area": f"area-{i % 3}", "agent": ("alpha", "beta")[i % 2],
             "task": "task-0001", "created": f"2026-10-{i % 28 + 1:02d}"} for i in range(45)]})

    def ids(self, path: str) -> list[int]:
        return [e["id"] for e in self.data(path)["data"]]

    def test_pages_of_fifty_walk_the_journal_newest_first(self):
        pages = [self.data(f"/api/v1/events?limit=50&offset={o}") for o in (0, 50, 100)]
        self.assertEqual([50, 50, 20], [len(p["data"]) for p in pages])
        self.assertEqual([120, 120, 120], [p["meta"]["total"] for p in pages])
        every = [e["id"] for p in pages for e in p["data"]]
        self.assertEqual(sorted(every, reverse=True), every)
        self.assertEqual(120, len(set(every)))

    def test_the_task_filter_is_a_case_insensitive_substring(self):
        body = self.data("/api/v1/events?task=0136&limit=500")
        self.assertEqual({"task-0136"}, {e["task"] for e in body["data"]})
        self.assertEqual(40, body["meta"]["total"])
        self.assertEqual(40, self.data("/api/v1/events?task=TASK-0136&limit=1")["meta"]["total"])
        self.assertEqual(120, self.data("/api/v1/events?task=task&limit=1")["meta"]["total"])
        self.assertEqual(0, self.data("/api/v1/events?task=9999")["meta"]["total"])

    def test_kind_and_result_filters_combine_and_total_follows(self):
        gates = self.data("/api/v1/events?kind=gate&limit=500")
        self.assertEqual({"gate"}, {e["kind"] for e in gates["data"]})
        self.assertEqual(60, gates["meta"]["total"])
        failed = self.data("/api/v1/events?result=fail&limit=500")
        self.assertEqual({"fail"}, {e["outcome"] for e in failed["data"]})
        self.assertEqual(30, failed["meta"]["total"])
        both = self.data("/api/v1/events?kind=gate&result=pass&task=0137&limit=500")
        self.assertEqual({("gate", "pass", "task-0137")},
                         {(e["kind"], e["outcome"], e["task"]) for e in both["data"]})
        self.assertEqual(len(both["data"]), both["meta"]["total"])

    def test_meta_carries_the_latest_id_and_the_kind_options(self):
        meta = self.data("/api/v1/events?limit=1")["meta"]
        self.assertEqual(sorted(events.KINDS), meta["kinds"])
        self.assertEqual(max(self.ids("/api/v1/events?limit=500")), meta["latest_id"])

    def test_detail_holds_only_the_contract_keys(self):
        event = self.data("/api/v1/events?limit=1")["data"][0]
        self.assertEqual({"reason", "duration_ms"}, set(event["detail"]))

    def test_paged_routes_default_limits_and_echo(self):
        self.assertEqual({"total": 120, "limit": 25, "offset": 0, "latest_id": 120,
                          "kinds": sorted(events.KINDS)}, self.data("/api/v1/events")["meta"])
        self.assertEqual(25, self.data("/api/v1/tasks")["meta"]["limit"])
        self.assertEqual(20, self.data("/api/v1/memory")["meta"]["limit"])
        self.assertEqual(25, self.data("/api/v1/handoffs")["meta"]["limit"])
        self.assertEqual({"total": 45, "limit": 7, "offset": 3},
                         self.data("/api/v1/memory?limit=7&offset=3")["meta"])

    def walk(self, path: str, key, maximum: int) -> None:
        """FR-123: every row exactly once, never more than `limit`, then empty pages with the true total."""
        full = self.data(f"{path}{'&' if '?' in path else '?'}limit={maximum}")
        total = full["meta"]["total"]
        everything = [key(r) for r in full["data"]]
        self.assertEqual(total, len(everything))
        for limit in (1, 4, 7):
            seen = []
            offset = 0
            while offset < total:
                page = self.data(f"{path}{'&' if '?' in path else '?'}limit={limit}&offset={offset}")
                self.assertLessEqual(len(page["data"]), limit)
                self.assertEqual({"total": total, "limit": limit, "offset": offset},
                                 {k: page["meta"][k] for k in ("total", "limit", "offset")})
                seen += [key(r) for r in page["data"]]
                offset += limit
            self.assertEqual(everything, seen, (path, limit))
        for offset in (total, total + 1, total + 1000):
            page = self.data(f"{path}{'&' if '?' in path else '?'}offset={offset}")
            meta = page["meta"]
            self.assertEqual(([], total, offset), (page["data"], meta["total"], meta["offset"]))
        self.assertError(f"{path}{'&' if '?' in path else '?'}limit={maximum + 1}", 422,
                         "validation_failed", "limit")

    def test_walking_offsets_returns_every_row_once_on_each_paged_route(self):
        self.walk("/api/v1/tasks", lambda r: r["id"], 500)
        self.walk("/api/v1/events", lambda r: r["id"], 500)
        self.walk("/api/v1/memory", lambda r: r["id"], 200)
        self.walk("/api/v1/handoffs", lambda r: r["task"], 200)

    def test_a_filtered_total_is_the_filtered_count(self):
        self.assertEqual(15, self.data("/api/v1/tasks?type=bugfix")["meta"]["total"])
        self.assertEqual(10, self.data("/api/v1/tasks?folder=done")["meta"]["total"])
        self.assertEqual(23, self.data("/api/v1/memory?agent=alpha")["meta"]["total"])
        self.assertEqual(15, self.data("/api/v1/memory?area=area-0")["meta"]["total"])

    def test_handoffs_paging_and_validity(self):
        pages = [self.data(f"/api/v1/handoffs?limit=25&offset={o}") for o in (0, 25, 50)]
        self.assertEqual([25, 25, 10], [len(p["data"]) for p in pages])
        self.assertTrue(all(p["meta"]["total"] == 60 for p in pages))
        every = [h["task"] for p in pages for h in p["data"]]
        self.assertEqual([f"task-{1000 + i}" for i in range(59, -1, -1)], every)
        self.assertEqual(1, len(self.data("/api/v1/handoffs?limit=1")["data"]))
        invalid = [h["task"] for p in pages for h in p["data"] if not h["valid"]]
        self.assertEqual(15, len(invalid))
        self.assertIn("task-1000", invalid)

    def test_the_handoff_reader_returns_the_same_problems_as_the_validator(self):
        for task, valid in (("task-1000", False), ("task-1001", True)):
            with self.subTest(task=task):
                doc = self.data(f"/api/v1/handoffs/{task}")["data"]
                paths = [str(TOOLS_DIR / "memory"), str(TOOLS_DIR / "pipeline")]
                code = (f"import json, sys\nsys.path[:0] = {paths!r}\nimport handoff\n"
                        f"path = handoff.handoff_path({task!r})\n"
                        f"print(json.dumps(handoff.validate_handoff(path, {task!r})))\n")
                env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)}
                done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                                      text=True, timeout=60, check=True)
                self.assertEqual(json.loads(done.stdout), doc["problems"])
                self.assertEqual(valid, doc["valid"])
                self.assertEqual(valid, doc["problems"] == [])


class HandoffChainOrderTest(unittest.TestCase):
    """views_harness.chain is chain_docs with a cache; the two must never disagree on the order."""

    def test_the_order_equals_the_harness_chain_docs_order(self):
        folder = tmproot.sandbox(self, "dash_chain_")
        specs = [("task-0001", "2026-01-01"), ("task-0002", "2026-01-03"), ("task-0003", "2026-01-03"),
                 ("task-0004", ""), ("task-0005", '"2026-02-01"'), ("task-0006", "2026-01-03"),
                 ("task-0007", "")]
        for index, (task, date) in enumerate(specs):
            path = folder / f"{task}.md"
            path.write_bytes(f"---\ntask: {task}\ndate: {date}\n---\nbody\n".encode())
            os.utime(path, (1_700_000_000 + (index * 37) % 5, 1_700_000_000 + (index * 37) % 5))
        (folder / "notes.md").write_text("not a handoff", encoding="utf-8")
        import handoff
        expected = [p.name for p in handoff.chain_docs(folder)]
        self.assertEqual(expected, [p.name for p, _ in views_harness.chain(folder)])
        self.assertEqual(7, len(expected))

    def test_a_missing_folder_is_an_empty_chain(self):
        self.assertEqual([], views_harness.chain(tmproot.sandbox(self, "dash_chain_") / "nope"))


class HandoffReaderTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        sb.write_handoff(root, "task-0001", body_extra="\n<script>alert(1)</script>\n<img src=x onerror=y>\n")

    def test_html_in_a_document_is_returned_verbatim_as_data(self):
        status, head, raw = self.server.get("/api/v1/handoffs/task-0001")
        self.assertEqual(200, status)
        self.assertTrue(head["Content-Type"].startswith("application/json"))
        doc = json.loads(raw)["data"]
        self.assertIn("<script>alert(1)</script>", doc["body"])
        self.assertIn("<img src=x onerror=y>", doc["body"])
        self.assertTrue(doc["body"].startswith("\n# Handoff: task-0001"))
        self.assertEqual("task-0001", doc["task"])


# --------------------------------------------------------------------------- task detail caps

class TaskDetailCapsTest(ServerCase):

    CRLF_BODY = "# Заголовок\r\nline two\r\n\r\n- [x] ünï ✓\r\n- [ ] open\r\n"

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        sb.write_task(root, "active", "task-0001", body="x" * 300_000)
        sb.write_task(root, "active", "task-0002", body="é" * 200_000)
        sb.write_task(root, "active", "task-0003", body=cls.CRLF_BODY)
        sb.write_task(root, "active", "task-0004", body="a" * 262_144)
        sb.write_task(root, "active", "task-0005", body="a" * 262_145)
        sb.seed(root, {"events": [{"kind": "gate", "task": "task-0006", "at": "2026-10-01T00:00:00Z",
                                   "stage": "test", "outcome": "pass", "detail": {"reason": f"n {i}"}}
                                  for i in range(250)]
                       + [{"kind": "gate", "task": "task-0007", "at": "2026-10-01T00:00:00Z",
                           "stage": "test", "outcome": "fail"} for _ in range(200)]})
        sb.write_task(root, "active", "task-0006")
        sb.write_task(root, "active", "task-0007")

    def test_a_body_over_262144_bytes_is_cut_and_flagged(self):
        task = self.data("/api/v1/tasks/task-0001")["data"]
        self.assertEqual(262_144, len(task["body"].encode("utf-8")))
        self.assertTrue(task["body_truncated"])

    def test_the_cut_lands_on_a_character_boundary(self):
        task = self.data("/api/v1/tasks/task-0002")["data"]
        self.assertTrue(task["body_truncated"])
        self.assertEqual("é" * 131_072, task["body"])

    def test_the_cap_is_inclusive(self):
        exact = self.data("/api/v1/tasks/task-0004")["data"]
        self.assertEqual((262_144, False), (len(exact["body"]), exact["body_truncated"]))
        over = self.data("/api/v1/tasks/task-0005")["data"]
        self.assertEqual((262_144, True), (len(over["body"]), over["body_truncated"]))

    def test_crlf_and_non_ascii_text_round_trips_unchanged(self):
        task = self.data("/api/v1/tasks/task-0003")["data"]
        self.assertEqual(self.CRLF_BODY, task["body"])
        self.assertFalse(task["body_truncated"])

    def test_only_the_newest_200_events_are_returned_oldest_first_and_flagged(self):
        task = self.data("/api/v1/tasks/task-0006")["data"]
        self.assertEqual(200, len(task["events"]))
        self.assertTrue(task["events_truncated"])
        self.assertEqual(("n 50", "n 249"), (task["events"][0]["detail"]["reason"],
                                             task["events"][-1]["detail"]["reason"]))
        self.assertEqual(250, task["gate_runs"])

    def test_exactly_200_events_are_not_truncated(self):
        task = self.data("/api/v1/tasks/task-0007")["data"]
        self.assertEqual((200, False, 200, 200), (len(task["events"]), task["events_truncated"],
                                                  task["gate_runs"], task["gate_failures"]))


# --------------------------------------------------------------------------- metrics

class MetricsTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)

        def ev(day: str, hhmm: str, kind: str, task: str, **fields) -> dict:
            return {"kind": kind, "task": task, "at": f"2026-10-{day}T{hhmm}:00Z", "pipeline": "build",
                    **fields}

        def gate(day, hhmm, task, stage, outcome, ms):
            return ev(day, hhmm, "gate", task, stage=stage, outcome=outcome,
                      detail={"duration_ms": ms, "cmd": "c", "exit": 0 if outcome == "pass" else 1})

        sb.seed(root, {"events": [
            ev("02", "00:00", "registered", "task-0001", stage="plan"),
            ev("02", "00:10", "transition", "task-0001", stage="implement", from_stage="plan"),
            gate("02", "00:20", "task-0001", "implement", "pass", 5000),
            ev("02", "00:40", "transition", "task-0001", stage="test", from_stage="implement"),
            gate("02", "00:50", "task-0001", "test", "fail", 3000),
            gate("02", "01:00", "task-0001", "test", "pass", 7000),
            ev("02", "02:00", "merged", "task-0001", stage="done"),
            ev("03", "00:00", "registered", "task-0002", stage="implement"),
            gate("03", "00:05", "task-0002", "implement", "fail", 1000),
            ev("03", "00:30", "rejected", "task-0002", stage="implement", from_stage="ready",
               detail={"reason": "r1"}),
            gate("03", "00:40", "task-0002", "implement", "pass", 9000),
            ev("03", "01:00", "merged", "task-0002", stage="done"),
            ev("03", "05:00", "registered", "task-0003", stage="implement"),
            gate("03", "05:10", "task-0003", "implement", "pass", 2000),
        ]})

    def metrics(self, query: str = "?since=2026-10-01") -> dict:
        return self.data(f"/api/v1/metrics{query}")["data"]

    def test_counts_and_span(self):
        data = self.metrics()
        self.assertEqual(14, data["events_total"])
        self.assertEqual({"gate": 6, "merged": 2, "registered": 3, "rejected": 1, "transition": 2},
                         data["kinds"])
        self.assertEqual("2026-10-02T00:00:00Z/2026-10-03T05:10:00Z", data["span"])

    def test_gate_medians_nearest_rank_p90_and_max_per_stage_in_pipeline_order(self):
        self.assertEqual([
            {"stage": "implement", "runs": 4, "failures": 1, "median_ms": 3500, "p90_ms": 9000,
             "max_ms": 9000},
            {"stage": "test", "runs": 2, "failures": 1, "median_ms": 5000, "p90_ms": 7000,
             "max_ms": 7000},
        ], self.metrics()["gates"])

    def test_the_slowest_gates_come_first(self):
        slowest = self.metrics()["slowest"]
        self.assertEqual([9000, 7000, 5000, 3000, 2000, 1000], [g["ms"] for g in slowest])
        self.assertEqual({"task": "task-0002", "stage": "implement", "ms": 9000, "passed": True,
                          "at": "2026-10-03T00:40:00Z"}, slowest[0])
        self.assertFalse(slowest[3]["passed"])

    def test_stage_dwell_is_the_time_between_stage_entries(self):
        self.assertEqual([{"stage": "plan", "n": 1, "median_s": 600, "p90_s": 600, "max_s": 600},
                          {"stage": "implement", "n": 1, "median_s": 1800, "p90_s": 1800, "max_s": 1800}],
                         self.metrics()["dwell"])

    def test_per_day_counts(self):
        self.assertEqual([
            {"day": "2026-10-02", "gates": 3, "gate_failures": 1, "merged": 1, "rejected": 0},
            {"day": "2026-10-03", "gates": 3, "gate_failures": 1, "merged": 1, "rejected": 1},
        ], self.metrics()["per_day"])

    def test_rejections_merges_and_lead_time(self):
        data = self.metrics()
        self.assertEqual({"total": 1, "latest": [{"task": "task-0002", "at": "2026-10-03T00:30:00Z",
                                                  "from_stage": "ready", "stage": "implement",
                                                  "reason": "r1"}]}, data["rejections"])
        self.assertEqual(2, data["merges_total"])
        self.assertEqual(5400, data["lead_time_s"])  # median of 7200 s and 3600 s

    def test_a_later_since_leaves_out_earlier_events(self):
        data = self.metrics("?since=2026-10-03")
        self.assertEqual(7, data["events_total"])
        self.assertEqual(1, data["merges_total"])
        self.assertEqual(3600, data["lead_time_s"])

    def test_the_default_window_is_the_last_14_days(self):
        self.assertEqual(self.metrics("?days=14"), self.metrics(""))


class MetricsWindowTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        now = datetime.now(timezone.utc)

        def at(days: float) -> str:
            return datetime.fromtimestamp(now.timestamp() - days * 86400, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")

        sb.seed(root, {"events": [
            {"kind": "gate", "task": "task-0001", "at": at(0.5), "stage": "test", "outcome": "pass",
             "detail": {"duration_ms": 10}},
            {"kind": "gate", "task": "task-0001", "at": at(20), "stage": "test", "outcome": "fail",
             "detail": {"duration_ms": 20}},
            {"kind": "gate", "task": "task-0001", "at": at(60), "stage": "test", "outcome": "fail",
             "detail": {"duration_ms": 30}},
        ]})

    def test_days_selects_a_rolling_window(self):
        counts = {d: self.data(f"/api/v1/metrics?days={d}")["data"]["events_total"] for d in (1, 14, 30, 90)}
        self.assertEqual({1: 1, 14: 1, 30: 2, 90: 3}, counts)

    def test_no_merge_in_the_window_means_a_null_lead_time(self):
        data = self.data("/api/v1/metrics?days=90")["data"]
        self.assertIsNone(data["lead_time_s"])
        self.assertEqual(0, data["merges_total"])


# --------------------------------------------------------------------------- memory

class MemoryTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        lessons = [
            {"signature": "ranked-lesson", "trigger": "t", "what": "the zebracorn problem", "why": "w",
             "fix": "f", "area": "hooks", "task": "task-0001", "agent": "senior-backend-dev",
             "created": "2026-10-11"},
            {"signature": "plain-lesson", "trigger": "t", "what": "something else", "why": "w", "fix": "f",
             "area": "hooks", "task": "", "agent": "qa-engineer", "created": "2026-10-12"},
            {"signature": "retired-lesson", "trigger": "t", "what": "zebracorn again", "why": "w", "fix": "f",
             "area": "docs", "task": "", "agent": "", "created": "2026-10-09"},
        ]
        sb.seed(root, {
            "lessons": lessons,
            "patterns": [{"name": "zebracorn-pattern", "use_when": "zebracorn", "body": "do it",
                          "task": "task-0001", "created": "2026-10-10"},
                         {"name": "other-pattern", "use_when": "never", "body": "", "task": "",
                          "created": "2026-10-13"}],
            "modules": [{"path": "plugin/tools/a.py", "responsibility": "alpha module"},
                        {"path": "plugin/tools/b.py", "responsibility": "beta module", "notes": "zebracorn"}],
            "retire": [{"signature": "retired-lesson", "reason": "withdrawn"}],
        })

    def rows(self, query: str = "") -> list[dict]:
        return self.data(f"/api/v1/memory{query}")["data"]

    def test_lessons_are_the_default_kind_newest_first_and_hide_retired_rows(self):
        self.assertEqual(["plain-lesson", "ranked-lesson"], [r["signature"] for r in self.rows()])

    def test_a_lesson_has_exactly_the_memory_db_columns(self):
        lesson = self.rows("?q=zebracorn")[0]
        self.assertEqual({"kind": "lesson", "signature": "ranked-lesson", "trigger": "t",
                          "what": "the zebracorn problem", "why": "w", "fix": "f", "area": "hooks",
                          "task": "task-0001", "agent": "senior-backend-dev", "retired": False,
                          "created": "2026-10-11T00:00:00Z"}, {k: v for k, v in lesson.items() if k != "id"})

    def test_a_query_ranks_the_matching_row_and_totals_the_matches(self):
        body = self.data("/api/v1/memory?q=zebracorn")
        self.assertEqual(["ranked-lesson"], [r["signature"] for r in body["data"]])
        self.assertEqual(1, body["meta"]["total"])
        self.assertEqual(0, self.data("/api/v1/memory?q=nothingmatchesthis")["meta"]["total"])

    def test_retired_modes(self):
        self.assertEqual(["retired-lesson"], [r["signature"] for r in self.rows("?retired=only")])
        self.assertTrue(self.rows("?retired=only")[0]["retired"])
        self.assertEqual(3, len(self.rows("?retired=include")))
        self.assertEqual(2, len(self.rows("?retired=exclude")))
        self.assertEqual(["retired-lesson"], [r["signature"] for r in self.rows("?retired=only&q=zebracorn")])
        self.assertEqual(2, len(self.rows("?retired=include&q=zebracorn")))

    def test_patterns_and_modules_have_their_own_shapes(self):
        pattern = self.rows("?kind=pattern&q=zebracorn")[0]
        self.assertEqual({"kind": "pattern", "name": "zebracorn-pattern", "use_when": "zebracorn",
                          "body": "do it", "task": "task-0001", "created": "2026-10-10T00:00:00Z"},
                         {k: v for k, v in pattern.items() if k != "id"})
        self.assertEqual(["other-pattern", "zebracorn-pattern"],
                         [r["name"] for r in self.rows("?kind=pattern")])
        modules = self.rows("?kind=module")
        self.assertEqual({"kind", "id", "path", "responsibility", "symbols", "notes", "updated"},
                         set(modules[0]))
        self.assertEqual(["plugin/tools/b.py"], [m["path"] for m in self.rows("?kind=module&q=zebracorn")])

    def test_agent_area_and_task_filters(self):
        self.assertEqual(["ranked-lesson"], [r["signature"] for r in self.rows("?agent=senior-backend-dev")])
        self.assertEqual(2, len(self.rows("?area=hooks")))
        self.assertEqual(["ranked-lesson"], [r["signature"] for r in self.rows("?task=task-0001")])
        patterns = self.rows("?kind=pattern&task=task-0001")
        self.assertEqual(["zebracorn-pattern"], [r["name"] for r in patterns])

    def test_filters_on_columns_a_kind_does_not_have_match_nothing(self):
        self.assertEqual([], self.rows("?kind=pattern&agent=qa-engineer"))
        self.assertEqual([], self.rows("?kind=module&area=hooks"))
        self.assertEqual([], self.rows("?kind=module&task=task-0001"))

    def test_the_like_fallback_when_the_query_has_no_usable_term(self):
        # fts_terms keeps words of 4+ letters, so "zeb" yields no FTS term and LIKE answers.
        self.assertEqual(["ranked-lesson"], [r["signature"] for r in self.rows("?q=ZEB")])

    def test_stats_numbers_equal_direct_sql(self):
        stats = self.data("/api/v1/memory/stats")["data"]
        db = self.root / ".agentry" / "memory" / "memory.db"
        conn = sqlite3.connect(db)
        try:
            counts = {k: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for k, t in (("lesson", "lessons"), ("pattern", "patterns"), ("module", "modules"))}
            by_agent = dict(conn.execute("SELECT agent, COUNT(*) FROM lessons WHERE agent != '' "
                                         "GROUP BY agent").fetchall())
            by_area = dict(conn.execute("SELECT area, COUNT(*) FROM lessons WHERE area != '' "
                                        "GROUP BY area").fetchall())
        finally:
            conn.close()
        self.assertEqual(db.stat().st_size, stats["db_bytes"])
        self.assertEqual(0, stats["wal_bytes"])
        self.assertEqual(counts, stats["counts"])
        self.assertEqual(by_agent, stats["by_agent"])
        self.assertEqual(by_area, stats["by_area"])
        self.assertEqual(30, len(stats["growth"]))
        self.assertEqual(sorted(g["day"] for g in stats["growth"]), [g["day"] for g in stats["growth"]])

    def test_the_memory_graph_links_rows_to_their_task_agent_and_area(self):
        graph = self.data("/api/v1/graphs/memory")["data"]
        nodes = {n["id"]: n for n in graph["nodes"]}
        self.assertNotIn("lesson:3", nodes)  # the retired lesson is not in the graph
        self.assertEqual(2, nodes["area:hooks"]["weight"])
        self.assertEqual({"kind": "area", "label": "hooks"},
                         {k: nodes["area:hooks"][k] for k in ("kind", "label")})
        self.assertEqual(2, nodes["task:task-0001"]["weight"])  # a lesson and a pattern
        self.assertEqual(3, nodes["lesson:1"]["weight"])
        self.assertEqual(1, nodes["agent:qa-engineer"]["weight"])
        self.assertEqual(len(graph["edges"]), sum(n["weight"] for n in graph["nodes"]) // 2)
        self.assertEqual({"source": "lesson:1", "target": "area:hooks", "kind": "area", "weight": 1},
                         next(e for e in graph["edges"] if e["target"] == "area:hooks"))


class MemoryFallbackTest(unittest.TestCase):
    """The LIKE fallback when FTS5 cannot rank, in process on a sandbox store."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_memfb_")
        sb.make_project(self.root)
        sb.seed(self.root, {"lessons": [
            {"signature": "zebra-lesson", "trigger": "t", "what": "the zebracorn problem", "why": "w",
             "fix": "f"},
            {"signature": "other-lesson", "trigger": "t", "what": "other things", "why": "w", "fix": "f"}]})
        self.ctx = stores.Ctx(self.root, TOOLS_DIR.parent, 1, "http://127.0.0.1:1", "t", self.root)

    def signatures(self, query: str) -> list[str]:
        rows, meta = views_memory.memory_rows(self.ctx, stores.Params(stores.parse_query(query)))
        self.assertEqual(len(rows), meta["total"])
        return [r["signature"] for r in rows]

    def test_a_query_with_no_usable_term_falls_back_to_like(self):
        with unittest.mock.patch.object(memory, "fts_query", return_value=""):
            self.assertEqual(["zebra-lesson"], self.signatures("q=zebracorn"))

    def test_a_store_without_the_fts_table_falls_back_to_like(self):
        conn = sqlite3.connect(self.root / ".agentry" / "memory" / "memory.db")
        conn.execute("DROP TABLE mem_fts")
        conn.commit()
        conn.close()
        self.assertEqual(["zebra-lesson"], self.signatures("q=zebracorn"))

    def test_like_is_case_insensitive_and_escapes_wildcards(self):
        with unittest.mock.patch.object(memory, "fts_query", return_value=""):
            self.assertEqual(["zebra-lesson"], self.signatures("q=ZEBRACORN"))
            self.assertEqual([], self.signatures("q=%25"))
            self.assertEqual([], self.signatures("q=_"))


class BigMemoryTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        sb.seed(root, {"lessons": [
            {"signature": f"common-{i:04d}", "trigger": "t", "what": "a commonword case", "why": "w",
             "fix": "f", "created": "2026-10-01"} for i in range(1500)]})

    def test_the_total_of_a_ranked_search_is_exact_beyond_any_page(self):
        body = self.data("/api/v1/memory?q=commonword&limit=200&offset=1400")
        self.assertEqual(1500, body["meta"]["total"])
        self.assertEqual(100, len(body["data"]))
        self.assertEqual(1500, self.data("/api/v1/memory?q=commonword&limit=1")["meta"]["total"])


class MemoryInjectionTest(ServerCase):

    LESSONS = (
        {"signature": "alpha-hooks-lesson", "trigger": "editing hooks", "what": "hooks failed open",
         "why": "w", "fix": "check the hooks before shipping", "area": "hooks"},
        {"signature": "beta-memory-lesson", "trigger": "memory store", "what": "memory rows vanished",
         "why": "w", "fix": "record the memory rows"},
        {"signature": "unrelated-lesson", "trigger": "t", "what": "zzzz yyyy xxxx", "why": "w", "fix": "f"},
    )

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        sb.seed(root, {"lessons": list(cls.LESSONS),
                       "patterns": [{"name": "hooks-pattern", "use_when": "hooks memory", "body": "b"}]})

    def profiles(self) -> dict:
        return self.data("/api/v1/memory/stats")["data"]["injection"]

    def expected(self, kinds: list[str], limit: int) -> tuple[int, int, int]:
        """matched, shown and bytes of the REAL SubagentStart hook on this project."""
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)}
        done = subprocess.run([sys.executable, str(TOOLS_DIR / "memory" / "inject.py"),
                               "--kinds", ",".join(kinds), "--limit", str(limit)],
                              env=env, input="", capture_output=True, text=True, timeout=60, check=True)
        if not done.stdout.strip():
            return 0, 0, 0
        block = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        head = re.match(r"## Project memory: (\d+)(?: of (\d+) matching)?", block)
        shown = int(head.group(1))
        return int(head.group(2) or shown), shown, len(block.encode("utf-8"))

    def test_with_no_active_task_nothing_would_be_injected(self):
        injection = self.profiles()
        self.assertEqual([], injection["active_tasks"])
        self.assertEqual(3800, injection["budget_bytes"])
        self.assertEqual(3, len(injection["profiles"]))
        for profile in injection["profiles"]:
            self.assertEqual((0, 0, 0), (profile["matched"], profile["shown"], profile["bytes"]))

    def test_with_an_active_task_every_profile_equals_the_real_hook(self):
        active = self.root / ".agentry" / "tasks" / "active"
        active.mkdir(parents=True, exist_ok=True)
        (active / "task-0200.md").write_text(
            "---\nid: 200\ntitle: Fix the hooks and the memory store\n---\nEditing hooks; the memory rows.\n",
            encoding="utf-8")
        self.addCleanup((active / "task-0200.md").unlink)
        injection = self.profiles()
        self.assertEqual(["task-0200"], injection["active_tasks"])
        planners = "architect|product-manager|sprint-prioritizer|rapid-prototyper"
        self.assertEqual({planners: ["module", "pattern", "lesson"], "spec-developer": ["module", "lesson"]},
                         {p["matcher"]: p["kinds"] for p in injection["profiles"][:2]})
        for profile in injection["profiles"]:
            with self.subTest(matcher=profile["matcher"]):
                expected = self.expected(profile["kinds"], profile["limit"])
                self.assertEqual(expected, (profile["matched"], profile["shown"], profile["bytes"]))
                self.assertLessEqual(profile["bytes"], injection["budget_bytes"])
        self.assertTrue(any(p["matched"] > 0 for p in injection["profiles"]))


# --------------------------------------------------------------------------- codegraph

class CodegraphTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        config = json.loads((root / ".agentry" / "pipeline.json").read_text(encoding="utf-8"))
        config["repos"] = {"app": {"path": "app"}, "gone": {"path": "nothere"}, "bare": {"path": "bare"}}
        (root / ".agentry" / "pipeline.json").write_text(json.dumps(config), encoding="utf-8")
        sb.make_codegraph(root, ["a/x1.py", "a/x2.py", "b/y.py", "c/z.py"],
                          [("a/x1.py", "b/y.py", 3), ("a/x2.py", "b/y.py", 1), ("b/y.py", "c/z.py", 2)])
        stars = [f"m{i:02d}/f.py" for i in range(30)]
        sb.make_codegraph(root / "app", ["hub/h.py", *stars], [("hub/h.py", s, 1) for s in stars])
        (root / "bare" / ".codegraph").mkdir(parents=True)
        (root / "bare" / ".codegraph" / "codegraph.db").write_bytes(b"")
        (root / "odd" / ".codegraph").mkdir(parents=True)

    def indexes(self) -> dict:
        return {i["key"]: i for i in self.data("/api/v1/codegraph")["data"]}

    def test_each_index_is_listed_with_its_counts(self):
        found = self.indexes()
        self.assertEqual({"root", "app", "bare"}, set(found))
        root = found["root"]
        self.assertEqual((True, 4, 8, 6, ".codegraph"), (root["supported"], root["files"], root["symbols"],
                                                         root["edges"], root["path"]))
        self.assertGreater(root["db_bytes"], 0)
        self.assertRegex(root["last_sync"], TIMESTAMP)
        self.assertEqual("app/.codegraph", found["app"]["path"])

    def test_an_empty_file_is_an_unsupported_index_not_an_error(self):
        bare = self.indexes()["bare"]
        self.assertEqual((False, 0, 0, 0), (bare["supported"], bare["files"], bare["symbols"], bare["edges"]))

    def test_the_module_graph_aggregates_files_into_folders(self):
        body = self.data("/api/v1/codegraph/root/graph")
        nodes = {n["id"]: n for n in body["data"]["nodes"]}
        self.assertEqual({"a": 1, "b": 2, "c": 1}, {k: v["weight"] for k, v in nodes.items()})
        self.assertEqual({"module"}, {n["kind"] for n in nodes.values()})
        self.assertEqual([("a", "b", 4), ("b", "c", 2)],
                         sorted((e["source"], e["target"], e["weight"]) for e in body["data"]["edges"]))
        self.assertIs(False, body["meta"]["truncated"])
        self.assertIsInstance(body["meta"]["elapsed_ms"], int)

    def test_the_file_level_graph(self):
        nodes = self.data("/api/v1/codegraph/root/graph?level=file")["data"]["nodes"]
        self.assertEqual({"a/x1.py", "a/x2.py", "b/y.py", "c/z.py"}, {n["id"] for n in nodes})

    def test_a_graph_over_the_limit_keeps_the_highest_degree_nodes(self):
        body = self.data("/api/v1/codegraph/app/graph?limit=10")
        nodes = body["data"]["nodes"]
        self.assertEqual(10, len(nodes))
        self.assertEqual("hub", nodes[0]["id"])
        self.assertEqual(30, nodes[0]["weight"])
        kept = {n["id"] for n in nodes}
        self.assertTrue(all(e["source"] in kept and e["target"] in kept for e in body["data"]["edges"]))
        self.assertEqual(9, len(body["data"]["edges"]))

    def test_an_unsupported_schema_is_an_empty_200_graph(self):
        body = self.data("/api/v1/codegraph/bare/graph")
        self.assertEqual({"nodes": [], "edges": []}, body["data"])
        self.assertIs(False, body["meta"]["truncated"])
        self.assertIn("elapsed_ms", body["meta"])

    def test_a_declared_key_without_an_index_is_404(self):
        self.assertError("/api/v1/codegraph/gone/graph", 404, "not_found")


class CodegraphEmptyTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)

    def test_a_project_without_an_index_has_no_code_indexes(self):
        self.assertEqual([], self.data("/api/v1/codegraph")["data"])


class CodegraphBudgetTest(unittest.TestCase):
    """The query budget and the stat-keyed cache, in process (a child cannot be made slow)."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_cg_")
        sb.make_project(self.root)
        files = [f"m{i}/f.py" for i in range(40)]
        self.db = sb.make_codegraph(self.root, files, [(files[0], f, 2) for f in files[1:]])
        self.ctx = stores.Ctx(self.root, TOOLS_DIR.parent, 1, "http://127.0.0.1:1", "t", self.root)
        views_memory._graph_cache.clear()
        self.addCleanup(views_memory._graph_cache.clear)

    def graph(self, query: str = "") -> tuple:
        return views_memory.codegraph_graph(self.ctx, stores.Params(stores.parse_query(query)), "root")

    def test_an_exhausted_budget_returns_what_it_has_flagged_truncated(self):
        started = time.monotonic()
        with unittest.mock.patch.object(views_memory, "QUERY_BUDGET_S", 0.0):
            data, meta = self.graph()
        self.assertTrue(meta["truncated"])
        self.assertLess(time.monotonic() - started, 3.5)
        self.assertLessEqual(len(data["nodes"]), 40)

    def test_two_calls_on_an_unchanged_file_query_the_index_once(self):
        opened = []
        real = stores.ro_connect
        with unittest.mock.patch.object(stores, "ro_connect",
                                        side_effect=lambda p: opened.append(p) or real(p)):
            first = self.graph()
            second = self.graph()
        self.assertEqual(1, len(opened))
        self.assertEqual(first, second)

    def test_a_changed_file_is_queried_again(self):
        opened = []
        real = stores.ro_connect
        with unittest.mock.patch.object(stores, "ro_connect",
                                        side_effect=lambda p: opened.append(p) or real(p)):
            self.graph()
            conn = sqlite3.connect(self.db)
            conn.execute("INSERT INTO files VALUES ('new/f.py', 'h', 'python', 1)")
            conn.commit()
            conn.close()
            os.utime(self.db, (time.time() + 10, time.time() + 10))
            self.graph()
        self.assertEqual(2, len(opened))

    def test_different_parameters_are_cached_separately(self):
        a = self.graph("limit=10")[0]
        b = self.graph("level=file&limit=10")[0]
        self.assertNotEqual(a["nodes"], b["nodes"])


# --------------------------------------------------------------------------- harness

class HarnessRealTreeTest(ServerCase):
    """The repository itself as the project: its own agents, rules, skills and hooks."""

    @classmethod
    def setUpClass(cls):
        cls.root = sb.REPO_ROOT
        cls.server = sb.start_server(cls.root)
        cls.addClassCleanup(cls.server.stop)

    def harness(self) -> dict:
        return self.data("/api/v1/harness")["data"]

    def test_every_agent_file_is_listed_and_the_roster_agents_have_a_department(self):
        agents = self.harness()["agents"]
        files = sorted((self.root / ".claude" / "agents").glob("*.md"))
        self.assertEqual(len(files), len(agents))
        roster = (self.root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        for agent in agents:
            with self.subTest(agent=agent["name"]):
                if re.search(rf"^\| {re.escape(agent['name'])} \|", roster, re.MULTILINE):
                    self.assertNotEqual("", agent["department"])
                    self.assertIn(agent["profile"], ("dev", "docs", "readonly"))
        backend = next(a for a in agents if a["name"] == "senior-backend-dev")
        self.assertEqual("Technical", backend["department"])
        self.assertEqual("dev", backend["profile"])
        self.assertIn("coding-style.md", backend["rules"])
        self.assertIn("feature-scaffold", backend["skills"])
        self.assertIn("Bash", backend["tools"])

    def test_rule_delivery(self):
        rules = {r["name"]: r for r in self.harness()["rules"]}
        for name in ("quality-standard.md", "communication.md", "task-creation.md", "code-retrieval.md",
                     "self-learning.md", "pipeline.md"):
            self.assertEqual("core", rules[name]["delivery"], name)
        for name in ("git-workflow.md", "orchestration.md"):
            self.assertEqual("main-thread", rules[name]["delivery"], name)
        self.assertEqual("targeted", rules["coding-style.md"]["delivery"])
        self.assertIn("senior-backend-dev", rules["coding-style.md"]["agents"])
        for rule in rules.values():
            self.assertEqual((self.root / ".claude" / "rules" / rule["name"]).stat().st_size, rule["bytes"])
            if rule["delivery"] == "unused":
                self.assertEqual([], rule["agents"])

    def test_skills_hooks_and_plugin(self):
        data = self.harness()
        skills = {(s["source"], s["name"]): s for s in data["skills"]}
        plugin_skills = list((self.root / "plugin" / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(plugin_skills), sum(1 for s in data["skills"] if s["source"] == "plugin"))
        self.assertEqual("/agentry:swarm", skills[("plugin", "swarm")]["command"])
        self.assertEqual("/verify", skills[("project", "verify")]["command"])
        self.assertIn("senior-backend-dev", skills[("plugin", "feature-scaffold")]["preloaded_by"])
        self.assertEqual("agentry", data["plugin"]["name"])
        self.assertEqual("directory", data["plugin"]["install"])
        gate = next(h for h in data["hooks"] if h["script"].endswith("pretool_gate.py"))
        self.assertEqual(("plugin", "PreToolUse", "tools/pipeline/pretool_gate.py", True),
                         (gate["source"], gate["event"], gate["script"], gate["script_exists"]))
        self.assertTrue(all(h["script_exists"] for h in data["hooks"]))


class HarnessFabricatedTreeTest(unittest.TestCase):
    """A hand-built tree: unused rules, a removed hook script, a cache install, user-only skills."""

    def setUp(self):
        base = tmproot.sandbox(self, "dash_harness_")
        self.project = base / "project"
        self.plugin = base / "plugins" / "cache" / "market" / "agentry" / "0.9.0"
        for folder in (self.project / ".claude" / "agents", self.project / ".claude" / "rules",
                       self.project / ".claude" / "skills" / "local", self.plugin / ".claude-plugin",
                       self.plugin / "hooks", self.plugin / "tools" / "hooks", self.plugin / "skills" / "go"):
            folder.mkdir(parents=True)
        (self.project / ".claude" / "CLAUDE.md").write_text(
            "# Roster\n\n**Technical department [ACTIVE]:**\n\n"
            "| Agent | Profile | Dispatch |\n|---|---|---|\n"
            "| alpha | dev | builds |\n| beta | readonly | reviews |\n\n"
            "**Support department:**\n\n| delta | docs | writes |\n\n@rules/core-rule.md\n",
            encoding="utf-8")
        agents = self.project / ".claude" / "agents"
        (agents / "alpha.md").write_text(
            "---\nname: alpha\ndescription: Builds things\nmodel: sonnet\ntools: Read, Write, Bash\n"
            "rules:\n  - core-rule.md\n  - targeted-rule.md\nskills:\n  - go\nprofile: dev\n---\nbody\n",
            encoding="utf-8")
        (agents / "gamma.md").write_text("---\nname: gamma\ndescription: Not in any table\n---\n",
                                         encoding="utf-8")
        rules = self.project / ".claude" / "rules"
        for name in ("core-rule.md", "targeted-rule.md", "lonely-rule.md", "git-workflow.md"):
            (rules / name).write_text(f"# {name}\n", encoding="utf-8")
        (self.plugin / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "agentry", "version": "0.9.0"}', encoding="utf-8")
        (self.plugin / "skills" / "go" / "SKILL.md").write_text(
            "---\nname: go\ndescription: Goes\ndisable-model-invocation: true\n---\n", encoding="utf-8")
        (self.project / ".claude" / "skills" / "local" / "SKILL.md").write_text(
            "---\nname: local\ndescription: Local skill\n---\n", encoding="utf-8")
        (self.plugin / "tools" / "hooks" / "present.py").write_text("", encoding="utf-8")
        (self.plugin / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Bash|Edit", "hooks": [
                {"type": "command", "command": 'python "${CLAUDE_PLUGIN_ROOT}/tools/hooks/present.py"',
                 "timeout": 20},
                {"type": "command", "command": 'python "${CLAUDE_PLUGIN_ROOT}/tools/hooks/removed.py"'}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}]}}), encoding="utf-8")
        self.ctx = stores.Ctx(self.project, self.plugin, 1, "http://127.0.0.1:1", "t", self.project)

    def harness(self) -> dict:
        return views_harness.harness(self.ctx, stores.Params({}))[0]

    def test_departments_profiles_and_the_agent_missing_from_the_tables(self):
        agents = {a["name"]: a for a in self.harness()["agents"]}
        alpha = agents["alpha"]
        self.assertEqual(("Technical", "dev", ["Read", "Write", "Bash"], ["core-rule.md", "targeted-rule.md"],
                          ["go"]), (alpha["department"], alpha["profile"], alpha["tools"], alpha["rules"],
                                    alpha["skills"]))
        self.assertEqual(("", ""), (agents["gamma"]["department"], agents["gamma"]["profile"]))

    def test_rules_are_core_main_thread_targeted_or_unused(self):
        rules = {r["name"]: r for r in self.harness()["rules"]}
        self.assertEqual({"core-rule.md": "core", "git-workflow.md": "main-thread",
                          "targeted-rule.md": "targeted", "lonely-rule.md": "unused"},
                         {k: v["delivery"] for k, v in rules.items()})
        self.assertEqual(["alpha"], rules["targeted-rule.md"]["agents"])
        self.assertEqual([], rules["lonely-rule.md"]["agents"])

    def test_a_hook_whose_script_was_removed_is_flagged(self):
        hooks = {h["script"]: h for h in self.harness()["hooks"] if h["script"]}
        self.assertTrue(hooks["tools/hooks/present.py"]["script_exists"])
        self.assertFalse(hooks["tools/hooks/removed.py"]["script_exists"])
        present = hooks["tools/hooks/present.py"]
        self.assertEqual((20, "Bash|Edit", "PreToolUse"),
                         (present["timeout"], present["matcher"], present["event"]))
        echo = next(h for h in self.harness()["hooks"] if h["command"] == "echo done")
        self.assertEqual(("", True, None), (echo["script"], echo["script_exists"], echo["timeout"]))

    def test_skills_carry_the_user_only_flag_command_and_preloader(self):
        skills = {s["name"]: s for s in self.harness()["skills"]}
        self.assertEqual(("plugin", "/agentry:go", True, ["alpha"]),
                         (skills["go"]["source"], skills["go"]["command"], skills["go"]["user_only"],
                          skills["go"]["preloaded_by"]))
        self.assertEqual(("project", "/local", False), (skills["local"]["source"], skills["local"]["command"],
                                                         skills["local"]["user_only"]))

    def test_a_plugin_under_a_plugin_cache_folder_is_a_cache_install(self):
        self.assertEqual("cache", self.harness()["plugin"]["install"])
        plain = stores.Ctx(self.project, self.project / "somewhere", 1, "http://127.0.0.1:1", "t",
                           self.project)
        data = views_harness.harness(plain, stores.Params({}))[0]
        self.assertEqual("directory", data["plugin"]["install"])

    def test_the_plugin_block(self):
        plugin = self.harness()["plugin"]
        self.assertEqual(("agentry", "0.9.0", str(self.plugin)),
                         (plugin["name"], plugin["version"], plugin["root"]))


# --------------------------------------------------------------------------- stores that are not there

class EmptyProjectTest(ServerCase):

    @classmethod
    def build(cls, root: Path) -> None:
        (root / ".agentry").mkdir()

    def test_every_route_is_200_and_zeroed(self):
        for path in ALL_15[:-1]:
            if path.endswith(("task-0106",)):
                continue
            with self.subTest(path=path):
                status, _, body = self.api(path)
                self.assertEqual(200, status, body)
                if path in PAGED:
                    self.assertEqual(0, body["meta"]["total"])
                    self.assertEqual([], body["data"])
        status = self.data("/api/v1/status")["data"]
        self.assertEqual(({"backlog": 0, "active": 0, "done": 0, "hidden_superseded": 0},
                          {"handoffs": [], "memory": []}), (status["counts"], status["debt"]))

    def test_id_routes_are_404(self):
        self.assertError("/api/v1/tasks/task-0001", 404, "not_found")
        self.assertError("/api/v1/handoffs/task-0001", 404, "not_found")
        self.assertError("/api/v1/codegraph/root/graph", 404, "not_found")

    def test_the_empty_event_store_is_zeros(self):
        self.assertEqual({"total": 0, "limit": 25, "offset": 0, "latest_id": 0,
                          "kinds": sorted(events.KINDS)}, self.data("/api/v1/events")["meta"])
        metrics = self.data("/api/v1/metrics")["data"]
        self.assertEqual((0, "", None, []), (metrics["events_total"], metrics["span"],
                                              metrics["lead_time_s"], metrics["gates"]))

    def test_the_empty_memory_store_is_zeros(self):
        stats = self.data("/api/v1/memory/stats")["data"]
        self.assertEqual(({"lesson": 0, "pattern": 0, "module": 0}, 0, 0, {}, {}, []),
                         (stats["counts"], stats["db_bytes"], stats["wal_bytes"], stats["by_agent"],
                          stats["by_area"], stats["stamps"]))
        self.assertEqual({"nodes": [], "edges": []}, self.data("/api/v1/graphs/memory")["data"])

    def test_nothing_was_created_by_asking(self):
        for path in ALL_15[:-1]:
            self.api(path)
        self.assertEqual([], sorted(p.name for p in (self.root / ".agentry").iterdir()))


class OldSchemaTest(ServerCase):
    """A run.db written before the late columns existed is read with those fields defaulted."""

    @classmethod
    def build(cls, root: Path) -> None:
        sb.make_project(root)
        sb.write_task(root, "active", "task-0001", title="Old run")
        conn = sqlite3.connect(root / ".agentry" / "state" / "run.db")
        conn.execute("CREATE TABLE runs (task TEXT PRIMARY KEY, type TEXT, stage TEXT, stage_status TEXT, "
                     "awaiting_human TEXT, updated TEXT)")
        conn.execute("INSERT INTO runs VALUES ('task-0001', 'feature', 'implement', 'in_progress', '', "
                     "'2026-10-01T10:00:00Z')")
        conn.commit()
        conn.close()

    def test_runs_and_status_default_the_missing_columns(self):
        run = self.data("/api/v1/runs")["data"][0]
        self.assertEqual(("task-0001", "Old run", "build", 0, "", "", False, []),
                         (run["task"], run["title"], run["pipeline"], run["retries"], run["epic"],
                          run["worktree"], run["umbrella"], run["subtasks"]))
        self.assertEqual("now", next(s["state"] for s in run["stages"] if s["name"] == "implement"))
        self.assertEqual(200, self.api("/api/v1/status")[0])
        self.assertEqual("task-0001", self.data("/api/v1/tasks/task-0001")["data"]["run"]["task"])


# --------------------------------------------------------------------------- 503

class UnavailableStoreTest(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_503_")
        sb.make_project(self.root)
        sb.seed(self.root, {"events": event_rows(5), "lessons": [
            {"signature": "a-lesson", "trigger": "t", "what": "w", "why": "w", "fix": "f"}]})
        self.server = sb.start_server(self.root)
        self.addCleanup(self.server.stop)

    def test_a_write_locked_events_store_is_503_with_retry_after_within_1_5_seconds(self):
        db = self.root / ".agentry" / "state" / "events.db"
        locker = sqlite3.connect(db, isolation_level=None)
        self.addCleanup(locker.close)
        locker.execute("PRAGMA journal_mode = DELETE")  # in WAL mode readers are not blocked by a writer
        locker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        status, head, raw = self.server.get("/api/v1/events")
        elapsed = time.monotonic() - started
        self.assertEqual(503, status)
        self.assertEqual("1", head["Retry-After"])
        self.assertEqual("unavailable", json.loads(raw)["code"])
        self.assertLess(elapsed, 1.5)
        locker.execute("ROLLBACK")
        self.assertEqual(200, self.server.get("/api/v1/events")[0])

    def test_a_store_that_is_not_sqlite_is_503_and_a_missing_one_is_an_empty_200(self):
        memory_db = self.root / ".agentry" / "memory" / "memory.db"
        memory_db.write_bytes(b"this is not a database" * 100)
        status, head, raw = self.server.get("/api/v1/memory")
        self.assertEqual((503, "1", "unavailable"), (status, head["Retry-After"], json.loads(raw)["code"]))
        memory_db.unlink()
        status, _, raw = self.server.get("/api/v1/memory")
        self.assertEqual((200, []), (status, json.loads(raw)["data"]))

    def test_a_corrupt_events_store_is_503_on_every_route_that_reads_it(self):
        (self.root / ".agentry" / "state" / "events.db").write_bytes(b"junk" * 200)
        for path in ("/api/v1/events", "/api/v1/metrics"):
            with self.subTest(path=path):
                self.assertEqual(503, self.server.get(path)[0])
        self.assertEqual(200, self.server.get("/api/v1/epics")[0])

    def test_the_server_keeps_answering_after_a_503(self):
        (self.root / ".agentry" / "memory" / "memory.db").write_bytes(b"junk" * 200)
        self.assertEqual(503, self.server.get("/api/v1/memory/stats")[0])
        self.assertEqual(200, self.server.get("/api/v1/status")[0])
        self.assertTrue(self.server.alive())


if __name__ == "__main__":
    unittest.main()
