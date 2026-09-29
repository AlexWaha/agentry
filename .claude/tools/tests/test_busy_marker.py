"""task-0057: the busy marker has an owner and a lifecycle, not just a timeout.

Every check here pins one thing the old marker got wrong:

- it recorded the pid of advance.py, which exits at once, so any liveness check
  over it would have called every healthy run dead;
- nothing released it when the covered work raised, and nothing stopped one
  marker being written for two tasks;
- a stale marker looked exactly like no marker, so the hook's nag never said why
  a marker that was supposed to silence it did not;
- three modules each knew the marker's file name.
"""

from __future__ import annotations

import gc
import io
import json
import os
import re
import subprocess
import sys
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import busy
import handoff
import state
import stop_gate
import supervisor
import tmproot

BUILD_PIPELINE = {
    "retry_budget": 3,
    "pipelines": {"build": {"stages": [
        {"name": "implement", "owner": "dev"},
        {"name": "test", "owner": "qa-engineer"},
    ]}},
}


class MarkerCase(unittest.TestCase):
    """A sandboxed state dir and an environment with no session pid, so nothing
    here depends on whether the suite happens to run inside Claude Code."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "busymarker")
        patcher = unittest.mock.patch.object(state, "STATE_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = unittest.mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(busy.SESSION_PID_ENV, None)

    def session(self) -> subprocess.Popen:
        """A live process standing in for the Claude Code session."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(self._reap, proc)
        return proc

    @staticmethod
    def _reap(proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    def cli(self, *args) -> tuple[int, dict]:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", *args]), \
                redirect_stdout(buf):
            code = advance.main()
        return code, json.loads(buf.getvalue())


class SessionOwnerTest(MarkerCase):
    def test_the_marker_records_the_session_pid_not_the_writers(self):
        proc = self.session()
        os.environ[busy.SESSION_PID_ENV] = str(proc.pid)

        busy.acquire("task-0007", "implement")

        marker = busy.read("task-0007")
        self.assertEqual(proc.pid, marker.owner_pid)
        self.assertNotEqual(os.getpid(), marker.owner_pid)
        self.assertEqual(proc.pid, json.loads(busy.path("task-0007").read_text())["owner_pid"])

    def test_no_session_pid_records_no_owner_never_the_writers_own(self):
        busy.acquire("task-0007", "implement")

        marker = busy.read("task-0007")
        self.assertIsNone(marker.owner_pid)
        # Owner unknown: the timeout is the only end, as before this task.
        self.assertEqual(busy.FRESH, marker.status)

    def test_a_junk_session_pid_records_no_owner(self):
        for junk in ("abc", "", "0", "-5", "1.5"):
            with self.subTest(junk=junk):
                os.environ[busy.SESSION_PID_ENV] = junk
                busy.acquire("task-0007", "implement")
                self.assertIsNone(busy.read("task-0007").owner_pid)

    def test_advance_busy_records_the_session_and_says_so(self):
        proc = self.session()
        os.environ[busy.SESSION_PID_ENV] = str(proc.pid)

        code, out = self.cli("--task", "task-0007", "--busy", "implement")

        self.assertEqual(0, code)
        self.assertEqual("busy", out["action"])
        self.assertEqual(proc.pid, busy.read("task-0007").owner_pid)
        self.assertIn(str(proc.pid), out["message"])

    def test_advance_busy_without_a_session_says_only_the_timeout_ends_it(self):
        code, out = self.cli("--task", "task-0007", "--busy", "implement")

        self.assertEqual(0, code)
        self.assertIn("no session pid", out["message"])


class OwnerLivenessTest(MarkerCase):
    def test_a_marker_is_released_the_moment_its_session_exits(self):
        proc = self.session()
        os.environ[busy.SESSION_PID_ENV] = str(proc.pid)
        busy.acquire("task-0007", "implement")
        self.assertEqual(busy.FRESH, busy.read("task-0007").status)
        self.assertTrue(stop_gate.busy_marker_fresh("task-0007"))

        proc.kill()
        proc.wait()

        # The marker was written a moment ago with the default 900s timeout, so
        # only the dead owner can explain this answer.
        self.assertEqual(busy.OWNER_GONE, busy.read("task-0007").status)
        self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))
        self.assertFalse(stop_gate.any_busy_marker_fresh())

    def test_liveness_is_asked_of_the_supervisors_probe(self):
        os.environ[busy.SESSION_PID_ENV] = str(os.getpid())
        busy.acquire("task-0007", "implement")

        with unittest.mock.patch.object(supervisor, "pid_alive", return_value=False) as probe:
            marker = busy.read("task-0007")

        probe.assert_called_once_with(os.getpid())
        self.assertEqual(busy.OWNER_GONE, marker.status)

    def test_the_helper_never_signals_a_process(self):
        # os.kill(pid, 9) terminates on Windows. A liveness probe that can kill
        # is a second, worse answer to a question pid_alive already owns.
        source = (PIPELINE_DIR / "busy.py").read_text(encoding="utf-8")
        self.assertNotIn("os.kill", source)


