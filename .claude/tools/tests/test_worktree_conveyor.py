"""task-0131 (epic-0004 S3): the conveyor drives several tasks, one per worktree.

Covers the zone `conveyor` files against the S1 contract frozen on the board:
`worktree.enabled/max_parallel/slots_used/live/status/dispatch_line/remove` and
`git_state.work_trees_for_task`. Everything the core zone owns is patched, so the
tests state what the conveyor does GIVEN the contract and stay valid when the
real module lands. Serial behaviour (flag off, or max_parallel 1) is pinned by
comparing against the existing stop-hook texts, which these tests do not edit.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from contextlib import contextmanager
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import approve
import diff_checkpoint
import git_state
import state
import stop_gate
import supervisor
import tmproot
import worktree
from test_conveyor_gaps import (
    BUILD_PIPELINE,
    PLAN_PIPELINE,
    PLAN_PIPELINE_UNSET,
    decide_output_with,
    plan_run,
    run,
)
from test_worktree_core import OTHER, TASK, WorkspaceCase

TREE = "src-wt/{}"
LINE = "Task worktree: E:/Personal/Agenty/src-wt/{task} (branch feature/{task}). Begin it."


def tree_run(task="task-0001", **kw) -> dict:
    """A run that lives in its own worktree."""
    return run(task=task, worktree=TREE.format(task), **kw)


@contextmanager
def contract(enabled=True, n=2, used=0, live=(), lines=None, live_error=False):
    """The core-zone functions the conveyor consumes, patched to a given state."""
    table = lines or {}
    with unittest.mock.patch.object(worktree, "enabled", return_value=enabled), \
            unittest.mock.patch.object(worktree, "max_parallel", return_value=n), \
            unittest.mock.patch.object(worktree, "slots_used", return_value=used,
                                       side_effect=None if used is not None else RuntimeError), \
            unittest.mock.patch.object(worktree, "live", return_value=list(live),
                                       side_effect=RuntimeError("doubt") if live_error else None), \
            unittest.mock.patch.object(worktree, "dispatch_line",
                                       side_effect=lambda t: table.get(t, "")):
        yield


def decide(runs=(), **kw):
    """stop_gate.decide() over synthetic runs. busy.tasks() is pinned empty: the
    real state dir may hold a marker for any task id, and the parallel hook reads
    it through doc_busy_marker_fresh()."""
    kw.setdefault("pipeline", BUILD_PIPELINE)
    with unittest.mock.patch.object(stop_gate.busy, "tasks", return_value=[]):
        return decide_output_with(list(runs), **kw)


def reason_of(out):
    return out["reason"] if out and out.get("decision") == "block" else None


START = "Start the next ready task task-0002"
PARKED = {"stage": "ready", "awaiting_human": "commit"}
UMBRELLA = {"id": "task-0050", "deps": [], "epic": "epic-0007", "umbrella": True}
PAIR = [{"id": "task-0028", "deps": [], "epic": "epic-0007"},
        {"id": "task-0029", "deps": [], "epic": "epic-0007"}]


class SlotCountTest(unittest.TestCase):
    """F5, F11: a free slot starts the next task, a parked run no longer blocks."""

    def test_a_parked_tree_run_does_not_block_the_queue_while_a_slot_is_free(self):
        parked = tree_run(stage="ready", awaiting_human="commit")
        with contract(n=2, used=1):
            self.assertIn(START, reason_of(decide([parked])))

    def test_a_full_slot_count_starts_nothing(self):
        parked = tree_run(stage="ready", awaiting_human="commit")
        with contract(n=2, used=2):
            self.assertIsNone(reason_of(decide([parked])))

    def test_each_parked_kind_frees_the_queue_in_parallel_mode(self):
        for label, row in (
                ("commit", tree_run(stage="ready", awaiting_human="commit")),
                ("push", tree_run(stage="ready", awaiting_human="push")),
                ("blocked", tree_run(stage="implement", status=state.ST_BLOCKED,
                                     awaiting_human="blocked"))):
            with self.subTest(parked=label), contract(n=2, used=1):
                self.assertIn(START, reason_of(decide([row])))

    def test_a_paused_tree_run_holds_nothing(self):
        paused = tree_run(stage="implement", status=state.ST_PAUSED)
        with contract(n=2, used=0):
            self.assertIn(START, reason_of(decide([paused])))

    def test_an_in_flight_tree_run_is_still_driven_before_anything_starts(self):
        with contract(n=2, used=1):
            reason = reason_of(decide([tree_run(stage="implement")]))
        self.assertIn("task-0001 is at stage 'implement'", reason)
        self.assertNotIn("Start the next ready task", reason)

    def test_a_busy_tree_run_is_skipped_and_the_next_task_starts(self):
        with contract(n=2, used=1):
            self.assertIn(START, reason_of(decide([tree_run(stage="implement")], busy=True)))

    def test_a_free_slot_with_an_empty_queue_starts_nothing(self):
        with contract(n=2, used=0):
            self.assertIsNone(reason_of(decide([tree_run(stage="ready", awaiting_human="commit")],
                                     backlog=())))

    def test_an_unreadable_slot_count_fails_closed(self):
        with contract(n=2), \
                unittest.mock.patch.object(worktree, "slots_used", side_effect=RuntimeError):
            self.assertIsNone(reason_of(decide([tree_run(stage="ready", awaiting_human="commit")])))


class ExclusiveRunTest(unittest.TestCase):
    """What still holds `src/` stops the queue even with free slots."""

    def test_a_legacy_run_holding_src_stops_the_queue(self):
        for label, row in (
                ("parked", run(stage="ready", awaiting_human="commit")),
                ("editing", run(stage="implement")),
                ("blocked", run(stage="implement", status=state.ST_BLOCKED,
                                awaiting_human="blocked")),
                ("merge wait", run(stage="done", awaiting_human="merge"))):
            with self.subTest(legacy=label), contract(n=3, used=0):
                self.assertIsNone(reason_of(decide([row], busy=True)))

    def test_an_umbrella_run_without_a_tree_stops_the_queue(self):
        umbrella = run(task="task-0100", stage="implement")
        with contract(n=3, used=0):
            self.assertIsNone(reason_of(decide([umbrella], busy=True)))

    def test_a_plan_flow_run_stops_the_queue(self):
        with contract(n=3, used=0):
            self.assertIsNone(reason_of(decide([plan_run(stage="approval", awaiting_human="plan")],
                                     pipeline=PLAN_PIPELINE)))

    def test_a_legacy_run_beside_tree_runs_is_still_exclusive(self):
        rows = [tree_run(task="task-0003", stage="ready", awaiting_human="commit"),
                run(task="task-0004", stage="ready", awaiting_human="commit")]
        with contract(n=3, used=1):
            self.assertIsNone(reason_of(decide(rows)))

    def test_a_paused_legacy_run_is_not_exclusive(self):
        with contract(n=2, used=0):
            self.assertIn(START, reason_of(decide([run(stage="implement",
                                                       status=state.ST_PAUSED)])))


class SerialIsUnchangedTest(unittest.TestCase):
    """Flag off or max_parallel 1: every decision is today's."""

    def assert_serial(self, **kw):
        with contract(**kw):
            self.assertIsNone(reason_of(decide([tree_run(**PARKED)])))
            self.assertIsNone(reason_of(decide([run(**PARKED)])))
            self.assertIn(START, reason_of(decide([])))

    def test_flag_off_with_room_for_more_is_serial(self):
        self.assert_serial(enabled=False, n=3, used=0)

    def test_max_parallel_one_with_the_flag_on_is_serial(self):
        self.assert_serial(enabled=True, n=1, used=0)

    def test_serial_mode_never_reads_the_slot_count(self):
        with contract(enabled=False, n=3), \
                unittest.mock.patch.object(worktree, "slots_used") as used:
            decide([tree_run(**PARKED)])
        used.assert_not_called()

    def test_serial_mode_keeps_the_single_marker_silencing_everything(self):
        with contract(enabled=True, n=1):
            self.assertIsNone(reason_of(decide([], any_busy=True)))
        with contract(enabled=True, n=3, used=0), \
                unittest.mock.patch.object(stop_gate, "any_busy_marker_fresh") as any_marker:
            decide([])
        any_marker.assert_not_called()


