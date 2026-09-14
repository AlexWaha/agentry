"""Four conveyor gaps found by running the harness on itself.

Each test pins one defect that cost a round trip every turn of a real session:

1. a run parked on a human (checkpoint, or a finished task waiting for the CEO
   to merge) let stop_gate.py offer the next backlog task, which would switch
   the branch out from under a human reading the diff;
2. nothing wrote the busy marker when a subagent was dispatched, so the hook
   nagged to advance a stage whose agent was still working;
3. a BLOCKED run was excluded from the free-slot calculation, so a new task
   could start on top of its dirty working tree;
4. `done` was reached on the ABSENCE of merge evidence - "no branch found in
   any repo" was read as "nothing to merge" rather than "cannot tell";
5. the backlog reader read only `depends_on`, so `superseded_by:` and
   `blocked_on:` were decorative - the queue kept offering a task whose work had
   been folded into another one, and a dependency on such a task could never be
   satisfied because a superseded task never reaches done/;
6. a run sitting on a stage the config does not define reported success and died
   quietly: no exit_gate means "configured and passed", so advance.py wrote
   stage=NULL under an "advanced" message, and NULL is in neither the editing
   set nor awaiting_human - so the Stop hook read a free slot and offered the
   next task, which is defects 1 and 3 back through another door;
7. the handoff-debt and memory-debt checks were read INSIDE that same free-slot
   branch, so any task parked on the CEO - the normal state of a pipeline with a
   human in it - switched off the only machine that reports undocumented work.
   Two done tasks went undocumented with `handoff.py --check` reporting 2 and
   nothing objecting, an hour after defect 3's fix gave a blocked run the
   `awaiting_human` marker that the branch reads.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
import unittest.mock
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
MEMORY_DIR = Path(__file__).resolve().parents[1] / "memory"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(MEMORY_DIR))
sys.path.insert(0, str(TESTS_DIR))

import advance
import approve
import git_state
import handoff
import state
import stop_gate
import tmproot
import update as memory_update

# The stock build flow, as a literal: these tests must keep asserting against a
# known stage list even when .agentry/pipeline.json is edited.
BUILD_PIPELINE = {
    "retry_budget": 3,
    "pipelines": {"build": {"stages": [
        {"name": "implement", "owner": "dev"},
        {"name": "test", "owner": "qa-engineer"},
        {"name": "review", "owner": "reviewer"},
        {"name": "ready", "owner": "orchestrator", "checkpoints": ["commit", "push"]},
        {"name": "done", "owner": "orchestrator"},
    ]}},
}


def run(task="task-0001", stage="implement", status=state.ST_IN_PROGRESS,
        awaiting_human="", **extra) -> dict:
    row = {
        "task": task, "type": "feature", "stage": stage, "stage_status": status,
        "awaiting_human": awaiting_human, "commit_approved": 0, "push_approved": 0,
        "retries": 0, "continuations": 0, "pipeline": state.BUILD,
    }
    row.update(extra)
    return row


class _FakeConn:
    def close(self):
        pass


def decide_with(runs: list[dict], backlog=("task-0002",), pipeline=None,
                set_fields=None, debt=(), mem_debt=(), count_nags=False) -> str | None:
    """stop_gate.decide() over a synthetic run set. Returns the block reason, or
    None when the hook allowed the stop. No DB, no git, no task files.

    busy_marker_fresh is mocked too: it reads the real .agentry/state/, so a live
    gate marker for the task id used here (written whenever the orchestrator
    dispatches a subagent for it) silenced the hook and failed these tests for
    an environmental reason. BusyMarkerTest covers that function directly.

    debt / mem_debt default to none, so every pre-existing assertion here is
    about a pipeline with its documentation paid up.

    count_nags is off by default, which is deliberate and not laziness. The real
    counter persists in a file under state.STATE_DIR: left live it would make
    every call here mutate the project's own state, and - measured, not guessed -
    it made two cases in DocumentationDebtTest fail because earlier subtests had
    already spent the budget for the same debt key. Off, `debt_nags` returns 1,
    so each call behaves as a first stop. DebtNagBoundTest turns it on with
    STATE_DIR pointed at a sandbox, and owns the bound."""
    queue = [{"id": t, "deps": []} for t in backlog]
    buf = io.StringIO()
    nag_patches = () if count_nags else (
        unittest.mock.patch.object(stop_gate, "debt_nags", return_value=1),
        unittest.mock.patch.object(stop_gate, "clear_debt_nags"))
    for p in nag_patches:
        p.start()
    with unittest.mock.patch.object(stop_gate.mode, "conveyor_runs", return_value=True), \
            unittest.mock.patch.object(stop_gate.state, "connect", return_value=_FakeConn()), \
            unittest.mock.patch.object(stop_gate.state, "all_runs", return_value=runs), \
            unittest.mock.patch.object(stop_gate.state, "load_pipeline",
                                       return_value=pipeline or {}), \
            unittest.mock.patch.object(stop_gate.state, "set_fields",
                                       set_fields or unittest.mock.MagicMock()), \
            unittest.mock.patch.object(stop_gate, "read_backlog", return_value=queue), \
            unittest.mock.patch.object(stop_gate, "handoff_debt", return_value=list(debt)), \
            unittest.mock.patch.object(stop_gate, "memory_debt", return_value=list(mem_debt)), \
            unittest.mock.patch.object(stop_gate, "latest_undocumented", return_value=None), \
            unittest.mock.patch.object(stop_gate, "reconcile_status_drift", return_value=([], [])), \
            unittest.mock.patch.object(stop_gate.approvals, "granted", return_value=True), \
            unittest.mock.patch.object(stop_gate, "busy_marker_fresh", return_value=False), \
            redirect_stdout(buf):
        stop_gate.decide()
    for p in nag_patches:
        p.stop()
    out = buf.getvalue().strip()
    return json.loads(out)["reason"] if out else None


class FreeSlotTest(unittest.TestCase):
    """Defects 1 and 3: what counts as an occupied working tree."""

    def test_empty_run_set_does_offer_the_next_task(self):
        # Control: without it the other assertions could pass vacuously.
        reason = decide_with([])
        self.assertIsNotNone(reason)
        self.assertIn("Start the next ready task task-0002", reason)

    def test_run_parked_at_a_checkpoint_offers_nothing(self):
        # Two checkpoints remain on the build flow: the commit and the push.
        for awaiting in ("commit", "push"):
            with self.subTest(awaiting_human=awaiting):
                self.assertIsNone(decide_with([run(stage="ready", awaiting_human=awaiting)]))

    def test_finished_run_waiting_for_the_ceo_merge_offers_nothing(self):
        # The reported case: task-0001 sat at 'done' waiting to be merged while
        # the hook told the orchestrator to start task-0003, then task-0002.
        self.assertIsNone(decide_with([run(stage="done", awaiting_human="merge")]))

    def test_blocked_run_offers_nothing_and_is_surfaced_once(self):
        # A blocked run never frees the slot - that is this test's original
        # guarantee and it is unchanged. What changed in task-0067: it is also
        # SURFACED on the first stop instead of being skipped in silence, which
        # is how a blocked run went ninety minutes with neither watchdog saying
        # a word. The marker that makes it once rather than a stop loop is
        # `awaiting_human`, set on that first stop.
        reason = decide_with([run(stage="implement", status=state.ST_BLOCKED)])
        self.assertIsNotNone(reason)
        self.assertIn("task-0001 is parked BLOCKED", reason)
        self.assertIn("--reject", reason)
        self.assertNotIn("Start the next ready task", reason)

        # Second stop, with the marker the first one wrote: silent, and still no
        # new task offered.
        self.assertIsNone(decide_with([run(stage="implement", status=state.ST_BLOCKED,
                                           awaiting_human=stop_gate.AWAITING_BLOCKED)]))

    def test_surfacing_a_blocked_run_records_the_marker_that_bounds_it(self):
        # Without the write, the surfacing above would repeat on every stop and
        # the session could never end: the marker is the whole difference
        # between raising it once and a stop loop.
        with unittest.mock.patch.object(stop_gate.state, "set_fields") as set_fields:
            decide_with([run(stage="implement", status=state.ST_BLOCKED)],
                        set_fields=set_fields)
        self.assertEqual(1, set_fields.call_count)
        self.assertEqual(stop_gate.AWAITING_BLOCKED,
                         set_fields.call_args.kwargs["awaiting_human"])

    def test_editing_run_is_still_driven_not_replaced(self):
        # Unchanged behaviour guard: an in-flight editing stage is continued.
        reason = decide_with([run(stage="implement")])
        self.assertIn("task-0001 is at stage 'implement'", reason)


class DocumentationDebtTest(unittest.TestCase):
    """Defect 7: the handoff-debt and memory-debt checks were hung off the
    free-slot calculation, so anything parked on a human switched them off.

    Measured an hour after task-0067 merged, with task-0010 parked blocked and
    two done tasks undocumented: `handoff.py --check` reported 2, and the Stop
    hook allowed the stop without a word. The condition that disabled the check
    was "some task is waiting for the CEO", which is the normal state of a
    pipeline with a human in it.

    A completed task's documentation is not less owed because another task is
    parked, so neither check reads the free slot any more."""

    HANDOFF = ({"task": "task-0067",
                "reason": "no handoff doc at .agentry/tasks/handoffs/task-0067.md"},)
    MEMORY = ({"task": "task-0067",
               "reason": "memory layers not reviewed after completion"},)

    def test_handoff_debt_is_reported_while_a_blocked_run_holds_the_slot(self):
        # The live case, exactly: a blocked run already surfaced (so step 1 is
        # silent about it) plus outstanding handoff debt. Before the fix this
        # returned None - the hook allowed the stop and nothing objected.
        reason = decide_with([run(stage="implement", status=state.ST_BLOCKED,
                                  awaiting_human=stop_gate.AWAITING_BLOCKED)],
                             debt=self.HANDOFF)
        self.assertIsNotNone(reason)
        self.assertIn("task-0067", reason)
        self.assertIn("handoff", reason.lower())

    def test_handoff_debt_is_reported_with_an_empty_backlog(self):
        # The debt is owed by a COMPLETED task, so it does not depend on there
        # being a next task to gate. An empty queue used to mean the debt
        # branch was never entered even with the slot free.
        reason = decide_with([], backlog=(), debt=self.HANDOFF)
        self.assertIsNotNone(reason)
        self.assertIn("task-0067", reason)
        self.assertIn("handoff", reason.lower())

    def test_handoff_debt_is_reported_while_the_ceo_reads_a_diff(self):
        for awaiting in ("commit", "push", "merge"):
            with self.subTest(awaiting_human=awaiting):
                reason = decide_with([run(stage="ready", awaiting_human=awaiting)],
                                     debt=self.HANDOFF)
                self.assertIsNotNone(reason)
                self.assertIn("task-0067", reason)

    def test_memory_debt_is_reported_while_a_blocked_run_holds_the_slot(self):
        reason = decide_with([run(stage="implement", status=state.ST_BLOCKED,
                                  awaiting_human=stop_gate.AWAITING_BLOCKED)],
                             debt=(), mem_debt=self.MEMORY)
        self.assertIsNotNone(reason)
        self.assertIn("task-0067", reason)
        self.assertIn("memory", reason.lower())

    def test_the_debt_message_names_the_command_that_clears_it(self):
        # A gate that refuses without saying how to satisfy it costs a round
        # trip every time (task-0066's criterion, applied here too).
        reason = decide_with([run(stage="ready", awaiting_human="commit")],
                             debt=self.HANDOFF)
        self.assertIn("handoff.py --for task-0067", reason)
        reason = decide_with([run(stage="ready", awaiting_human="commit")],
                             mem_debt=self.MEMORY)
        self.assertIn("update.py --stamp --task task-0067", reason)

    def test_handoff_debt_is_reported_before_memory_debt(self):
        # A doc must exist before it can be distilled, so the order is not
        # cosmetic: reporting the memory debt first would ask for a
        # distillation of a document nobody has written.
        reason = decide_with([run(stage="ready", awaiting_human="commit")],
                             debt=self.HANDOFF, mem_debt=self.MEMORY)
        self.assertIn("handoff", reason.lower())
        self.assertNotIn("--stamp", reason)

    def test_driving_an_in_flight_stage_still_outranks_the_debt(self):
        # Precedence guard: the debt report is the last thing before idling, not
        # a new way to interrupt a stage that is actually being advanced.
        reason = decide_with([run(stage="implement")], debt=self.HANDOFF)
        self.assertIn("task-0001 is at stage 'implement'", reason)

    def test_surfacing_a_blocked_run_still_outranks_the_debt(self):
        # Same precedence, for the run nobody is driving: the CEO hears about
        # the blocker first, and the debt on the stop after it.
        reason = decide_with([run(stage="implement", status=state.ST_BLOCKED)],
                             debt=self.HANDOFF)
        self.assertIn("task-0001 is parked BLOCKED", reason)

    def test_starting_a_task_still_gates_on_the_debt_first(self):
        # Unchanged behaviour guard for the free-slot path: when there IS a
        # ready task, the debt is still framed as the thing to do before it.
        reason = decide_with([], debt=self.HANDOFF)
        self.assertIn("Before starting task-0002", reason)
        self.assertIn("task-0067", reason)

    def test_no_debt_and_nothing_to_drive_still_allows_the_stop(self):
        # The control. Without it every assertion above could pass on a hook
        # that blocks unconditionally, which would be a stop loop.
        self.assertIsNone(decide_with([run(stage="ready", awaiting_human="commit")]))
        self.assertIsNone(decide_with([], backlog=()))


class DebtNagBoundTest(unittest.TestCase):
    """The debt block repeats, and repeating has to end somewhere.

    The reasoning that lets it repeat at all is that the agent can clear the
    debt, which assumes the demand is satisfiable. A handoff doc that keeps
    failing `min_section_chars`, or a store that keeps refusing the stamp, makes
    it unsatisfiable - and then an unbounded block is the stop loop the module
    docstring promises never to create. Every other branch in decide() bounds
    itself; this one now does too.

    Both properties are pinned here, because a fix for either one alone is a
    defect: the debt must still be raised, and the raising must terminate."""

    DEBT = ({"task": "task-0067", "reason": "section 'Key decisions' is too thin"},)
    MEM = ({"task": "task-0067", "reason": "memory layers not reviewed"},)

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "debtnag")
        p = unittest.mock.patch.object(state, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def stop(self, **kw) -> str | None:
        """One stop event with a run parked on the CEO and the REAL counter."""
        return decide_with([run(stage="ready", awaiting_human="commit")],
                           count_nags=True, **kw)

    def test_the_debt_is_raised_up_to_the_ceiling_then_escalated_then_silent(self):
        for n in range(1, stop_gate.DEBT_NAG_CEILING + 1):
            with self.subTest(stop=n):
                reason = self.stop(debt=self.DEBT)
                self.assertIn("has no valid handoff doc", reason)
                self.assertNotIn("AskUserQuestion", reason)

        # One escalation, naming the card and saying it will not repeat.
        reason = self.stop(debt=self.DEBT)
        self.assertIn("AskUserQuestion", reason)
        self.assertIn("task-0067", reason)
        self.assertIn(f"survived {stop_gate.DEBT_NAG_CEILING} stops", reason)

        # And then the session can actually end, which is the whole point.
        for n in range(3):
            with self.subTest(after_escalation=n):
                self.assertIsNone(self.stop(debt=self.DEBT))

    def test_the_count_survives_the_process(self):
        # Each stop is a fresh interpreter, so a counter in memory would reset
        # every time and bound nothing. The file is the mechanism.
        self.stop(debt=self.DEBT)
        self.assertEqual(
            {"key": "handoff:task-0067", "count": 1},
            json.loads((self.tmp / stop_gate.DEBT_NAG_FILE).read_text(encoding="utf-8")))

    def test_a_different_debt_starts_its_own_budget(self):
        for _ in range(stop_gate.DEBT_NAG_CEILING + 2):
            self.stop(debt=self.DEBT)
        # Progress on the handoff half is not a spent budget for the memory half.
        reason = self.stop(mem_debt=self.MEM)
        self.assertIn("has not been distilled", reason)
        self.assertNotIn("AskUserQuestion", reason)

    def test_paying_the_debt_restores_the_full_budget(self):
        for _ in range(stop_gate.DEBT_NAG_CEILING + 2):
            self.stop(debt=self.DEBT)
        self.assertIsNone(self.stop())                     # debt paid, count cleared
        self.assertFalse((self.tmp / stop_gate.DEBT_NAG_FILE).exists())
        reason = self.stop(debt=self.DEBT)                 # a NEW debt, later
        self.assertIn("has no valid handoff doc", reason)

    def test_a_corrupt_counter_file_heals_instead_of_pinning_the_count_at_one(self):
        # A process killed mid-write leaves a file json.loads cannot parse. While
        # that share the function's outer handler, every call returned 1 and none
        # of them rewrote the file, so the nag was unbounded again with no
        # escalation - the failure the counter was added to remove. The count
        # must climb from garbage, not sit at 1.
        for junk in ("{not json", "", "[1, 2, 3]", "42"):
            with self.subTest(junk=junk):
                (self.tmp / stop_gate.DEBT_NAG_FILE).write_text(junk, encoding="utf-8")
                self.assertEqual([1, 2, 3], [stop_gate.debt_nags("handoff:task-0067")
                                             for _ in range(3)])

    def test_a_corrupt_counter_file_still_escalates_through_decide(self):
        # The same property at the level that matters: the bound holds end to end.
        (self.tmp / stop_gate.DEBT_NAG_FILE).write_text("{not json", encoding="utf-8")
        for n in range(stop_gate.DEBT_NAG_CEILING):
            with self.subTest(stop=n + 1):
                self.assertIn("has no valid handoff doc", self.stop(debt=self.DEBT))
        self.assertIn("AskUserQuestion", self.stop(debt=self.DEBT))
        self.assertIsNone(self.stop(debt=self.DEBT))

    def test_a_counter_that_cannot_persist_keeps_reporting_rather_than_going_quiet(self):
        # debt_nags() returns 1 on any internal error, which is what a state dir
        # it cannot write looks like. Then the bound is lost and the report is
        # not, which is the right way round: the silence is what task-0072 was
        # filed for. count_nags=False forces exactly that value.
        for n in range(5):
            with self.subTest(stop=n):
                self.assertIn("has no valid handoff doc",
                              decide_with([run(stage="ready", awaiting_human="commit")],
                                          debt=self.DEBT))


class BlockedRunSurfacingBoundTest(unittest.TestCase):
    """task-0067's bound, re-pinned because task-0072 changes the branch it
    rests on: a blocked run is surfaced ONCE and then left alone.

    The two properties have to hold together. Surfacing must stay bounded (an
    unbounded nag is a stop loop, the opposite failure), and the debt checks
    must no longer be silenced by the marker that bounds it. So the sequence
    below runs three consecutive stops over one blocked run."""

    DEBT = ({"task": "task-0067", "reason": "no handoff doc"},)

    def blocked(self, surfaced: bool) -> list[dict]:
        return [run(stage="implement", status=state.ST_BLOCKED,
                    awaiting_human=stop_gate.AWAITING_BLOCKED if surfaced else "")]

    def test_surfaced_once_then_silent_forever(self):
        first = decide_with(self.blocked(False))
        self.assertIn("task-0001 is parked BLOCKED", first)
        # Every later stop, with the marker the first one wrote: nothing. Ten
        # of them, because "once" is the whole point and a bound that holds for
        # one repeat is not a bound.
        for i in range(10):
            with self.subTest(stop=i + 2):
                self.assertIsNone(decide_with(self.blocked(True)))

    def test_surfaced_once_and_the_debt_is_still_reported_after(self):
        first = decide_with(self.blocked(False), debt=self.DEBT)
        self.assertIn("task-0001 is parked BLOCKED", first)
        self.assertNotIn("task-0067", first)

        # Stop 2 onwards: the blocker is not raised again, and the debt that
        # the marker used to hide is. Both properties, simultaneously.
        for i in range(3):
            with self.subTest(stop=i + 2):
                later = decide_with(self.blocked(True), debt=self.DEBT)
                self.assertNotIn("is parked BLOCKED", later)
                self.assertIn("task-0067", later)

        # And once the debt is paid the session is finally allowed to end, with
        # the blocked run still parked. That is the bound, intact.
        self.assertIsNone(decide_with(self.blocked(True)))


class MemoryStampRegistrationGateTest(unittest.TestCase):
    """task-0066: one sentence of rules/pipeline.md, enforced by half a gate.

    "The next registration is blocked until this task's handoff doc exists AND
    its memory review is stamped" - and advance.py contained the word `memory`
    zero times. The handoff half fired correctly every time, which is what made
    the other half's absence hard to see; task-0033 and task-0058 both ran the
    whole pipeline and closed unstamped with nothing objecting.

    The gate is also invisible while someone volunteers to do its job: the
    orchestrator stamps by hand out of habit, so for task-0009 the stamp existed
    and the missing gate made no difference."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "memgate")
        self.dirs = {}
        for name in ("backlog", "active", "done"):
            d = self.tmp / name
            d.mkdir()
            self.dirs[name] = d
        # The task that wants to register, and the completed task that owes the
        # stamp. task_dir() walks TASK_DIRS, so patching the map is enough.
        (self.dirs["active"] / "task-0100.md").write_text(
            "---\ntask: task-0100\nspec: none\n---\n\n"
            "## Acceptance Criteria\n\n- [ ] the gate fires\n", encoding="utf-8")
        (self.dirs["done"] / "task-0099.md").write_text(
            "---\ntask: task-0099\n---\n", encoding="utf-8")
        self.stamps = self.tmp / "stamps"
        self.stamps.mkdir()
        for target, attr, value in (
                (state, "TASK_DIRS", self.dirs),
                (memory_update, "DONE_DIR", self.dirs["done"]),
                (memory_update, "STAMP_DIR", self.stamps)):
            p = unittest.mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        # Forced on rather than read from the shipped pipeline.json: a test that
        # passes because of config is the way the handoff gate's twin sat
        # switched off for months (task-0067, forbid_dev_null).
        for target, name, value in (
                (memory_update, "cfg", {"enabled": True, "baseline": ""}),
                (handoff, "uncovered_done_tasks", [])):
            p = unittest.mock.patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_an_unstamped_completed_task_refuses_the_next_registration(self):
        refusal = advance.check_task_ready("task-0100")
        self.assertIn("task-0099", refusal)
        self.assertIn("memory", refusal.lower())

    def test_the_refusal_names_the_command_that_satisfies_it(self):
        # As the handoff gate's message does. A gate that refuses without saying
        # how to clear it costs a round trip every single time.
        refusal = advance.check_task_ready("task-0100")
        self.assertIn("memory.py --record", refusal)
        self.assertIn("update.py --stamp --task task-0099", refusal)
        self.assertIn("--none", refusal)

    def test_registration_proceeds_once_the_task_is_stamped(self):
        (self.stamps / "task-0099.json").write_text(
            '{"task": "task-0099", "stamped_at": "2026-09-14T00:00:00+00:00", '
            '"counts": {"lesson": 1}, "none": false}', encoding="utf-8")
        self.assertEqual("", advance.check_task_ready("task-0100"))

    def test_a_task_at_or_below_the_baseline_is_grandfathered(self):
        # Adopting the harness mid-project must not demand a distillation of
        # every task that predates the store.
        with unittest.mock.patch.object(
                memory_update, "cfg",
                return_value={"enabled": True, "baseline": "task-0099"}):
            self.assertEqual("", advance.check_task_ready("task-0100"))

    def test_the_handoff_debt_is_reported_before_the_memory_debt(self):
        # Order is not cosmetic: the memory rows are distilled FROM the handoff
        # doc, so demanding the distillation of a document nobody has written
        # yet is an instruction that cannot be followed.
        with unittest.mock.patch.object(
                handoff, "uncovered_done_tasks",
                return_value=[{"task": "task-0099", "reason": "no handoff doc"}]):
            refusal = advance.check_task_ready("task-0100")
        self.assertIn("handoff debt", refusal)
        self.assertNotIn("--stamp", refusal)

    def stamp_it(self, task="task-0099"):
        (self.stamps / f"{task}.json").write_text(
            f'{{"task": "{task}", "stamped_at": "2026-09-14T00:00:00+00:00", '
            f'"counts": {{"lesson": 1}}, "none": false}}', encoding="utf-8")

    def criteria_less(self):
        (self.dirs["active"] / "task-0100.md").write_text(
            "---\ntask: task-0100\nspec: none\n---\n", encoding="utf-8")

    def test_a_broken_handoff_checker_does_not_blind_the_other_checks(self):
        # HIGH-2. The handoff call sat bare under check_task_ready's blanket
        # `except Exception: return ""`, so a broken handoff.py returned "allow"
        # past the acceptance-criteria check and the spec-approval gate - the
        # pipeline's floor - and not merely past its own gate. One checker's
        # failure may only disable that checker.
        self.stamp_it()
        self.criteria_less()
        with unittest.mock.patch.object(handoff, "uncovered_done_tasks",
                                        side_effect=RuntimeError("boom")):
            self.assertIn("Acceptance Criteria", advance.check_task_ready("task-0100"))

    def test_a_broken_handoff_checker_still_lets_the_memory_gate_fire(self):
        # The other half of the same containment: the check AFTER the broken one
        # must still run, not just the ones before it.
        with unittest.mock.patch.object(handoff, "uncovered_done_tasks",
                                        side_effect=RuntimeError("boom")):
            self.assertIn("memory debt", advance.check_task_ready("task-0100"))

    def test_a_broken_memory_module_does_not_block_registration(self):
        # Fail-open, like every other gate here: a checker bug must not stop
        # real work. It must also not swallow the checks that come after it.
        with unittest.mock.patch.object(memory_update, "unstamped_done_tasks",
                                        side_effect=RuntimeError("boom")):
            self.assertEqual("", advance.check_task_ready("task-0100"))
            (self.dirs["active"] / "task-0100.md").write_text(
                "---\ntask: task-0100\nspec: none\n---\n", encoding="utf-8")
            self.assertIn("Acceptance Criteria", advance.check_task_ready("task-0100"))