class ExpiredIsNotAbsentTest(MarkerCase):
    def test_the_two_are_reported_as_different_things(self):
        self.assertEqual(busy.ABSENT, busy.read("task-0007").status)

        busy.acquire("task-0007", "implement", timeout=0)

        self.assertEqual(busy.EXPIRED, busy.read("task-0007").status)
        self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))

    def test_the_nag_names_an_expired_marker_and_stays_quiet_about_an_absent_one(self):
        self.assertEqual("", stop_gate.busy_marker_note("task-0007"))

        busy.acquire("task-0007", "implement", timeout=0)
        note = stop_gate.busy_marker_note("task-0007")

        self.assertIn("EXPIRED", note)
        self.assertIn("task-0007", note)
        self.assertIn("--idle", note)

    def test_the_note_is_byte_stable_so_the_repeat_backstop_still_counts(self):
        # A changing sentence resets the reason-repeat count on every stop (the
        # backstop reads the text), so the note must not carry an elapsed time.
        busy.acquire("task-0007", "implement", timeout=0)
        first = stop_gate.busy_marker_note("task-0007")

        with unittest.mock.patch.object(busy.time, "time", return_value=busy.time.time() + 5000):
            second = stop_gate.busy_marker_note("task-0007")

        self.assertEqual(first, second)

    def test_the_nag_names_a_marker_whose_owner_is_gone(self):
        proc = self.session()
        os.environ[busy.SESSION_PID_ENV] = str(proc.pid)
        busy.acquire("task-0007", "implement")
        proc.kill()
        proc.wait()

        note = stop_gate.busy_marker_note("task-0007")

        self.assertIn(str(proc.pid), note)
        self.assertIn("gone", note)

    def test_a_fresh_marker_gets_no_note(self):
        busy.acquire("task-0007", "implement")
        self.assertEqual("", stop_gate.busy_marker_note("task-0007"))