class BusyMarkerTest(unittest.TestCase):
    """F6: doc-scaffold markers still silence; a run's marker counts as a slot."""

    def test_a_doc_scaffold_marker_silences_the_hook_in_parallel_mode(self):
        with contract(n=2, used=0), \
                unittest.mock.patch.object(stop_gate, "doc_busy_marker_fresh",
                                           return_value=True):
            self.assertIsNone(reason_of(decide([])))

    def test_a_marker_of_a_live_run_does_not_silence_the_start(self):
        # any_busy is the serial reader; the parallel hook must not consult it.
        with contract(n=2, used=1):
            out = decide([tree_run(stage="implement")], busy=True, any_busy=True)
        self.assertIn(START, reason_of(out))

    def test_doc_marker_reader_ignores_the_markers_of_live_runs(self):
        fresh = {"task-0001": True, "task-0009": True}
        with unittest.mock.patch.object(stop_gate.busy, "tasks", return_value=list(fresh)), \
                unittest.mock.patch.object(stop_gate, "busy_marker_fresh",
                                           side_effect=lambda t: fresh[t]):
            self.assertTrue(stop_gate.doc_busy_marker_fresh([run(task="task-0001")]))
            fresh["task-0009"] = False
            self.assertFalse(stop_gate.doc_busy_marker_fresh([run(task="task-0001")]))

    def test_the_marker_of_a_finished_run_is_a_doc_marker(self):
        with unittest.mock.patch.object(stop_gate.busy, "tasks", return_value=["task-0001"]), \
                unittest.mock.patch.object(stop_gate, "busy_marker_fresh", return_value=True):
            self.assertTrue(stop_gate.doc_busy_marker_fresh([run(stage="done")]))

    def test_doc_marker_reader_is_fail_open(self):
        with unittest.mock.patch.object(stop_gate.busy, "tasks", side_effect=OSError):
            self.assertFalse(stop_gate.doc_busy_marker_fresh([]))


class UmbrellaTest(unittest.TestCase):
    """An umbrella runs in `src/`: exclusive both ways (plan 2.12)."""

    def decide(self, queue, **kw):
        with unittest.mock.patch.object(stop_gate, "swarm_on", return_value=True), \
                unittest.mock.patch.object(stop_gate, "umbrella_epics", return_value=set()):
            return decide(queue=queue, **kw)

    def test_a_queued_umbrella_waits_while_a_slot_is_in_use(self):
        parked = [tree_run(stage="ready", awaiting_human="commit")]
        with contract(n=2, used=1):
            self.assertIsNone(reason_of(self.decide([UMBRELLA], runs=parked)))

    def test_a_queued_umbrella_starts_once_the_slots_are_empty(self):
        with contract(n=2, used=0):
            out = self.decide([UMBRELLA])
        self.assertIn("Start the next ready task task-0050", reason_of(out))

    def test_an_epic_swarm_demand_waits_while_a_slot_is_in_use(self):
        parked = [tree_run(stage="ready", awaiting_human="commit")]
        with contract(n=2, used=1):
            self.assertIsNone(reason_of(self.decide(PAIR, runs=parked)))
        with contract(n=2, used=0):
            self.assertIn("skills/swarm/SKILL.md", reason_of(self.decide(PAIR)))

    def test_a_waiting_umbrella_is_not_overtaken_by_an_ordinary_task(self):
        queue = [UMBRELLA, {"id": "task-0060", "deps": []}]
        parked = [tree_run(stage="ready", awaiting_human="commit")]
        with contract(n=2, used=1):
            self.assertIsNone(reason_of(self.decide(queue, runs=parked)))


