"""QA for task-0131 (epic-0004 S3): the conveyor drives several tasks, one per worktree.

Independent of test_worktree_conveyor.py: every case here runs the REAL stop hook,
approve, diff_checkpoint and supervisor against a sandbox workspace holding a real
git repo, a real run store, real worktrees and real busy-marker files. Nothing the
core zone owns is patched, so what is asserted is what the live hooks would do.

Layout: the Stop hook decision table (slots, exclusivity, markers, serial
equivalence, start text), the reconciler, resume, pause, the commit-checkpoint diff,
the supervisor, the dispatch line (byte-stability and the reason-repeat backstop),
the hook budget (no subprocess), and the force-majeure flow through real advance.py.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import approve
import busy
import diff_checkpoint
import state
import stop_gate
import supervisor
import tmproot
import worktree
from test_worktree_core import OTHER, TASK, WorkspaceCase
from test_worktree_registration import RegistrationCase

THIRD = "task-0132"
NEXT = "task-0140"
PIPELINES = {"pipelines": {"build": {"editing_stages": ["implement", "test", "review"], "stages": [
    {"name": "implement", "owner": "dev"},
    {"name": "test", "owner": "qa-engineer"},
    {"name": "review", "owner": "reviewer"},
    {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit"]},
    {"name": "done", "owner": "user", "terminal": True}]}}}
TRIAGE = {"pipelines": {"build": {"editing_stages": ["implement", "test", "review"], "stages": [
    {"name": "triage", "owner": "dev"},
    {"name": "implement", "owner": "dev"},
    {"name": "test", "owner": "qa-engineer"},
    {"name": "review", "owner": "reviewer"},
    {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit"]},
    {"name": "done", "owner": "user", "terminal": True}]}}}
UMBRELLA_HEAD = "epic: epic-0007\numbrella: true\n"
EPIC_HEAD = "epic: epic-0007\n"
PAUSED = {"stage_status": state.ST_PAUSED, "paused_status": state.ST_IN_PROGRESS}

# What the hook did, as one word. The tests assert on these and on exact texts.
SILENT, RELEASE, START, SWARM, SURFACE, RECONCILE, NAG = (
    "silent", "release", "start", "swarm", "surface-blocked", "reconcile", "nag")
LEGACY_START = (f"Start the next ready task {NEXT}: create its branch per git-workflow.md, then "
                f"run: python .claude/tools/pipeline/advance.py --task {NEXT}. Drive it through "
                f"the pipeline without asking the user.")


def kind_of(out: dict | None) -> str:
    if out is None:
        return SILENT
    reason = out.get("reason") if out.get("decision") == "block" else None
    if reason is None:
        return RELEASE
    for marker, kind in (("Start the next ready task", START), ("Swarm mode is on", SWARM),
                         ("parked BLOCKED", SURFACE), ("Reconciled task-status drift", RECONCILE)):
        if marker in reason:
            return kind
    return NAG


def patch_stop_gate(case: unittest.TestCase, sandbox: WorkspaceCase, swarm=lambda: False) -> None:
    """Point the stop hook at the sandbox and pin everything that is not under test.
    busy.tasks and busy_marker_fresh stay REAL: the sandbox state dir holds the markers."""
    for target, name, value in (
            (stop_gate, "ACTIVE_DIR", sandbox.dirs["active"]),
            (stop_gate, "DONE_DIR", sandbox.dirs["done"]),
            (stop_gate, "BACKLOG_DIR", sandbox.dirs["backlog"]),
            (stop_gate.mode, "conveyor_runs", lambda: True),
            (stop_gate.approvals, "granted", lambda checkpoint: True),
            (stop_gate, "handoff_debt", list),
            (stop_gate, "memory_debt", list),
            (stop_gate, "latest_undocumented", lambda: None),
            (stop_gate, "swarm_on", swarm)):
        patcher = unittest.mock.patch.object(target, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)
    # A marker is live only while its recorded owner session runs: this process is it.
    env = unittest.mock.patch.dict(os.environ, {"CLAUDE_PID": str(os.getpid())})
    env.start()
    case.addCleanup(env.stop)


class StopCase(WorkspaceCase):
    """A sandbox with the flag on and two slots, and the real stop hook pointed at it."""

    swarm = False

    def setUp(self):
        super().setUp()
        self.config("on", **PIPELINES)
        patch_stop_gate(self, self, swarm=lambda: self.swarm)

    # --- fixtures ----------------------------------------------------------------

    def tree(self, task, stage="implement", **fields) -> dict:
        return self.make_tree(task, stage, **fields)

    def legacy(self, task, stage="implement", head="", **fields) -> dict:
        if head:
            self.task_file(task, head, "active")
        return self.run_row(task, stage, "", **fields)

    def task_file(self, task, head="", folder="backlog") -> None:
        (self.dirs[folder] / f"{task}.md").write_text(
            f"---\nid: 1\n{head}---\n## Acceptance Criteria\n- [ ] one\n", encoding="utf-8")

    def queue(self, *tasks, head="") -> None:
        for task in tasks:
            self.task_file(task, head)

    def mark(self, task, stage="implement") -> None:
        busy.acquire(task, stage)
        self.addCleanup(busy.release, task)

    def parked(self, task, **kw):
        return {"stage": "ready", "awaiting_human": "commit", **kw}

    def set_flag(self, name: str) -> None:
        blocks = {"absent": None, "off": {"enabled": False, "max_parallel": 3},
                  "on1": {"enabled": True, "max_parallel": 1},
                  "on3": {"enabled": True, "max_parallel": 3},
                  "bad": {"enabled": True, "max_parallel": "x"}}
        self.config(blocks[name], **PIPELINES)

    # --- the hook ----------------------------------------------------------------

    def stop(self, active: bool = False) -> dict | None:
        out = io.StringIO()
        with unittest.mock.patch.object(stop_gate, "STOP_HOOK_ACTIVE", active), \
                contextlib.redirect_stdout(out):
            stop_gate.decide()
        text = out.getvalue().strip()
        return json.loads(text) if text else None

    def kind(self) -> str:
        return kind_of(self.stop())


class SlotTableTest(StopCase):
    """F5, F11, 2.8: slots against max_parallel, parked runs stop blocking the queue."""

    def test_a_busy_run_is_skipped_and_the_next_task_starts_in_the_free_slot(self):
        self.tree(TASK)
        self.mark(TASK)
        self.queue(NEXT)
        self.assertEqual(START, self.kind())

    def test_two_busy_runs_fill_both_slots_and_the_hook_stays_silent(self):
        self.tree(TASK)
        self.tree(OTHER)
        self.mark(TASK)
        self.mark(OTHER)
        self.queue(NEXT)
        self.assertEqual(SILENT, self.kind())

    def test_the_idle_run_is_driven_while_its_busy_neighbour_is_skipped(self):
        self.tree(TASK)
        self.tree(OTHER)
        self.mark(TASK)
        self.queue(NEXT)
        reason = self.stop()["reason"]
        self.assertIn(f"{OTHER} is at stage 'implement'", reason)
        self.assertNotIn(TASK, reason)
        self.assertEqual(0, self.row(TASK)["continuations"])

    def test_a_busy_run_counts_as_a_slot_beside_a_parked_one(self):
        self.tree(TASK)
        self.mark(TASK)
        self.tree(OTHER, "ready", awaiting_human="commit")
        self.queue(NEXT)
        self.assertEqual(RELEASE, self.kind())

    def test_a_third_slot_lets_two_parked_runs_make_room(self):
        self.set_flag("on3")
        self.tree(TASK, **self.parked(TASK))
        self.tree(OTHER, **self.parked(OTHER))
        self.queue(NEXT)
        self.assertEqual(START, self.kind())

    def test_a_paused_tree_run_gives_its_slot_up(self):
        self.tree(TASK, **PAUSED)
        self.tree(OTHER, **self.parked(OTHER))
        self.queue(NEXT)
        self.assertEqual(START, self.kind())

    def test_a_blocked_tree_run_keeps_its_slot(self):
        self.tree(TASK, stage_status=state.ST_BLOCKED, awaiting_human="blocked")
        self.tree(OTHER, **self.parked(OTHER))
        self.queue(NEXT)
        self.assertEqual(RELEASE, self.kind())

    def test_a_blocked_tree_run_that_was_never_surfaced_is_surfaced_before_anything_starts(self):
        self.tree(TASK, stage_status=state.ST_BLOCKED)
        self.queue(NEXT)
        self.assertEqual(SURFACE, self.kind())

    def test_an_unmet_dependency_keeps_a_free_slot_unused(self):
        self.tree(TASK, **self.parked(TASK))
        self.task_file(NEXT, f"depends_on: [{TASK}]\n")
        self.assertEqual(RELEASE, self.kind())

    def test_a_tree_whose_directory_vanished_still_holds_its_slot(self):
        self.tree(TASK)
        self.tree(OTHER)
        self.mark(TASK)
        self.mark(OTHER)
        for task in (TASK, OTHER):
            self.git(self.src, "worktree", "remove", "--force", str(self.root / "src-wt" / task))
        self.queue(NEXT)
        self.assertEqual(SILENT, self.kind())

    def test_the_slot_count_that_starts_the_task_is_the_one_the_contract_reports(self):
        self.tree(TASK, **self.parked(TASK))
        self.queue(NEXT)
        self.assertEqual(1, worktree.slots_used())
        self.assertEqual(START, self.kind())
        self.tree(OTHER, **self.parked(OTHER))
        self.assertEqual(2, worktree.slots_used())
        self.assertEqual(RELEASE, self.kind())


class ExclusiveRunTest(StopCase):
    """What holds `src/` stops the queue in both directions."""

    def test_each_kind_of_legacy_run_holds_the_whole_queue(self):
        cases = (
            ("editing", NAG, dict(stage="implement")),
            ("parked at commit", RELEASE, dict(stage="ready", awaiting_human="commit")),
            ("parked at push", RELEASE, dict(stage="ready", awaiting_human="push")),
            ("blocked", SILENT, dict(stage="implement", stage_status=state.ST_BLOCKED,
                                     awaiting_human="blocked")),
            ("merge wait", None, dict(stage="done", awaiting_human="merge")),
            ("stranded stage", None, dict(stage="approval", awaiting_human="plan")))
        for label, expected, fields in cases:
            with self.subTest(label), self.fresh_run(**fields):
                self.queue(NEXT)
                got = self.kind()
                self.assertNotEqual(START, got)
                if expected:
                    self.assertEqual(expected, got)

    @contextlib.contextmanager
    def fresh_run(self, stage, **fields):
        self.sql("DELETE FROM runs")
        self.run_row(TASK, stage, "", **fields)
        yield

    def test_a_legacy_run_beside_a_parked_tree_run_still_stops_the_queue(self):
        self.tree(TASK, **self.parked(TASK))
        self.legacy(OTHER, **self.parked(OTHER))
        self.queue(NEXT)
        self.assertNotEqual(START, self.kind())

    def test_a_paused_legacy_run_holds_nothing(self):
        self.legacy(TASK, **PAUSED)
        self.queue(NEXT)
        self.assertEqual(START, self.kind())

    def test_a_live_umbrella_without_a_tree_stops_the_queue(self):
        self.legacy("task-0100", "ready", head=UMBRELLA_HEAD, awaiting_human="commit")
        self.queue(NEXT)
        self.assertNotEqual(START, self.kind())

    def test_a_parked_tree_run_makes_a_queued_umbrella_wait(self):
        self.swarm = True
        self.tree(TASK, **self.parked(TASK))
        self.queue("task-0050", head=UMBRELLA_HEAD)
        self.assertEqual(RELEASE, self.kind())

    def test_an_ordinary_task_queued_behind_a_waiting_umbrella_does_not_overtake_it(self):
        self.swarm = True
        self.tree(TASK, **self.parked(TASK))
        self.queue("task-0050", head=UMBRELLA_HEAD)
        self.queue("task-0060")
        self.assertEqual(RELEASE, self.kind())

    def test_the_umbrella_starts_once_no_slot_is_in_use(self):
        self.swarm = True
        self.queue("task-0050", head=UMBRELLA_HEAD)
        out = self.stop()
        self.assertEqual(START, kind_of(out))
        self.assertIn("task-0050", out["reason"])

    def test_an_epic_pair_is_demanded_as_a_swarm_only_when_no_slot_is_in_use(self):
        self.swarm = True
        self.queue("task-0028", "task-0029", head=EPIC_HEAD)
        self.assertEqual(SWARM, self.kind())
        self.tree(TASK, **self.parked(TASK))
        self.assertEqual(RELEASE, self.kind())


class MarkerTest(StopCase):
    """F6: only a marker with no live run still silences a free slot."""

    def test_a_marker_for_a_task_with_no_run_silences_the_start(self):
        self.tree(TASK, **self.parked(TASK))
        self.mark("task-0099")
        self.queue(NEXT)
        self.assertEqual(SILENT, self.kind())

    def test_a_marker_for_a_finished_run_is_a_doc_scaffold_and_silences(self):
        self.tree(TASK, **self.parked(TASK))
        self.legacy(OTHER, "done")
        self.mark(OTHER, "done")
        self.queue(NEXT)
        self.assertEqual(SILENT, self.kind())

    def test_the_marker_of_the_parked_run_does_not_silence_the_start(self):
        self.tree(TASK, **self.parked(TASK))
        self.mark(TASK, "ready")
        self.queue(NEXT)
        self.assertEqual(START, self.kind())

    def test_serial_mode_still_lets_any_marker_silence_everything(self):
        for flag in ("absent", "off", "on1"):
            with self.subTest(flag):
                self.set_flag(flag)
                self.sql("DELETE FROM runs")
                self.legacy(TASK, **self.parked(TASK))
                self.mark(TASK, "ready")
                self.queue(NEXT)
                self.assertEqual(SILENT, self.kind())


class SerialEquivalenceTest(StopCase):
    """Flag off, no block, max_parallel 1 or an unreadable one: today's decisions."""

    SERIAL = ("absent", "off", "on1", "bad")

    def decide_all(self, build):
        """build(self) sets up a world; returns {flag: kind} for the serial configs."""
        seen = {}
        for flag in self.SERIAL:
            self.set_flag(flag)
            self.sql("DELETE FROM runs")
            build()
            seen[flag] = self.kind()
        return seen

    def test_a_parked_legacy_run_holds_the_queue(self):
        seen = self.decide_all(lambda: (self.legacy(TASK, **self.parked(TASK)), self.queue(NEXT)))
        self.assertEqual({flag: RELEASE for flag in self.SERIAL}, seen)

    def test_a_parked_tree_run_holds_the_queue_when_there_is_one_slot(self):
        seen = self.decide_all(lambda: (self.legacy(TASK, "ready", awaiting_human="commit"),
                                        self.queue(NEXT)))
        self.assertEqual({flag: RELEASE for flag in self.SERIAL}, seen)
        for flag in self.SERIAL:
            with self.subTest(flag):
                self.set_flag("on3")
                self.sql("DELETE FROM runs")
                self.set_flag(flag)
                added = worktree.add(TASK, "feature")
                self.run_row(TASK, "ready", added["path"], awaiting_human="commit")
                self.assertEqual(RELEASE, self.kind())
                self.git(self.src, "worktree", "remove", "--force", added["abs"])
                self.git(self.src, "branch", "-D", added["branch"])

    def test_a_blocked_legacy_run_surfaces_once_then_holds_the_queue(self):
        self.set_flag("off")
        self.legacy(TASK, stage_status=state.ST_BLOCKED)
        self.queue(NEXT)
        self.assertEqual([SURFACE, SILENT], [self.kind(), self.kind()])

    def test_an_editing_legacy_run_is_driven(self):
        seen = self.decide_all(lambda: (self.legacy(TASK), self.queue(NEXT)))
        self.assertEqual({flag: NAG for flag in self.SERIAL}, seen)

    def test_a_paused_run_frees_the_queue(self):
        seen = self.decide_all(lambda: (self.legacy(TASK, **PAUSED), self.queue(NEXT)))
        self.assertEqual({flag: START for flag in self.SERIAL}, seen)

    def test_the_start_text_is_todays_when_the_flag_is_off_or_absent(self):
        for flag in ("absent", "off"):
            with self.subTest(flag):
                self.set_flag(flag)
                self.sql("DELETE FROM runs")
                self.queue(NEXT)
                self.assertEqual(LEGACY_START, self.stop()["reason"])

    def test_a_task_in_a_tree_made_before_a_rollback_holds_the_checkout_again(self):
        added = self.tree(TASK, **self.parked(TASK))
        self.queue(NEXT)
        self.assertEqual(START, self.kind())
        self.set_flag("off")
        self.assertEqual(RELEASE, self.kind())
        self.assertTrue(Path(added["abs"]).is_dir())

    def test_max_parallel_one_with_the_flag_on_never_reads_the_slot_count(self):
        self.set_flag("on1")
        self.tree(TASK, **self.parked(TASK))
        self.queue(NEXT)
        with unittest.mock.patch.object(worktree, "slots_used") as used:
            self.assertEqual(RELEASE, self.kind())
        used.assert_not_called()


