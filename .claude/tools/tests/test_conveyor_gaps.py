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

import inspect
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
import approvals
import approve
import gate as gate_module
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
                set_fields=None, debt=(), mem_debt=(), count_nags=False,
                busy=False, any_busy=False, latest=None) -> str | None:
    """stop_gate.decide() over a synthetic run set. Returns the block reason, or
    None when the hook allowed the stop. No DB, no git, no task files.

    busy_marker_fresh is mocked too: it reads the real .agentry/state/, so a live
    gate marker for the task id used here (written whenever the orchestrator
    dispatches a subagent for it) silenced the hook and failed these tests for
    an environmental reason. BusyMarkerTest covers that function directly.
    any_busy_marker_fresh is mocked for the same reason and it is the stronger
    case: it globs that directory, so a marker for ANY task - one this test never
    heard of - would short-circuit decide() and turn every assertion here into a
    silent None. `busy` / `any_busy` turn them on for the tests that are about
    the marker itself.

    debt / mem_debt default to none, so every pre-existing assertion here is
    about a pipeline with its documentation paid up. `latest` defaults to None
    for the same reason, and it is a patch, so a probe that forgets to pass it
    silently tests a branch that cannot fire - which is how a green result was
    once recorded for site 6.

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
            unittest.mock.patch.object(stop_gate, "latest_undocumented", return_value=latest), \
            unittest.mock.patch.object(stop_gate, "reconcile_status_drift", return_value=([], [])), \
            unittest.mock.patch.object(stop_gate.approvals, "granted", return_value=True), \
            unittest.mock.patch.object(stop_gate, "busy_marker_fresh", return_value=busy), \
            unittest.mock.patch.object(stop_gate, "any_busy_marker_fresh",
                                       return_value=any_busy), \
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


class StopHookActiveBackstopTest(unittest.TestCase):
    """The reason-repeat backstop: `stop_hook_active` honoured as a last resort.

    task-0018 gave every one of the eleven block sites a budget, so the question
    this class has to answer first is what is left for a backstop to catch. Two
    things, and both are pinned below. Five of those budgets are per RUN and per
    STAGE, so a loop that keeps the stage moving - or that never charges at all
    because its branch re-enters before the write - spends nothing; and three
    sites are bounded by an ARGUMENT rather than by a counter ("this can only
    fire once", "this reconciles before it blocks", "bounding this would buy
    silence"), which holds exactly as long as the argument does.

    The danger in the other direction is the one task-0018 nearly shipped: a
    global quiet path that silences a demand nothing else reports. So the tests
    that matter most here are the negative ones - the blocked-run surfacing and
    both documentation demands must survive the backstop, and a trip that has no
    run to park must escalate rather than go quiet."""

    DEBT = ({"task": "task-0067", "reason": "section 'Key decisions' is too thin"},)

    def setUp(self):
        self.PIPE = {"stop_hook_repeat_ceiling": 3}
        self.tmp = tmproot.sandbox(self, "stoprepeat")
        for p in (unittest.mock.patch.object(state, "STATE_DIR", self.tmp),
                  unittest.mock.patch.object(stop_gate, "STOP_HOOK_ACTIVE", True)):
            p.start()
            self.addCleanup(p.stop)
        self.set_fields = unittest.mock.MagicMock()

    def stop(self, runs, **kw) -> str | None:
        kw.setdefault("pipeline", self.PIPE)
        kw.setdefault("set_fields", self.set_fields)
        return decide_with(runs, **kw)

    def parked(self) -> list:
        return [c for c in self.set_fields.call_args_list
                if c.kwargs.get("stage_status") == state.ST_BLOCKED]

    # A run that is being driven and never advances. `continuations` stays 0 in
    # every stop because the run row is rebuilt each time - which is not an
    # artifact of the fixture but the shape of the real gap: advance.py zeroes
    # that column on every stage transition, so a loop that keeps transitioning
    # never accumulates towards continuation_ceiling at all.
    def looping_run(self) -> list[dict]:
        return [run(stage="implement", status=state.ST_IN_PROGRESS)]

    def test_the_payload_flag_is_read_instead_of_discarded(self):
        # FR-38, the literal criterion: main() used to json.load(sys.stdin) and
        # throw the result away, comment and all.
        for sent, expected in ((True, True), (False, False), (None, False)):
            with self.subTest(stop_hook_active=sent):
                payload = json.dumps({"hook_event_name": "Stop",
                                      "stop_hook_active": sent})
                with unittest.mock.patch.object(stop_gate, "STOP_HOOK_ACTIVE", False), \
                        unittest.mock.patch.object(sys, "stdin", io.StringIO(payload)), \
                        unittest.mock.patch.object(stop_gate, "decide", return_value=0):
                    stop_gate.main()
                    self.assertIs(expected, stop_gate.STOP_HOOK_ACTIVE)

    def test_an_unreadable_payload_leaves_the_backstop_disarmed(self):
        # NFR-4 in the direction that matters: losing the bound keeps the hook
        # reporting, losing the report is the silent halt.
        for junk in ("", "{not json", "null", "[1,2,3]"):
            with self.subTest(payload=junk):
                with unittest.mock.patch.object(stop_gate, "STOP_HOOK_ACTIVE", True), \
                        unittest.mock.patch.object(sys, "stdin", io.StringIO(junk)), \
                        unittest.mock.patch.object(stop_gate, "decide", return_value=0):
                    stop_gate.main()
                    self.assertFalse(stop_gate.STOP_HOOK_ACTIVE)

    def test_two_identical_reasons_are_a_retry_and_the_third_parks_the_run(self):
        # RQ-7: two identical blocks are a legitimate retry. Three is a loop.
        for n in (1, 2):
            with self.subTest(stop=n):
                self.assertIn("task-0001", self.stop(self.looping_run()))
                self.assertEqual([], self.parked())

        self.assertIsNone(self.stop(self.looping_run()))   # the stop is released
        self.assertEqual(["task-0001"], [c.args[1] for c in self.parked()])

    def test_the_park_is_surfaced_rather_than_silent(self):
        # The defect task-0018 nearly shipped, in its new clothes: parking a run
        # and saying nothing halts the conveyor with no channel reporting it. The
        # park is only acceptable because the NEXT stop raises it by name.
        for _ in range(3):
            self.stop(self.looping_run())

        # First, the park must not stamp `awaiting_human` itself. Step 2 surfaces
        # only while `aw != AWAITING_BLOCKED`, so a park that set it would make
        # the run BLOCKED and already-surfaced in one write, and the conveyor
        # would halt in silence for ever. Asserted against the recorded call
        # rather than through a second decide(), because set_fields is a mock:
        # nothing the park writes is read back, so feeding a hand-built run into
        # the next stop would keep passing even after that regression.
        park = self.parked()[-1]
        self.assertEqual("task-0001", park.args[1])
        self.assertNotIn("awaiting_human", park.kwargs)

        surfaced = self.stop([run(stage="implement", status=state.ST_BLOCKED)])
        self.assertIn("task-0001 is parked BLOCKED", surfaced)
        self.assertIn("CEO", surfaced)

    def test_a_park_at_ready_is_surfaced_too(self):
        # The awkward case, and the one the mock hides: site 1 fires only while
        # `awaiting_human` is 'commit' or 'push', so the run charge() parks there
        # is BLOCKED with `aw` NON-EMPTY. That is exactly the pair task-0018's
        # Critical got wrong (`if not aw` read it as already surfaced), so the
        # park this task adds has to be fed back in with `aw` intact.
        ready = [run(stage="ready", awaiting_human="commit", commit_approved=1)]
        for n in (1, 2):
            with self.subTest(stop=n):
                self.assertIn("checkpoint approved", self.stop(ready))
        self.assertIsNone(self.stop(ready))
        self.assertEqual(["task-0001"], [c.args[1] for c in self.parked()])

        # The parked run, with the awaiting_human it necessarily still carries.
        surfaced = self.stop([run(stage="ready", awaiting_human="commit",
                                  commit_approved=1, status=state.ST_BLOCKED)])
        self.assertIn("task-0001 is parked BLOCKED", surfaced)

    def test_the_backstop_is_independent_of_the_continuation_counter(self):
        # FR-38's last criterion. continuation_ceiling is 30 and this run has
        # spent 0 of it on every stop, so charge() would never park it; the
        # backstop does, on its own evidence.
        pipe = dict(self.PIPE, continuation_ceiling=30)
        for _ in range(3):
            self.stop(self.looping_run(), pipeline=pipe)
        self.assertEqual(["task-0001"], [c.args[1] for c in self.parked()])

    def test_three_different_reasons_do_not_trip_it(self):
        # The comparison is on the reason, not on the count. Three stops, three
        # different runs, no park - otherwise a busy conveyor would park itself.
        for task in ("task-0001", "task-0002", "task-0003"):
            with self.subTest(task=task):
                reason = self.stop([run(task=task, stage="implement")])
                self.assertIn(task, reason)
        self.assertEqual([], self.parked())

    def test_alternating_reasons_never_accumulate(self):
        # The sharper version: the same reason six times, but never twice in a
        # row. Consecutive means consecutive.
        for _ in range(6):
            self.assertIsNotNone(self.stop(self.looping_run()))
            self.assertIsNotNone(self.stop([run(task="task-0099", stage="implement")]))
        self.assertEqual([], self.parked())

    def test_the_ceiling_comes_from_pipeline_json(self):
        # FR-38: the value 3 is config, not a literal. Prove it by moving it.
        pipe = {"stop_hook_repeat_ceiling": 5}
        for n in range(1, 5):
            with self.subTest(stop=n):
                self.assertIsNotNone(self.stop(self.looping_run(), pipeline=pipe))
                self.assertEqual([], self.parked())
        self.assertIsNone(self.stop(self.looping_run(), pipeline=pipe))
        self.assertEqual(["task-0001"], [c.args[1] for c in self.parked()])

    def test_a_ceiling_below_one_disables_the_backstop_entirely(self):
        # The off switch, and the fail-open direction for a nonsense value: the
        # hook keeps reporting forever rather than going quiet.
        for n in range(8):
            with self.subTest(stop=n):
                self.assertIsNotNone(
                    self.stop(self.looping_run(), pipeline={"stop_hook_repeat_ceiling": 0}))
        self.assertEqual([], self.parked())

    def test_a_demand_with_no_run_to_park_escalates_instead_of_going_quiet(self):
        # Site 11's residual risk from task-0018, and both debt demands. There is
        # nothing to park, so the trip MUST speak. Going quiet here would be the
        # silent halt rules/orchestration.md says never to buy over noise.
        for n in (1, 2):
            with self.subTest(stop=n):
                reason = self.stop([], backlog=("task-0002",))
                self.assertIn("Start the next ready task task-0002", reason)

        escalation = self.stop([], backlog=("task-0002",))
        self.assertIn("no run to park", escalation)
        self.assertIn("AskUserQuestion", escalation)
        self.assertIn("Start the next ready task task-0002", escalation)
        self.assertEqual([], self.parked())

        # ONE escalation, then quiet about that exact sentence - the CEO owns it.
        self.assertIsNone(self.stop([], backlog=("task-0002",)))

    def test_it_cannot_swallow_the_blocked_run_surfacing(self):
        # The surfacing is bounded by an ARGUMENT (one-shot, stamped into
        # awaiting_human), not by a counter, so it is exactly what a backstop
        # could quietly eat. It cannot: the stamp means the same sentence never
        # occurs twice in a row, so the count never reaches the ceiling.
        first = self.stop([run(stage="implement", status=state.ST_BLOCKED)])
        self.assertIn("task-0001 is parked BLOCKED", first)
        for i in range(6):
            with self.subTest(stop=i + 2):
                # Silent because of the stamp, and provably not because of us.
                self.assertIsNone(self.stop(
                    [run(stage="implement", status=state.ST_BLOCKED,
                         awaiting_human=stop_gate.AWAITING_BLOCKED)]))
        # The count for that sentence is stuck at its first utterance: the six
        # silent stops never reached block(), so nothing incremented it and it
        # cannot approach the ceiling however long the run stays parked.
        stored = json.loads(
            (self.tmp / stop_gate.STOP_REPEAT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(1, stored["count"])
        self.assertIn("task-0001 is parked BLOCKED", stored["reason"])

        # And a second blocked run is still surfaced after all that.
        second = self.stop([run(task="task-0004", stage="implement",
                                status=state.ST_BLOCKED)])
        self.assertIn("task-0004 is parked BLOCKED", second)

    def test_the_debt_demand_is_still_raised_and_its_escalation_still_lands(self):
        # The two demands nothing else in the harness reports. The backstop may
        # defer them; it may not replace bounded_block()'s escalation with
        # silence. Run the real nag counter so both bounds interact for real.
        runs = [run(stage="ready", awaiting_human="commit")]
        seen = [self.stop(runs, debt=self.DEBT, count_nags=True) for _ in range(6)]
        self.assertTrue(any(s and "has no valid handoff doc" in s for s in seen),
                        "the debt was never uttered")
        self.assertTrue(any(s and "AskUserQuestion" in s for s in seen),
                        "the debt was swallowed before it could escalate")
        self.assertTrue(any(s and "task-0067" in s for s in seen))

    def test_the_count_survives_the_process(self):
        # Each stop is a fresh interpreter and the payload carries no count
        # (measured: the Stop schema is stop_hook_active + optional fields, and
        # stopHookBlockingCount is internal). The file is the mechanism.
        self.stop(self.looping_run())
        stored = json.loads(
            (self.tmp / stop_gate.STOP_REPEAT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(1, stored["count"])
        self.assertIn("task-0001", stored["reason"])

    def test_a_corrupt_counter_file_heals_instead_of_pinning_the_count(self):
        # debt_nags() had exactly this bug: json.loads sharing the outer handler
        # meant a file left corrupt by a killed process returned 1 for ever.
        for junk in ("{not json", "", "[1, 2, 3]", "42"):
            with self.subTest(junk=junk):
                (self.tmp / stop_gate.STOP_REPEAT_FILE).write_text(junk, encoding="utf-8")
                self.assertEqual([1, 2, 3],
                                 [stop_gate.stop_repeats("same reason") for _ in range(3)])

    def test_a_disarmed_flag_reproduces_the_unbounded_loop_it_replaces(self):
        # The before/after in one test. With the flag discarded - which is what
        # main() did until this task, so STOP_HOOK_ACTIVE was effectively always
        # False - the same reason repeats with nothing counting and nothing
        # parked. This is the defect, reproduced, not described.
        with unittest.mock.patch.object(stop_gate, "STOP_HOOK_ACTIVE", False):
            for i in range(12):
                with self.subTest(stop=i):
                    self.assertIsNotNone(self.stop(self.looping_run()))
            self.assertEqual([], self.parked())
            self.assertFalse((self.tmp / stop_gate.STOP_REPEAT_FILE).is_file())

        # Armed, the identical sequence terminates.
        for _ in range(3):
            self.stop(self.looping_run())
        self.assertEqual(["task-0001"], [c.args[1] for c in self.parked()])


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

    def _fake_git(self, refs=(), merged=(), tagged=False, first_parent=("main",)):
        """Minimal git stand-in: which refs exist, which are in main, whether
        main carries a '[task-NNNN]' commit, and the first-parent chain.

        The chain is what tells a real merge from an empty branch (task-0065):
        containment holds for both, so a ref counts as merged only when it sits
        OFF the chain, where a merge commit leaves the side it pulled in. The
        default models that shape - the chain is the trunk own commits, and a
        declared branch reaches the trunk from the side.
        """
        def _git(repo, *args, timeout=None):
            if args[0] == "fetch":
                return 0, ""
            if args[0] == "branch":                      # branch_for
                return 0, ""
            if args[0] == "log":                         # task_in_main
                return 0, "abc1234 [task-0043] work" if tagged else ""
            if args[0] == "rev-parse":
                ref = args[-1].replace("^{commit}", "")
                return (0, ref) if ref in refs or ref == "main" else (1, "")
            if args[0] == "rev-list":                    # the first-parent chain
                return 0, "\n".join(first_parent)
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

    def test_a_declared_branch_that_merely_points_at_main_parks(self):
        # task-0065: contained in the trunk, but sitting ON its first-parent
        # chain - the shape of a branch with no commits, not of a merge.
        out = self._finish({"branch": "bugfix/task-0001"},
                           refs=("origin/bugfix/task-0001",),
                           merged=("origin/bugfix/task-0001",),
                           first_parent=("main", "origin/bugfix/task-0001"))
        self.assertEqual("park", out["action"])
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

    # task-0065: pointer containment is not merge evidence. An empty branch is
    # an ancestor of main the moment it is cut, which is the state EVERY task
    # starts in, so the old ancestry test reported the task as merged before it
    # had shipped a line.

    def test_a_branch_with_no_commits_is_never_reported_as_merged(self):
        # The exact live case: git checkout -b, nothing committed yet.
        self.git("checkout", "-b", "techdebt/task-9101")
        self.git("checkout", "main")
        self.assertEqual(self.git("rev-parse", "techdebt/task-9101"),
                         self.git("rev-parse", "main"))

        self.assertIsNone(git_state.merged_into_main(self.repo, "techdebt/task-9101"))
        self.assertIsNot(True, self.report("task-9101")[0]["in_main"])

    def test_an_empty_branch_stays_unmerged_while_main_moves_on(self):
        # Main advancing past the empty branch - by its own commits and by
        # merging somebody else's work - is still not this task shipping.
        self.git("checkout", "-b", "techdebt/task-9102")
        self.git("checkout", "main")
        self.commit("unrelated work on main")
        self.work_branch("feature/task-9103", "somebody else's branch")
        self.git("merge", "--no-ff", "-m", "merge a different branch", "feature/task-9103")

        self.assertIsNone(git_state.merged_into_main(self.repo, "techdebt/task-9102"))
        self.assertIsNot(True, self.report("task-9102")[0]["in_main"])
        # ... while the branch that really was merged still reads as carried.
        self.assertIs(True, git_state.merged_into_main(self.repo, "feature/task-9103"))

    def test_the_three_outcomes_stay_distinct(self):
        self.work_branch("feature/task-9104", "carried work")
        self.git("merge", "--no-ff", "-m", "merge it", "feature/task-9104")
        self.work_branch("feature/task-9105", "work that was never merged")
        self.git("checkout", "-b", "feature/task-9106")      # empty: cannot tell
        self.git("checkout", "main")

        self.assertIs(True, git_state.merged_into_main(self.repo, "feature/task-9104"))
        self.assertIs(False, git_state.merged_into_main(self.repo, "feature/task-9105"))
        self.assertIsNone(git_state.merged_into_main(self.repo, "feature/task-9106"))
        self.assertIsNone(git_state.merged_into_main(self.repo, "feature/task-does-not-exist"))

    def test_junk_git_output_fails_towards_cannot_tell(self):
        for answer in ((1, ""), (128, "fatal: not a git repository"), (0, "")):
            with self.subTest(answer=answer):
                with unittest.mock.patch.object(git_state, "_git", return_value=answer):
                    self.assertIsNone(
                        git_state.merged_into_main(self.repo, "feature/task-9107"))

    def test_a_fast_forward_merge_with_no_tag_parks_rather_than_closing(self):
        # The cost of requiring positive evidence, stated as a test: a
        # fast-forward leaves the branch tip ON main's first-parent chain, which
        # is byte-identical to an empty branch. Cannot tell, so it parks - and a
        # tagged commit is what closes it.
        self.work_branch("feature/task-9108", "work merged by fast-forward")
        self.git("merge", "--ff-only", "feature/task-9108")
        self.assertEqual(self.git("rev-parse", "feature/task-9108"),
                         self.git("rev-parse", "main"))
        self.assertIsNone(git_state.merged_into_main(self.repo, "feature/task-9108"))
        self.assertIsNot(True, self.report("task-9108")[0]["in_main"])

        self.work_branch("feature/task-9109", "[task-9109] tagged work")
        self.git("merge", "--ff-only", "feature/task-9109")
        self.assertTrue(self.report("task-9109")[0]["in_main"])

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
        # The approvals level is pinned, and it is not cosmetic: approvals.read()
        # reads .agentry/state/approvals from the LIVE workspace, so without this
        # these tests assert solo-mode behaviour only while the CEO happens to
        # have the dial at `manual`. It went unnoticed because advance.py could
        # not act on the level at all (task-0083); the moment it could, the
        # workspace sitting at `assisted` auto-approved the commit inside
        # test_solo_mode_does_not_close_a_task_whose_commit_was_never_approved
        # and the assertion read 1. Solo mode is the subject here, the dial is not.
        p = unittest.mock.patch.object(approvals, "read", return_value=approvals.MANUAL)
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


class ContinuationBoundTest(unittest.TestCase):
    """FR-37: no branch of decide() blocks for ever, and no HEALTHY task is
    parked for being slow.

    Both halves are the test. A counter that increments is trivially easy to
    write and proves nothing: the failure this project has actually produced is
    the other one - a live task parked BLOCKED by a counter that climbed for
    reasons unrelated to looping. So every bound below is asserted together with
    the stop before it, which must still drive the work.

    The eleven block sites in decide(), N of N, and which door each goes
    through:

      charge() - blocks on a run, spends that stage's continuation budget:
        1. approved checkpoint pending (`ready` + commit/push approved)
        2. editing stage, gate FAILED
        3. editing stage, in progress
      bounded_block() - blocks on something with NO run row, spends the nag
      budget for that demand's key:
        4. handoff debt before starting a backlog task      key `handoff:`
        5. memory debt before starting a backlog task       key `memory:`
        6. the latest merged task undocumented              key `main-handoff:`
        7. handoff debt with no other work to drive         key `handoff:`
        8. memory debt with no other work to drive          key `memory:`
      Site 6's namespace is not decoration: the reconciliation at the top of
      decide() clears a key only when its namespace was evaluated, and site 6's
      source (latest_undocumented) is not evaluated there. Bounds asserted by
      DebtBoundBeforeStartingTest, site 6 included.
      deliberately uncounted, because they cannot repeat:
        9. surfacing a BLOCKED run - one-shot, marked by `awaiting_human`
       10. drift reconciliation - it reconciles BEFORE blocking, so the next
           stop finds nothing left to reconcile
      deliberately uncounted, and the one residual risk:
       11. "start the next ready task" - there is no run to charge (that is what
           the instruction asks the orchestrator to create), and bounding it
           would make the queue go silent, which is the halt failure
           rules/orchestration.md says never to buy. It repeats only while the
           orchestrator declines an instruction it could satisfy in one turn.
    """

    def charge(self, cont: int, ceiling: int = 30):
        """One charge() against a run with `cont` continuations already spent.
        Returns (may_block, the set_fields mock)."""
        calls = unittest.mock.MagicMock()
        with unittest.mock.patch.object(stop_gate.state, "set_fields", calls):
            return stop_gate.charge(None, run(continuations=cont), ceiling), calls

    def test_the_last_continuation_in_the_budget_is_still_spent_not_parked(self):
        # Off by one in the other direction parks a task one stop early, which
        # is the failure mode this whole class exists for.
        may_block, calls = self.charge(29, ceiling=30)
        self.assertTrue(may_block)
        self.assertEqual(30, calls.call_args.kwargs["continuations"])
        self.assertNotIn("stage_status", calls.call_args.kwargs)

    def test_the_budget_ends_in_a_park_and_the_caller_stops_blocking(self):
        may_block, calls = self.charge(30, ceiling=30)
        self.assertFalse(may_block)
        self.assertEqual(state.ST_BLOCKED, calls.call_args.kwargs["stage_status"])

    def test_a_healthy_editing_run_is_driven_every_stop_below_the_ceiling(self):
        # The whole budget, one stop at a time: each one must still say what to
        # do, and none of them may park the run.
        for cont in (0, 1, 15, 29):
            with self.subTest(continuations=cont):
                calls = unittest.mock.MagicMock()
                reason = decide_with([run(stage="implement", continuations=cont)],
                                     set_fields=calls)
                self.assertIn("task-0001 is at stage 'implement'", reason)
                self.assertEqual(cont + 1, calls.call_args.kwargs["continuations"])
                self.assertNotIn(state.ST_BLOCKED,
                                 [c.kwargs.get("stage_status")
                                  for c in calls.call_args_list])

    def test_an_approved_checkpoint_never_acted_on_stops_repeating(self):
        # Before task-0018 this branch was uncounted: the hook told the
        # orchestrator to commit, on every stop, for ever.
        for gate, flag in (("commit", "commit_approved"), ("push", "push_approved")):
            with self.subTest(gate=gate):
                calls = unittest.mock.MagicMock()
                reason = decide_with([run(stage="ready", awaiting_human=gate,
                                          **{flag: 1})], set_fields=calls)
                self.assertIn("checkpoint approved", reason)
                self.assertEqual(1, calls.call_args.kwargs["continuations"])

                # Past the budget the stop that spends it parks the run and
                # falls through; the park is then surfaced on the NEXT stop,
                # where the status reads BLOCKED. Both halves are asserted -
                # pinning only the first would pin the silence.
                calls = unittest.mock.MagicMock()
                decide_with([run(stage="ready", awaiting_human=gate,
                                 continuations=30, **{flag: 1})],
                            set_fields=calls, backlog=())
                self.assertEqual(state.ST_BLOCKED,
                                 calls.call_args.kwargs["stage_status"])

                nxt = decide_with([run(stage="ready", awaiting_human=gate,
                                       status=state.ST_BLOCKED, **{flag: 1})])
                self.assertIn("task-0001 is parked BLOCKED", nxt)

    def test_the_park_this_branch_produces_is_surfaced_not_silent(self):
        """Bounding a branch must not trade a loud failure for a quiet one.

        The park sets `stage_status` BLOCKED while `awaiting_human` is still
        'commit' - that branch cannot fire with any other value - so a
        surfacing keyed on "is anything awaited" never fired. Measured before
        the fix: two stops, both None, no writes on the second, and the queue
        held because `ready` counts as occupied. Nothing else covers it -
        supervisor.classify answers HEALTHY for both a blocked run and an
        awaited one, and handle_run does not notify on HEALTHY."""
        blocked = run(stage="ready", awaiting_human="commit", commit_approved=1,
                      status=state.ST_BLOCKED)
        calls = unittest.mock.MagicMock()
        reason = decide_with([blocked], set_fields=calls)

        self.assertIsNotNone(reason)
        self.assertIn("task-0001 is parked BLOCKED", reason)
        self.assertIn("--reject", reason)
        self.assertNotIn("Start the next ready task", reason)
        # Stamped, so the surfacing is once rather than a stop loop.
        self.assertEqual(stop_gate.AWAITING_BLOCKED,
                         calls.call_args.kwargs["awaiting_human"])
        self.assertIsNone(decide_with([run(stage="ready", status=state.ST_BLOCKED,
                                           awaiting_human=stop_gate.AWAITING_BLOCKED)]))

    def test_a_busy_marker_keeps_the_approved_checkpoint_branch_quiet(self):
        # The orchestrator is mid-commit. Nagging it now spends the budget of a
        # run that is being worked on, which is how a healthy task gets parked.
        calls = unittest.mock.MagicMock()
        self.assertIsNone(decide_with([run(stage="ready", awaiting_human="commit",
                                           commit_approved=1)],
                                      set_fields=calls, busy=True, backlog=()))
        self.assertEqual(0, calls.call_count)

    def test_a_fresh_marker_anywhere_silences_every_run_less_branch(self):
        # Debt, the queue and the drift reconciler all talk to an agent that is
        # already working. The handoff-debt loop measured on tasks 0004, 0005,
        # 0007, 0008 and 0009 is this case.
        debt = ({"task": "task-0067", "reason": "no handoff doc"},)
        for kw in ({"debt": debt}, {"mem_debt": debt}, {}):
            with self.subTest(**kw):
                self.assertIsNone(decide_with([], any_busy=True, **kw))
        # Control: without the marker each of those DOES speak up.
        self.assertIn("task-0067", decide_with([], debt=debt))
        self.assertIn("Start the next ready task", decide_with([]))

    def test_continuations_has_exactly_one_writer(self):
        # The invariant is structural, not a habit: a new branch cannot bump the
        # counter its own way, and cannot quietly skip it either, because
        # charge() is the only thing that writes the column.
        src = Path(stop_gate.__file__).read_text(encoding="utf-8")
        self.assertEqual(1, src.count("continuations=cont"))
        self.assertIn("continuations=cont", inspect.getsource(stop_gate.charge))

    def test_every_block_site_in_decide_is_accounted_for(self):
        # Twelve. Eleven are enumerated in this class's docstring; the twelfth
        # is task-0020's, the run-loop branch for a live run on a stage its own
        # flow defines and editing_stages does not name. It goes through the
        # charge() door like the other five run-bearing sites, which is what
        # UnlistedStageIsDrivenTest asserts on the counter itself. A new branch
        # changes a count here and the author has to say which door it uses.
        src = inspect.getsource(stop_gate.decide)
        self.assertEqual(7, src.count("return block("))
        self.assertEqual(5, src.count("return bounded_block("))
        # And none of them may reach the channel directly. block() is where the
        # reason-repeat backstop lives (task-0019) and bounded_block() routes
        # through it, so emit() is the one way to print a block WITHOUT a bound.
        # Counting only the two names above would let a twelfth branch written as
        # `return emit(...)` leave both numbers untouched and skip the backstop
        # entirely - a bypass that did not exist before emit() got a public name.
        self.assertEqual(0, src.count("emit("))


class ReadyStageOccupiesTest(unittest.TestCase):
    """FR-37: a task at `ready` owns its branch and its dirty tree whether or
    not a human is being waited on.

    With every checkpoint auto-approved, `awaiting_human` is empty and `ready`
    is not an editing stage, so the free-slot calculation read a free slot and
    the queue started the next backlog task on top of a checked-out branch."""

    def test_a_ready_run_with_no_awaiting_human_is_not_a_free_slot(self):
        self.assertIsNone(decide_with([run(stage="ready")]))

    def test_the_other_stages_still_answer_as_before(self):
        # Control: the fix must not freeze the queue on a finished run.
        self.assertIn("Start the next ready task",
                      decide_with([run(stage="done")]))


# Site 6's demand: the newest task-tagged commit on main with no handoff doc.
# Module level rather than a class attribute, which ruff reads as a mutable
# default (RUF012) - the sibling DEBT/MEM fixtures are tuples and escape it.
LATEST_UNDOCUMENTED = {"task": "task-0077", "sha": "abcdef123456",
                       "reason": "no handoff doc"}


class DebtBoundBeforeStartingTest(unittest.TestCase):
    """The debt block that gates a backlog task was the unbounded copy.

    Its twin, the one that fires when there is nothing else to drive, has been
    bounded since task-0072's follow-up; this one repeated for ever, and it is
    the copy that fired in the measured loop. Both now spend the same budget,
    keyed by the debt, because it is one debt and which copy speaks depends only
    on whether a backlog task happened to exist."""

    DEBT = ({"task": "task-0067", "reason": "no handoff doc"},)

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "startdebt")
        p = unittest.mock.patch.object(state, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def stop(self, **kw) -> str | None:
        """One stop with a free slot, a ready backlog task, and the REAL counter."""
        return decide_with([], count_nags=True, **kw)

    def test_the_debt_gates_the_start_then_escalates_then_goes_quiet(self):
        for n in range(1, stop_gate.DEBT_NAG_CEILING + 1):
            with self.subTest(stop=n):
                reason = self.stop(debt=self.DEBT)
                self.assertIn("Before starting task-0002", reason)
                self.assertNotIn("AskUserQuestion", reason)

        reason = self.stop(debt=self.DEBT)
        self.assertIn("AskUserQuestion", reason)
        self.assertIn("task-0067", reason)

        for n in range(3):
            with self.subTest(after_escalation=n):
                self.assertIsNone(self.stop(debt=self.DEBT))

    def test_a_spent_key_does_not_mute_the_same_demand_when_it_recurs(self):
        """A spent nag key must not outlive the demand that spent it.

        The clearing used to sit at the LAST branch of decide(), which the run
        loop returns before on every stop that has work to drive. Measured
        against the pre-fix code: budget spent, three stops with an editing run
        in flight, file still {"key": "handoff:task-0067", "count": 5}, and the
        same demand recurring was allowed without being uttered once. The count
        is bookkeeping, so it is now reconciled above the loop, on every stop."""
        for _ in range(stop_gate.DEBT_NAG_CEILING + 2):
            self.stop(debt=self.DEBT)
        # Stops with a run to drive: the loop returns and every branch below it
        # is skipped. The debt is gone in this window (the doc was written).
        for _ in range(3):
            decide_with([run(stage="implement")], count_nags=True)
        self.assertFalse((self.tmp / stop_gate.DEBT_NAG_FILE).exists())
        # The doc is edited back below min_section_chars later on.
        reason = self.stop(debt=self.DEBT)
        self.assertIn("Before starting task-0002", reason)
        self.assertNotIn("AskUserQuestion", reason)

    def test_the_merged_task_demand_escalates_then_goes_quiet(self):
        """Site 6 is bounded by a namespace of its own, and needs its own test.

        It fires only when handoff and memory debt are both empty, so the
        reconciliation at the top of decide() sees an EMPTY live set on exactly
        the stops this demand speaks on. Sharing the `handoff:` namespace made
        every one of those stops look like "that key is dead": measured, eight
        consecutive stops all at count 1, never escalating and never muting.
        `main-handoff:` is outside RECONCILED_PREFIXES, so an evaluation that
        did not look at it leaves it alone."""
        for n in range(1, stop_gate.DEBT_NAG_CEILING + 1):
            with self.subTest(stop=n):
                reason = self.stop(latest=LATEST_UNDOCUMENTED)
                self.assertIn("task-0077", reason)
                self.assertNotIn("AskUserQuestion", reason)

        self.assertIn("AskUserQuestion", self.stop(latest=LATEST_UNDOCUMENTED))
        for n in range(3):
            with self.subTest(after_escalation=n):
                self.assertIsNone(self.stop(latest=LATEST_UNDOCUMENTED))

    def test_the_merged_task_demand_keeps_its_own_budget(self):
        # The namespace must separate the budgets in both directions: spending
        # the handoff budget must not mute a demand that has said nothing.
        for _ in range(stop_gate.DEBT_NAG_CEILING + 2):
            self.stop(debt=self.DEBT)
        reason = self.stop(latest=LATEST_UNDOCUMENTED)
        self.assertIn("task-0077", reason)
        self.assertNotIn("AskUserQuestion", reason)

    def test_an_outstanding_key_keeps_its_spent_budget(self):
        # The other half: clearing per key must not hand a live demand a fresh
        # budget on every stop, which would make the bound unreachable.
        for _ in range(stop_gate.DEBT_NAG_CEILING):
            self.stop(debt=self.DEBT)
        self.assertIn("AskUserQuestion", self.stop(debt=self.DEBT))

    def test_the_two_copies_of_one_debt_share_one_budget(self):
        # Spend it through the free-slot copy...
        for _ in range(stop_gate.DEBT_NAG_CEILING + 2):
            self.stop(debt=self.DEBT)
        # ...and the no-work-to-drive copy is spent too. Two budgets for one
        # demand would mean twice as many stops before the CEO hears about it.
        self.assertIsNone(decide_with([run(stage="ready", awaiting_human="commit")],
                                      count_nags=True, debt=self.DEBT))


class ScaffoldWritesABusyMarkerTest(unittest.TestCase):
    """handoff.py --for is run immediately before the agent that fills the doc
    is dispatched, so it is the signal that the work is in flight. Without it
    the Stop hook demands the doc on every stop while it is being written."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "scaffoldbusy")
        for target, attr, value in (
                (state, "STATE_DIR", self.tmp),
                (handoff, "handoff_dir", lambda: self.tmp / "handoffs"),
                (handoff, "_merge_facts", lambda task: ("abc123", "a.py")),
                (handoff, "_task_title", lambda task: "A title")):
            p = unittest.mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def test_scaffolding_marks_the_task_busy(self):
        self.assertFalse(stop_gate.busy_marker_fresh("task-0099"))
        created, msg = handoff.scaffold("task-0099")
        self.assertTrue(created)
        self.assertTrue(stop_gate.busy_marker_fresh("task-0099"))
        self.assertTrue(stop_gate.any_busy_marker_fresh())
        # A marker the reader cannot see the end of is the halt failure. The
        # message names both the bound and the command that ends it early.
        self.assertIn(str(int(state.BUSY_TIMEOUT)), msg)
        self.assertIn("--idle", msg)

    def test_the_marker_goes_when_the_doc_validates(self):
        # 900s is a ceiling, not a price to pay in full: the marker's reason is
        # "an agent is filling this doc", and it ends when the doc validates.
        handoff.scaffold("task-0099")
        self.assertTrue(stop_gate.busy_marker_fresh("task-0099"))

        handoff.drop_scaffold_markers({"task-0099"})          # still owed
        self.assertTrue(stop_gate.busy_marker_fresh("task-0099"))

        handoff.drop_scaffold_markers(set())                  # doc validates
        self.assertFalse(stop_gate.busy_marker_fresh("task-0099"))

    def test_a_gate_marker_is_never_dropped_by_the_handoff_check(self):
        # A marker written by advance.py around a live stage belongs to a RUN.
        # Unlinking it would start nagging an agent that is still working, so
        # only markers this module wrote (stage == "handoff") are touched.
        state.write_busy_marker("task-0100", "implement")
        handoff.drop_scaffold_markers(set())
        self.assertTrue(stop_gate.busy_marker_fresh("task-0100"))


# The plan flow, as a literal, in the shape task-0020 gave it. Same reason
# BUILD_PIPELINE is a literal: these assertions must survive an edit to
# .agentry/pipeline.json. `approval` carries `checkpoint` SINGULAR, which is
# what the real file says and what advance.stage_checkpoints() does not read -
# the difference is load-bearing below, so it is reproduced rather than tidied.
PLAN_PIPELINE = {
    "retry_budget": 3,
    "pipelines": {"plan": {
        "editing_stages": ["formalize", "draft", "plan-review", "breakdown"],
        "stages": [
            {"name": "formalize", "owner": "architect"},
            {"name": "draft", "owner": "architect"},
            {"name": "plan-review", "owner": "reviewer"},
            {"name": "approval", "owner": "ceo", "checkpoint": "plan"},
            {"name": "breakdown", "owner": "product-manager"},
            {"name": "done", "owner": "ceo"},
        ],
    }},
}

# The same flow with the key as it shipped before task-0020. Kept so the defect
# can be forced back in one argument instead of being described in a comment.
PLAN_PIPELINE_UNSET = json.loads(json.dumps(PLAN_PIPELINE))
PLAN_PIPELINE_UNSET["pipelines"]["plan"]["editing_stages"] = []


def plan_run(task="task-0001", stage="draft", **extra) -> dict:
    return run(task=task, stage=stage, pipeline=state.PLAN, **extra)


class PlanRunIsInFlightTest(unittest.TestCase):
    """FR-39: `editing = state.EDITING_STAGES` made half the conveyor invisible.

    The constant names three BUILD stages, so a `plan` run mid-flight matched no
    branch of the run loop: nothing drove it, and the free-slot test read a tree
    nobody was holding, so the queue started a backlog task on top of it.

    The source swap alone does not fix it, and that is the substance of the
    task rather than a footnote: the shipped config said `"editing_stages": []`
    for this flow, so resolving from config would have made a plan run LESS in
    flight than the constant did. The set had to be decided and written down."""

    # Two branches can speak about the same run, and their messages share a
    # prefix, so every assertion below names the branch rather than the task.
    # Measured: with `editing = state.EDITING_STAGES` put back, all of this
    # class passed on `assertIn("task-0001 is at stage 'draft'")` alone, because
    # the fallback branch says that too. A test that cannot see the defect it
    # was written for is worse than no test.
    DRIVEN = "Finish the stage work (dispatch the owning"
    UNLISTED = "editing_stages does not list"

    def test_a_plan_run_mid_flight_is_driven_as_editing_work(self):
        reason = decide_with([plan_run(stage="draft")], pipeline=PLAN_PIPELINE)
        self.assertIsNotNone(reason)
        self.assertIn("task-0001 is at stage 'draft'", reason)
        self.assertIn(self.DRIVEN, reason)
        self.assertNotIn(self.UNLISTED, reason)
        self.assertNotIn("Start the next ready task", reason)

    def test_every_agent_owned_plan_stage_is_driven_not_just_draft(self):
        # The criterion names `draft`; pinning only `draft` would pass with a
        # one-element list and leave the other three stages exactly as broken.
        for stage in ("formalize", "draft", "plan-review", "breakdown"):
            with self.subTest(stage=stage):
                reason = decide_with([plan_run(stage=stage)], pipeline=PLAN_PIPELINE)
                self.assertIn(f"task-0001 is at stage '{stage}'", reason)
                self.assertIn(self.DRIVEN, reason)
                self.assertNotIn(self.UNLISTED, reason)

    def test_the_empty_key_that_shipped_is_the_state_the_fix_had_to_reach(self):
        # RED, forced back: with editing_stages [] the drive branch cannot see
        # the run. What it must NOT do any more is go silent, so both halves are
        # asserted - the stage is not driven as editing work, and the run is
        # still spoken about, by the branch task-0020 added for exactly this.
        reason = decide_with([plan_run(stage="draft")], pipeline=PLAN_PIPELINE_UNSET)
        self.assertIsNotNone(reason)
        self.assertIn(self.UNLISTED, reason)
        self.assertNotIn(self.DRIVEN, reason)
        self.assertNotIn("Start the next ready task", reason)

    def test_the_shipped_config_really_lists_the_plan_stages(self):
        # The fixtures above are literals, so every assertion in this class
        # would pass against a pipeline.json that still says []. This is the one
        # test that reads the real file, and it is what makes the rest mean
        # something on this repository.
        listed = state.editing_stages(state.load_pipeline(), state.PLAN)
        self.assertIn("draft", listed)
        self.assertEqual(("formalize", "draft", "plan-review", "breakdown"), listed)

    def test_a_plan_run_holds_the_queue_once_its_budget_is_spent(self):
        # The queue half, which the drive branch hides: while it returns a block
        # the free-slot test is never reached. Spend the budget and the branch
        # falls through, which is the stop the old code would have offered
        # task-0002 on.
        calls = unittest.mock.MagicMock()
        first = decide_with([plan_run(stage="draft", continuations=30)],
                            pipeline=PLAN_PIPELINE, set_fields=calls)
        self.assertEqual(state.ST_BLOCKED, calls.call_args.kwargs["stage_status"])
        self.assertNotIn("Start the next ready task", first or "")

        # And on the stop after the park, still nothing offered: the run is
        # surfaced BLOCKED by name.
        nxt = decide_with([plan_run(stage="draft", status=state.ST_BLOCKED)],
                          pipeline=PLAN_PIPELINE)
        self.assertIn("task-0001 is parked BLOCKED", nxt)
        self.assertNotIn("Start the next ready task", nxt)

    def test_a_finished_plan_run_still_frees_the_slot(self):
        # Control against the opposite failure: a set that holds the queue for
        # ever is as broken as one that never holds it.
        self.assertIn("Start the next ready task",
                      decide_with([plan_run(stage="done")], pipeline=PLAN_PIPELINE))

    def test_a_busy_marker_silences_the_plan_drive_branch_too(self):
        calls = unittest.mock.MagicMock()
        self.assertIsNone(decide_with([plan_run(stage="draft")], pipeline=PLAN_PIPELINE,
                                      set_fields=calls, busy=True, any_busy=True,
                                      backlog=()))
        self.assertEqual(0, calls.call_count)


class UnlistedStageIsDrivenTest(unittest.TestCase):
    """FR-39, fourth criterion: a stage the flow defines and editing_stages does
    not name must not fall out of the loop in silence.

    Invisible while the set was a build-only constant - every build stage is
    claimed by a branch above - and a live hole the moment the set is config,
    because leaving a stage out of the key would otherwise mean "the conveyor
    never mentions this run again"."""

    # The example is `draft` under the config as it SHIPPED (editing_stages
    # empty), not `approval`. `approval` was this class's example until the
    # checkpoint set became config-resolved, at which point it stopped being an
    # unlisted stage and became a protected one - the two tests below errored on
    # `NoneType` the moment that landed, which is the branch correctly going
    # quiet. An omitted work stage is what this branch is actually for.
    def test_the_unlisted_stage_is_named_and_charged(self):
        calls = unittest.mock.MagicMock()
        reason = decide_with([plan_run(stage="draft")], pipeline=PLAN_PIPELINE_UNSET,
                             set_fields=calls)
        self.assertIn("task-0001 is at stage 'draft' of the 'plan' flow", reason)
        self.assertIn("advance.py --task task-0001", reason)
        # Charged through the same door as the other run-bearing sites: the
        # counter is what bounds it, and asserting the text alone would pass on
        # an unbounded branch - which is the defect task-0018 spent a task on.
        self.assertEqual(1, calls.call_args.kwargs["continuations"])

    def test_it_parks_the_run_when_the_budget_is_gone_rather_than_repeating(self):
        # A real queue, not `backlog=()`. An empty backlog makes the free-slot
        # branch unreachable whatever it decides, which would hide the same hole
        # the neighbouring test exists to catch, from the other side.
        calls = unittest.mock.MagicMock()
        decide_with([plan_run(stage="draft", continuations=30)],
                    pipeline=PLAN_PIPELINE_UNSET, set_fields=calls)
        self.assertEqual(state.ST_BLOCKED, calls.call_args.kwargs["stage_status"])

    def test_the_drive_branch_does_not_nag_over_a_human(self):
        # A GUARD, pinned as a guard: no writer of `awaiting_human` produces an
        # editing-stage run with the column set today, so this state is
        # assembled rather than reproduced and the test proves only that the
        # guard is present. It is here because the editing set is configuration
        # now - put a checkpoint stage in some flow's editing_stages and this
        # becomes reachable, at which point the conveyor would nag over the
        # human and task-0019's backstop would park the run on the third stop.
        calls = unittest.mock.MagicMock()
        self.assertIsNone(decide_with([plan_run(stage="draft", awaiting_human="plan")],
                                      pipeline=PLAN_PIPELINE, set_fields=calls))
        self.assertEqual(0, calls.call_count)

    def test_the_parked_run_still_occupies_the_tree_on_the_stop_that_parks_it(self):
        # THE PARKING STOP ITSELF, reproduced rather than assembled. The first
        # version of this test handed decide() a run that was ALREADY
        # ST_BLOCKED, which the surfacing branch intercepts and blocks on before
        # the free-slot test is ever reached - so it passed with the clause it
        # was written for deleted. Feed the state the machine actually has at
        # that moment instead: in_progress, budget exhausted, so charge() parks
        # it inside this call and the loop falls through to the queue.
        reason = decide_with([plan_run(stage="draft", continuations=30)],
                             pipeline=PLAN_PIPELINE_UNSET)
        self.assertNotIn("Start the next ready task", reason or "")

    def test_the_stop_after_the_park_surfaces_it_rather_than_offering_work(self):
        # The next stop, with the status charge() wrote. Different branch,
        # different guarantee: this one is the surfacing, and it is asserted
        # separately so that neither test can stand in for the other.
        nxt = decide_with([plan_run(stage="draft", status=state.ST_BLOCKED)],
                          pipeline=PLAN_PIPELINE_UNSET)
        self.assertIn("task-0001 is parked BLOCKED", nxt)
        self.assertNotIn("Start the next ready task", nxt)

    def test_a_checkpoint_stage_is_never_nagged_as_stage_work(self):
        # `ready` carries `checkpoints`, so its work is the CEO's approval and
        # the demand would be addressed to nobody. Since task-0019 that stable
        # sentence would also park the run on the third repeat, costing an
        # approval the CEO had already given. Silence here, and the queue is
        # held by the free-slot test instead.
        calls = unittest.mock.MagicMock()
        self.assertIsNone(decide_with([run(stage="ready")], pipeline=BUILD_PIPELINE,
                                      set_fields=calls))
        self.assertEqual(0, calls.call_count)

    def test_a_stage_the_flow_does_not_define_is_left_to_stranded(self):
        # Excluded deliberately: advance.py blocks a stranded run by name, and
        # the free-slot test holds the queue. Nagging it here would duplicate
        # one report and spend a budget on a run whose recovery is already
        # written down.
        self.assertIsNone(decide_with([run(stage="ghost-stage")],
                                      pipeline=BUILD_PIPELINE))

    def test_an_unreadable_pipeline_does_not_make_every_run_a_nag(self):
        # Fail-open control. No stage names means no stage is "defined", so this
        # branch claims nothing and the hook behaves as it did before the branch
        # existed.
        self.assertIn("Start the next ready task task-0002",
                      decide_with([run(stage="ghost-stage")], pipeline={}))


# The plan flow with `approval` wrongly listed as an editing stage. Not a
# configuration anyone should write - it is the one that proves the drive branch
# excludes a checkpoint stage on the strength of the stage definition rather
# than on the strength of it being absent from editing_stages.
PLAN_PIPELINE_APPROVAL_LISTED = json.loads(json.dumps(PLAN_PIPELINE))
PLAN_PIPELINE_APPROVAL_LISTED["pipelines"]["plan"]["editing_stages"] = [
    "formalize", "draft", "plan-review", "approval", "breakdown"]


class CheckpointStageFromConfigTest(unittest.TestCase):
    """The root the CEO asked to be fixed: the free-slot test derived from the
    literal stage name `ready`, so it was right for `build` by coincidence and
    blind to every other flow.

    The plan flow's `approval` is the same kind of stage - a human's - and
    matched neither the literal here nor any branch of the run loop, so the
    conveyor drove the CEO's approval stage AND read its tree as free. A
    special case for `approval` would have been the same bug with a second
    name in it. Both halves are asserted for both flows, because they are one
    decision: what is not nagged must still occupy."""

    def test_a_plan_run_at_approval_is_silent_and_holds_the_queue(self):
        # The case the CEO named. One call, both halves: nothing said about the
        # run, and task-0002 not started while it sits there.
        calls = unittest.mock.MagicMock()
        self.assertIsNone(decide_with([plan_run(stage="approval")],
                                      pipeline=PLAN_PIPELINE, set_fields=calls))
        # Silent AND uncharged - a nag that spends the budget would park the run
        # on the third stop even if the text never reached the CEO.
        self.assertEqual(0, calls.call_count)

    def test_a_build_run_at_ready_is_silent_and_holds_the_queue(self):
        # The same two halves for the flow that already worked, now reached
        # through the config rather than through its name being spelled in the
        # source. This is what the removed literal used to guarantee.
        calls = unittest.mock.MagicMock()
        self.assertIsNone(decide_with([run(stage="ready")], pipeline=BUILD_PIPELINE,
                                      set_fields=calls))
        self.assertEqual(0, calls.call_count)

    def test_both_spellings_of_the_checkpoint_key_count(self):
        # `approval` says `checkpoint`, `ready` says `checkpoints`, and no reader
        # in the tree reads the singular. Pinned so that normalising the odd one
        # out cannot quietly unprotect the stage.
        self.assertEqual(("approval",), state.checkpoint_stages(PLAN_PIPELINE, state.PLAN))
        self.assertEqual(("ready",), state.checkpoint_stages(BUILD_PIPELINE, state.BUILD))

    def test_a_checkpoint_stage_wins_even_if_editing_stages_lists_it(self):
        # The two halves move together or not at all. With `approval` wrongly in
        # editing_stages the drive branch must still leave it alone, and the
        # free-slot test must still hold the tree for it - otherwise the pair
        # comes apart exactly where task-0018's Critical came from.
        calls = unittest.mock.MagicMock()
        self.assertIsNone(decide_with([plan_run(stage="approval")],
                                      pipeline=PLAN_PIPELINE_APPROVAL_LISTED,
                                      set_fields=calls))
        self.assertEqual(0, calls.call_count)

    def test_an_unreadable_config_still_holds_the_tree_for_ready(self):
        # NFR-4, and the direction matters: a config we cannot read must not turn
        # a checkpoint into a free slot. Falls back to the module constant for
        # build exactly as editing_stages() does.
        self.assertEqual(state.CHECKPOINT_STAGES, state.checkpoint_stages({}, state.BUILD))
        self.assertIsNone(decide_with([run(stage="ready")], pipeline={}))

    def test_a_flow_that_declares_no_checkpoint_gets_none_invented(self):
        # An empty answer from a READABLE config is the config speaking. Only an
        # unreadable flow falls back, which is what keeps the fallback from
        # becoming a hidden default.
        no_cp = json.loads(json.dumps(PLAN_PIPELINE))
        for s in no_cp["pipelines"]["plan"]["stages"]:
            s.pop("checkpoint", None)
        self.assertEqual((), state.checkpoint_stages(no_cp, state.PLAN))

    def test_the_word_ready_is_gone_from_the_decide_source(self):
        # The literal is the thing being removed, so its absence is the
        # assertion. Two readers had it; the run loop's remaining `ready`
        # branches key on commit and push, which are build-only columns in
        # run.db, and generalising those needs the checkpoint that does not
        # exist yet - so they stay and are counted here rather than banned.
        src = inspect.getsource(stop_gate.decide)
        code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        self.assertEqual(2, code.count('== "ready"'))
        self.assertIn('aw == "commit"', code)


class BuildFlowUnchangedTest(unittest.TestCase):
    """FR-39's third criterion: for the flow that already worked this is a
    source swap, not a semantic one.

    The swap is only safe because `state.editing_stages()` falls back to the
    module constant for `build` when the key is absent, so the fixtures here -
    which carry no editing_stages key at all - resolve to exactly what the
    constant gave the old code."""

    def test_each_editing_stage_is_driven_as_before(self):
        for stage in state.EDITING_STAGES:
            with self.subTest(stage=stage):
                reason = decide_with([run(stage=stage)], pipeline=BUILD_PIPELINE)
                self.assertIn(f"task-0001 is at stage '{stage}'", reason)
                # The drive branch, not task-0020's fallback: the two messages
                # share a prefix and only this phrase tells them apart.
                self.assertIn("Finish the stage work (dispatch the owning", reason)
                self.assertNotIn("editing_stages does not list", reason)

    def test_the_absent_key_resolves_to_the_module_constant(self):
        self.assertEqual(state.EDITING_STAGES,
                         state.editing_stages(BUILD_PIPELINE, state.BUILD))

    def test_a_ready_run_with_no_awaiting_human_is_still_silent_and_still_occupies(self):
        # Both halves of the decision to leave `ready` out of editing_stages and
        # keep it as its own clause in the free-slot test.
        self.assertIsNone(decide_with([run(stage="ready")], pipeline=BUILD_PIPELINE))

    def test_a_done_run_still_frees_the_slot(self):
        self.assertIn("Start the next ready task",
                      decide_with([run(stage="done")], pipeline=BUILD_PIPELINE))


class CrashIsReportedTest(unittest.TestCase):
    """FR-40: a Stop hook that crashes must not look like one that released.

    The spec asked for this as a `StopFailure` hook registration. Measured on
    build 2.1.269, that event is not what it sounds like: it fires INSTEAD of
    Stop when an API error ends the turn (its `error` field carries
    `rate_limit`, `max_output_tokens` and ten siblings), and it cannot observe a
    hook failure at all. The reporting channel for THAT already exists in the
    harness - the `stop-hook-error` notification, raised on any non-zero exit
    from a Stop hook - and the single thing hiding it was main() catching every
    exception and returning 0, the one exit code the harness shows nothing for.

    So what is pinned here is the exit code, not a message: on exit 0 there is
    nothing for a test to read, because the silence IS the defect."""

    @contextmanager
    def crashing(self, exc=None):
        err = io.StringIO()
        out = io.StringIO()
        boom = exc or RuntimeError("deliberate crash inside decide")
        with unittest.mock.patch.object(stop_gate, "decide", side_effect=boom), \
                unittest.mock.patch.object(sys, "stdin",
                                           io.StringIO('{"hook_event_name": "Stop"}')), \
                unittest.mock.patch.object(sys, "stderr", err), \
                redirect_stdout(out):
            yield lambda: stop_gate.main(), err, out

    def test_a_crash_exits_non_zero_so_the_harness_reports_it(self):
        # Delete the `return CRASH_EXIT` line (or put back `return allow()`) and
        # this is the assertion that goes red: 0 is the value that produced no
        # notification, no stderr and no stream event in the measurement.
        with self.crashing() as (call, _err, _out):
            self.assertNotEqual(0, call())

    def test_a_crash_still_fails_open(self):
        # NFR-4, and the half that must not be traded for the half above. Exit 2
        # is the only code that BLOCKS a stop and feeds stderr to the model, so
        # reporting the crash must never reach for it: that would trap the
        # session in a stop loop on the way to being loud about it.
        with self.crashing() as (call, _err, out):
            self.assertNotEqual(2, call())
        # And nothing may reach stdout, which is where a block decision lives.
        self.assertEqual("", out.getvalue())

    def test_the_traceback_reaches_stderr(self):
        # The exit code raises the notification; stderr is what ctrl+o then
        # shows. Without it the human is told a hook failed and not which one.
        with self.crashing() as (call, err, _out):
            call()
        text = err.getvalue()
        self.assertIn("deliberate crash inside decide", text)
        self.assertIn("Traceback", text)

    def test_a_healthy_stop_stays_silent(self):
        # The other direction: this must not turn every ordinary stop into a
        # notification. A decide() that returns normally keeps its own code and
        # writes no stderr.
        err = io.StringIO()
        with unittest.mock.patch.object(stop_gate, "decide", return_value=0), \
                unittest.mock.patch.object(sys, "stdin",
                                           io.StringIO('{"hook_event_name": "Stop"}')), \
                unittest.mock.patch.object(sys, "stderr", err):
            self.assertEqual(0, stop_gate.main())
        self.assertEqual("", err.getvalue())

    def test_junk_in_the_payload_does_not_swallow_a_crash(self):
        # Fail-open direction under junk. The payload read has its own handler,
        # and an unreadable one must not route around the report: whatever the
        # stdin held, a crashing decide() still exits non-zero and still says
        # why. `None` and `[]` are the shapes that parse and then explode on
        # .get(), which is how the stop-repeat counter was pinned at 1 for ever.
        for junk in ("", "{not json", "null", "[1,2,3]", '{"stop_hook_active": "yes"}'):
            with self.subTest(payload=junk):
                err = io.StringIO()
                with unittest.mock.patch.object(stop_gate, "decide",
                                                side_effect=RuntimeError("boom")), \
                        unittest.mock.patch.object(sys, "stdin", io.StringIO(junk)), \
                        unittest.mock.patch.object(sys, "stderr", err):
                    self.assertNotEqual(0, stop_gate.main())
                self.assertIn("boom", err.getvalue())

    def test_a_crash_that_is_not_an_exception_still_propagates(self):
        # BaseException is deliberately NOT caught: KeyboardInterrupt and
        # SystemExit are the user and the interpreter, not a hook defect, and
        # swallowing them into a tidy exit code is how a Ctrl-C gets eaten.
        with self.assertRaises(KeyboardInterrupt):
            with self.crashing(KeyboardInterrupt()) as (call, _err, _out):
                call()


# Deliberately module-level, matching BUILD_PIPELINE/PLAN_PIPELINE above: a
# dict as a class attribute trips RUF012 (mutable default), and this one is
# never mutated by the tests that read it.
GATE_TIMEOUT_PIPELINE = {"pipelines": {"build": {"stages": [
    {"name": "test", "owner": "qa-engineer",
     "exit_gate": {"cmd": "sleep 999999", "expect_exit": 0}},
]}}}


class GateNeverReturnsTest(unittest.TestCase):
    """task-0022, loop shape 4: a stage exit-gate command that never returns.

    gate.run_gate() bounds the subprocess call with `timeout=900` and catches
    subprocess.TimeoutExpired. If either half were missing, a hung gate
    command would hang gate.py, which hangs advance.py, which hangs the
    orchestrator turn that called it - nothing downstream (not stop_gate.py,
    not the supervisor) can see a process that never returns, only a stage
    that never advances. No sleeping: the timeout is proven by mocking
    subprocess.run to raise the exception the real timeout raises after
    900 seconds, not by waiting for one."""

    def test_a_hung_gate_command_degrades_to_a_failed_gate_not_a_hang(self):
        timeout_exc = subprocess.TimeoutExpired(cmd="sleep 999999", timeout=900)
        with unittest.mock.patch.object(gate_module.subprocess, "run",
                                        side_effect=timeout_exc):
            result = gate_module.run_gate(GATE_TIMEOUT_PIPELINE, "test", task=None)

        self.assertEqual(124, result["exit"])
        self.assertFalse(result["passed"])
        self.assertIn("timed out", result["output"])

    def test_the_subprocess_call_itself_asks_for_a_bound(self):
        # The other half: gate.py must ASK for a timeout, not merely survive
        # one. Without `timeout=` in the call, subprocess.run blocks for real
        # and the except clause above never has anything to catch.
        calls = unittest.mock.MagicMock(
            return_value=unittest.mock.MagicMock(returncode=0, stdout="", stderr=""))
        with unittest.mock.patch.object(gate_module.subprocess, "run", calls):
            gate_module.run_gate(GATE_TIMEOUT_PIPELINE, "test", task=None)

        self.assertIn("timeout", calls.call_args.kwargs)
        self.assertGreater(calls.call_args.kwargs["timeout"], 0)


class CrossMechanismInteractionTest(unittest.TestCase):
    """The interaction task-0022 was reframed around: task-0018's per-stage
    charge() budget, task-0019's reason-repeat backstop, and task-0020's
    config-resolved stage sets, all firing on the SAME run.

    None of the three tasks' own suites cross them. StopHookActiveBackstopTest
    fixes `continuations` at 0 by construction, and says why in its own
    comment: advance.py zeroes that column on every stage transition, so a
    run that keeps transitioning never accumulates. PlanRunIsInFlightTest and
    UnlistedStageIsDrivenTest never arm stop_hook_active. So a run that is
    genuinely being charged for real work, while ALSO tripping the backstop on
    a stable reason, was unpinned - which is exactly what happened during this
    task's own session: a healthy task was parked at continuations=4, nowhere
    near the ceiling of 30, because the backstop counts CONSECUTIVE ARMED
    stops and charge() counts every stop whether armed or not. One unarmed
    stop (a human just spoke) plus three armed ones reproduces it: the
    backstop trips on its third armed repeat while the budget has spent four."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "crossmech")
        p = unittest.mock.patch.object(state, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def _sequence(self, r, active_pattern, pipeline=None, **kw):
        """Feed decide() the same run through `active_pattern` stops,
        updating `continuations` from what the PREVIOUS stop actually wrote -
        the same bookkeeping advance.py persists between real stops, rather
        than a fixture frozen at one value. Returns (reasons, the set_fields
        mock covering every stop)."""
        calls = unittest.mock.MagicMock()
        reasons = []
        for active in active_pattern:
            with unittest.mock.patch.object(stop_gate, "STOP_HOOK_ACTIVE", active):
                reasons.append(decide_with([r], set_fields=calls, pipeline=pipeline, **kw))
            for c in reversed(calls.call_args_list):
                if "continuations" in c.kwargs:
                    r = dict(r, continuations=c.kwargs["continuations"])
                    break
        return reasons, calls

    def _parks(self, calls) -> list:
        return [c for c in calls.call_args_list
                if c.kwargs.get("stage_status") == state.ST_BLOCKED]

    def test_a_low_continuation_editing_run_is_still_parked_by_the_backstop(self):
        r = run(stage="implement", continuations=0)
        reasons, calls = self._sequence(r, [False, True, True, True])

        # Three stops speak the same demand; the fourth parks in silence and
        # is surfaced on the NEXT stop instead (StopHookActiveBackstopTest
        # already owns that surfacing - this class is only about the numbers
        # that lead up to it).
        for reason in reasons[:3]:
            self.assertIn("task-0001 is at stage 'implement'", reason)
        self.assertIsNone(reasons[3])

        self.assertEqual(1, len(self._parks(calls)))
        cont_writes = [c.kwargs["continuations"] for c in calls.call_args_list
                      if "continuations" in c.kwargs]
        self.assertEqual([1, 2, 3, 4], cont_writes)
        self.assertLess(cont_writes[-1], 30)  # nowhere near continuation_ceiling

    def test_the_ready_checkpoint_branch_parks_the_same_way(self):
        # A different charge() call site (site 1, not site 3) feeding the same
        # backstop - the approval already given is not what trips it.
        r = run(stage="ready", awaiting_human="commit", commit_approved=1,
               continuations=0)
        reasons, calls = self._sequence(r, [False, True, True, True])

        for reason in reasons[:3]:
            self.assertIn("checkpoint approved", reason)
        self.assertIsNone(reasons[3])

        parks = self._parks(calls)
        self.assertEqual(1, len(parks))
        # The park never stamps awaiting_human itself - see
        # StopHookActiveBackstopTest.test_the_park_is_surfaced_rather_than_silent
        # for why that matters. Re-asserted here because this is a different
        # call site reaching the same park() call.
        self.assertNotIn("awaiting_human", parks[0].kwargs)

    def test_a_plan_pipeline_editing_stage_is_parked_the_same_way(self):
        r = plan_run(stage="draft", continuations=0)
        reasons, calls = self._sequence(r, [False, True, True, True],
                                        pipeline=PLAN_PIPELINE)

        for reason in reasons[:3]:
            self.assertIn("task-0001 is at stage 'draft'", reason)
        self.assertIsNone(reasons[3])

        self.assertEqual(1, len(self._parks(calls)))
        cont_writes = [c.kwargs["continuations"] for c in calls.call_args_list
                      if "continuations" in c.kwargs]
        self.assertEqual([1, 2, 3, 4], cont_writes)

    def test_an_unlisted_stage_run_is_parked_the_same_way(self):
        # task-0020's twelfth branch: charged through the same charge() door
        # as the other five run-bearing sites, so it is just as reachable by
        # the backstop as the branches that predate it.
        r = plan_run(stage="draft", continuations=0)
        reasons, calls = self._sequence(r, [False, True, True, True],
                                        pipeline=PLAN_PIPELINE_UNSET)

        for reason in reasons[:3]:
            self.assertIn("editing_stages does not list", reason)
        self.assertIsNone(reasons[3])
        self.assertEqual(1, len(self._parks(calls)))


if __name__ == "__main__":
    unittest.main()