class StartInstructionTest(unittest.TestCase):
    """F10: advance.py creates the branch and the tree."""

    LEGACY = ("Start the next ready task task-0002: create its branch per git-workflow.md, "
              "then run: python .claude/tools/pipeline/advance.py --task task-0002. "
              "Drive it through the pipeline without asking the user.")

    def test_flag_on_says_advance_creates_the_branch_and_the_tree(self):
        with contract(enabled=True, n=2):
            reason = reason_of(decide([]))
        self.assertIn("advance.py creates its branch and its worktree", reason)
        self.assertIn("do not run git checkout -b yourself", reason)
        self.assertIn("advance.py --task task-0002.", reason)
        self.assertNotIn("create its branch per git-workflow.md", reason)

    def test_flag_on_with_max_parallel_one_gives_the_same_instruction(self):
        with contract(enabled=True, n=1):
            self.assertIn("creates its branch and its worktree", reason_of(decide([])))

    def test_flag_off_keeps_todays_text_byte_for_byte(self):
        with contract(enabled=False, n=2):
            self.assertEqual(self.LEGACY, reason_of(decide([])))

    def test_an_umbrella_keeps_the_branch_instruction_with_the_flag_on(self):
        # An umbrella gets no tree and runs in `src/` on its own branch, so the agent
        # still creates that branch; telling it advance.py does would leave it on main.
        legacy = ("Start the next ready task task-0050: create its branch per git-workflow.md, "
                  "then run: python .claude/tools/pipeline/advance.py --task task-0050. "
                  "Drive it through the pipeline without asking the user.")
        for n in (1, 2):
            with self.subTest(max_parallel=n), contract(enabled=True, n=n, used=0), \
                    unittest.mock.patch.object(stop_gate, "swarm_on", return_value=True), \
                    unittest.mock.patch.object(stop_gate, "umbrella_epics", return_value=set()):
                self.assertEqual(legacy, reason_of(decide(queue=[UMBRELLA])))

    def test_an_ordinary_task_beside_a_queued_umbrella_still_gets_the_tree_text(self):
        with contract(enabled=True, n=2), \
                unittest.mock.patch.object(stop_gate, "swarm_on", return_value=False):
            reason = reason_of(decide(queue=[{"id": "task-0002", "deps": []}]))
        self.assertIn("advance.py creates its branch and its worktree", reason)


class DispatchLineTest(unittest.TestCase):
    """2.10: every nag for a run in a worktree names the tree."""

    NAME = "Task worktree: E:/Personal/Agenty/src-wt/task-0001 (branch feature/task-0001)."

    def nag(self, row, pipeline=BUILD_PIPELINE, lines=None, **kw):
        with contract(n=2, used=1, lines={"task-0001": self.NAME} if lines is None else lines):
            return reason_of(decide([row], pipeline=pipeline, **kw))

    def test_the_drive_nag_carries_the_line(self):
        reason = self.nag(tree_run(stage="implement"))
        self.assertIn(f"Do not ask the user whether to continue. {self.NAME}", reason)

    def test_the_gate_failed_nag_carries_the_line(self):
        reason = self.nag(tree_run(stage="test", status=state.ST_GATE_FAILED))
        self.assertIn("gate FAILED", reason)
        self.assertIn(f"Do not ask the user. {self.NAME}", reason)

    def test_the_approved_checkpoint_nag_carries_the_line(self):
        reason = self.nag(tree_run(stage="ready", awaiting_human="commit", commit_approved=1))
        self.assertIn("checkpoint approved", reason)
        self.assertIn(self.NAME, reason)

    def test_the_unlisted_stage_nag_carries_the_line(self):
        reason = self.nag(plan_run(stage="draft", worktree=TREE.format("task-0001")),
                          pipeline=PLAN_PIPELINE_UNSET)
        self.assertIn("editing_stages does not list", reason)
        self.assertIn(self.NAME, reason)

    def test_the_line_goes_before_the_busy_marker_note(self):
        reason = self.nag(tree_run(stage="implement"), marker_note=" (marker note)")
        self.assertTrue(reason.endswith(f"{self.NAME} (marker note)"), reason)

    def test_a_run_without_a_tree_gets_todays_text(self):
        plain = self.nag(run(stage="implement"), lines={})
        self.assertTrue(plain.endswith("Do not ask the user whether to continue."), plain)

    def test_the_nag_is_byte_stable_between_stops(self):
        row = tree_run(stage="implement")
        self.assertEqual(self.nag(row), self.nag(row))

    def test_a_failing_line_reader_leaves_the_nag_unchanged(self):
        with contract(n=2, used=1), \
                unittest.mock.patch.object(worktree, "dispatch_line", side_effect=RuntimeError):
            reason = reason_of(decide([tree_run(stage="implement")]))
        self.assertTrue(reason.endswith("Do not ask the user whether to continue."), reason)