class StartInstructionTest(StopCase):
    """F10: with the flag on, advance.py creates the branch and the tree."""

    def test_the_text_says_advance_creates_both_and_forbids_git_checkout(self):
        self.queue(NEXT)
        reason = self.stop()["reason"]
        self.assertEqual(
            f"Start the next ready task {NEXT}: advance.py creates its branch and its worktree, "
            f"so do not run git checkout -b yourself. Run: python "
            f".claude/tools/pipeline/advance.py --task {NEXT}. Drive it through the pipeline "
            f"without asking the user.", reason)

    def test_the_instruction_holds_for_every_flag_on_configuration(self):
        for flag in ("on1", "on3", "bad"):
            with self.subTest(flag):
                self.set_flag(flag)
                self.queue(NEXT)
                self.assertIn("advance.py creates its branch and its worktree",
                              self.stop()["reason"])

    def test_an_umbrella_start_keeps_the_branch_instruction_it_gets_no_tree(self):
        # An umbrella runs in `src/` on its own branch: the agent still creates it.
        self.swarm = True
        self.queue("task-0050", head=UMBRELLA_HEAD)
        self.assertEqual(
            "Start the next ready task task-0050: create its branch per git-workflow.md, then "
            "run: python .claude/tools/pipeline/advance.py --task task-0050. Drive it through "
            "the pipeline without asking the user.", self.stop()["reason"])