class BlockedRunIsVisibleAtSessionStartTest(unittest.TestCase):
    """The second surface of the same `awaiting_human` write (task-0067, D-2).

    The Stop hook owns surfacing a blocked run mid-session, because its output is
    the only one that reaches the live session. This covers the other end: a
    session that STARTS with a run already parked blocked. The resume summary ran
    at SessionStart all along, but it printed the blocked run as one more
    in-flight line under "Drive these through the pipeline via advance.py" - and
    advance.py refuses to move a blocked run, so the only line about the run that
    most needed attention was wrong advice."""

    def resume(self, runs: list[dict]) -> str:
        with unittest.mock.patch.object(state, "all_runs", return_value=runs):
            return state._resume_text(_FakeConn())

    def test_a_blocked_run_is_named_as_needing_the_ceo(self):
        text = self.resume([run(stage="implement", status=state.ST_BLOCKED,
                                awaiting_human="blocked")])
        self.assertIn("BLOCKED, needs the CEO", text)
        self.assertIn("task-0001", text)
        self.assertIn("--reject", text)

    def test_an_ordinary_run_gets_no_blocked_line(self):
        # The control: the callout must not fire on every resume, or it stops
        # meaning anything.
        text = self.resume([run(stage="implement")])
        self.assertIn("task-0001", text)
        self.assertNotIn("BLOCKED", text)