class ReconcileTest(unittest.TestCase):
    """2.8: the reconciler NAMES a merged task's tree and never removes it (the Stop
    hook has 20 s and worktree.remove is up to three git calls with sleeps)."""

    TASK = "task-0007"

    def setUp(self):
        self.root = tmproot.sandbox(self, "conveyor_reconcile_").resolve()
        self.active = self.root / "active"
        self.active.mkdir()
        self.tree = self.root / "src-wt" / self.TASK
        self.tree.mkdir(parents=True)
        self.runs: list[dict] = []
        self.remove = unittest.mock.Mock(side_effect=AssertionError("the hook must not remove"))
        for target, name, value in (
                (stop_gate, "ACTIVE_DIR", self.active),
                (state, "ROOT", self.root),
                (state, "all_runs", lambda conn: self.runs),
                (state, "set_fields", unittest.mock.Mock()),
                (state, "move_task_to_done", unittest.mock.Mock(return_value=True)),
                (state, "move_swarm_subtasks", unittest.mock.Mock(return_value=[])),
                (worktree, "recorded", lambda t: f"src-wt/{t}"),
                (worktree, "remove", self.remove)):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def reconcile(self, merged=True):
        with unittest.mock.patch.object(git_state, "task_tagged_in_main", return_value=merged):
            return stop_gate.reconcile_status_drift(object())

    def finished_run(self):
        (self.active / f"{self.TASK}.md").write_text("---\nid: 7\n---\n", encoding="utf-8")
        self.runs.append(tree_run(task=self.TASK, stage="done", awaiting_human="merge"))

    def test_a_confirmed_merge_names_the_tree_and_removes_nothing(self):
        self.finished_run()
        moved, _ = self.reconcile()
        self.assertEqual([self.TASK], moved)
        self.assertEqual([f"{self.TASK} (src-wt/{self.TASK})"], stop_gate._CTX["left"])
        self.remove.assert_not_called()
        self.assertTrue(self.tree.is_dir())

    def test_a_stale_run_closed_by_its_tag_names_the_tree_too(self):
        self.runs.append(tree_run(task=self.TASK, stage="ready", commit_approved=1))
        with unittest.mock.patch.object(state, "task_dir", return_value="done"):
            _, closed = self.reconcile()
        self.assertEqual([self.TASK], closed)
        self.assertEqual([f"{self.TASK} (src-wt/{self.TASK})"], stop_gate._CTX["left"])
        self.remove.assert_not_called()

    def test_an_unmerged_task_names_nothing(self):
        self.finished_run()
        self.assertEqual(([], []), self.reconcile(merged=False))
        self.assertEqual([], stop_gate._CTX["left"])

    def test_a_tree_that_is_already_gone_is_not_named(self):
        self.finished_run()
        self.tree.rmdir()
        moved, _ = self.reconcile()
        self.assertEqual([self.TASK], moved)
        self.assertEqual([], stop_gate._CTX["left"])

    def test_a_task_without_a_tree_names_nothing(self):
        self.finished_run()
        with unittest.mock.patch.object(worktree, "recorded", return_value=""):
            moved, _ = self.reconcile()
        self.assertEqual([self.TASK], moved)
        self.assertEqual([], stop_gate._CTX["left"])

    def test_a_recorded_reader_that_raises_never_breaks_the_reconciler(self):
        self.finished_run()
        with unittest.mock.patch.object(worktree, "recorded", side_effect=RuntimeError("boom")):
            moved, _ = self.reconcile()
        self.assertEqual([self.TASK], moved)
        self.assertEqual([], stop_gate._CTX["left"])

    def test_the_bookkeeping_block_names_the_tree_and_the_command_that_removes_it(self):
        stop_gate._CTX["left"] = [f"{self.TASK} (src-wt/{self.TASK})"]
        self.addCleanup(stop_gate._CTX.update, left=[])
        out = decide([], backlog=(), drift=([self.TASK], []))
        self.assertIn(f"worktree still in place: {self.TASK} (src-wt/{self.TASK})", out["reason"])
        self.assertIn("worktree.py --remove --task", out["reason"])

    def test_nothing_left_adds_nothing_to_the_bookkeeping_block(self):
        out = decide([], backlog=(), drift=([self.TASK], []))
        self.assertNotIn("worktree", out["reason"])


class IdleOrphanLineTest(unittest.TestCase):
    """Plan section 3: the idle hook says in one line that a task worktree is left over."""

    ORPHAN = (("path", "src-wt/task-0009"), ("task", "task-0009"), ("why", "run done"))

    def orphans(self):
        return [dict(self.ORPHAN)]

    def idle(self, orphans, enabled=True):
        with contract(enabled=enabled, n=2), \
                unittest.mock.patch.object(worktree, "orphans", return_value=orphans):
            return decide([], backlog=())

    def test_an_idle_hook_with_a_leftover_tree_releases_with_one_line(self):
        out = self.idle(self.orphans())
        self.assertNotIn("decision", out)
        message = out["systemMessage"]
        self.assertEqual(1, len(message.splitlines()), message)
        self.assertIn("1 task worktree(s) left over, e.g. src-wt/task-0009 (run done)", message)
        self.assertIn("worktree.py --sweep", message)

    def test_an_idle_hook_with_no_leftover_stays_silent(self):
        self.assertIsNone(self.idle([]))

    def test_the_flag_off_never_looks_for_leftovers(self):
        with contract(enabled=False), \
                unittest.mock.patch.object(worktree, "orphans",
                                           side_effect=AssertionError("must not be read")):
            self.assertIsNone(decide([], backlog=()))

    def test_an_orphan_reader_that_raises_leaves_the_hook_silent(self):
        with contract(enabled=True), \
                unittest.mock.patch.object(worktree, "orphans", side_effect=RuntimeError("x")):
            self.assertIsNone(decide([], backlog=()))

    def test_a_parked_run_keeps_its_own_release_text(self):
        parked = [tree_run(**PARKED)]
        with contract(enabled=True, n=1, used=1), \
                unittest.mock.patch.object(worktree, "orphans", return_value=self.orphans()):
            out = decide(parked, backlog=())
        self.assertNotIn("left over", out["systemMessage"])


