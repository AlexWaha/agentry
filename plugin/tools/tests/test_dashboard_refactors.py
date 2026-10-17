"""The four write-free harness readers the dashboard relies on (spec-0002 FR-4 to FR-7, NFR-5, NFR-6).

events._open_existing() opens the journal read-only, state.is_abandoned / covering_umbrella take an
optional connection, handoff.owed() is the pure half of uncovered_done_tasks(), and
update.unstamped_done_tasks() passes a connection through. With conn=None every one of them must
behave exactly as before; the full existing suite is the other half of that proof.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import hashlib
import io
import sqlite3
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _dir in (TOOLS_DIR / "memory", TOOLS_DIR / "pipeline", TOOLS_DIR / "dashboard", TESTS_DIR):
    sys.path.insert(0, str(_dir))

import busy
import events
import handoff
import state
import stores
import tmproot
import update

EPIC = "epic-0042"
UMBRELLA = "task-9300"
SUBTASK = "task-9301"
WAIVER = "---\ntask: {task}\nwaived: true\nreason: swept with its umbrella\n---\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class JournalCase(unittest.TestCase):

    def setUp(self):
        self.state_dir = tmproot.sandbox(self, "dash_events_") / "state"
        patcher = unittest.mock.patch.object(events, "STATE_DIR", self.state_dir)
        patcher.start()
        self.addCleanup(patcher.stop)


class EventsOpenExistingTest(JournalCase):

    def test_the_connection_cannot_write(self):
        events.append("gate", "task-0001", stage="test", outcome="pass")
        conn = events._open_existing()
        self.addCleanup(conn.close)
        for statement in ("INSERT INTO events (task, kind) VALUES ('x', 'gate')",
                          "UPDATE events SET stage = 'z'", "DELETE FROM events", "DROP TABLE events"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.OperationalError):
                conn.execute(statement)
        self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def test_rows_come_back_by_column_name(self):
        events.append("gate", "task-0001", stage="test", outcome="pass")
        conn = events._open_existing()
        self.addCleanup(conn.close)
        self.assertEqual("test", conn.execute("SELECT stage FROM events").fetchone()["stage"])

    def test_no_file_means_no_journal_and_nothing_is_created(self):
        self.assertIsNone(events._open_existing())
        self.assertFalse(events.db_path().exists())
        self.assertFalse(self.state_dir.exists())

    def test_a_file_without_the_table_means_no_journal(self):
        self.state_dir.mkdir(parents=True)
        other = sqlite3.connect(events.db_path())
        other.execute("CREATE TABLE unrelated (a)")
        other.commit()
        other.close()
        self.assertIsNone(events._open_existing())

    def test_a_corrupt_file_means_no_journal(self):
        self.state_dir.mkdir(parents=True)
        events.db_path().write_bytes(b"not a database" * 50)
        self.assertIsNone(events._open_existing())

    def test_a_corrupt_journal_is_an_error_for_the_cli_and_empty_for_the_fail_open_readers(self):
        # The CLI path stays strict (one error line, exit 2); last_at and the dashboard stay fail-open.
        self.state_dir.mkdir(parents=True)
        events.db_path().write_bytes(b"not a database" * 50)
        with self.assertRaises(sqlite3.Error):
            events.task_report("task-0001")
        with self.assertRaises(sqlite3.Error):
            events.summary()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = events.main(["--summary"])
        self.assertEqual((2, ""), (code, out.getvalue()))
        self.assertIn("events.py: cannot read", err.getvalue())
        self.assertEqual("", events.last_at("task-0001"))
        self.assertIsNone(events._open_existing())

    def test_appending_still_writes_and_reads_still_work(self):
        self.assertTrue(events.append("registered", "task-0001", stage="plan"))
        self.assertTrue(events.append("gate", "task-0001", stage="test", outcome="fail"))
        report = events.task_report("task-0001")
        self.assertEqual(2, report["events"])
        self.assertEqual({"test": {"runs": 1, "failures": 1}}, report["gates"])
        self.assertNotEqual("", events.last_at("task-0001", "gate"))

    def test_an_error_while_probing_reads_as_no_journal(self):
        # NFR-6: the empty value, never an exception, and the connection is not leaked.
        events.append("gate", "task-0001", stage="test", outcome="pass")
        with unittest.mock.patch.object(events, "_has_table", side_effect=RuntimeError("boom")):
            self.assertIsNone(events._open_existing())
        with unittest.mock.patch.object(events.sqlite3, "connect",
                                        side_effect=sqlite3.OperationalError("locked")):
            self.assertIsNone(events._open_existing())
        conn = events._open_existing()
        self.addCleanup(conn.close)
        self.assertIsNotNone(conn)

    def test_the_file_is_untouched_by_a_read_open(self):
        events.append("gate", "task-0001", stage="test", outcome="pass")
        before = sha256(events.db_path())
        conn = events._open_existing()
        conn.execute("SELECT * FROM events").fetchall()
        conn.close()
        self.assertEqual(before, sha256(events.db_path()))


class DebtCase(unittest.TestCase):
    """A sandbox project whose state module paths are patched in, as test_umbrella_debt does."""

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_refactor_").resolve()
        tasks = self.root / ".agentry" / "tasks"
        self.dirs = {name: tasks / name for name in ("backlog", "active", "done")}
        self.handoffs = tasks / "handoffs"
        self.stamps = self.root / ".agentry" / "state" / "memory"
        for folder in (*self.dirs.values(), self.handoffs, self.stamps):
            folder.mkdir(parents=True)
        for target, name, value in (
                (state, "ROOT", self.root), (state, "TASK_DIRS", self.dirs),
                (state, "STATE_DIR", self.root / ".agentry" / "state"),
                (state, "DB_PATH", self.root / ".agentry" / "state" / "run.db"),
                (update, "DONE_DIR", self.dirs["done"]), (update, "STAMP_DIR", self.stamps)):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config = lambda: {"enabled": True, "baseline": ""}  # noqa: E731
        for module in (handoff, update):
            patcher = unittest.mock.patch.object(module, "cfg", side_effect=config)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- fixtures ----------------------------------------------------------------

    def put(self, task: str, head: str = "", folder: str = "done") -> None:
        (self.dirs[folder] / f"{task}.md").write_text(f"---\nid: 1\n{head}\n---\nbody\n", encoding="utf-8")

    def document(self, task: str, valid: bool = True) -> None:
        text = WAIVER.format(task=task) if valid else f"---\ntask: {task}\n---\nthin\n"
        (self.handoffs / f"{task}.md").write_text(text, encoding="utf-8")

    def stamp(self, task: str) -> None:
        (self.stamps / f"{task}.json").write_text("{}", encoding="utf-8")

    def register(self, task: str, stage: str = "done", **fields) -> None:
        conn = state.connect()
        try:
            state.create_run(conn, task, "feature", stage)
            if fields:
                state.set_fields(conn, task, **fields)
        finally:
            conn.close()

    def scenario(self) -> None:
        """Covered, uncovered, umbrella-covered and abandoned done tasks."""
        self.put("task-9001")                                  # documented and stamped
        self.document("task-9001")
        self.stamp("task-9001")
        self.put("task-9002")                                  # owes both
        self.put("task-9005")                                  # a doc that fails validation
        self.document("task-9005", valid=False)
        self.put("task-9004")                                  # dropped by the User
        self.register("task-9004", stage_status=state.ST_ABANDONED)
        self.put(UMBRELLA, f"epic: {EPIC}\numbrella: true\nsubtasks: [{SUBTASK}]")
        self.register(UMBRELLA, commit_approved=1)             # a merged umbrella owes its own doc
        self.put(SUBTASK, f"epic: {EPIC}")                     # covered by that umbrella

    def readonly(self):
        conn = stores.ro_connect(state.DB_PATH)
        self.addCleanup(conn.close)
        return conn


class StateReadersTest(DebtCase):

    def test_both_readers_answer_the_same_with_and_without_a_connection(self):
        self.scenario()
        conn = self.readonly()
        for task in ("task-9001", "task-9002", "task-9004", UMBRELLA, SUBTASK, "task-0000"):
            with self.subTest(task=task):
                self.assertEqual(state.is_abandoned(task), state.is_abandoned(task, conn))
                self.assertEqual(state.covering_umbrella(task), state.covering_umbrella(task, conn))
        self.assertTrue(state.is_abandoned("task-9004", conn))
        self.assertEqual(UMBRELLA, state.covering_umbrella(SUBTASK, conn))
        self.assertIsNone(state.covering_umbrella("task-9002", conn))

    def test_given_a_connection_neither_opens_the_store_nor_changes_it(self):
        self.scenario()
        conn = self.readonly()
        before = sha256(state.DB_PATH)
        with unittest.mock.patch.object(state, "connect", side_effect=AssertionError("connect")):
            self.assertTrue(state.is_abandoned("task-9004", conn))
            self.assertEqual(UMBRELLA, state.covering_umbrella(SUBTASK, conn))
            self.assertFalse(state.is_abandoned("task-9001", conn))
        self.assertEqual(before, sha256(state.DB_PATH))

    def test_the_connection_stays_open_for_the_caller(self):
        self.scenario()
        conn = self.readonly()
        state.covering_umbrella(SUBTASK, conn)
        state.is_abandoned("task-9004", conn)
        self.assertEqual(1, conn.execute("SELECT 1").fetchone()[0])

    def test_without_a_connection_the_store_is_opened_as_before(self):
        with unittest.mock.patch.object(state, "connect", wraps=state.connect) as spy:
            self.assertFalse(state.is_abandoned("task-0000"))
            self.assertIsNone(state.covering_umbrella("task-0000"))
        self.assertGreaterEqual(spy.call_count, 1)

    def test_a_broken_connection_answers_the_safe_direction(self):
        class Broken:
            def execute(self, *_):
                raise sqlite3.OperationalError("locked")

        self.put(SUBTASK, f"epic: {EPIC}")
        self.assertFalse(state.is_abandoned("task-9004", Broken()))
        self.assertIsNone(state.covering_umbrella(SUBTASK, Broken()))


def legacy_uncovered_done_tasks() -> list:
    """uncovered_done_tasks() exactly as it read before the refactor, as the reference."""
    try:
        if not handoff.enabled():
            return []
        done_dir = state.ROOT / ".agentry" / "tasks" / "done"
        if not done_dir.is_dir():
            return []
        out = []
        for f in sorted(done_dir.glob("task-*.md")):
            task = f.stem
            if not handoff.covered_required(task):
                continue
            path = handoff.handoff_path(task)
            if not path.is_file():
                reason = f"no handoff doc at {handoff.rel(path)}"
            else:
                problems = handoff.validate_handoff(path, task)
                if not problems:
                    continue
                reason = "; ".join(problems)
            umbrella = state.covering_umbrella(task)
            if umbrella and handoff.covered_required(umbrella):
                continue
            if state.is_abandoned(task):
                continue
            out.append({"task": task, "reason": reason})
        handoff.drop_scaffold_markers({d["task"] for d in out})
        return out
    except Exception:
        return []


class HandoffOwedTest(DebtCase):

    def test_owed_equals_the_legacy_result_on_every_kind_of_done_task(self):
        self.scenario()
        expected = legacy_uncovered_done_tasks()
        self.assertEqual(["task-9002", "task-9005", UMBRELLA], [d["task"] for d in expected])
        self.assertEqual(expected, handoff.owed())
        self.assertEqual(expected, handoff.uncovered_done_tasks())

    def test_a_connection_gives_the_same_list_and_never_opens_the_store(self):
        self.scenario()
        conn = self.readonly()
        expected = legacy_uncovered_done_tasks()
        before = sha256(state.DB_PATH)
        with unittest.mock.patch.object(state, "connect", side_effect=AssertionError("connect")):
            self.assertEqual(expected, handoff.owed(conn))
        self.assertEqual(before, sha256(state.DB_PATH))

    def test_owed_keeps_the_busy_marker_and_uncovered_done_tasks_releases_it(self):
        self.scenario()
        busy.acquire("task-9001", "handoff")  # its doc validates, so the marker's reason is over
        self.assertTrue(busy.path("task-9001").is_file())
        handoff.owed()
        self.assertTrue(busy.path("task-9001").is_file())
        handoff.uncovered_done_tasks()
        self.assertFalse(busy.path("task-9001").is_file())

    def test_a_marker_of_a_task_still_owed_survives_both(self):
        self.scenario()
        busy.acquire("task-9002", "handoff")
        handoff.owed()
        handoff.uncovered_done_tasks()
        self.assertTrue(busy.path("task-9002").is_file())

    def test_an_internal_error_fails_open_to_an_empty_list(self):
        self.scenario()
        with unittest.mock.patch.object(handoff, "enabled", side_effect=RuntimeError("boom")):
            self.assertEqual([], handoff.owed())
            self.assertEqual([], handoff.uncovered_done_tasks())
        with unittest.mock.patch.object(state, "covering_umbrella", side_effect=RuntimeError("boom")):
            self.assertEqual([], handoff.owed())

    def test_a_disabled_gate_owes_nothing(self):
        self.scenario()
        with unittest.mock.patch.object(handoff, "enabled", return_value=False):
            self.assertEqual([], handoff.owed())
            self.assertEqual([], handoff.uncovered_done_tasks())

    def test_uncovered_done_tasks_is_the_scan_followed_by_the_marker_release(self):
        self.scenario()
        listed = [{"task": "task-9002", "reason": "x"}]
        with unittest.mock.patch.object(handoff, "_scan", return_value=listed) as scan, \
                unittest.mock.patch.object(handoff, "drop_scaffold_markers") as drop:
            self.assertEqual(listed, handoff.uncovered_done_tasks())
        scan.assert_called_once_with()
        drop.assert_called_once_with({"task-9002"})

    def test_nothing_to_scan_or_a_failed_scan_releases_no_marker(self):
        self.scenario()
        for effect in ({"return_value": None}, {"side_effect": RuntimeError("boom")}):
            with self.subTest(effect=effect), \
                    unittest.mock.patch.object(handoff, "_scan", **effect), \
                    unittest.mock.patch.object(handoff, "drop_scaffold_markers") as drop:
                self.assertEqual([], handoff.uncovered_done_tasks())
            drop.assert_not_called()


class UnstampedTest(DebtCase):

    def test_unstamped_equals_with_and_without_a_connection(self):
        self.scenario()
        conn = self.readonly()
        plain = update.unstamped_done_tasks()
        self.assertEqual(["task-9002", "task-9005", UMBRELLA], [d["task"] for d in plain])
        before = sha256(state.DB_PATH)
        with unittest.mock.patch.object(state, "connect", side_effect=AssertionError("connect")):
            self.assertEqual(plain, update.unstamped_done_tasks(conn))
        self.assertEqual(before, sha256(state.DB_PATH))

    def test_an_internal_error_fails_open_to_an_empty_list(self):
        self.scenario()
        with unittest.mock.patch.object(update, "enabled", side_effect=RuntimeError("boom")):
            self.assertEqual([], update.unstamped_done_tasks())
        with unittest.mock.patch.object(update, "stamp_path", side_effect=RuntimeError("boom")):
            self.assertEqual([], update.unstamped_done_tasks(self.readonly()))

    def test_a_stamped_task_is_not_owed(self):
        self.scenario()
        self.stamp("task-9002")
        self.assertNotIn("task-9002", [d["task"] for d in update.unstamped_done_tasks()])


if __name__ == "__main__":
    unittest.main()
