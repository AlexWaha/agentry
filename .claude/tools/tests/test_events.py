"""task-0046 S1: the append-only event journal (pipeline/events.py).

Pins the contract the writers (S2) code against: the closed kind set, the
append-only triggers, fail-open on an unwritable path, concurrent writers, the
two aggregate queries against a seeded journal, and the one-time copy of the
legacy gate_log rows that never touches the live table.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import events
import tmproot

TASK = "task-0042"

WORKER = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import events
events.STATE_DIR = Path(sys.argv[2])
for i in range(50):
    events.append("gate", sys.argv[3], stage="implement", outcome="pass",
                  via="worker", detail={"n": i})
"""


class JournalCase(unittest.TestCase):
    """A sandboxed state directory the module is pointed at."""

    def setUp(self):
        self.state = tmproot.sandbox(self, "events_") / "state"
        patcher = unittest.mock.patch.object(events, "STATE_DIR", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self, sql: str = "SELECT * FROM events ORDER BY id", args=()) -> list:
        conn = sqlite3.connect(events.db_path())
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def seed(self, rows: list) -> None:
        """Insert rows with explicit timestamps (append() stamps 'now').
        Each row: (at, task, kind, stage, from_stage, outcome, detail dict)."""
        events.append("registered", "seed-init")  # creates the schema
        conn = events._connect()
        try:
            for at, task, kind, stage, from_stage, outcome, detail in rows:
                conn.execute(events._INSERT, (
                    at, "", task, "build", kind, stage, from_stage, outcome,
                    "test", json.dumps(detail)))
        finally:
            conn.close()