class ReconcilerTest(StopCase):
    """2.8: the reconciler NAMES a merged task's tree and removes nothing (the Stop hook
    has 20 s; advance.py and `worktree.py --remove` own the removal)."""

    def merged(self, task=TASK, **fields) -> dict:
        added = self.tree(task, "done", awaiting_human="merge", **fields)
        self.commit(Path(added["abs"]), "work.txt", f"[{task}] work")
        self.git(self.src, "merge", "--no-ff", "-m", f"[{task}] merge", added["branch"])
        (self.dirs["active"] / f"{task}.md").write_text("---\nid: 1\n---\n", encoding="utf-8")
        return added

    def test_a_merge_nobody_confirmed_keeps_its_tree_and_its_branch_and_names_the_tree(self):
        added = self.merged()
        out = self.stop()
        self.assertEqual(RECONCILE, kind_of(out))
        self.assertTrue(Path(added["abs"]).is_dir())
        self.assertEqual(added["branch"], self.git(self.src, "branch", "--list", added["branch"]
                                                   ).lstrip("*+ ").strip())
        self.assertIn(f"worktree still in place: {TASK} (src-wt/{TASK})", out["reason"])
        self.assertIn("worktree.py --remove --task <task>", out["reason"])
        self.assertEqual("done", self.row()["stage"])

    def test_a_dirty_tree_is_untouched(self):
        added = self.merged()
        (Path(added["abs"]) / "scratch.txt").write_text("unsaved", encoding="utf-8")
        out = self.stop()
        self.assertEqual(RECONCILE, kind_of(out))
        self.assertTrue((Path(added["abs"]) / "scratch.txt").is_file())
        self.assertIn(f"worktree still in place: {TASK}", out["reason"])
        self.assertEqual("done", self.row()["stage"])

    def test_the_naming_follows_the_recorded_tree_not_the_flag(self):
        for flag in ("off", "absent"):
            with self.subTest(flag):
                self.sql("DELETE FROM runs")
                self.set_flag("on3")
                task = THIRD if flag == "off" else NEXT
                added = self.merged(task=task)
                self.set_flag(flag)
                out = self.stop()
                self.assertEqual(RECONCILE, kind_of(out))
                self.assertIn(f"worktree still in place: {task}", out["reason"])
                self.assertTrue(Path(added["abs"]).is_dir())

    def test_a_tree_removed_by_hand_is_not_named(self):
        added = self.merged()
        self.git(self.src, "worktree", "remove", added["abs"])
        out = self.stop()
        self.assertEqual(RECONCILE, kind_of(out))
        self.assertNotIn("worktree", out["reason"])

    def test_a_legacy_merge_reconciles_with_nothing_to_name(self):
        self.legacy(TASK, "done", awaiting_human="merge")
        self.commit(self.src, "work.txt", f"[{TASK}] work")
        (self.dirs["active"] / f"{TASK}.md").write_text("---\nid: 1\n---\n", encoding="utf-8")
        out = self.stop()
        self.assertEqual(RECONCILE, kind_of(out))
        self.assertNotIn("worktree", out["reason"])

    def test_the_left_list_is_empty_again_on_the_next_stop(self):
        self.merged()
        self.stop()
        self.assertEqual([], stop_gate._CTX["left"])

    def test_the_next_idle_stop_reports_the_leftover_tree_in_one_line(self):
        self.merged()
        self.assertEqual(RECONCILE, self.kind())
        out = self.stop()
        self.assertEqual(RELEASE, kind_of(out))
        self.assertEqual(1, len(out["systemMessage"].splitlines()), out["systemMessage"])
        self.assertIn(f"src-wt/{TASK} (run done)", out["systemMessage"])

    def test_only_advance_removes_a_tree_and_the_hook_modules_never_do(self):
        self.assertIn("worktree.remove(", inspect.getsource(advance))
        for module in (stop_gate, approve, supervisor, diff_checkpoint):
            source = inspect.getsource(module)
            self.assertNotIn("rmtree", source, module.__name__)
            self.assertNotIn("worktree.remove(", source, module.__name__)
            self.assertNotIn('"worktree", "remove"', source, module.__name__)