def paused(task, **kw) -> dict:
    return run(task=task, status=state.ST_PAUSED, paused_status=state.ST_IN_PROGRESS, **kw)


class ResumeSlotsTest(unittest.TestCase):
    """F7: resume checks the slot count, not 'another task holds an editing stage'."""

    def resume(self, row, others=(), **ct):
        with contract(**ct), \
                unittest.mock.patch.object(state, "load_pipeline", return_value=BUILD_PIPELINE), \
                unittest.mock.patch.object(state, "all_runs", return_value=[row, *others]), \
                unittest.mock.patch.object(state, "set_fields") as set_fields:
            result = approve.resume_run(object(), row)
        return result, set_fields

    def test_a_full_slot_count_refuses_and_names_the_holders(self):
        live = [{"task": "task-0003", "worktree": "src-wt/task-0003"},
                {"task": "task-0004", "worktree": "src-wt/task-0004"}]
        (message, error), set_fields = self.resume(
            paused("task-0001", worktree="src-wt/task-0001"), n=2, used=2, live=live)
        self.assertEqual("", message)
        self.assertIn("all 2 task slots are in use (task-0003, task-0004)", error)
        self.assertNotIn("holds an editing stage", error)
        set_fields.assert_not_called()

    def test_a_free_slot_resumes_even_beside_an_editing_tree_run(self):
        other = tree_run(task="task-0003", stage="implement")
        (message, error), set_fields = self.resume(
            paused("task-0001", worktree="src-wt/task-0001"), others=[other], n=2, used=1)
        self.assertEqual("", error)
        self.assertIn("task-0001 resumed", message)
        self.assertEqual(state.ST_IN_PROGRESS, set_fields.call_args.kwargs["stage_status"])

    def test_a_tree_run_paused_at_a_checkpoint_also_needs_a_slot(self):
        row = paused("task-0001", stage="ready", worktree="src-wt/task-0001")
        (_, error), _ = self.resume(row, n=2, used=2)
        self.assertIn("task slots are in use", error)

    def test_a_legacy_run_ignores_tree_runs_when_taking_the_checkout_back(self):
        other = tree_run(task="task-0003", stage="implement")
        (_, error), _ = self.resume(paused("task-0001"), others=[other], n=2, used=1)
        self.assertEqual("", error)

    def test_a_legacy_run_still_refuses_beside_another_legacy_editing_run(self):
        other = run(task="task-0003", stage="implement")
        (_, error), _ = self.resume(paused("task-0001"), others=[other], n=2, used=0)
        self.assertIn("task-0003 holds an editing stage", error)

    def test_serial_mode_keeps_the_editing_stage_refusal_for_a_tree_run(self):
        other = tree_run(task="task-0003", stage="implement")
        for ct in ({"enabled": False, "n": 2}, {"enabled": True, "n": 1}):
            with self.subTest(**ct):
                (_, error), _ = self.resume(
                    paused("task-0001", worktree="src-wt/task-0001"), others=[other], **ct)
                self.assertIn("task-0003 holds an editing stage", error)
                self.assertIn("at most one task may", error)

    def test_an_unreadable_slot_count_refuses_the_resume(self):
        row = paused("task-0001", worktree="src-wt/task-0001")
        (_, error), _ = self.resume(row, n=2, used=None)
        self.assertIn("task slots are in use", error)

    def test_an_unreadable_live_set_refuses_the_resume_of_a_tree_run_and_an_umbrella(self):
        for row in (paused("task-0001", worktree="src-wt/task-0001"),
                    paused("task-0001", umbrella="true")):
            with self.subTest(umbrella=row.get("umbrella", "")):
                (message, error), set_fields = self.resume(row, n=2, used=0, live_error=True)
                self.assertEqual("", message)
                self.assertIn("could not be read", error)
                set_fields.assert_not_called()

    def test_a_paused_umbrella_does_not_resume_beside_live_tree_runs(self):
        live = [{"task": "task-0003", "worktree": "src-wt/task-0003", "umbrella": False}]
        (message, error), set_fields = self.resume(
            paused("task-0001", umbrella="true"), n=2, used=1, live=live)
        self.assertEqual("", message)
        self.assertIn("task-0001 is an umbrella", error)
        self.assertIn("(task-0003)", error)
        set_fields.assert_not_called()

    def test_a_paused_umbrella_resumes_when_no_slot_is_in_use(self):
        (message, error), _ = self.resume(paused("task-0001", umbrella="true"), n=2, used=0)
        self.assertEqual("", error)
        self.assertIn("task-0001 resumed", message)

    def test_a_paused_umbrella_still_refuses_beside_a_legacy_editing_run(self):
        other = run(task="task-0003", stage="implement")
        (_, error), _ = self.resume(
            paused("task-0001", umbrella="true"), others=[other], n=2, used=0)
        self.assertIn("task-0003 holds an editing stage", error)

    def test_a_tree_run_does_not_resume_beside_a_live_umbrella(self):
        live = [{"task": "task-0009", "worktree": "", "umbrella": True}]
        (message, error), set_fields = self.resume(
            paused("task-0001", worktree="src-wt/task-0001"), n=2, used=0, live=live)
        self.assertEqual("", message)
        self.assertIn("task-0009 is a swarm umbrella", error)
        set_fields.assert_not_called()

    def test_serial_mode_does_not_apply_the_umbrella_slot_rule(self):
        live = [{"task": "task-0003", "worktree": "src-wt/task-0003", "umbrella": False}]
        for ct in ({"enabled": False, "n": 2}, {"enabled": True, "n": 1}):
            with self.subTest(**ct):
                (_, error), _ = self.resume(paused("task-0001", umbrella="true"),
                                            used=1, live=live, **ct)
                self.assertEqual("", error)


