"""The swarm flag, the third dial: swarm.py, mode.py --show, SessionStart, gate."""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TOOLS_DIR / "hooks"))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import approvals
import mode
import session_start
import state
import swarm
import tmproot


def run_main(module_main, *argv):
    buf = io.StringIO()
    with unittest.mock.patch.object(sys, "argv", ["x.py", *argv]), contextlib.redirect_stdout(buf):
        code = module_main()
    return code, buf.getvalue()


class SwarmDialTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "swarm_dial_")
        patcher = unittest.mock.patch.object(swarm, "SWARM_PATH", self.tmp / "swarm")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_is_off_when_no_file_exists(self):
        self.assertEqual("off", swarm.read())

    def test_set_on_persists_and_reports_the_change(self):
        code, out = run_main(swarm._main, "on")
        self.assertEqual(0, code)
        self.assertTrue(out.startswith("off -> on:"), out)
        self.assertEqual("on", swarm.read())
        self.assertEqual("on\n", (self.tmp / "swarm").read_text(encoding="utf-8"))

    def test_set_off_after_on(self):
        swarm.write("on")
        code, out = run_main(swarm._main, "--off")
        self.assertEqual(0, code)
        self.assertTrue(out.startswith("on -> off:"), out)
        self.assertEqual("off", swarm.read())

    def test_show_prints_value_and_description_and_changes_nothing(self):
        swarm.write("on")
        for args in ((), ("--show",), ("show",)):
            with self.subTest(args=args):
                code, out = run_main(swarm._main, *args)
                self.assertEqual(0, code)
                self.assertEqual(f"on: {swarm.DESCRIPTIONS['on']}\n", out)
        self.assertEqual("on", swarm.read())

    def test_an_invalid_value_is_refused_and_stores_nothing(self):
        code, out = run_main(swarm._main, "maybe")
        self.assertEqual(1, code)
        self.assertIn("unknown value 'maybe'", out)
        self.assertFalse((self.tmp / "swarm").exists())

    def test_a_garbage_file_reads_as_off(self):
        (self.tmp / "swarm").write_text("banana\n", encoding="utf-8")
        self.assertEqual("off", swarm.read())


class SwarmLaneTest(unittest.TestCase):

    def tearDown(self):
        self._reload(None)

    def _reload(self, lane):
        env = dict(os.environ)
        env.pop("PIPELINE_LANE", None)
        if lane is not None:
            env["PIPELINE_LANE"] = lane
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            st = importlib.reload(state)
            return st, importlib.reload(swarm)

    def test_unset_lane_uses_the_plain_file_beside_mode_and_approvals(self):
        st, sw = self._reload(None)
        self.assertEqual("swarm", sw.SWARM_PATH.name)
        self.assertEqual(st.STATE_DIR, sw.SWARM_PATH.parent)

    def test_a_lane_suffixes_the_swarm_file_from_the_state_accessor(self):
        st, sw = self._reload("night")
        self.assertEqual("swarm.night", sw.SWARM_PATH.name)
        self.assertTrue(sw.SWARM_PATH.name.endswith(st.LANE_SUFFIX))
        self.assertEqual(st.STATE_DIR, sw.SWARM_PATH.parent)


class ShowAllThreeDialsTest(unittest.TestCase):

    def test_mode_show_prints_mode_approvals_and_swarm(self):
        with unittest.mock.patch.object(approvals, "read", return_value="assisted"), \
                unittest.mock.patch.object(swarm, "read", return_value="on"):
            code, out = run_main(mode.main, "--show")
        lines = out.splitlines()
        self.assertEqual(0, code)
        self.assertEqual(3, len(lines), out)
        self.assertTrue(lines[0].startswith("mode      "), lines[0])
        self.assertEqual(f"approvals assisted: {approvals.DESCRIPTIONS['assisted']}", lines[1])
        self.assertEqual(f"swarm     on: {swarm.DESCRIPTIONS['on']}", lines[2])

    def test_setting_a_mode_still_prints_one_line(self):
        with unittest.mock.patch.object(mode, "write"):
            code, out = run_main(mode.main, "talk")
        self.assertEqual(0, code)
        self.assertEqual(1, len(out.splitlines()), out)


class SessionStartDialsTest(unittest.TestCase):

    def start(self, level="manual", flag="off", runs=()):
        root = tmproot.sandbox(self, "ss_dials_")
        for task in runs:
            note = root / ".agentry" / "tasks" / "swarm" / task["task"] / "resume.md"
            if task.get("note"):
                note.parent.mkdir(parents=True)
                note.write_text("done: none\n", encoding="utf-8")
        rows = [{"task": t["task"], "stage": t.get("stage", "implement")} for t in runs]
        buf = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.object(session_start, "ROOT", root))
            stack.enter_context(unittest.mock.patch.object(mode, "read", return_value=mode.default()))
            stack.enter_context(unittest.mock.patch.object(approvals, "read", return_value=level))
            stack.enter_context(unittest.mock.patch.object(swarm, "read", return_value=flag))
            stack.enter_context(unittest.mock.patch.object(state, "connect", return_value=_Conn()))
            stack.enter_context(unittest.mock.patch.object(state, "all_runs", return_value=rows))
            stack.enter_context(contextlib.redirect_stdout(buf))
            session_start.pipeline_mode()
        return buf.getvalue()

    def test_all_defaults_print_nothing(self):
        self.assertEqual("", self.start())

    def test_a_non_default_approvals_level_is_printed(self):
        out = self.start(level="auto")
        self.assertIn("pipeline approvals: auto - ", out)
        self.assertIn("/pipeline-approvals-manual", out)

    def test_swarm_on_is_printed_with_the_way_back(self):
        out = self.start(flag="on")
        self.assertIn("pipeline swarm: on - ", out)
        self.assertIn("/pipeline-swarm-off", out)

    def test_swarm_on_with_an_in_flight_umbrella_prints_the_resume_note_path(self):
        out = self.start(flag="on", runs=[{"task": "task-0150", "note": True}])
        self.assertIn(".agentry/tasks/swarm/task-0150/resume.md", out)

    def test_a_finished_run_and_a_run_without_a_note_print_no_path(self):
        out = self.start(flag="on", runs=[{"task": "task-0150", "note": True, "stage": "done"},
                                          {"task": "task-0151", "note": False}])
        self.assertNotIn("resume.md", out)

    def test_swarm_off_never_prints_a_resume_path(self):
        out = self.start(flag="off", runs=[{"task": "task-0150", "note": True}])
        self.assertNotIn("resume.md", out)


class _Conn:
    def close(self):
        pass


class AgentsCannotSetTheSwarmDialTest(unittest.TestCase):

    PIPE = "plugin/tools/pipeline"

    def test_setting_is_denied_and_reading_is_allowed(self):
        for command in (f"python {self.PIPE}/swarm.py on", f"python {self.PIPE}/swarm.py off"):
            with self.subTest(command=command):
                self.assertEqual("swarm.py", agent_gate.sets_pipeline_state(command))
        for command in (f"python {self.PIPE}/swarm.py", f"python {self.PIPE}/swarm.py --show"):
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.sets_pipeline_state(command))


if __name__ == "__main__":
    unittest.main()