class BusyMarkerTest(unittest.TestCase):
    """Defect 2: dispatching a subagent must silence the hook through a
    documented call, not hand-written marker JSON."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_state"
        self.tmp.mkdir(exist_ok=True)
        patcher = unittest.mock.patch.object(state, "STATE_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for f in self.tmp.glob("gate-*.json"):
            f.unlink()
        self.tmp.rmdir()

    def _cli(self, *args) -> dict:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["advance.py", *args]), \
                redirect_stdout(buf):
            advance.main()
        return json.loads(buf.getvalue())

    def test_busy_flag_keeps_the_hook_quiet_and_idle_releases_it(self):
        self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))

        out = self._cli("--task", "task-0007", "--busy", "implement")
        self.assertEqual("busy", out["action"])
        self.assertTrue(stop_gate.busy_marker_fresh("task-0007"))

        out = self._cli("--task", "task-0007", "--idle")
        self.assertEqual("idle", out["action"])
        self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))

    def test_stale_marker_does_not_keep_the_hook_quiet(self):
        with unittest.mock.patch.object(advance, "GATE_TIMEOUT", 0):
            self._cli("--task", "task-0007", "--busy", "implement")
        self.assertTrue(advance.gate_marker_path("task-0007").is_file())
        self.assertFalse(stop_gate.busy_marker_fresh("task-0007"))


class MergeEvidenceTest(unittest.TestCase):
    """Defect 4: nothing reaches done because evidence was absent."""

    def _fake_git(self, refs=(), merged=(), tagged=False):
        """Minimal git stand-in: which refs exist, which are in main, and
        whether main carries a '[task-NNNN]' commit."""
        def _git(repo, *args, timeout=None):
            if args[0] == "fetch":
                return 0, ""
            if args[0] == "branch":                      # branch_for
                return 0, ""
            if args[0] == "log":                         # task_in_main
                return 0, "abc1234 [task-0043] work" if tagged else ""
            if args[0] == "rev-parse":
                return (0, args[-1]) if args[-1] in refs else (1, "")
            if args[0] == "merge-base":
                return (0, "") if args[2] in merged else (1, "")
            return 1, ""
        return _git

    def _finish(self, frontmatter, **git):
        buf = io.StringIO()
        with unittest.mock.patch.object(git_state, "repos", return_value=[Path("repo")]), \
                unittest.mock.patch.object(git_state, "_git", self._fake_git(**git)), \
                unittest.mock.patch.object(advance, "_task_frontmatter", return_value=frontmatter), \
                unittest.mock.patch.object(advance, "_merge_wait",
                                           side_effect=lambda t, r, w: {**r, "awaiting_human": w}), \
                unittest.mock.patch.object(state, "move_task", return_value=True), \
                redirect_stdout(buf):
            advance.finish_or_wait_for_merge("task-0043", run(task="task-0043", stage="done"))
        return json.loads(buf.getvalue())

    def test_no_branch_and_no_tagged_commit_parks(self):
        out = self._finish({})
        self.assertEqual("park", out["action"])
        self.assertEqual("merge", out["awaiting_human"])
        self.assertIn("both signals are missing", out["message"])
        self.assertIn("carrier branch", out["message"])
        self.assertIn("tagged commit", out["message"])
        self.assertIn("UNKNOWN, not merged", out["message"])

    def test_declared_branch_that_is_merged_reaches_done(self):
        out = self._finish({"branch": "bugfix/task-0001"},
                           refs=("origin/bugfix/task-0001",),
                           merged=("origin/bugfix/task-0001",))
        self.assertEqual("done", out["action"])
        self.assertIn("Merged into main", out["message"])

    def test_declared_branch_that_is_unmerged_parks(self):
        out = self._finish({"branch": "bugfix/task-0001"},
                           refs=("origin/bugfix/task-0001",))
        self.assertEqual("park", out["action"])
        self.assertEqual("merge", out["awaiting_human"])
        self.assertIn("does not carry this task yet", out["message"])


class LocalMergeDetectionTest(unittest.TestCase):
    """task-0048: merge detection read only remote refs, so in `solo` mode - the
    mode whose whole point is that the trunk is not pushed - no task could ever
    reach done. Measured live: task-0003 parked at awaiting_human=merge with main
    genuinely carrying its work.

    Runs against a real throwaway repo rather than a git stand-in: the defect was
    exactly a wrong assumption about what git answers, which a stand-in built on
    the same assumption cannot catch."""

    def setUp(self):
        # A fresh directory per test: git leaves its object files read-only, so a
        # shared path that failed to delete on Windows broke the NEXT test's setUp.
        # sandbox() owns both halves of that - it is project-local (task-0069) and
        # its delete clears the read-only bit instead of ignoring the failure.
        self.tmp = tmproot.sandbox(self, "conveyor_repo_")
        self.repo = self.tmp / "work"
        self.repo.mkdir()

        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.commit("first")

        p = unittest.mock.patch.object(git_state, "repos", return_value=[self.repo])
        p.start()
        self.addCleanup(p.stop)
        p = unittest.mock.patch.object(state, "load_pipeline",
                                       return_value={"main_branch": "main"})
        p.start()
        self.addCleanup(p.stop)

    def git(self, *args) -> str:
        p = subprocess.run(["git", "-C", str(self.repo), *args],
                           capture_output=True, text=True)
        self.assertEqual(0, p.returncode, f"git {' '.join(args)}: {p.stderr}")
        return (p.stdout or "").strip()

    def commit(self, message: str) -> None:
        f = self.repo / "log.txt"
        f.write_text(f.read_text(encoding="utf-8") + message + "\n"
                     if f.exists() else message + "\n", encoding="utf-8")
        self.git("add", "log.txt")
        self.git("commit", "-m", message)

    def work_branch(self, name: str, message: str) -> None:
        """A branch with one commit of its own, left checked out on main."""
        self.git("checkout", "-b", name)
        self.commit(message)
        self.git("checkout", "main")

    def report(self, task: str) -> list[dict]:
        return git_state.task_report(task, do_fetch=False)

    def test_ancestry_alone_closes_a_task_with_no_tag_and_no_remote(self):
        self.work_branch("techdebt/task-9001", "work")
        self.git("merge", "--no-ff", "-m", "merge the branch", "techdebt/task-9001")

        self.assertFalse(git_state.task_in_main(self.repo, "task-9001"))
        self.assertEqual([{"repo": "work", "branch": "techdebt/task-9001",
                           "pushed": False, "in_main": True}],
                         self.report("task-9001"))

    def test_a_tagged_commit_alone_closes_the_squash_case(self):
        self.work_branch("feature/task-9002", "work")
        self.commit("[task-9002] the same work, squashed")

        self.assertIs(False, git_state.merged_into_main(self.repo, "feature/task-9002"))
        self.assertTrue(self.report("task-9002")[0]["in_main"])

    def test_neither_signal_is_unknown_rather_than_done(self):
        # No branch and no tag at all: an empty report, which advance.py parks on.
        self.assertEqual([], self.report("task-9003"))
        # A branch that exists but is merged nowhere: known, and known unmerged.
        self.work_branch("feature/task-9004", "work")
        self.assertIs(False, self.report("task-9004")[0]["in_main"])

    def test_a_repository_with_no_remote_resolves_the_trunk(self):
        self.assertEqual("", self.git("remote"))
        self.assertEqual("main", git_state.trunk(self.repo))
        self.assertTrue(git_state.base_is_current(self.repo))

    def test_pushed_is_false_for_a_local_branch_and_true_for_a_remote_one(self):
        self.git("init", "--bare", str(self.tmp / "remote.git"))
        self.git("remote", "add", "origin", str(self.tmp / "remote.git"))
        self.work_branch("feature/task-9005", "pushed work")
        self.work_branch("feature/task-9006", "local work")
        self.git("push", "-u", "origin", "main", "feature/task-9005")

        self.assertTrue(git_state.is_pushed(self.repo, "feature/task-9005"))
        self.assertFalse(git_state.is_pushed(self.repo, "feature/task-9006"))
        self.assertTrue(self.report("task-9005")[0]["pushed"])
        self.assertFalse(self.report("task-9006")[0]["pushed"])

        # And the trunk follows local main once it moves ahead of the pushed copy,
        # which is what a locally merged task branch does.
        self.git("merge", "--no-ff", "-m", "merge locally", "feature/task-9006")
        self.assertEqual("main", git_state.trunk(self.repo))
        self.assertTrue(self.report("task-9006")[0]["in_main"])

    # task-0004, FR-4 inventory (git_state row): three commit spellings count,
    # not one. The bracket-only grep made every other spelling read as unmerged.

    def test_a_conventional_commit_subject_counts_as_a_tag(self):
        self.commit("task-9007: the work, with no brackets anywhere")
        self.assertTrue(git_state.task_in_main(self.repo, "task-9007"))

    def test_gits_own_default_merge_message_counts_as_a_tag(self):
        # The solo-mode case: nothing tagged, and the id survives only in the
        # merge commit git writes itself.
        self.work_branch("bugfix/task-9008", "untagged work")
        self.git("merge", "--no-ff", "bugfix/task-9008")
        self.assertIn("Merge branch 'bugfix/task-9008'",
                      self.git("log", "-1", "--format=%s"))
        self.assertTrue(git_state.task_in_main(self.repo, "task-9008"))

    def test_a_mention_inside_a_sentence_is_not_a_tag(self):
        self.commit("refactor the queue; this unblocks task-9009 later")
        self.assertFalse(git_state.task_in_main(self.repo, "task-9009"))

    def test_a_longer_id_sharing_the_prefix_is_not_this_task(self):
        # The merge arm had no terminator, so 'task-9011' matched 'task-90111'.
        self.work_branch("feature/task-90111", "a different task entirely")
        self.git("merge", "--no-ff", "feature/task-90111")
        self.assertTrue(git_state.task_in_main(self.repo, "task-90111"))
        self.assertFalse(git_state.task_in_main(self.repo, "task-9011"))

    def test_a_commit_about_the_task_is_not_the_tasks_own_subject(self):
        # Both used to match: the arm accepted any non-word char before the id,
        # so a space and a quote qualified.
        self.commit("hotfix for task-9012: patch the fallout")
        self.commit('Revert "task-9013: the work"')
        self.assertFalse(git_state.task_in_main(self.repo, "task-9012"))
        self.assertFalse(git_state.task_in_main(self.repo, "task-9013"))

    def test_a_sub_task_prefix_is_not_a_tag(self):
        # The epic convention was deliberately not ported, so its spelling must
        # not silently resolve either - a task-9010 is not a sub-task-9010.
        self.commit("sub-task-9010: work under an epic branch")
        self.assertFalse(git_state.task_in_main(self.repo, "task-9010"))


class BacklogFilterTest(unittest.TestCase):
    """Defect 5: which frontmatter fields take a task out of the ready set.

    Runs against a temporary tasks tree, never the live one - a test in this
    suite already read live state once and failed for an environmental reason."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_tasks"
        for name in ("backlog", "active", "done"):
            (self.tmp / name).mkdir(parents=True, exist_ok=True)
        for attr, name in (("BACKLOG_DIR", "backlog"), ("ACTIVE_DIR", "active"),
                           ("DONE_DIR", "done")):
            p = unittest.mock.patch.object(stop_gate, attr, self.tmp / name)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for d in ("backlog", "active", "done"):
            for f in (self.tmp / d).glob("*.md"):
                f.unlink()
            (self.tmp / d).rmdir()
        self.tmp.rmdir()

    def _task(self, task, where="backlog", deps=(), blocked_on="", superseded_by=""):
        (self.tmp / where / f"{task}.md").write_text(
            "---\n"
            f"id: {task.split('-')[1]}\n"
            f"depends_on: [{', '.join(deps)}]\n"
            f"blocked_on:{(' ' + blocked_on) if blocked_on else ''}\n"
            f"superseded_by:{(' ' + superseded_by) if superseded_by else ''}\n"
            "---\n\n"
            "## Body\n\n"
            "Prose that quotes `blocked_on:` and `superseded_by:` as field names.\n",
            encoding="utf-8")

    def _ready(self):
        return [t["id"] for t in stop_gate.read_backlog()
                if all(stop_gate.dep_satisfied(d, {}) for d in t["deps"])]

    def test_blank_fields_still_offer_the_task(self):
        # Control: the template ships both fields blank on every task.
        self._task("task-0100")
        self.assertEqual(["task-0100"], self._ready())

    def test_superseded_task_is_not_offered(self):
        self._task("task-0100", superseded_by="task-0200")
        self.assertEqual([], self._ready())

    def test_non_empty_blocked_on_is_not_offered(self):
        self._task("task-0100", blocked_on="CEO ruling on the pricing model")
        self.assertEqual([], self._ready())

    def test_dependency_on_a_superseded_task_resolves_through_its_successor(self):
        self._task("task-0100", superseded_by="task-0200")
        self._task("task-0200", where="done")
        self._task("task-0101", deps=("task-0100",))
        self.assertEqual(["task-0101"], self._ready())

    def test_dependency_on_a_superseded_task_waits_for_the_unfinished_successor(self):
        self._task("task-0100", superseded_by="task-0200")
        self._task("task-0200")  # successor still queued, not done
        self._task("task-0101", deps=("task-0100",))
        self.assertEqual(["task-0200"], self._ready())

    def test_dangling_supersede_pointer_does_not_hang_the_dependent_task(self):
        self._task("task-0100", superseded_by="task-0900")  # no such file anywhere
        self._task("task-0101", deps=("task-0100",))
        self.assertEqual(["task-0101"], self._ready())

    def test_supersede_cycle_does_not_hang_the_dependent_task(self):
        self._task("task-0100", superseded_by="task-0200")
        self._task("task-0200", superseded_by="task-0100")
        self._task("task-0101", deps=("task-0100",))
        self.assertEqual(["task-0101"], self._ready())

    def test_ordinary_unfinished_dependency_still_blocks(self):
        self._task("task-0100")
        self._task("task-0101", deps=("task-0102",))  # never created
        self.assertEqual(["task-0100"], self._ready())