class ReleaseTest(MarkerCase):
    def test_hold_releases_when_the_covered_block_raises(self):
        with self.assertRaises(RuntimeError):
            with busy.hold("task-0007", "implement"):
                self.assertEqual(busy.FRESH, busy.read("task-0007").status)
                raise RuntimeError("the covered work failed")

        self.assertEqual(busy.ABSENT, busy.read("task-0007").status)
        self.assertFalse(busy.path("task-0007").exists())

    def test_hold_releases_on_a_normal_exit(self):
        with busy.hold("task-0007", "implement"):
            self.assertTrue(busy.path("task-0007").is_file())

        self.assertFalse(busy.path("task-0007").exists())

    def test_a_marker_that_cannot_be_written_does_not_fail_the_covered_work(self):
        ran = []
        with unittest.mock.patch.object(busy, "acquire", side_effect=OSError("disk full")):
            with busy.hold("task-0007", "implement"):
                ran.append(True)

        self.assertEqual([True], ran)

    def test_the_gate_stage_releases_the_marker_when_the_gate_raises(self):
        for attr, value in (("DB_PATH", self.tmp / "run.db"),):
            patcher = unittest.mock.patch.object(state, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = unittest.mock.patch.object(state, "load_pipeline", return_value=BUILD_PIPELINE)
        patcher.start()
        self.addCleanup(patcher.stop)
        conn = state.connect()
        state.create_run(conn, "task-0007", "feature", "implement")
        conn.close()
        # advance.main() dies mid-run and never closes its own connection; the
        # traceback keeps it alive, and Windows will not delete an open run.db.
        self.addCleanup(gc.collect)
        seen = []

        def gate_that_raises(*args, **kwargs):
            seen.append(busy.read("task-0007").status)
            raise RuntimeError("gate blew up")

        with unittest.mock.patch.object(advance, "run_gate", side_effect=gate_that_raises), \
                unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", "task-0007"]), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                advance.main()

        self.assertEqual([busy.FRESH], seen)
        self.assertEqual(busy.ABSENT, busy.read("task-0007").status)


class OneMarkerOneTaskTest(MarkerCase):
    def test_a_marker_for_two_tasks_is_refused_and_writes_nothing(self):
        for spelled in ("task-0009,task-0010", "task-0009 task-0010", "task-0009;task-0010",
                        "", "0009", "task-", "../task-0009"):
            with self.subTest(task=spelled):
                with self.assertRaises(ValueError):
                    busy.acquire(spelled, "implement")
        self.assertEqual([], busy.tasks())

    def test_advance_busy_refuses_two_tasks_and_exits_nonzero(self):
        code, out = self.cli("--task", "task-0009,task-0010", "--busy", "implement")

        self.assertEqual(1, code)
        self.assertEqual("error", out["action"])
        self.assertEqual([], busy.tasks())

    def test_a_hand_written_marker_naming_another_task_is_not_trusted(self):
        # The shape that caused failure mode 1: one file, standing for two tasks.
        busy.path("task-0010").write_text(json.dumps({
            "task": "task-0009", "stage": "handoff", "started": busy.time.time(),
            "timeout": 900}), encoding="utf-8")

        self.assertEqual(busy.INVALID, busy.read("task-0010").status)
        self.assertFalse(stop_gate.busy_marker_fresh("task-0010"))
        self.assertIn("unreadable", stop_gate.busy_marker_note("task-0010"))

    def test_a_second_acquire_for_the_same_task_replaces_the_first(self):
        busy.acquire("task-0007", "implement")
        busy.acquire("task-0007", "test")

        self.assertEqual(["task-0007"], busy.tasks())
        self.assertEqual("test", busy.read("task-0007").stage)


class FailOpenTest(MarkerCase):
    def test_a_garbled_marker_reads_invalid_and_does_not_silence_the_hook(self):
        for body in ("{not json", "[]", "null", '{"task": "task-0007"}',
                     '{"task": "task-0007", "started": "x", "timeout": 900}'):
            with self.subTest(body=body):
                busy.path("task-0007").write_text(body, encoding="utf-8")
                self.assertEqual(busy.INVALID, busy.read("task-0007").status)
                self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))

    def test_a_nan_timeout_or_start_reads_expired_and_does_not_silence_the_hook(self):
        for body in ('{"task": "task-0007", "started": 1.0, "timeout": NaN}',
                     '{"task": "task-0007", "started": NaN, "timeout": 900}'):
            with self.subTest(body=body):
                busy.path("task-0007").write_text(body, encoding="utf-8")
                self.assertEqual(busy.EXPIRED, busy.read("task-0007").status)
                self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))

    def test_an_old_format_marker_is_ignored_for_liveness(self):
        # Written before this task: `pid` was the writer's, already dead. It must
        # not be read as an owner, or every leftover one becomes OWNER_GONE by
        # accident of a field name.
        busy.path("task-0007").write_text(json.dumps({
            "task": "task-0007", "stage": "implement", "pid": 999999,
            "started": busy.time.time(), "timeout": 900}), encoding="utf-8")

        marker = busy.read("task-0007")

        self.assertEqual(busy.FRESH, marker.status)
        self.assertIsNone(marker.owner_pid)

    def test_a_reader_that_blows_up_reads_as_not_busy(self):
        busy.acquire("task-0007", "implement")
        with unittest.mock.patch.object(busy, "path", side_effect=PermissionError("denied")):
            self.assertEqual(busy.INVALID, busy.read("task-0007").status)
            self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))
            self.assertFalse(stop_gate.any_busy_marker_fresh())

    def test_an_unlistable_state_dir_reads_as_not_busy(self):
        with unittest.mock.patch.object(busy, "tasks", side_effect=OSError("denied")):
            self.assertFalse(stop_gate.any_busy_marker_fresh())


class EveryWriterUsesTheHelperTest(MarkerCase):
    """The marker's file name lives in busy.py and nowhere else - not in the
    reader, not in the writers, not in tests."""

    def test_no_other_source_file_knows_the_marker_file_name(self):
        # Assembled from parts so this file does not trip the scan it runs.
        prefix = "gate" + "-"
        pattern = re.compile(re.escape(prefix) + r"(\{|\*|\"|')")
        offenders = [str(p.relative_to(TOOLS_DIR)) for p in sorted(TOOLS_DIR.rglob("*.py"))
                     if p.name not in ("busy.py", Path(__file__).name)
                     and pattern.search(p.read_text(encoding="utf-8"))]

        self.assertEqual([], offenders)

    def test_scaffolding_writes_through_the_helper(self):
        for target, attr, value in (
                (handoff, "handoff_dir", lambda: self.tmp / "handoffs"),
                (handoff, "_merge_facts", lambda task: ("abc123", "a.py")),
                (handoff, "_task_title", lambda task: "A title")):
            patcher = unittest.mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        proc = self.session()
        os.environ[busy.SESSION_PID_ENV] = str(proc.pid)

        handoff.scaffold("task-0099")

        marker = busy.read("task-0099")
        self.assertEqual("handoff", marker.stage)
        self.assertEqual(proc.pid, marker.owner_pid)

    def test_dropping_scaffold_markers_reads_through_the_helper(self):
        busy.acquire("task-0099", "handoff")
        busy.acquire("task-0100", "implement")

        handoff.drop_scaffold_markers(set())

        self.assertEqual(["task-0100"], busy.tasks())


if __name__ == "__main__":
    unittest.main()
