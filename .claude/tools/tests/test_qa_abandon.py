"""QA coverage for task-0122 (`approve.py --abandon`), on top of test_approve_abandon.

Probes the readers and neighbours of an abandoned run that the dev tests do not
reach: the Stop hook (owed list, dependency check, drift reconciler), the debt
readers with a swarm umbrella, odd run shapes (blocked, plan flow, file already in
done/ or missing), neighbour isolation, and the events report. Everything runs on
the sandbox of the base case; the live folders and run store are never touched.
"""

# ruff: noqa: E402, F401  (sys.path is extended before the sibling imports resolve; tmproot is imported only to arm the in-project temp redirect)

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "memory"))
sys.path.insert(0, str(TESTS_DIR.parent / "pipeline"))
sys.path.insert(0, str(TESTS_DIR))

import advance
import events
import git_state
import handoff
import state
import stop_gate
import tmproot
import update
from test_approve_abandon import AbandonCase as _Base

TASK = "task-9500"
OTHER = "task-9501"
UMBRELLA = "task-9510"
SUB = "task-9511"
EPIC = "epic-0042"
REASON = "CEO dropped the work"


class OddRunShapesTest(_Base):

    def test_a_blocked_run_parked_for_the_ceo_is_abandoned_and_released(self):
        self.put("active")
        self.register(stage="test", stage_status=state.ST_BLOCKED, awaiting_human="blocked")
        code, out = self.abandon()
        self.assertEqual(0, code, out)
        row = self.row()
        self.assertEqual(("done", state.ST_ABANDONED, ""),
                         (row["stage"], row["stage_status"], row["awaiting_human"]))

    def test_a_plan_flow_run_is_abandoned_like_a_build_flow_run(self):
        self.put("backlog")
        conn = state.connect()
        try:
            state.create_run(conn, TASK, "feature", "draft", "plan")
        finally:
            conn.close()
        code, out = self.abandon()
        self.assertEqual(0, code, out)
        row = self.row()
        self.assertEqual(("plan", "done", state.ST_ABANDONED),
                         (row["pipeline"], row["stage"], row["stage_status"]))

    def test_a_run_whose_file_already_sits_in_done_is_abandoned_without_a_move(self):
        self.put("done")
        self.register()
        code, out = self.abandon()
        self.assertEqual(0, code, out)
        self.assertEqual(state.ST_ABANDONED, self.row()["stage_status"])
        self.assertEqual("done", state.task_dir(TASK))

    def test_a_run_with_no_task_file_at_all_is_abandoned(self):
        self.register()
        code, out = self.abandon()
        self.assertEqual(0, code, out)
        self.assertEqual(state.ST_ABANDONED, self.row()["stage_status"])
        self.assertIsNone(state.task_dir(TASK))

    def test_a_neighbouring_run_in_flight_is_left_exactly_as_it_was(self):
        self.put("active")
        self.put("active", OTHER)
        self.register()
        self.register(OTHER, stage="review", awaiting_human="commit")
        before = self.row(OTHER)
        self.abandon()
        self.assertEqual(before, self.row(OTHER))
        self.assertEqual("active", state.task_dir(OTHER))

    def test_is_abandoned_is_false_for_a_task_with_no_run_row(self):
        self.assertFalse(state.is_abandoned("task-9999"))

    def test_is_abandoned_is_false_for_a_merged_done_run(self):
        self.put("done")
        self.register(stage="done", commit_approved=1)
        self.assertFalse(state.is_abandoned(TASK))