class StrandedStageTest(unittest.TestCase):
    """Defect 6: a stage absent from the config counted as a passed gate.

    Runs against a throwaway run.db and a literal stage list - never the live
    state - so the assertions cannot be moved by a pipeline.json edit."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_tmp_run"
        self.tmp.mkdir(exist_ok=True)
        for attr, value in (("STATE_DIR", self.tmp), ("DB_PATH", self.tmp / "run.db")):
            p = unittest.mock.patch.object(state, attr, value)
            p.start()
            self.addCleanup(p.stop)
        p = unittest.mock.patch.object(state, "load_pipeline", return_value=BUILD_PIPELINE)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for f in self.tmp.iterdir():
            f.unlink()
        self.tmp.rmdir()

    def _seed(self, task, stage):
        conn = state.connect()
        state.create_run(conn, task, "feature", stage)
        conn.close()

    def _row(self, task) -> dict:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()

    def _cli(self, module, *args) -> dict:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["cli.py", *args]), \
                redirect_stdout(buf):
            module.main()
        return json.loads(buf.getvalue())

    def test_unknown_stage_blocks_instead_of_advancing(self):
        self._seed("task-0500", "ghost-stage")
        out = self._cli(advance, "--task", "task-0500")

        self.assertEqual("blocked", out["action"])
        self.assertIn("'ghost-stage' is not part of the build flow", out["message"])
        self.assertIn("implement, test, review, ready, done", out["message"])
        self.assertIn("--reject", out["message"])

    def test_unknown_stage_never_writes_a_null_stage(self):
        self._seed("task-0500", "ghost-stage")
        for _ in range(2):  # the second call must not drift further either
            self._cli(advance, "--task", "task-0500")
            row = self._row("task-0500")
            self.assertEqual("ghost-stage", row["stage"])
            self.assertEqual(state.ST_BLOCKED, row["stage_status"])

    def test_stop_hook_offers_nothing_while_a_run_is_stranded(self):
        # Neither status may free the slot. The blocked one is also surfaced now
        # (task-0067), which is not an offer of work: what this test forbids is
        # the queue handing the tree to task-0002, and that is asserted in both
        # subtests.
        for status in (state.ST_IN_PROGRESS, state.ST_BLOCKED):
            with self.subTest(stage_status=status):
                reason = decide_with([run(stage="ghost-stage", status=status)],
                                     pipeline=BUILD_PIPELINE)
                if status == state.ST_BLOCKED:
                    self.assertIn("parked BLOCKED", reason)
                else:
                    self.assertIsNone(reason)

    def test_an_unreadable_pipeline_disables_the_check_rather_than_stranding(self):
        # Fail-open control: with no readable stage list the hook behaves as
        # before, so a broken config cannot freeze the queue.
        reason = decide_with([run(stage="ghost-stage")], pipeline={})
        self.assertIn("Start the next ready task task-0002", reason)

    def test_a_configured_stage_still_advances(self):
        self._seed("task-0501", "implement")
        out = self._cli(advance, "--task", "task-0501")

        self.assertEqual("advanced", out["action"])
        self.assertEqual("test", self._row("task-0501")["stage"])

    def test_the_terminal_stage_still_routes_to_the_merge_check(self):
        self._seed("task-0502", "done")
        with unittest.mock.patch.object(advance, "finish_or_wait_for_merge",
                                        return_value=0) as finish, \
                unittest.mock.patch.object(sys, "argv", ["advance.py", "--task", "task-0502"]), \
                redirect_stdout(io.StringIO()):
            advance.main()

        self.assertEqual(1, finish.call_count)
        self.assertEqual("done", self._row("task-0502")["stage"])

    def test_reject_recovers_a_stranded_run(self):
        self._seed("task-0500", "ghost-stage")
        self._cli(advance, "--task", "task-0500")

        out = self._cli(approve, "--task", "task-0500", "--reject")

        self.assertTrue(out["ok"])
        row = self._row("task-0500")
        self.assertEqual("implement", row["stage"])
        self.assertEqual(state.ST_IN_PROGRESS, row["stage_status"])


class SoloCheckpointTest(unittest.TestCase):
    """task-0047, second half: in solo mode the ready stage must not gate on a
    push, because no push happens - the approved branch is merged into the trunk
    locally and the CEO pushes the trunk himself later.

    Measured live: the trunk already carried task-0003 and advance.py still
    returned awaiting_human=push, a checkpoint on a step the mode had removed,
    which no approval could clear. Every remaining task of the unattended run
    sat behind it.

    Throwaway run.db and a literal stage list, so a pipeline.json edit cannot
    move these assertions."""

    def setUp(self):
        self.mode = "pr"
        self.tmp = Path(__file__).resolve().parent / "_tmp_solo"
        self.tmp.mkdir(exist_ok=True)
        for attr, value in (("STATE_DIR", self.tmp), ("DB_PATH", self.tmp / "run.db")):
            p = unittest.mock.patch.object(state, attr, value)
            p.start()
            self.addCleanup(p.stop)
        # side_effect, not return_value: self.mode is read at call time so a test
        # can flip the mode between two calls.
        p = unittest.mock.patch.object(
            state, "load_pipeline",
            side_effect=lambda: {**BUILD_PIPELINE, "workflow": {"mode": self.mode}})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._clean)

    def _clean(self):
        for f in self.tmp.iterdir():
            f.unlink()
        self.tmp.rmdir()

    def _seed(self, task, stage="ready"):
        conn = state.connect()
        state.create_run(conn, task, "feature", stage)
        conn.close()

    def _row(self, task) -> dict:
        conn = state.connect()
        try:
            return state.get_run(conn, task)
        finally:
            conn.close()

    def _cli(self, module, *args) -> dict:
        buf = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["cli.py", *args]), \
                redirect_stdout(buf):
            module.main()
        return json.loads(buf.getvalue())

    @contextmanager
    def _trunk_carries(self, task):
        """The trunk carrying the task, at the seam advance.py reads it. Yields
        the task_report mock so a test can assert it was never consulted."""
        report = [{"repo": "repo", "branch": f"feature/{task}", "in_main": True}]
        with unittest.mock.patch.object(git_state, "task_report",
                                        return_value=report) as mock, \
                unittest.mock.patch.object(advance, "_task_frontmatter", return_value={}), \
                unittest.mock.patch.object(state, "move_task", return_value=True):
            yield mock

    def test_ready_yields_two_checkpoints_in_pr_and_one_in_solo(self):
        ready = state.get_stage(BUILD_PIPELINE, "ready")
        self.mode = "pr"
        self.assertEqual(["commit", "push"], advance.stage_checkpoints(ready))
        self.mode = "solo"
        self.assertEqual(["commit"], advance.stage_checkpoints(ready))

    def test_pr_mode_still_parks_for_the_push(self):
        self.mode = "pr"
        self._seed("task-0600")

        self.assertEqual("commit", self._cli(advance, "--task", "task-0600")["awaiting_human"])
        self._cli(approve, "--task", "task-0600", "--gate", "commit")

        out = self._cli(advance, "--task", "task-0600")
        self.assertEqual("park", out["action"])
        self.assertEqual("push", out["awaiting_human"])
        self.assertIn("push approval", out["message"])

        # And it stays parked: re-running without the approval advances nothing.
        out = self._cli(advance, "--task", "task-0600")
        self.assertEqual("push", out["awaiting_human"])
        self.assertEqual("ready", self._row("task-0600")["stage"])

    def test_pr_mode_reaches_done_once_both_checkpoints_are_approved(self):
        self.mode = "pr"
        self._seed("task-0603")
        self._cli(advance, "--task", "task-0603")
        for gate in ("commit", "push"):
            self._cli(approve, "--task", "task-0603", "--gate", gate)

        with self._trunk_carries("task-0603"):
            out = self._cli(advance, "--task", "task-0603")
        self.assertEqual("done", out["action"])

    def test_solo_mode_closes_a_committed_task_the_trunk_carries(self):
        self.mode = "solo"
        self._seed("task-0601")

        out = self._cli(advance, "--task", "task-0601")
        self.assertEqual("commit", out["awaiting_human"])
        self.assertIn("merge the branch into the trunk locally", out["message"])
        self._cli(approve, "--task", "task-0601", "--gate", "commit")

        with self._trunk_carries("task-0601"):
            out = self._cli(advance, "--task", "task-0601")

        self.assertEqual("done", out["action"])
        self.assertEqual("", out["awaiting_human"])
        self.assertEqual("done", self._row("task-0601")["stage"])

    def test_solo_mode_does_not_close_a_task_whose_commit_was_never_approved(self):
        self.mode = "solo"
        self._seed("task-0602")

        with self._trunk_carries("task-0602") as report:
            out = self._cli(advance, "--task", "task-0602")
            self.assertEqual(0, report.call_count)  # never reached the merge check

        self.assertEqual("park", out["action"])
        self.assertEqual("commit", out["awaiting_human"])
        self.assertEqual("ready", self._row("task-0602")["stage"])
        self.assertEqual(0, self._row("task-0602")["commit_approved"])


if __name__ == "__main__":
    unittest.main()