class ResumeTest(StopCase):
    """F7: resume checks the slots."""

    def resume(self, task):
        conn = state.connect()
        try:
            return approve.resume_run(conn, state.get_run(conn, task))
        finally:
            conn.close()

    def test_a_full_slot_count_refuses_and_leaves_the_run_paused(self):
        self.tree(TASK, **PAUSED)
        self.tree(OTHER)
        self.tree(THIRD)
        message, error = self.resume(TASK)
        self.assertEqual("", message)
        self.assertIn(f"all 2 task slots are in use ({OTHER}, {THIRD})", error)
        self.assertEqual(state.ST_PAUSED, self.row(TASK)["stage_status"])

    def test_a_parked_run_holds_a_slot_against_a_resume(self):
        self.tree(TASK, **PAUSED)
        self.tree(OTHER, **self.parked(OTHER))
        self.tree(THIRD, **self.parked(THIRD))
        self.assertIn("task slots are in use", self.resume(TASK)[1])

    def test_a_paused_neighbour_gives_a_slot_back_so_the_resume_goes_through(self):
        self.tree(TASK, **PAUSED)
        self.tree(OTHER)
        self.tree(THIRD, **PAUSED)
        message, error = self.resume(TASK)
        self.assertEqual("", error)
        self.assertIn(f"{TASK} resumed", message)
        self.assertEqual(state.ST_IN_PROGRESS, self.row(TASK)["stage_status"])

    def test_the_refusal_is_the_old_one_when_there_is_one_slot(self):
        self.set_flag("on1")
        self.tree(TASK, **PAUSED)
        self.legacy(OTHER)
        _, error = self.resume(TASK)
        self.assertIn(f"{OTHER} holds an editing stage", error)

    def test_an_umbrella_does_not_resume_beside_live_tree_runs(self):
        # Plan 2.12: an umbrella is exclusive in BOTH directions.
        self.legacy("task-0100", head=UMBRELLA_HEAD, **PAUSED)
        self.tree(TASK)
        message, error = self.resume("task-0100")
        self.assertEqual("", message)
        self.assertIn("task-0100 is an umbrella", error)
        self.assertEqual(state.ST_PAUSED, self.row("task-0100")["stage_status"])

    def test_an_umbrella_resumes_once_the_tree_runs_are_paused(self):
        self.legacy("task-0100", head=UMBRELLA_HEAD, **PAUSED)
        self.tree(TASK, **PAUSED)
        message, error = self.resume("task-0100")
        self.assertEqual("", error)
        self.assertIn("task-0100 resumed", message)

    def test_a_tree_run_does_not_resume_beside_a_live_umbrella(self):
        self.legacy("task-0100", head=UMBRELLA_HEAD)
        self.tree(TASK, **PAUSED)
        message, error = self.resume(TASK)
        self.assertEqual("", message)
        self.assertIn("task-0100 is a swarm umbrella", error)
        self.assertEqual(state.ST_PAUSED, self.row(TASK)["stage_status"])