class AssessPauseTest(unittest.TestCase):
    """F8: a pause reads the task's tree, and fails closed when it cannot."""

    def assess(self, status, trees, git_out=(0, "")):
        git = unittest.mock.Mock(return_value=git_out)
        with unittest.mock.patch.object(worktree, "status", return_value=status), \
                unittest.mock.patch.object(git_state, "work_trees_for_task", create=True,
                                           return_value=trees), \
                unittest.mock.patch.object(git_state, "branch_for", return_value=""), \
                unittest.mock.patch.object(approve, "_git", git):
            return approve.assess_pause("task-0001"), git

    def test_an_ok_tree_is_the_one_inspected(self):
        tree = Path("E:/Personal/Agenty/src-wt/task-0001")
        (kind, problem), git = self.assess(worktree.STATUS_OK, [tree])
        self.assertEqual((approve.PAUSE_CLEAN, ""), (kind, problem))
        self.assertEqual(tree, git.call_args_list[0].args[0])

    def test_dirty_work_in_the_tree_refuses_the_pause_and_names_the_tree(self):
        tree = Path("E:/Personal/Agenty/src-wt/task-0001")
        (_, problem), _ = self.assess(worktree.STATUS_OK, [tree], git_out=(0, " M a.py\n"))
        self.assertIn("uncommitted work", problem)
        self.assertIn("src-wt", problem.replace("\\", "/"))

    def test_a_missing_or_invalid_tree_refuses_and_never_touches_git(self):
        for status in (worktree.STATUS_MISSING, worktree.STATUS_INVALID):
            with self.subTest(status=status):
                (kind, problem), git = self.assess(status, [Path("E:/Personal/Agenty/src")])
                self.assertEqual("", kind)
                self.assertIn(status, problem)
                self.assertIn("worktree.py --repair --task task-0001", problem)
                git.assert_not_called()

    def test_a_legacy_task_is_inspected_where_it_always_was(self):
        repo = Path("E:/Personal/Agenty/src")
        (kind, problem), git = self.assess(worktree.STATUS_LEGACY, [repo])
        self.assertEqual((approve.PAUSE_CLEAN, ""), (kind, problem))
        self.assertEqual(repo, git.call_args_list[0].args[0])


class DiffCheckpointTest(unittest.TestCase):
    """F8: the commit-checkpoint diff comes from the task's tree."""

    def changes(self, status, trees):
        git = unittest.mock.Mock(return_value=(0, " M a.py"))
        with unittest.mock.patch.object(worktree, "status", return_value=status), \
                unittest.mock.patch.object(git_state, "work_trees_for_task", create=True,
                                           return_value=trees), \
                unittest.mock.patch.object(git_state, "_git", git):
            return diff_checkpoint.changed_files("task-0001"), git

    def test_an_ok_tree_is_the_one_read(self):
        tree = Path("E:/Personal/Agenty/src-wt/task-0001")
        text, git = self.changes(worktree.STATUS_OK, [tree])
        self.assertEqual(" M a.py", text)
        self.assertEqual(tree, git.call_args_list[0].args[0])

    def test_a_missing_or_invalid_tree_shows_a_note_and_runs_no_git(self):
        for status in (worktree.STATUS_MISSING, worktree.STATUS_INVALID):
            with self.subTest(status=status):
                text, git = self.changes(status, [])
                self.assertIn(f"task worktree {status}", text)
                self.assertIn("worktree.py --repair --task task-0001", text)
                git.assert_not_called()

    def test_the_capped_diff_carries_the_same_note(self):
        with unittest.mock.patch.object(worktree, "status",
                                        return_value=worktree.STATUS_MISSING):
            self.assertIn("task worktree MISSING", diff_checkpoint.capped_diff("task-0001"))

    def deliver(self, status):
        inject = unittest.mock.Mock(return_value=True)
        with unittest.mock.patch.object(worktree, "status", return_value=status), \
                unittest.mock.patch.object(diff_checkpoint, "at_commit_parking_point",
                                           return_value=True), \
                unittest.mock.patch.object(diff_checkpoint, "inject_diff", inject), \
                unittest.mock.patch.object(diff_checkpoint, "capped_diff",
                                           return_value="# tree\n M a.py"):
            return diff_checkpoint.deliver("task-0001", inject=True), inject

    def test_a_task_in_a_worktree_never_gets_the_console_diff(self):
        # The built-in /diff reads the session checkout, not the tree of the task.
        note, inject = self.deliver(worktree.STATUS_OK)
        inject.assert_not_called()
        self.assertIn("does not read this task's worktree", note)
        self.assertNotIn("queued", note)
        self.assertIn("--- diff ---\n# tree\n M a.py", note)

    def test_a_legacy_task_keeps_the_console_diff(self):
        note, inject = self.deliver(worktree.STATUS_LEGACY)
        inject.assert_called_once_with("task-0001")
        self.assertIn("/diff queued", note)