class AppendTest(JournalCase):
    def test_append_stores_every_field_and_returns_true(self):
        with unittest.mock.patch.dict("os.environ", {"PIPELINE_LANE": "side"}):
            ok = events.append("transition", TASK, stage="test", from_stage="implement",
                               outcome="gate_passed", via="advance", pipeline="build",
                               detail={"retries": 0})
        self.assertTrue(ok)
        [row] = self.rows()
        self.assertEqual(
            (row["lane"], row["task"], row["pipeline"], row["kind"], row["stage"],
             row["from_stage"], row["outcome"], row["via"]),
            ("side", TASK, "build", "transition", "test", "implement",
             "gate_passed", "advance"))
        self.assertEqual(json.loads(row["detail"]), {"retries": 0})
        self.assertRegex(row["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_invalid_lane_in_environment_is_recorded_as_default_lane(self):
        with unittest.mock.patch.dict("os.environ", {"PIPELINE_LANE": "../evil"}):
            events.append("parked", TASK)
        self.assertEqual(self.rows()[0]["lane"], "")

    def test_unknown_kind_is_skipped_with_a_stderr_note(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok = events.append("exploded", TASK)
        self.assertFalse(ok)
        self.assertIn("[event-skipped]", err.getvalue())
        self.assertFalse(events.db_path().exists())

    def test_oversized_detail_is_replaced_by_a_marker(self):
        events.append("gate", TASK, detail={"output": "x" * 10000})
        detail = json.loads(self.rows()[0]["detail"])
        self.assertEqual(list(detail), ["truncated"])

    def test_kinds_are_the_closed_set_from_the_plan(self):
        self.assertEqual(events.KINDS, {
            "registered", "transition", "gate", "blocked", "parked", "approved",
            "rejected", "continued", "merged", "closed", "paused", "resumed"})


class AppendOnlyTest(JournalCase):
    def test_update_is_aborted_by_trigger(self):
        events.append("parked", TASK, stage="ready")
        conn = sqlite3.connect(events.db_path())
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            conn.execute("UPDATE events SET stage = 'implement'")
        self.assertIn("append-only", str(ctx.exception))
        self.assertEqual(self.rows()[0]["stage"], "ready")

    def test_delete_is_aborted_by_trigger(self):
        events.append("parked", TASK)
        conn = sqlite3.connect(events.db_path())
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            conn.execute("DELETE FROM events")
        self.assertIn("append-only", str(ctx.exception))
        self.assertEqual(len(self.rows()), 1)

    def test_module_exposes_no_update_or_delete_function(self):
        names = {n for n in dir(events) if not n.startswith("_")}
        self.assertFalse({n for n in names if n.startswith(("update", "delete", "remove"))})


class ConcurrencyTest(JournalCase):
    def test_four_writers_of_fifty_events_lose_nothing(self):
        env_state = str(self.state)
        events.append("registered", "seed-init")  # schema exists before the race
        procs = [
            subprocess.Popen(
                [sys.executable, "-B", "-c", WORKER, str(PIPELINE_DIR), env_state,
                 f"task-w{n}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for n in range(4)
        ]
        outputs = [p.communicate(timeout=120) for p in procs]
        for proc, (_out, err) in zip(procs, outputs):
            self.assertEqual(proc.returncode, 0, err)
            self.assertNotIn("[event-skipped]", err)
        self.assertEqual(len(self.rows("SELECT id FROM events WHERE kind='gate'")), 200)
        for n in range(4):
            count = self.rows("SELECT COUNT(*) AS c FROM events WHERE task = ?",
                              (f"task-w{n}",))[0]["c"]
            self.assertEqual(count, 50)
        ids = [r["id"] for r in self.rows("SELECT id FROM events")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_four_first_writers_racing_to_create_the_schema_all_land(self):
        procs = [
            subprocess.Popen(
                [sys.executable, "-B", "-c", WORKER, str(PIPELINE_DIR), str(self.state),
                 f"task-r{n}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for n in range(4)
        ]
        errors = [p.communicate(timeout=120)[1] for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0, 0, 0], errors)
        self.assertEqual(errors, ["", "", "", ""])
        self.assertEqual(len(self.rows()), 200)


class FailOpenTest(JournalCase):
    def test_unwritable_state_path_skips_the_event_and_does_not_raise(self):
        blocker = self.state.parent / "blocker"
        blocker.write_text("a file where the state directory should be", encoding="utf-8")
        err = io.StringIO()
        with unittest.mock.patch.object(events, "STATE_DIR", blocker / "state"), \
                contextlib.redirect_stderr(err):
            ok = events.append("blocked", TASK, stage="test")
            self.assertFalse(ok)
            self.assertEqual(events.last_at(TASK), "")
            self.assertEqual(events.task_report(TASK)["events"], 0)
            self.assertEqual(events.summary()["gate_failure_rate"], {})
        self.assertIn("[event-skipped]", err.getvalue())

    def test_corrupt_journal_file_skips_the_event_and_does_not_raise(self):
        self.state.mkdir(parents=True)
        events.db_path().write_bytes(b"this is not a sqlite database" * 50)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(events.append("blocked", TASK))
            self.assertEqual(events.last_at(TASK), "")
        self.assertIn("[event-skipped]", err.getvalue())

    def test_reads_never_create_the_journal(self):
        self.assertEqual(events.last_at(TASK), "")
        events.task_report(TASK)
        events.summary()
        self.assertFalse(events.db_path().exists())


class LastAtTest(JournalCase):
    def test_returns_newest_timestamp_per_task_and_kind(self):
        self.seed([
            ("2026-10-01T10:00:00Z", TASK, "gate", "implement", "", "fail", {}),
            ("2026-10-01T10:05:00Z", TASK, "gate", "implement", "", "pass", {}),
            ("2026-10-01T10:09:00Z", TASK, "parked", "ready", "", "", {}),
            ("2026-10-01T10:30:00Z", "task-other", "gate", "test", "", "pass", {}),
        ])
        self.assertEqual(events.last_at(TASK, "gate"), "2026-10-01T10:05:00Z")
        self.assertEqual(events.last_at(TASK), "2026-10-01T10:09:00Z")
        self.assertEqual(events.last_at(TASK, "merged"), "")
        self.assertEqual(events.last_at("task-none", "gate"), "")


class TaskReportTest(JournalCase):
    def seed_cycle(self):
        self.seed([
            ("2026-10-01T10:00:00Z", TASK, "registered", "implement", "", "", {}),
            ("2026-10-01T10:04:00Z", TASK, "gate", "implement", "", "fail", {"cmd": "t", "exit": 1}),
            ("2026-10-01T10:08:00Z", TASK, "gate", "implement", "", "pass", {"cmd": "t", "exit": 0}),
            ("2026-10-01T10:10:00Z", TASK, "transition", "test", "implement", "", {}),
            ("2026-10-01T10:20:00Z", TASK, "transition", "review", "test", "", {}),
            ("2026-10-01T10:25:00Z", TASK, "rejected", "implement", "review", "",
             {"reason": "missing test"}),
            ("2026-10-01T10:25:00Z", TASK, "transition", "implement", "review", "", {}),
            ("2026-10-01T10:40:00Z", TASK, "transition", "test", "implement", "", {}),
            ("2026-10-01T10:50:00Z", TASK, "transition", "review", "test", "", {}),
            ("2026-10-01T10:55:00Z", TASK, "transition", "ready", "review", "", {}),
            ("2026-10-01T11:00:00Z", TASK, "merged", "done", "ready", "", {}),
            ("2026-10-01T10:00:00Z", "task-other", "registered", "implement", "", "", {}),
        ])

    def test_counts_stage_entries_and_attempt_durations(self):
        self.seed_cycle()
        report = events.task_report(TASK)
        implement = report["stages"]["implement"]
        self.assertEqual(implement["entries"], 2)
        self.assertEqual([a["seconds"] for a in implement["attempts"]], [600, 900])
        self.assertEqual(report["stages"]["test"]["entries"], 2)
        self.assertEqual([a["seconds"] for a in report["stages"]["test"]["attempts"]],
                         [600, 600])
        self.assertEqual(report["stages"]["review"]["entries"], 2)
        self.assertEqual(report["stages"]["ready"]["entries"], 1)
        self.assertEqual(report["stages"]["ready"]["attempts"][0]["seconds"], 300)
        self.assertFalse(report["stages"]["ready"]["attempts"][0]["open"])
        self.assertTrue(report["merged"])

    def test_lists_every_rejection_with_its_reason(self):
        self.seed_cycle()
        [rejection] = events.task_report(TASK)["rejections"]
        self.assertEqual(rejection["reason"], "missing test")
        self.assertEqual((rejection["stage"], rejection["from_stage"]),
                         ("implement", "review"))

    def test_counts_gate_runs_and_failures_per_stage(self):
        self.seed_cycle()
        self.assertEqual(events.task_report(TASK)["gates"],
                         {"implement": {"runs": 2, "failures": 1}})

    def test_unfinished_task_has_an_open_last_attempt(self):
        self.seed_cycle()
        report = events.task_report("task-other")
        self.assertTrue(report["stages"]["implement"]["attempts"][0]["open"])
        self.assertFalse(report["merged"])

    def test_unknown_task_returns_an_empty_report(self):
        self.seed_cycle()
        report = events.task_report("task-9999")
        self.assertEqual((report["events"], report["stages"], report["rejections"]),
                         (0, {}, []))


class SummaryTest(JournalCase):
    def seed_two_tasks(self):
        self.seed([
            ("2026-09-30T09:00:00Z", "task-a", "registered", "implement", "", "", {}),
            ("2026-09-30T09:10:00Z", "task-a", "gate", "implement", "", "fail", {}),
            ("2026-09-30T09:20:00Z", "task-a", "gate", "implement", "", "pass", {}),
            ("2026-09-30T10:00:00Z", "task-a", "merged", "done", "", "", {}),
            ("2026-10-02T08:00:00Z", "task-b", "registered", "implement", "", "", {}),
            ("2026-10-02T08:30:00Z", "task-b", "gate", "implement", "", "pass", {}),
            ("2026-10-02T08:40:00Z", "task-b", "gate", "test", "", "fail", {}),
            ("2026-10-02T08:50:00Z", "task-b", "gate", "test", "", "fail", {}),
            ("2026-10-02T09:00:00Z", "task-b", "gate", "test", "", "pass", {}),
            ("2026-10-02T12:00:00Z", "task-b", "merged", "done", "", "", {}),
        ])

    def test_gate_failure_rate_per_stage_and_registration_to_merge(self):
        self.seed_two_tasks()
        result = events.summary()
        self.assertEqual(result["gate_failure_rate"]["implement"],
                         {"runs": 3, "failures": 1, "rate": 0.3333})
        self.assertEqual(result["gate_failure_rate"]["test"],
                         {"runs": 3, "failures": 2, "rate": 0.6667})
        self.assertEqual(result["registration_to_merge"],
                         {"task-a": 3600, "task-b": 14400})

    def test_since_restricts_gate_rows_and_merges(self):
        self.seed_two_tasks()
        result = events.summary("2026-10-01")
        self.assertEqual(result["gate_failure_rate"]["implement"],
                         {"runs": 1, "failures": 0, "rate": 0.0})
        self.assertEqual(result["registration_to_merge"], {"task-b": 14400})

    def test_empty_journal_summary_is_empty(self):
        self.assertEqual(events.summary(), {"since": "", "gate_failure_rate": {},
                                            "registration_to_merge": {}})


class GateLogCopyTest(JournalCase):
    def make_run_db(self, name: str, rows: list) -> Path:
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / name
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE gate_log (task TEXT, stage TEXT, cmd TEXT, "
                         "exit INTEGER, at TEXT)")
            conn.executemany("INSERT INTO gate_log VALUES (?, ?, ?, ?, ?)", rows)
            conn.commit()
        finally:
            conn.close()
        return path

    def test_first_creation_copies_gate_log_rows_from_every_run_db(self):
        self.make_run_db("run.db", [
            (TASK, "implement", "cmd-a", 1, "2026-10-01T10:00:00Z"),
            (TASK, "implement", "cmd-a", 0, "2026-10-01T10:05:00Z")])
        self.make_run_db("run.side.db", [
            ("task-side", "test", "cmd-b", 0, "2026-10-01T11:00:00Z")])
        events.append("registered", TASK)
        gates = self.rows("SELECT * FROM events WHERE kind = 'gate' ORDER BY id")
        self.assertEqual(
            [(g["lane"], g["task"], g["stage"], g["outcome"], g["via"], g["at"]) for g in gates],
            [("", TASK, "implement", "fail", "migrated", "2026-10-01T10:00:00Z"),
             ("", TASK, "implement", "pass", "migrated", "2026-10-01T10:05:00Z"),
             ("side", "task-side", "test", "pass", "migrated", "2026-10-01T11:00:00Z")])
        self.assertEqual(json.loads(gates[0]["detail"]), {"cmd": "cmd-a", "exit": 1})

    def test_the_live_gate_log_table_is_left_intact_and_not_copied_twice(self):
        source = self.make_run_db("run.db", [
            (TASK, "implement", "cmd-a", 0, "2026-10-01T10:00:00Z")])
        events.append("registered", TASK)
        events.append("parked", TASK)
        self.assertEqual(len(self.rows("SELECT id FROM events WHERE kind = 'gate'")), 1)
        conn = sqlite3.connect(source)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM gate_log").fetchone()[0], 1)
        finally:
            conn.close()

    def test_run_db_without_gate_log_is_skipped_silently_and_creation_still_succeeds(self):
        self.state.mkdir(parents=True)
        conn = sqlite3.connect(self.state / "run.db")
        conn.execute("CREATE TABLE runs (task TEXT)")
        conn.commit()
        conn.close()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertTrue(events.append("registered", TASK))
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(self.rows()), 1)


class CliTest(JournalCase):
    def run_main(self, argv: list) -> tuple:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = events.main(argv)
        return code, json.loads(out.getvalue())

    def test_task_flag_prints_the_task_report(self):
        events.append("registered", TASK, stage="implement")
        code, data = self.run_main(["--task", TASK])
        self.assertEqual(code, 0)
        self.assertEqual((data["task"], data["events"]), (TASK, 1))

    def test_summary_flag_prints_aggregates(self):
        events.append("gate", TASK, stage="test", outcome="fail")
        code, data = self.run_main(["--summary", "--since", "2000-01-01"])
        self.assertEqual(code, 0)
        self.assertEqual(data["gate_failure_rate"]["test"]["failures"], 1)

    def test_malformed_since_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            events.main(["--summary", "--since", "yesterday"])
        self.assertEqual(ctx.exception.code, 2)

    def test_task_and_summary_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            events.main(["--task", TASK, "--summary"])


if __name__ == "__main__":
    unittest.main()