class PauseTest(StopCase):
    """F8: a pause reads the task's tree and fails closed."""

    def setUp(self):
        super().setUp()
        self.added = self.tree(TASK)
        self.dir = Path(self.added["abs"])

    def conn_pause(self):
        conn = state.connect()
        try:
            return approve.pause_run(conn, state.get_run(conn, TASK), "urgent")
        finally:
            conn.close()

    def test_a_clean_tree_with_no_commits_pauses_as_clean(self):
        self.assertEqual((approve.PAUSE_CLEAN, ""), approve.assess_pause(TASK))

    def test_a_tree_whose_branch_carries_commits_pauses_as_committed(self):
        self.commit(self.dir, "work.txt", "work")
        self.assertEqual((approve.PAUSE_COMMITTED, ""), approve.assess_pause(TASK))

    def test_an_edit_in_the_tree_refuses_and_names_the_tree(self):
        (self.dir / "README.md").write_text("edit\n", encoding="utf-8")
        kind, problem = approve.assess_pause(TASK)
        self.assertEqual("", kind)
        self.assertIn("README.md", problem)
        self.assertIn(self.dir.name, problem)

    def test_an_edit_in_the_trunk_checkout_is_not_this_tasks_work(self):
        (self.src / "README.md").write_text("trunk\n", encoding="utf-8")
        self.assertEqual((approve.PAUSE_CLEAN, ""), approve.assess_pause(TASK))

    def test_an_untracked_file_in_the_tree_refuses(self):
        (self.dir / "new.txt").write_text("x\n", encoding="utf-8")
        self.assertIn("new.txt", approve.assess_pause(TASK)[1])

    def broken(self, how):
        if how == "missing":
            self.git(self.src, "worktree", "remove", "--force", str(self.dir))
        else:
            self.sql("UPDATE runs SET worktree = ? WHERE task = ?", "../outside", TASK)

    def test_a_missing_or_invalid_tree_refuses_without_asking_git_and_never_reads_src(self):
        (self.src / "README.md").write_text("trunk edit\n", encoding="utf-8")
        for how, word in (("missing", "MISSING"), ("invalid", "INVALID")):
            with self.subTest(how):
                if how == "invalid":
                    self.sql("UPDATE runs SET worktree = ? WHERE task = ?", "../outside", TASK)
                else:
                    self.broken("missing")
                with unittest.mock.patch.object(approve, "_git",
                                                side_effect=AssertionError("git was asked")):
                    kind, problem = approve.assess_pause(TASK)
                self.assertEqual("", kind)
                self.assertIn(word, problem)
                self.assertNotIn("README.md", problem)
                self.assertIn(f"worktree.py --repair --task {TASK}", problem)

    def test_pause_run_leaves_the_run_untouched_when_the_tree_is_missing(self):
        self.broken("missing")
        message, error = self.conn_pause()
        self.assertEqual("", message)
        self.assertIn("MISSING", error)
        self.assertEqual(state.ST_IN_PROGRESS, self.row()["stage_status"])

    def test_pause_run_pauses_a_clean_tree_run_and_frees_its_slot(self):
        self.assertEqual(1, worktree.slots_used())
        message, error = self.conn_pause()
        self.assertEqual("", error)
        self.assertIn("paused", message)
        self.assertEqual(0, worktree.slots_used())

    def test_a_legacy_run_is_still_judged_by_the_checkout_it_lives_in(self):
        self.sql("UPDATE runs SET worktree = '' WHERE task = ?", TASK)
        self.assertEqual((approve.PAUSE_CLEAN, ""), approve.assess_pause(TASK))
        (self.src / "README.md").write_text("edit\n", encoding="utf-8")
        self.assertIn("uncommitted work", approve.assess_pause(TASK)[1])


