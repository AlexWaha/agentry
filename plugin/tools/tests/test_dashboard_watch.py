"""task-0141: the dashboard change watcher (watch.py).

Two kinds of test. The tick logic is driven by calling watch.tick directly with
the client count and the broadcast replaced, which is deterministic and fast.
The pieces that need a real client (idle cost, the event latency, the baseline
taken before hello) run the watcher thread beside an in-process WebSocket server.
Spec-0002 FR-3, FR-49 to FR-53 and NFR-7, NFR-8.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR / "dashboard"))
sys.path.insert(0, str(TESTS_DIR))

import dashboard_sandbox as sandbox
import stores
import test_dashboard_ws_session as kit
import tmproot
import watch
import ws

EVENT_KEYS = [
    "at", "detail", "from_stage", "id", "kind", "lane", "outcome", "pipeline", "stage", "task", "via"]


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_runs_db(path: Path, rows: tuple[tuple[str, str], ...] = (("task-0001", "implement"),)) -> None:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS runs (task TEXT PRIMARY KEY, stage TEXT, updated TEXT)")
        conn.executemany("INSERT OR REPLACE INTO runs VALUES (?, ?, '2026-10-16')", rows)
        conn.commit()


class TickCase(unittest.TestCase):
    """watch.tick against a sandbox project, one pretend client."""

    def setUp(self):
        self.project = kit.make_project(tmproot.sandbox(self, "watch"))
        self.plugin = self.project / "plugin"
        for part in ("skills", "hooks", ".claude-plugin"):
            (self.plugin / part).mkdir(parents=True)
        self.ctx = stores.Ctx(
            project_root=self.project, plugin_root=self.plugin, port=1, origin="http://127.0.0.1:1",
            token="t", dist_dir=self.project / "dist")
        self.sent: list[dict] = []
        for patcher in (unittest.mock.patch.object(ws, "client_count", return_value=1),
                        unittest.mock.patch.object(ws, "broadcast", side_effect=self.sent.append)):
            patcher.start()
            self.addCleanup(patcher.stop)
        watch.stop()
        self.addCleanup(watch.stop)
        self.state = self.project / ".agentry" / "state"

    def tick(self) -> list[dict]:
        watch.tick(self.ctx)
        out, self.sent[:] = list(self.sent), []
        return out

    def baseline(self) -> None:
        self.assertEqual(self.tick(), [], "the first tick only takes the baseline")

    def topics_after(self, change) -> list[list[str]]:
        """Baseline, apply `change`, tick: the topics of every invalidate sent."""
        self.baseline()
        change()
        out = self.tick()
        self.assertEqual({m["type"] for m in out} - {"invalidate"}, set())
        return [m["payload"]["topics"] for m in out]


class EventsTest(TickCase):
    """FR-51."""

    def test_rows_present_before_the_baseline_are_not_delivered(self):
        kit.add_events(self.project, 5)
        self.baseline()
        self.assertEqual(self.tick(), [])

    def test_three_appended_rows_arrive_as_one_message_with_ascending_ids(self):
        self.baseline()
        for task in ("task-0001", "task-0002", "task-0003"):
            kit.add_event(self.project, task=task)
        out = self.tick()
        self.assertEqual([m["type"] for m in out], ["events.appended"])
        events = out[0]["payload"]["events"]
        self.assertEqual([e["id"] for e in events], [1, 2, 3])
        self.assertEqual([e["task"] for e in events], ["task-0001", "task-0002", "task-0003"])
        self.assertEqual(self.tick(), [], "the cursor moved: nothing is delivered twice")

    def test_event_has_the_shape_of_the_contract_and_only_known_detail_keys(self):
        self.baseline()
        kit.add_event(self.project, kind="gate", stage="test",
                      detail={"cmd": "pytest", "exit": 1, "duration_ms": 1234, "reason": "x", "secret": "no"})
        event = self.tick()[0]["payload"]["events"][0]
        self.assertEqual(sorted(event), EVENT_KEYS)
        self.assertEqual(event["detail"], {"reason": "x", "duration_ms": 1234, "exit": 1, "cmd": "pytest"})
        self.assertEqual((event["kind"], event["stage"], event["from_stage"]), ("gate", "test", "plan"))

    def test_row_whose_detail_is_not_json_gets_an_empty_detail(self):
        self.baseline()
        with contextlib.closing(sqlite3.connect(stores.events_db(self.project))) as conn:
            conn.execute("INSERT INTO events (task, kind, detail) VALUES ('task-0001', 'gate', 'not json')")
            conn.commit()
        self.assertEqual(self.tick()[0]["payload"]["events"][0]["detail"], {})

    def test_exactly_200_new_rows_are_one_events_appended(self):
        self.baseline()
        kit.add_events(self.project, 200)
        out = self.tick()
        self.assertEqual([m["type"] for m in out], ["events.appended"])
        self.assertEqual(len(out[0]["payload"]["events"]), 200)

    def test_201_new_rows_in_one_tick_are_a_burst_resync_and_the_cursor_jumps_to_the_end(self):
        self.baseline()
        kit.add_events(self.project, 201)
        out = self.tick()
        self.assertEqual(out, [ws.message("resync", {"reason": "burst"})])
        self.assertEqual(self.tick(), [])
        kit.add_event(self.project)
        after = self.tick()
        self.assertEqual([e["id"] for e in after[0]["payload"]["events"]], [202])

    def test_recreated_journal_with_lower_ids_is_a_resync_then_new_rows_are_delivered(self):
        kit.add_events(self.project, 5)
        self.baseline()
        kit.remove_events_db(self.project)
        kit.create_events_db(self.project)
        kit.add_events(self.project, 2)
        self.assertEqual(self.tick(), [ws.message("resync", {"reason": "cursor_regression"})])
        kit.add_event(self.project)
        out = self.tick()
        self.assertEqual([m["type"] for m in out], ["events.appended"])
        self.assertEqual([e["id"] for e in out[0]["payload"]["events"]], [3])

    def test_journal_that_vanishes_is_skipped_quietly(self):
        kit.add_events(self.project, 2)
        self.baseline()
        kit.remove_events_db(self.project)
        self.assertEqual(self.tick(), [])


class LockedStoreTest(TickCase):
    """FR-50 and FR-3: a locked store is skipped, never raised, never held open."""

    def hold_lock(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("BEGIN EXCLUSIVE")
        self.addCleanup(conn.close)
        return conn

    def test_write_lock_on_events_db_for_two_ticks_sends_nothing_and_logs_nothing(self):
        kit.remove_events_db(self.project)
        kit.create_events_db(self.project, wal=False)
        self.baseline()
        kit.add_event(self.project)
        lock = self.hold_lock(stores.events_db(self.project))
        stderr = io.StringIO()
        with self.assertNoLogs(), contextlib.redirect_stderr(stderr):
            first, second = self.tick(), self.tick()
        self.assertEqual((first, second), ([], []))
        self.assertEqual(stderr.getvalue(), "")
        lock.execute("ROLLBACK")
        out = self.tick()
        self.assertEqual([m["type"] for m in out], ["events.appended"])
        self.assertEqual([e["id"] for e in out[0]["payload"]["events"]], [1])

    def test_locked_run_store_does_not_look_like_a_change(self):
        make_runs_db(self.state / "run.db")
        self.baseline()
        make_runs_db(self.state / "run.db", (("task-0001", "test"),))
        lock = self.hold_lock(self.state / "run.db")
        self.assertEqual(self.tick(), [], "unreadable means skipped, not changed")
        lock.execute("ROLLBACK")
        self.assertEqual([m["payload"]["topics"] for m in self.tick()], [["runs", "status"]])

    def test_no_store_file_is_held_open_after_a_tick(self):
        make_runs_db(self.state / "run.db")
        write(self.project / ".agentry" / "memory" / "memory.db", "memory")
        kit.add_event(self.project)
        self.baseline()
        kit.add_event(self.project)
        self.tick()
        for store in (stores.events_db(self.project), self.state / "run.db",
                      self.project / ".agentry" / "memory" / "memory.db"):
            with self.subTest(store=store.name):
                moved = store.with_name(store.name + ".moved")
                os.replace(store, moved)
                os.replace(moved, store)


class InvalidateTest(TickCase):
    """FR-52, one test per change source."""

    def test_idle_ticks_with_no_change_send_nothing(self):
        self.baseline()
        for _ in range(3):
            self.assertEqual(self.tick(), [])

    def test_changed_runs_table_sends_runs_and_status(self):
        make_runs_db(self.state / "run.db")
        self.assertEqual(
            self.topics_after(lambda: make_runs_db(self.state / "run.db", (("task-0001", "test"),))),
            [["runs", "status"]])

    def test_new_lane_store_sends_runs_and_status(self):
        make_runs_db(self.state / "run.db")
        self.assertEqual(
            self.topics_after(lambda: make_runs_db(self.state / "run.talk.db")), [["runs", "status"]])

    def test_a_run_store_backup_file_is_not_a_lane(self):
        make_runs_db(self.state / "run.db")
        self.assertEqual(
            self.topics_after(lambda: make_runs_db(self.state / "run.db.bak-20261016")), [])

    def test_changed_memory_db_sends_memory(self):
        memory = self.project / ".agentry" / "memory" / "memory.db"
        write(memory, "a")
        self.assertEqual(self.topics_after(lambda: write(memory, "a longer file")), [["memory"]])

    def test_changed_memory_wal_sends_memory(self):
        memory = self.project / ".agentry" / "memory" / "memory.db"
        write(memory, "a")
        self.assertEqual(
            self.topics_after(lambda: write(memory.with_name("memory.db-wal"), "wal")), [["memory"]])

    def test_task_folders_and_epics_send_tasks(self):
        tasks = self.project / ".agentry" / "tasks"
        write(tasks / "backlog" / "task-0001.md", "one")
        changes = {
            "new file in backlog": lambda: write(tasks / "backlog" / "task-0002.md"),
            "file moved to active": lambda: (tasks / "backlog" / "task-0001.md").replace(
                tasks / "active" / "task-0001.md"),
            "file edited in active": lambda: write(tasks / "active" / "task-0001.md", "edited longer"),
            "new epic": lambda: write(tasks / "epics" / "epic-0001.md"),
            "file moved to done": lambda: (tasks / "active" / "task-0001.md").replace(
                tasks / "done" / "task-0001.md"),
        }
        for name, change in changes.items():
            with self.subTest(name):
                self.baseline()
                change()
                self.assertEqual([m["payload"]["topics"] for m in self.tick()], [["tasks"]])

    def test_handoff_listing_sends_handoffs_and_status(self):
        handoffs = self.project / ".agentry" / "tasks" / "handoffs"
        self.assertEqual(
            self.topics_after(lambda: write(handoffs / "task-0001.md")), [["handoffs", "status"]])

    def test_memory_stamp_sends_handoffs_and_status(self):
        self.assertEqual(
            self.topics_after(lambda: write(self.state / "memory" / "task-0001.json", "{}")),
            [["handoffs", "status"]])

    def test_dial_files_and_the_supervisor_record_send_status_and_runs(self):
        for name in ("mode", "approvals", "swarm", "mode.talk", "supervisor.json", "supervisor.talk.json"):
            with self.subTest(name):
                changed = self.topics_after(lambda name=name: write(self.state / name, name))
                self.assertEqual(changed, [["status", "runs"]])

    def test_pipeline_json_sends_status_and_runs(self):
        pipeline = self.project / ".agentry" / "pipeline.json"
        write(pipeline, "{}")
        self.assertEqual(
            self.topics_after(lambda: write(pipeline, '{"workflow": {"mode": "solo"}}')),
            [["status", "runs"]])

    def test_supervisor_log_and_other_state_files_are_not_a_status_change(self):
        for name in ("supervisor.log", "supervisor.lock", "probe.txt"):
            with self.subTest(name):
                self.assertEqual(self.topics_after(lambda name=name: write(self.state / name, "line")), [])

    def test_harness_sources_each_send_harness(self):
        claude, plugin = self.project / ".claude", self.plugin
        changes = {
            "agent": lambda: write(claude / "agents" / "a.md"),
            "rule": lambda: write(claude / "rules" / "r.md"),
            "settings": lambda: write(claude / "settings.json", "{}"),
            "local settings": lambda: write(claude / "settings.local.json", "{}"),
            "roster": lambda: write(claude / "CLAUDE.md", "# roster"),
            "plugin skill": lambda: write(plugin / "skills" / "s" / "SKILL.md"),
            "project skill": lambda: write(claude / "skills" / "p" / "SKILL.md"),
            "hooks": lambda: write(plugin / "hooks" / "hooks.json", "{}"),
            "plugin manifest": lambda: write(plugin / ".claude-plugin" / "plugin.json", "{}"),
        }
        for name, change in changes.items():
            with self.subTest(name):
                self.baseline()
                change()
                self.assertEqual([m["payload"]["topics"] for m in self.tick()], [["harness"]])

    def test_codegraph_index_of_the_root_and_of_a_repo_send_codegraph(self):
        write(self.project / ".agentry" / "pipeline.json", json.dumps({"repos": {"app": {"path": "app"}}}))
        for relative in (".codegraph/codegraph.db", "app/.codegraph/codegraph.db"):
            with self.subTest(relative):
                self.assertEqual(
                    self.topics_after(lambda r=relative: write(self.project / r, "index" * (len(r) % 7 + 1))),
                    [["codegraph"]])

    def test_a_bad_repos_entry_does_not_stop_the_ticks(self):
        repos = {"num": {"path": 5}, "list": {"path": ["x"]}, "empty": {"path": ""}, "junk": 7,
                 "ok": {"path": "app"}}
        write(self.project / ".agentry" / "pipeline.json", json.dumps({"repos": repos}))
        self.assertEqual(
            self.topics_after(lambda: write(self.project / "app" / ".codegraph" / "codegraph.db", "index")),
            [["codegraph"]])
        for config in ('{"repos": [1]}', '{"repos": 3}', "[]"):
            with self.subTest(config):
                pipeline = self.project / ".agentry" / "pipeline.json"
                topics = self.topics_after(lambda c=config: write(pipeline, c))
                self.assertEqual(topics[0][:2], ["status", "runs"])  # seen, so the tick did not abort
                self.assertEqual(self.tick(), [])

    def test_two_sources_in_one_tick_send_one_invalidate_with_both_topic_sets(self):
        memory = self.project / ".agentry" / "memory" / "memory.db"
        write(memory, "a")

        def change():
            write(memory, "a longer file")
            write(self.project / ".agentry" / "tasks" / "backlog" / "task-0009.md")
            write(self.project / ".claude" / "agents" / "b.md")

        self.assertEqual(self.topics_after(change), [["memory", "tasks", "harness"]])

    def test_overlapping_topics_are_listed_once(self):
        make_runs_db(self.state / "run.db")

        def change():
            make_runs_db(self.state / "run.db", (("task-0001", "test"),))
            write(self.state / "mode", "build")

        self.assertEqual(self.topics_after(change), [["runs", "status"]])

    def test_events_and_invalidate_in_one_tick_are_two_messages_events_first(self):
        self.baseline()
        kit.add_event(self.project)
        write(self.project / ".agentry" / "tasks" / "backlog" / "task-0001.md")
        self.assertEqual([m["type"] for m in self.tick()], ["events.appended", "invalidate"])


class IdleTest(TickCase):
    def test_tick_with_no_client_forgets_the_baseline_and_reads_nothing(self):
        self.baseline()
        calls: list[str] = []
        with unittest.mock.patch.object(ws, "client_count", return_value=0), \
                unittest.mock.patch.object(stores, "ro_connect", side_effect=calls.append), \
                unittest.mock.patch("os.scandir", side_effect=calls.append):
            self.assertEqual(self.tick(), [])
        self.assertEqual(calls, [])
        self.assertIsNone(watch._state)

    def test_first_tick_after_idle_takes_a_new_baseline_instead_of_replaying_the_gap(self):
        self.baseline()
        with unittest.mock.patch.object(ws, "client_count", return_value=0):
            self.tick()
        kit.add_events(self.project, 300)
        write(self.project / ".agentry" / "tasks" / "backlog" / "task-0001.md")
        self.assertEqual(self.tick(), [], "what happened while nobody listened is not replayed")


class LiveWatcherTest(unittest.TestCase):
    """The watcher thread beside a real WebSocket server."""

    def setUp(self):
        self.project = kit.make_project(tmproot.sandbox(self, "watch-live"))
        (self.project / "plugin").mkdir()
        self.server = kit.InProcessServer(self.project).start()
        self.clients: list[kit.RawClient] = []
        watch.stop()
        self.addCleanup(self._teardown)

    def _teardown(self):
        for client in self.clients:
            client.close()
        watch.stop()
        self.server.stop()
        self.assertTrue(kit.wait_until(lambda: ws.client_count() == 0))

    def connect(self) -> kit.RawClient:
        client = kit.RawClient(self.server.port)
        self.clients.append(client)
        self.assertEqual(client.message()["type"], "hello")
        return client

    def test_with_no_client_for_three_seconds_the_watcher_touches_no_store_and_no_directory(self):
        calls: list[str] = []
        real_connect, real_scandir = stores.ro_connect, os.scandir

        def counting_connect(path):
            calls.append(str(path))
            return real_connect(path)

        def counting_scandir(path="."):
            calls.append(str(path))
            return real_scandir(path)

        root = str(self.project)
        with unittest.mock.patch.object(watch, "TICK_SECONDS", 0.2), \
                unittest.mock.patch.object(stores, "ro_connect", counting_connect), \
                unittest.mock.patch("os.scandir", counting_scandir):
            watch.start(self.server.ctx)
            time.sleep(3.1)
            idle_calls = [c for c in calls if root in c]
            self.assertEqual(idle_calls, [])
            client = self.connect()
            self.assertTrue(kit.wait_until(lambda: any(root in c for c in calls), 2.0),
                            "a connected client must make the watcher read")
            client.close()

    def test_a_row_appended_after_hello_reaches_the_client_within_two_seconds(self):
        watch.start(self.server.ctx)
        client = self.connect()
        started = time.monotonic()
        kit.add_event(self.project, task="task-0141", stage="implement")
        message = client.message(4.0)
        elapsed = time.monotonic() - started
        self.assertEqual(message["type"], "events.appended")
        self.assertEqual(message["payload"]["events"][0]["task"], "task-0141")
        self.assertLess(elapsed, 2.0)

    def test_a_change_made_right_after_hello_is_never_missed(self):
        """The baseline is taken before hello (the prime hook), not at the first tick."""
        with unittest.mock.patch.object(watch, "TICK_SECONDS", 0.3):
            watch.start(self.server.ctx)
            for attempt in range(3):
                with self.subTest(attempt=attempt):
                    client = self.connect()
                    write(self.project / ".agentry" / "tasks" / "backlog" / f"task-000{attempt}.md")
                    kit.add_event(self.project)
                    types = {client.message(3.0)["type"], client.message(3.0)["type"]}
                    self.assertEqual(types, {"events.appended", "invalidate"})
                    client.close()
                    kit.wait_until(lambda: ws.client_count() == 0)
                    time.sleep(0.5)  # let a tick see the empty room and drop its baseline

    def test_all_connected_clients_receive_the_same_push(self):
        watch.start(self.server.ctx)
        clients = [self.connect() for _ in range(3)]
        kit.add_event(self.project)
        for client in clients:
            self.assertEqual(client.message(4.0)["type"], "events.appended")

    def test_start_is_idempotent_and_stop_ends_the_thread(self):
        watch.start(self.server.ctx)
        thread = watch._thread
        watch.start(self.server.ctx)
        self.assertIs(watch._thread, thread)
        watch.stop()
        self.assertFalse(thread.is_alive())
        self.assertIsNone(watch._thread)

    def test_an_unexpected_error_in_a_tick_does_not_stop_the_watcher(self):
        with unittest.mock.patch.object(watch, "TICK_SECONDS", 0.05), \
                unittest.mock.patch.object(watch, "tick", side_effect=RuntimeError("boom")) as tick:
            watch.start(self.server.ctx)
            self.assertTrue(kit.wait_until(lambda: tick.call_count >= 3, 3.0))
            self.assertTrue(watch._thread.is_alive())


class RealServerPushTest(unittest.TestCase):
    """The same pushes through the real server.py: watcher started at launch, close_all at stop."""

    @classmethod
    def setUpClass(cls):
        root = tmproot.mkdtemp("watch-real")
        cls.addClassCleanup(tmproot.rmtree, root)
        cls.project = sandbox.make_project(root)
        sandbox.seed(cls.project, {"events": [{"kind": "registered", "task": "task-0001"}]})
        cls.server = sandbox.start_server(cls.project)
        cls.addClassCleanup(cls.server.stop)

    def setUp(self):
        self.clients: list[kit.RawClient] = []
        self.addCleanup(lambda: [c.close() for c in self.clients])

    def connect(self, server=None) -> kit.RawClient:
        server = server or self.server
        client = kit.RawClient(server.port, path=f"/ws?token={server.token}")
        self.clients.append(client)
        self.assertEqual(client.status, 101)
        return client

    def test_row_appended_after_hello_reaches_a_raw_socket_client_in_under_two_seconds(self):
        client = self.connect()
        hello = client.message()
        self.assertEqual(hello["type"], "hello")
        started = time.monotonic()
        kit.add_event(self.project, task="task-0141")
        message = client.message(4.0)
        elapsed = time.monotonic() - started
        self.assertEqual(message["type"], "events.appended")
        self.assertEqual(message["payload"]["events"][0]["task"], "task-0141")
        self.assertGreater(message["payload"]["events"][0]["id"], hello["payload"]["latest_event_id"])
        self.assertLess(elapsed, 2.0)

    def test_a_new_task_file_reaches_the_client_as_an_invalidate_for_tasks(self):
        client = self.connect()
        client.message()
        write(self.project / ".agentry" / "tasks" / "backlog" / "task-0777.md", "---\nid: 0777\n---\n")
        self.assertEqual(client.message(4.0)["payload"], {"topics": ["tasks"]})

    def test_stopping_the_server_closes_the_client_with_1001(self):
        with sandbox.start_server(self.project) as server:
            client = self.connect(server)
            client.message()
            exit_code = server.stop()
            self.assertEqual(client.close_code(), 1001)
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