class SupervisorTest(unittest.TestCase):
    """F8 progress evidence and the relaunch prompt (2.10)."""

    def setUp(self):
        self.tree = tmproot.sandbox(self, "conveyor_progress_").resolve()
        edited = self.tree / "f.txt"
        edited.write_text("x", encoding="utf-8")
        # progress_at ignores a stamp from the future, and a file written a moment
        # ago can read as one against the coarse clock time.time() uses on Windows.
        past = time.time() - 60
        os.utime(edited, (past, past))

    def progress(self, trees):
        legacy = unittest.mock.Mock(return_value=[Path("E:/Personal/Agenty/src")])
        with unittest.mock.patch.object(git_state, "work_trees_for_task", create=True,
                                        return_value=trees), \
                unittest.mock.patch.object(git_state, "repos_for_task", legacy), \
                unittest.mock.patch.object(git_state, "_git",
                                           return_value=(0, "?? f.txt\0")), \
                unittest.mock.patch.object(state, "last_event_at", return_value=None), \
                unittest.mock.patch.object(state, "task_dir", return_value=None):
            return supervisor.progress_at("task-0001"), legacy

    def test_progress_is_read_from_the_tasks_tree(self):
        (at, source), _ = self.progress([self.tree])
        self.assertEqual("the working tree", source)
        self.assertIsNotNone(at)

    def test_a_task_without_a_readable_tree_has_no_working_tree_evidence_and_no_src(self):
        (at, source), legacy = self.progress([])
        self.assertIsNone(at)
        self.assertEqual("", source)
        legacy.assert_not_called()

    def test_the_relaunch_prompt_carries_the_dispatch_line(self):
        with contract(lines={"task-0001": LINE.format(task="task-0001")}):
            argv = supervisor.spawn_argv({}, "task-0001", "implement", "owner exited")
        plain = supervisor.RELAUNCH_PROMPT.format(
            task="task-0001", stage="implement", why="owner exited")
        self.assertEqual(f"{plain} {LINE.format(task='task-0001')}", argv[-1])

    def test_a_task_without_a_tree_gets_the_prompt_byte_for_byte(self):
        with contract(lines={}):
            argv = supervisor.spawn_argv({}, "task-0001", "implement", "owner exited")
        self.assertEqual(supervisor.RELAUNCH_PROMPT.format(
            task="task-0001", stage="implement", why="owner exited"), argv[-1])

    def test_a_failing_line_reader_leaves_the_prompt_unchanged(self):
        with unittest.mock.patch.object(worktree, "dispatch_line", side_effect=RuntimeError):
            argv = supervisor.spawn_argv({}, "task-0001", "implement", "owner exited")
        self.assertEqual(supervisor.RELAUNCH_PROMPT.format(
            task="task-0001", stage="implement", why="owner exited"), argv[-1])


# --- the same behaviour against the REAL worktree.py, a real run store and real trees ---