class DiffCheckpointTest(StopCase):
    """F8: the commit-checkpoint diff comes from the task's tree."""

    def setUp(self):
        super().setUp()
        self.added = self.tree(TASK, "ready", awaiting_human="commit")
        self.dir = Path(self.added["abs"])

    def test_a_tracked_edit_in_the_tree_is_in_the_diff_and_a_trunk_edit_is_not(self):
        (self.dir / "README.md").write_text("tree edit\n", encoding="utf-8")
        (self.src / "README.md").write_text("trunk edit\n", encoding="utf-8")
        diff = diff_checkpoint.capped_diff(TASK)
        self.assertIn("tree edit", diff)
        self.assertNotIn("trunk edit", diff)

    def test_a_missing_tree_gives_one_line_naming_the_repair_not_the_trunk(self):
        (self.src / "README.md").write_text("trunk edit\n", encoding="utf-8")
        self.git(self.src, "worktree", "remove", "--force", str(self.dir))
        for text in (diff_checkpoint.capped_diff(TASK), diff_checkpoint.changed_files(TASK)):
            self.assertEqual(
                f"task worktree MISSING: no changes can be shown. Repair it: python "
                f".claude/tools/pipeline/worktree.py --repair --task {TASK}", text)

    def test_an_invalid_record_gives_the_same_note_and_runs_no_git(self):
        self.sql("UPDATE runs SET worktree = ? WHERE task = ?", "src-wt/task-9999", TASK)
        with unittest.mock.patch.object(diff_checkpoint.git_state, "_git",
                                        side_effect=AssertionError("git was asked")):
            text = diff_checkpoint.changed_files(TASK)
        self.assertTrue(text.startswith("task worktree INVALID: no changes can be shown"), text)

    def test_the_commit_park_text_carries_the_note_for_a_missing_tree(self):
        self.git(self.src, "worktree", "remove", "--force", str(self.dir))
        text = diff_checkpoint.deliver(TASK, inject=False)
        self.assertIn("task worktree MISSING", text)
        self.assertIn("--- diff ---", text)

    def test_a_legacy_run_is_read_where_it_always_was(self):
        self.sql("UPDATE runs SET worktree = '' WHERE task = ?", TASK)
        (self.src / "README.md").write_text("trunk edit\n", encoding="utf-8")
        self.assertIn("trunk edit", diff_checkpoint.capped_diff(TASK))


class SupervisorTest(StopCase):
    """F8 progress evidence and the relaunch prompt."""

    def setUp(self):
        super().setUp()
        self.added = self.tree(TASK)
        self.dir = Path(self.added["abs"])

    def touch(self, path: Path) -> None:
        path.write_text("x\n", encoding="utf-8")
        past = time.time() - 120
        os.utime(path, (past, past))

    def test_progress_comes_from_the_tree_and_not_from_the_trunk_checkout(self):
        self.touch(self.src / "trunk.txt")
        self.assertEqual((None, ""), supervisor.progress_at(TASK))
        self.touch(self.dir / "tree.txt")
        self.assertEqual("the working tree", supervisor.progress_at(TASK)[1])

    def test_a_missing_or_invalid_tree_is_no_evidence_even_with_edits_in_the_trunk(self):
        self.touch(self.src / "trunk.txt")
        self.git(self.src, "worktree", "remove", "--force", str(self.dir))
        self.assertEqual((None, ""), supervisor.progress_at(TASK))
        self.sql("UPDATE runs SET worktree = ? WHERE task = ?", "../outside", TASK)
        self.assertEqual((None, ""), supervisor.progress_at(TASK))

    def test_a_legacy_run_still_reads_the_checkout(self):
        self.sql("UPDATE runs SET worktree = '' WHERE task = ?", TASK)
        self.touch(self.src / "trunk.txt")
        self.assertEqual("the working tree", supervisor.progress_at(TASK)[1])

    def prompt(self):
        return supervisor.spawn_argv({}, TASK, "implement", "owner exited")[-1]

    def plain(self):
        return supervisor.RELAUNCH_PROMPT.format(task=TASK, stage="implement", why="owner exited")

    def test_the_relaunch_prompt_ends_with_the_dispatch_line_for_a_tree_task(self):
        self.assertEqual(f"{self.plain()} {worktree.dispatch_line(TASK)}", self.prompt())

    def test_the_relaunch_prompt_names_a_missing_tree_and_how_to_repair_it(self):
        self.git(self.src, "worktree", "remove", "--force", str(self.dir))
        self.assertTrue(self.prompt().endswith(
            f"Task worktree: MISSING for {TASK} - run python "
            f".claude/tools/pipeline/worktree.py --repair --task {TASK}; do not edit."))

    def test_the_relaunch_prompt_is_byte_identical_for_a_legacy_task(self):
        self.sql("UPDATE runs SET worktree = '' WHERE task = ?", TASK)
        self.assertEqual(self.plain(), self.prompt())


