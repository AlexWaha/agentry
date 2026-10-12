"""The pipeline status line (task-0026, FR-52): six facts plus the swarm dial,
read from run.db without writing to it, and a defined line for every bad state."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import unittest
import unittest.mock
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "hooks"))
sys.path.insert(0, str(TESTS_DIR))

import approvals
import mode
import state
import status_line
import swarm
import tmproot


class StatusLineCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "status_line_")
        self.db = self.tmp / "run.db"
        for target, name, value in (
            (state, "DB_PATH", self.db),
            (mode, "MODE_PATH", self.tmp / "mode"),
            (approvals, "APPROVALS_PATH", self.tmp / "approvals"),
            (swarm, "SWARM_PATH", self.tmp / "swarm"),
        ):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, name, value in (
            (state, "lane_name", lambda: "default"),
            (state, "lane_repo", lambda lane=None: "agentry"),
        ):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_run(self, task, stage, status="in_progress", awaiting="", **flags):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            state.init_db(conn)
            conn.execute(
                "INSERT INTO runs (task, type, stage, stage_status, awaiting_human, "
                "pipeline, updated) VALUES (?, 'feature', ?, ?, ?, 'build', 'now')",
                (task, stage, status, awaiting))
            for column, value in flags.items():
                conn.execute(f"UPDATE runs SET {column} = ? WHERE task = ?", (value, task))
            conn.commit()
        finally:
            conn.close()

    def line(self):
        return status_line.render()


class SixFieldsTest(StatusLineCase):

    def test_a_task_parked_at_the_commit_checkpoint_shows_all_six_fields_and_swarm(self):
        self.add_run("task-0900", "ready", awaiting="commit")
        approvals.write("auto")
        swarm.write("on")
        plain = self.line().replace(status_line.YELLOW, "").replace(status_line.RESET, "")
        self.assertEqual(
            "task-0900 | stage ready | owed: commit approval | lane default [agentry] "
            "| mode build | approvals auto | swarm on", plain)

    def test_the_owed_action_is_highlighted_only_when_something_is_owed(self):
        self.add_run("task-0900", "ready", awaiting="commit")
        self.assertIn(f"{status_line.YELLOW}commit approval{status_line.RESET}", self.line())
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE runs SET awaiting_human = '', stage = 'implement'")
        conn.commit()
        conn.close()
        line = self.line()
        self.assertIn("owed: none", line)
        self.assertNotIn(status_line.YELLOW, line)

    def test_an_approved_commit_is_no_longer_owed(self):
        self.add_run("task-0900", "ready", awaiting="commit", commit_approved=1)
        self.assertIn("owed: none", self.line())

    def test_push_plan_and_merge_waits_get_their_own_wording(self):
        for awaiting, words in (("push", "push approval"), ("plan", "plan approval")):
            with self.subTest(awaiting=awaiting):
                self.db.unlink(missing_ok=True)
                self.add_run("task-0900", "ready", awaiting=awaiting)
                self.assertIn(words, self.line())

    def test_a_blocked_run_owes_an_unblock_and_a_paused_run_owes_nothing(self):
        self.add_run("task-0900", "review", status="blocked", awaiting="blocked")
        self.assertIn("stage review (blocked)", self.line())
        self.assertIn("owed: " + status_line.YELLOW + "unblock", self.line())
        self.db.unlink()
        self.add_run("task-0901", "implement", status="paused")
        line = self.line()
        self.assertIn("stage implement (paused)", line)
        self.assertIn("owed: none", line)

    def test_an_unknown_park_reason_is_shown_as_stored(self):
        self.add_run("task-0900", "ready", awaiting="something-new")
        self.assertIn("something-new", self.line())


class FocalRunTest(StatusLineCase):

    def test_a_run_parked_on_the_ceo_is_shown_before_a_run_being_driven(self):
        self.add_run("task-0100", "implement")
        self.add_run("task-0200", "ready", awaiting="commit")
        line = self.line()
        self.assertTrue(line.startswith("task-0200 +1 | stage ready"), line)

    def test_a_blocked_run_needs_the_ceo_so_it_comes_before_a_run_being_driven(self):
        self.add_run("task-0100", "implement")
        self.add_run("task-0200", "review", status="blocked", awaiting="blocked")
        self.assertTrue(self.line().startswith("task-0200 +1 | stage review (blocked)"))

    def test_a_run_being_driven_is_shown_before_a_paused_one(self):
        self.add_run("task-0100", "review", status="paused")
        self.add_run("task-0200", "implement")
        self.assertTrue(self.line().startswith("task-0200 +1 | stage implement"))

    def test_done_runs_are_not_in_flight(self):
        self.add_run("task-0100", "done")
        self.add_run("task-0200", "test")
        self.assertTrue(self.line().startswith("task-0200 | stage test"))


class EmptyAndDegradedTest(StatusLineCase):

    def test_no_store_is_the_defined_empty_state_and_the_dials_still_show(self):
        self.assertEqual(
            "no run in flight | lane default [agentry] | mode build | approvals manual "
            "| swarm off", self.line())

    def test_only_finished_runs_is_the_same_empty_state(self):
        self.add_run("task-0100", "done")
        self.assertTrue(self.line().startswith("no run in flight | lane"))

    def test_an_unreadable_store_renders_a_degraded_line_not_an_error(self):
        self.db.write_bytes(b"this is not a sqlite database" * 50)
        line = self.line()
        self.assertTrue(line.startswith("run.db unreadable | lane"), line)

    def test_a_store_without_the_runs_table_is_degraded_too(self):
        sqlite3.connect(self.db).close()
        self.assertTrue(self.line().startswith("run.db unreadable | lane"))

    def test_a_failing_dial_shows_a_question_mark_and_the_rest_survive(self):
        with unittest.mock.patch.object(swarm, "read", side_effect=OSError("boom")):
            line = self.line()
        self.assertIn("swarm ?", line)
        self.assertIn("mode build", line)

    def test_a_crash_inside_render_still_prints_one_line_and_exits_zero(self):
        out = io.StringIO()
        with unittest.mock.patch.object(status_line, "render", side_effect=RuntimeError("x")), \
                unittest.mock.patch.object(sys, "stdin", io.StringIO("{}")), \
                contextlib.redirect_stdout(out):
            code = status_line.main()
        self.assertEqual(0, code)
        self.assertEqual("pipeline status unavailable\n", out.getvalue())


class LaneTest(StatusLineCase):

    def test_the_default_lane_renders_as_default_with_its_repo(self):
        self.assertIn("lane default [agentry]", self.line())

    def test_a_named_lane_renders_its_name_and_repo(self):
        with unittest.mock.patch.object(state, "lane_name", lambda: "web"), \
                unittest.mock.patch.object(state, "lane_repo", lambda lane=None: "frontend"):
            self.assertIn("lane web [frontend]", self.line())

    def test_a_lane_with_no_repo_entry_renders_the_name_alone(self):
        with unittest.mock.patch.object(state, "lane_repo", lambda lane=None: None):
            line = self.line()
        self.assertIn("lane default | mode", line)


class ReadOnlyTest(StatusLineCase):

    def test_rendering_never_creates_the_store(self):
        self.line()
        self.assertFalse(self.db.exists())

    def test_rendering_leaves_the_store_byte_identical(self):
        self.add_run("task-0900", "ready", awaiting="commit")
        before = self.db.read_bytes()
        self.line()
        self.assertEqual(before, self.db.read_bytes())

    def test_the_store_is_opened_with_mode_ro(self):
        self.add_run("task-0900", "ready")
        real = sqlite3.connect
        seen = []

        def spy(target, *args, **kwargs):
            seen.append(target)
            return real(target, *args, **kwargs)

        with unittest.mock.patch.object(sqlite3, "connect", spy):
            self.line()
        self.assertTrue(seen and all(t.endswith("?mode=ro") for t in seen), seen)

    def test_a_pipeline_script_holding_a_write_lock_does_not_stop_the_line(self):
        self.add_run("task-0900", "ready", awaiting="commit")
        writer = sqlite3.connect(self.db, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE runs SET retries = 5")
        self.assertIn("task-0900 | stage ready", self.line())
        writer.execute("ROLLBACK")


class RegistrationTest(unittest.TestCase):

    def test_settings_register_the_status_line_and_keep_the_hook_matchers(self):
        settings = json.loads((TOOLS_DIR.parent / "settings.json").read_text(encoding="utf-8"))
        entry = settings["statusLine"]
        self.assertEqual("command", entry["type"])
        self.assertIn("tools/hooks/status_line.py", entry["command"])
        self.assertTrue((TOOLS_DIR / "hooks" / "status_line.py").is_file())
        matchers = [g.get("matcher") for g in settings["hooks"]["PreToolUse"]]
        self.assertIn("Bash|Edit|Write|MultiEdit|NotebookEdit", matchers)


if __name__ == "__main__":
    unittest.main()