PIPELINES = {"pipelines": {"build": {"editing_stages": ["implement", "test", "review"], "stages": [
    {"name": "implement", "owner": "dev"},
    {"name": "test", "owner": "qa-engineer"},
    {"name": "review", "owner": "reviewer"},
    {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit"]},
    {"name": "done", "owner": "user", "terminal": True}]}}}


class RealWorkspaceCase(WorkspaceCase):
    """A sandbox workspace with the flag on and two slots, and stop_gate pointed at it."""

    def setUp(self):
        super().setUp()
        self.config("on", **PIPELINES)
        for target, name, value in (
                (stop_gate, "ACTIVE_DIR", self.dirs["active"]),
                (stop_gate, "DONE_DIR", self.dirs["done"]),
                (stop_gate, "BACKLOG_DIR", self.dirs["backlog"]),
                (stop_gate.mode, "conveyor_runs", lambda: True),
                (stop_gate.approvals, "granted", lambda checkpoint: True),
                (stop_gate, "handoff_debt", list),
                (stop_gate, "memory_debt", list),
                (stop_gate, "latest_undocumented", lambda: None),
                (stop_gate, "swarm_on", lambda: False),
                (stop_gate.busy, "tasks", list)):
            patcher = unittest.mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def queue(self, *tasks):
        for task in tasks:
            (self.dirs["backlog"] / f"{task}.md").write_text(
                "---\nid: 1\n---\n## Acceptance Criteria\n- [ ] one\n", encoding="utf-8")

    def stop(self) -> dict | None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            stop_gate.decide()
        text = out.getvalue().strip()
        return json.loads(text) if text else None


class RealStopHookTest(RealWorkspaceCase):

    def test_a_parked_tree_run_leaves_the_queue_open_and_the_start_text_names_the_tree(self):
        self.make_tree(TASK, stage="ready", awaiting_human="commit")
        self.queue("task-0140")
        reason = reason_of(self.stop())
        self.assertIn("Start the next ready task task-0140", reason)
        self.assertIn("advance.py creates its branch and its worktree", reason)

    def test_two_tree_runs_fill_both_slots_and_nothing_starts(self):
        self.make_tree(TASK, stage="ready", awaiting_human="commit")
        self.make_tree(OTHER, stage="ready", awaiting_human="commit")
        self.queue("task-0140")
        self.assertIsNone(reason_of(self.stop()))

    def test_the_drive_nag_names_the_real_tree_and_branch(self):
        self.make_tree(TASK, stage="implement")
        reason = reason_of(self.stop())
        self.assertIn(f"src-wt/{TASK}", reason)
        self.assertIn(f"branch feature/{TASK}", reason)
        self.assertEqual(reason, reason_of(self.stop()), "the nag must be byte-stable")

    def test_the_flag_off_makes_a_recorded_tree_hold_the_whole_queue_again(self):
        self.make_tree(TASK, stage="ready", awaiting_human="commit")
        self.queue("task-0140")
        self.config("off", **PIPELINES)
        self.assertIsNone(reason_of(self.stop()))

    def test_a_legacy_run_beside_a_tree_run_stops_the_queue(self):
        self.make_tree(TASK, stage="ready", awaiting_human="commit")
        self.run_row(OTHER, "ready", awaiting_human="commit")
        self.queue("task-0140")
        self.assertIsNone(reason_of(self.stop()))

    def reconcile(self):
        conn = state.connect()
        try:
            return stop_gate.reconcile_status_drift(conn)
        finally:
            conn.close()

    def merge(self, added):
        self.commit(Path(added["abs"]), "work.txt", f"[{TASK}] work")
        self.git(self.src, "merge", "--no-ff", "-m", f"[{TASK}] merge", added["branch"])
        (self.dirs["active"] / f"{TASK}.md").write_text("---\nid: 1\n---\n", encoding="utf-8")

    def test_a_merged_task_keeps_its_tree_and_its_branch_and_the_tree_is_named(self):
        added = self.make_tree(TASK, stage="done", awaiting_human="merge")
        self.merge(added)
        moved, _ = self.reconcile()
        self.assertEqual([TASK], moved)
        self.assertTrue(Path(added["abs"]).exists())
        self.assertIn(added["branch"], self.git(self.src, "branch", "--list", added["branch"]))
        self.assertEqual([f"{TASK} (src-wt/{TASK})"], stop_gate._CTX["left"])

    def test_a_task_whose_tree_is_already_gone_is_closed_without_a_note(self):
        added = self.make_tree(TASK, stage="done", awaiting_human="merge")
        self.merge(added)
        self.git(self.src, "worktree", "remove", added["abs"])
        moved, _ = self.reconcile()
        self.assertEqual([TASK], moved)
        self.assertEqual([], stop_gate._CTX["left"])

    def test_the_idle_hook_reports_the_leftover_tree_of_a_closed_task(self):
        added = self.make_tree(TASK, stage="done", awaiting_human="merge")
        self.merge(added)
        self.reconcile()
        out = self.stop()
        self.assertIn(f"src-wt/{TASK}", out["systemMessage"])
        self.assertIn("(run done)", out["systemMessage"])


class RealResumeTest(RealWorkspaceCase):

    def resume(self, task):
        conn = state.connect()
        try:
            return approve.resume_run(conn, state.get_run(conn, task))
        finally:
            conn.close()

    def test_resume_is_refused_on_two_live_trees_and_allowed_after_one_is_paused(self):
        self.make_tree(TASK, stage="implement", stage_status=state.ST_PAUSED,
                       paused_status=state.ST_IN_PROGRESS)
        self.make_tree(OTHER, stage="implement")
        self.make_tree("task-0132", stage="implement")
        message, error = self.resume(TASK)
        self.assertEqual("", message)
        self.assertIn("all 2 task slots are in use (task-0131, task-0132)", error)
        conn = state.connect()
        state.set_fields(conn, OTHER, stage_status=state.ST_PAUSED)
        conn.close()
        message, error = self.resume(TASK)
        self.assertEqual("", error)
        self.assertIn(f"{TASK} resumed", message)


class RealReadersTest(RealWorkspaceCase):
    """pause, diff and progress read the task's tree and never the trunk checkout."""

    def setUp(self):
        super().setUp()
        self.added = self.make_tree(TASK, stage="implement")
        self.tree = Path(self.added["abs"])

    def test_a_pause_sees_edits_in_the_tree_and_ignores_the_trunk(self):
        (self.src / "README.md").write_text("trunk edit\n", encoding="utf-8")
        self.assertEqual((approve.PAUSE_CLEAN, ""), approve.assess_pause(TASK))
        (self.tree / "README.md").write_text("tree edit\n", encoding="utf-8")
        _, problem = approve.assess_pause(TASK)
        self.assertIn("uncommitted work", problem)
        self.assertIn("README.md", problem)

    def test_a_pause_of_a_task_whose_tree_vanished_is_refused(self):
        subprocess.run(["git", "-C", str(self.src), "worktree", "remove", "--force",
                        str(self.tree)], capture_output=True, check=True)
        _, problem = approve.assess_pause(TASK)
        self.assertIn("MISSING", problem)
        self.assertIn("worktree.py --repair", problem)

    def test_the_commit_diff_comes_from_the_tree(self):
        (self.src / "README.md").write_text("trunk edit\n", encoding="utf-8")
        (self.tree / "new.txt").write_text("only in the tree\n", encoding="utf-8")
        text = diff_checkpoint.changed_files(TASK)
        self.assertIn("new.txt", text)
        self.assertNotIn("README.md", text)

    def test_progress_comes_from_the_tree_and_not_from_the_trunk(self):
        (self.src / "README.md").write_text("trunk edit\n", encoding="utf-8")
        self.assertNotEqual("the working tree", supervisor.progress_at(TASK)[1])
        new = self.tree / "new.txt"
        new.write_text("only in the tree\n", encoding="utf-8")
        past = time.time() - 60
        os.utime(new, (past, past))
        self.assertEqual("the working tree", supervisor.progress_at(TASK)[1])

    def test_the_relaunch_prompt_carries_the_real_dispatch_line(self):
        prompt = supervisor.spawn_argv({}, TASK, "implement", "owner exited")[-1]
        self.assertTrue(prompt.endswith(worktree.dispatch_line(TASK)), prompt)
        self.assertIn(f"branch feature/{TASK}", prompt)


if __name__ == "__main__":
    unittest.main()