class DispatchLineTest(StopCase):
    """2.10: the line is in every nag, byte-stable, and the backstop still fires."""

    def reason(self, active=False) -> str:
        return self.stop(active)["reason"]

    def test_every_run_nag_ends_with_the_real_line(self):
        added = self.tree(TASK)
        line = worktree.dispatch_line(TASK)
        self.assertTrue(line.startswith(f"Worktree: {Path(added['abs']).as_posix()} "))
        cases = (
            ("drive", dict(stage="implement")),
            ("gate failed", dict(stage="test", stage_status=state.ST_GATE_FAILED)),
            ("commit approved", dict(stage="ready", awaiting_human="commit", commit_approved=1)))
        for label, fields in cases:
            with self.subTest(label):
                stage = fields.pop("stage")
                self.sql("UPDATE runs SET stage = ? WHERE task = ?", stage, TASK)
                self.set_fields(TASK, **fields)
                self.assertTrue(self.reason().endswith(f" {line}"), label)

    def set_fields(self, task, **fields):
        conn = state.connect()
        try:
            state.set_fields(conn, task, **fields)
        finally:
            conn.close()

    def test_a_legacy_run_gets_the_nag_it_always_got(self):
        self.legacy(TASK)
        self.assertEqual(
            f"{TASK} is at stage 'implement'. Finish the stage work (dispatch the owning "
            f"subagent if needed), then run: python .claude/tools/pipeline/advance.py "
            f"--task {TASK}. Do not ask the user whether to continue.", self.reason())

    def test_the_nag_is_byte_stable_across_stops_time_and_continuation_counts(self):
        self.tree(TASK)
        first = self.reason()
        time.sleep(1.1)
        second = self.reason()
        self.assertEqual(first, second)
        self.assertGreaterEqual(self.row()["continuations"], 2)

    def test_the_nag_changes_when_the_tree_does(self):
        added = self.tree(TASK)
        before = self.reason()
        self.git(self.src, "worktree", "remove", "--force", added["abs"])
        after = self.reason()
        self.assertNotEqual(before, after)
        self.assertIn(f"Task worktree: MISSING for {TASK}", after)

    def test_a_missing_tree_nag_says_to_repair_it_and_not_to_edit(self):
        added = self.tree(TASK)
        self.git(self.src, "worktree", "remove", "--force", added["abs"])
        reason = self.reason()
        self.assertIn(f"worktree.py --repair --task {TASK}; do not edit.", reason)

    def test_the_reason_repeat_backstop_parks_a_tree_run_after_the_ceiling(self):
        self.tree(TASK)
        kinds = [kind_of(self.stop(active=i > 0)) for i in range(6)]
        self.assertEqual([NAG, NAG, NAG, RELEASE, SURFACE, SILENT], kinds)
        row = self.row()
        self.assertEqual(state.ST_BLOCKED, row["stage_status"])
        self.assertEqual("blocked", row["awaiting_human"])

    def test_the_backstop_counts_the_same_for_a_tree_run_and_a_legacy_run(self):
        added = self.tree(TASK)
        tree_kinds = [kind_of(self.stop(active=i > 0)) for i in range(6)]
        self.sql("DELETE FROM runs")
        # The tree outlives its run row here, which the idle hook would (rightly) report.
        self.git(self.src, "worktree", "remove", "--force", added["abs"])
        (self.state_file()).unlink(missing_ok=True)
        self.legacy(TASK)
        legacy_kinds = [kind_of(self.stop(active=i > 0)) for i in range(6)]
        self.assertEqual(legacy_kinds, tree_kinds)

    def state_file(self) -> Path:
        return state.STATE_DIR / stop_gate.STOP_REPEAT_FILE


class ResumeTextTest(StopCase):
    """The session-start summary names the tree (2.10)."""

    def text(self) -> str:
        conn = state.connect()
        try:
            return state._resume_text(conn)
        finally:
            conn.close()

    def test_a_tree_run_line_ends_with_the_dispatch_line(self):
        self.tree(TASK)
        self.assertIn(f"  - {TASK}: stage=implement status=in_progress "
                      f"{worktree.dispatch_line(TASK)}\n", self.text() + "\n")

    def test_a_legacy_run_line_is_the_old_one(self):
        self.legacy(TASK)
        self.assertIn(f"  - {TASK}: stage=implement status=in_progress\n", self.text() + "\n")

    def test_a_missing_tree_says_so_in_the_summary(self):
        added = self.tree(TASK)
        self.git(self.src, "worktree", "remove", "--force", added["abs"])
        self.assertIn(f"Task worktree: MISSING for {TASK}", self.text())


class HookBudgetTest(StopCase):
    """The Stop hook runs with a time budget (task-0124): the new paths run no subprocess."""

    def no_subprocess(self):
        return unittest.mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess"))

    def test_starting_the_next_task_beside_a_parked_tree_run_runs_no_subprocess(self):
        self.tree(TASK, **self.parked(TASK))
        self.queue(NEXT)
        with self.no_subprocess():
            self.assertEqual(START, self.kind())

    def test_nagging_a_tree_run_with_its_line_runs_no_subprocess(self):
        self.tree(TASK)
        with self.no_subprocess():
            self.assertEqual(NAG, self.kind())

    def test_a_full_slot_count_and_a_busy_run_run_no_subprocess(self):
        self.tree(TASK)
        self.tree(OTHER)
        self.mark(TASK)
        self.mark(OTHER)
        self.queue(NEXT)
        with self.no_subprocess():
            self.assertEqual(SILENT, self.kind())