class AdvanceGuardIsNarrowTest(_Base):

    def test_a_done_run_that_was_not_abandoned_still_goes_through_the_merge_check(self):
        self.put("done")
        self.register(stage="done", commit_approved=1)
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", TASK]), \
                unittest.mock.patch.object(advance, "finish_or_wait_for_merge",
                                           return_value=0) as merge, \
                contextlib.redirect_stdout(out):
            code = advance.main()
        self.assertEqual(0, code)
        merge.assert_called_once()

    def test_advance_never_registers_a_second_run_for_an_abandoned_task(self):
        self.put("active")
        self.register()
        self.abandon()
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv",
                                        ["advance.py", "--task", TASK, "--type", "feature"]), \
                unittest.mock.patch.object(advance, "finish_or_wait_for_merge",
                                           side_effect=AssertionError("merge check")), \
                contextlib.redirect_stdout(out):
            code = advance.main()
        self.assertEqual(0, code)
        report = json.loads(out.getvalue())
        self.assertEqual(("done", state.ST_ABANDONED), (report["action"], report["stage_status"]))
        self.assertEqual(state.ST_ABANDONED, self.row()["stage_status"])


class StopHookReadersTest(_Base):

    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.object(stop_gate, "DONE_DIR", self.dirs["done"])
        patcher.start()
        self.addCleanup(patcher.stop)
        active = unittest.mock.patch.object(stop_gate, "ACTIVE_DIR", self.dirs["active"])
        active.start()
        self.addCleanup(active.stop)

    def test_the_ceo_is_owed_nothing_for_an_abandoned_run(self):
        self.put("active")
        self.register(stage="ready", awaiting_human="commit")
        self.abandon()
        self.assertEqual([], stop_gate.owed_to_ceo([self.row()]))

    def test_a_dependent_task_sees_an_abandoned_dependency_as_met(self):
        self.put("active")
        self.register()
        self.abandon()
        runs = {TASK: self.row()}
        self.assertTrue(stop_gate.dep_satisfied(TASK, runs))

    def test_a_dependency_with_no_run_and_no_done_file_is_still_unmet(self):
        self.assertFalse(stop_gate.dep_satisfied("task-9999", {}))

    def test_the_drift_reconciler_leaves_an_abandoned_run_alone_when_main_names_the_task(self):
        self.put("active")
        self.register()
        self.abandon()
        before = self.row()
        conn = state.connect()
        try:
            with unittest.mock.patch.object(git_state, "task_tagged_in_main", return_value=True):
                moved, closed = stop_gate.reconcile_status_drift(conn)
        finally:
            conn.close()
        self.assertEqual(([], []), (moved, closed))
        self.assertEqual(before, self.row())


class UmbrellaDebtTest(_Base):

    def make(self, abandoned: bool) -> None:
        self.put("done", UMBRELLA, f"epic: {EPIC}\numbrella: true\nsubtasks: [{SUB}]")
        self.put("done", SUB, f"epic: {EPIC}")
        self.register(UMBRELLA, stage="done", commit_approved=1)
        if abandoned:
            conn = state.connect()
            try:
                state.set_fields(conn, UMBRELLA, stage_status=state.ST_ABANDONED)
            finally:
                conn.close()

    def handoff_debt(self) -> list:
        return [d["task"] for d in handoff.uncovered_done_tasks()]

    def memory_debt(self) -> list:
        return [d["task"] for d in update.unstamped_done_tasks()]

    def test_a_merged_umbrella_owes_its_own_doc_and_stamp_and_covers_the_subtask(self):
        self.make(abandoned=False)
        self.assertEqual([UMBRELLA], self.handoff_debt())
        self.assertEqual([UMBRELLA], self.memory_debt())

    def test_an_abandoned_umbrella_owes_nothing_but_leaves_the_subtask_uncovered(self):
        self.make(abandoned=True)
        self.assertEqual([SUB], self.handoff_debt())
        self.assertEqual([SUB], self.memory_debt())


class EventsReportTest(_Base):

    def test_the_abandoned_row_is_listed_with_its_reason_in_the_journal(self):
        self.put("active")
        self.register()
        self.abandon()
        rows = self.journal("abandoned")
        self.assertEqual([REASON], [r["detail"]["reason"] for r in rows])

    def test_the_events_report_does_not_leave_the_last_stage_attempt_open(self):
        self.put("active")
        self.register()
        self.abandon()
        report = events.task_report(TASK)
        self.assertEqual([False], [a["open"] for a in report["stages"]["implement"]["attempts"]])


if __name__ == "__main__":
    unittest.main()