class FailClosedAndEdgesTest(StopCase):
    """Branches the tables above cannot reach: unreadable inputs and unusual stages."""

    def test_the_sandbox_workspace_lives_inside_the_projects_run_root(self):
        self.assertTrue(self.root.is_relative_to(tmproot.RUN_ROOT.resolve()), self.root)

    def test_an_unreadable_slot_count_starts_nothing_even_with_a_free_slot(self):
        self.tree(TASK, **self.parked(TASK))
        self.queue(NEXT)
        with unittest.mock.patch.object(worktree, "slots_used", side_effect=OSError("locked")):
            self.assertEqual(RELEASE, self.kind())

    def test_an_unreadable_slot_count_refuses_a_resume(self):
        self.tree(TASK, **PAUSED)
        with unittest.mock.patch.object(worktree, "slots_used", side_effect=OSError("locked")):
            conn = state.connect()
            try:
                _, error = approve.resume_run(conn, state.get_run(conn, TASK))
            finally:
                conn.close()
        self.assertIn("task slots are in use", error)

    def test_a_nag_for_a_stage_the_flow_does_not_list_still_names_the_tree(self):
        self.config("on", **TRIAGE)
        self.tree(TASK, "triage")
        reason = self.stop()["reason"]
        self.assertIn("editing_stages does not list", reason)
        self.assertTrue(reason.endswith(f" {worktree.dispatch_line(TASK)}"), reason)

    def test_a_run_parked_blocked_on_this_very_stop_still_holds_the_checkout(self):
        # A stage no list names as work: only the blocked clause of holds_tree keeps the
        # queue from starting a task on top of the run the ceiling has just parked.
        self.config("on", **TRIAGE)
        self.legacy(TASK, "triage", continuations=30)
        self.queue(NEXT)
        self.assertNotEqual(START, self.kind())
        self.assertEqual(state.ST_BLOCKED, self.row()["stage_status"])

    def test_the_line_is_separated_from_the_nag_by_one_space(self):
        self.tree(TASK)
        reason = self.stop()["reason"]
        self.assertIn("whether to continue. Worktree: ", reason)

    def test_a_line_reader_that_raises_leaves_the_nag_exactly_the_legacy_text(self):
        self.tree(TASK)
        with unittest.mock.patch.object(worktree, "dispatch_line", side_effect=RuntimeError):
            reason = self.stop()["reason"]
        self.assertEqual(
            f"{TASK} is at stage 'implement'. Finish the stage work (dispatch the owning "
            f"subagent if needed), then run: python .claude/tools/pipeline/advance.py "
            f"--task {TASK}. Do not ask the user whether to continue.", reason)

    def test_a_legacy_run_does_not_resume_beside_another_legacy_editing_run_with_slots_on(self):
        self.legacy(TASK, **PAUSED)
        self.legacy(OTHER)
        conn = state.connect()
        try:
            _, error = approve.resume_run(conn, state.get_run(conn, TASK))
        finally:
            conn.close()
        self.assertIn(f"{OTHER} holds an editing stage", error)

    def test_the_reconciler_forgets_a_left_tree_once_it_has_been_reported(self):
        added = self.tree(TASK, "done", awaiting_human="merge")
        self.commit(Path(added["abs"]), "work.txt", f"[{TASK}] work")
        self.git(self.src, "merge", "--no-ff", "-m", f"[{TASK}] merge", added["branch"])
        (self.dirs["active"] / f"{TASK}.md").write_text("---\nid: 1\n---\n", encoding="utf-8")
        conn = state.connect()
        try:
            stop_gate.reconcile_status_drift(conn)
            self.assertEqual(1, len(stop_gate._CTX["left"]))
            self.addCleanup(stop_gate._CTX.update, left=[])
            stop_gate.reconcile_status_drift(conn)
            self.assertEqual([], stop_gate._CTX["left"])
        finally:
            conn.close()

    def test_a_run_closed_because_its_file_left_active_has_its_tree_named_too(self):
        added = self.tree(TASK, "ready", commit_approved=1)
        self.commit(Path(added["abs"]), "work.txt", f"[{TASK}] work")
        self.git(self.src, "merge", "--no-ff", "-m", f"[{TASK}] merge", added["branch"])
        out = self.stop()
        self.assertEqual(RECONCILE, kind_of(out), out)
        self.assertIn("closed stale run(s)", out["reason"])
        self.assertIn(f"worktree still in place: {TASK}", out["reason"])
        self.assertTrue(Path(added["abs"]).is_dir())
        self.assertEqual("done", self.row()["stage"])


class ForceMajeureTest(RegistrationCase):
    """2.8: pause frees a slot, the urgent task registers in its own tree, resume waits."""

    def setUp(self):
        super().setUp()
        patch_stop_gate(self, self)

    def stop(self) -> dict | None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            stop_gate.decide()
        text = out.getvalue().strip()
        return json.loads(text) if text else None

    def resume(self, task):
        conn = state.connect()
        try:
            return approve.resume_run(conn, state.get_run(conn, task))
        finally:
            conn.close()

    def pause(self, task):
        conn = state.connect()
        try:
            return approve.pause_run(conn, state.get_run(conn, task), "urgent fix")
        finally:
            conn.close()

    def test_the_urgent_task_takes_the_freed_slot_in_its_own_tree_and_resume_waits_for_one(self):
        self.task_file(TASK)
        self.task_file(OTHER)
        self.advance(TASK)
        self.advance(OTHER)
        self.assertEqual(2, worktree.slots_used())

        message, error = self.pause(TASK)
        self.assertEqual("", error, message)
        self.assertEqual(1, worktree.slots_used())

        self.task_file(THIRD, "priority: urgent\n")
        busy.acquire(OTHER, "implement")
        self.addCleanup(busy.release, OTHER)
        out = self.stop()
        self.assertEqual(START, kind_of(out))
        self.assertIn(f"Start the next ready task {THIRD}", out["reason"])
        self.assertIn("advance.py creates its branch and its worktree", out["reason"])

        started = self.advance(THIRD)
        self.assertEqual("started", started["action"])
        self.assertTrue((self.tree(THIRD) / ".git").is_file())
        self.assertEqual(f"src-wt/{THIRD}", self.row(THIRD)["worktree"])
        self.assertEqual(2, worktree.slots_used())
        self.assertEqual("main", self.git(self.src, "rev-parse", "--abbrev-ref", "HEAD"))

        message, error = self.resume(TASK)
        self.assertEqual("", message)
        self.assertIn(f"all 2 task slots are in use ({OTHER}, {THIRD})", error)
        self.assertEqual(state.ST_PAUSED, self.row(TASK)["stage_status"])

        message, error = self.pause(THIRD)
        self.assertEqual("", error, message)
        message, error = self.resume(TASK)
        self.assertEqual("", error)
        self.assertIn(f"{TASK} resumed", message)
        self.assertTrue(self.tree(TASK).is_dir(), "a paused task keeps its tree")


class ImportSafetyTest(unittest.TestCase):
    """The conveyor modules import worktree at top level: it must be deployed first."""

    def test_the_four_conveyor_modules_import_worktree_without_a_cycle(self):
        code = (f"import sys; sys.path.insert(0, {str(PIPELINE_DIR)!r}); "
                "import worktree, stop_gate, approve, supervisor, diff_checkpoint")
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stderr)


if __name__ == "__main__":
    unittest.main()
