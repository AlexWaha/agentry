"""FR-50 and FR-51: what the Stop hook says to the HUMAN, and to the terminal.

Contract C-8 gives each audience its own field: `reason` for the model,
`systemMessage` for the person reading the terminal, `terminalSequence` for the
terminal itself. Until task-0024 the Stop hook only filled the first, so nothing
the pipeline said reached the User.

What is pinned here:

1. every block path in stop_gate.py carries a non-empty `systemMessage` that is
   not the model's sentence (the audiences differ: "run advance.py" is noise to a
   User), and the human-asking ones say a decision is owed;
2. a stop released while a run is parked on the User says what the User owes it, with no
   `decision`, so the stop is still allowed;
3. four distinct `terminalSequence` values, one per event, each inside the
   allowlist Claude Code applies (measured in the 2.1.295 binary), and an event
   nobody defined degrades to a plain block (NFR-4).
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(TESTS_DIR))

import session_start
import state
import stop_gate
import tmproot  # noqa: F401  (arms the in-project temp redirect)
from test_conveyor_gaps import (
    BUILD_PIPELINE,
    PLAN_PIPELINE,
    PLAN_PIPELINE_UNSET,
    _FakeConn,
    decide_output_with,
    plan_run,
    run,
)

EVENTS = ("commit", "push", "blocked", "gate-failure")

# The model's vocabulary. If one of these shows up in the sentence meant for the
# User, the two audiences have been merged again.
MODEL_ONLY = ("advance.py", "approve.py", "AskUserQuestion", "Do not ask", "--reject")

DEBT = [{"task": "task-0067", "reason": "no handoff doc"}]
MEM_DEBT = [{"task": "task-0067"}]
LATEST = {"task": "task-0066", "sha": "a" * 40, "reason": "no handoff doc"}

# The escape sequences Claude Code lets through (build 2.1.295): BEL, and OSC
# 0/1/2/9/99/777 ended by BEL or ESC \, at most 4096 bytes, an OSC 9 body that
# does not open with a digit. A mirror of that rule, so a sequence the build would
# drop whole is a red test here rather than a silent no-op in the User's terminal.
OSC = re.compile(r"\x1b\](\d+)(?:;([^\x07\x1b]*))?(?:\x07|\x1b\\)")
ALLOWED_OSC = (0, 1, 2, 9, 99, 777)


def accepted_by_claude_code(sequence: str) -> bool:
    if not sequence or len(sequence.encode("utf-8")) > 4096:
        return False
    pos = 0
    while pos < len(sequence):
        if sequence[pos] == "\x07":
            pos += 1
            continue
        m = OSC.match(sequence, pos)
        if m is None or int(m.group(1)) not in ALLOWED_OSC:
            return False
        if m.group(1) == "9" and re.match(r"[\s+-]*\d", m.group(2) or ""):
            return False
        pos = m.end()
    return True


def printed(call) -> dict | None:
    """The JSON the hook printed while `call` ran, or None if it printed nothing."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        call()
    text = buf.getvalue().strip()
    return json.loads(text) if text else None


class BlockPathsSpeakToTheHumanTest(unittest.TestCase):
    """One scenario per block site in decide(). The table is the N-of-N: a block
    path missing from it is a path nobody checked."""

    SCENARIOS = (
        # (name, decide kwargs, terminal event, does it ask the User for a decision)
        (
            "blocked run surfaced",
            {"runs": [run(stage="implement", status=state.ST_BLOCKED)]},
            "blocked",
            True,
        ),
        (
            "approved commit awaiting the git action",
            {"runs": [run(stage="ready", awaiting_human="commit", commit_approved=1)]},
            None,
            False,
        ),
        (
            "approved push awaiting the git action",
            {"runs": [run(stage="ready", awaiting_human="push", push_approved=1)]},
            None,
            False,
        ),
        ("gate failed", {"runs": [run(status=state.ST_GATE_FAILED)]}, "gate-failure", False),
        ("editing stage in flight", {"runs": [run()]}, None, False),
        (
            "stage the flow does not list",
            {"runs": [plan_run(stage="draft")], "pipeline": PLAN_PIPELINE_UNSET},
            None,
            False,
        ),
        ("start the next ready task", {"runs": []}, None, False),
        ("status drift reconciled", {"runs": [], "backlog": (), "drift": (["task-0003"], [])}, None, False),
        ("handoff debt gating a start", {"runs": [], "debt": DEBT}, None, False),
        ("memory debt gating a start", {"runs": [], "mem_debt": MEM_DEBT}, None, False),
        ("merged-task handoff debt gating a start", {"runs": [], "latest": LATEST}, None, False),
        ("handoff debt with no other work", {"runs": [], "backlog": (), "debt": DEBT}, None, False),
        ("memory debt with no other work", {"runs": [], "backlog": (), "mem_debt": MEM_DEBT}, None, False),
    )

    def output(self, kwargs: dict) -> dict:
        kw = {"pipeline": BUILD_PIPELINE, **kwargs}
        return decide_output_with(kw.pop("runs"), **kw)

    def test_every_block_carries_a_human_sentence_that_is_not_the_models(self):
        for name, kwargs, _event, _asks in self.SCENARIOS:
            with self.subTest(path=name):
                out = self.output(kwargs)
                self.assertIsNotNone(out, "the scenario no longer blocks")
                self.assertEqual("block", out["decision"])
                human = out["systemMessage"]
                self.assertIsInstance(human, str)
                self.assertTrue(human.strip())
                self.assertNotEqual(out["reason"], human)
                self.assertIn("owed by you", human)
                for word in MODEL_ONLY:
                    self.assertNotIn(word, human)

    def test_only_the_paths_that_need_the_ceo_say_a_decision_is_owed(self):
        for name, kwargs, _event, asks in self.SCENARIOS:
            with self.subTest(path=name):
                human = self.output(kwargs)["systemMessage"]
                if asks:
                    self.assertIn("Decision owed by you", human)
                else:
                    self.assertNotIn("Decision owed", human)

    def test_the_terminal_event_is_the_one_the_path_names(self):
        for name, kwargs, event, _asks in self.SCENARIOS:
            with self.subTest(path=name):
                out = self.output(kwargs)
                if event is None:
                    self.assertNotIn("terminalSequence", out)
                else:
                    self.assertEqual(stop_gate.terminal_sequence(event), out["terminalSequence"])

    def test_the_bound_escalation_of_a_debt_asks_the_ceo(self):
        # Past the nag ceiling bounded_block() stops repeating and hands the debt
        # to the User. That is a decision, so it says so and rings the blocked bell.
        with unittest.mock.patch.object(stop_gate, "debt_nags", return_value=stop_gate.DEBT_NAG_CEILING + 1):
            out = printed(
                lambda: stop_gate.bounded_block(
                    "handoff:task-0067", "Handoff debt on completed task task-0067", "write the doc"
                )
            )
        self.assertEqual("block", out["decision"])
        self.assertIn("Decision owed by you", out["systemMessage"])
        self.assertEqual(stop_gate.terminal_sequence("blocked"), out["terminalSequence"])

    def test_the_repeat_backstop_speaks_to_the_human_on_both_outcomes(self):
        ceiling = stop_gate.STOP_REPEAT_CEILING_DEFAULT
        common = (
            unittest.mock.patch.object(stop_gate, "STOP_HOOK_ACTIVE", True),
            unittest.mock.patch.object(stop_gate, "stop_repeats", return_value=ceiling),
            unittest.mock.patch.object(stop_gate.state, "load_pipeline", return_value={}),
        )
        for p in common:
            p.start()
            self.addCleanup(p.stop)

        # A live run is parked: no block, but the park is announced.
        with unittest.mock.patch.object(stop_gate, "park_looping_run", return_value="task-0001"):
            out = printed(lambda: stop_gate.block("same sentence", "ignored"))
        self.assertNotIn("decision", out)
        self.assertIn("task-0001", out["systemMessage"])
        self.assertIn("Decision owed by you", out["systemMessage"])
        self.assertEqual(stop_gate.terminal_sequence("blocked"), out["terminalSequence"])

        # Nothing to park: it escalates, still a block, still a human sentence.
        with unittest.mock.patch.object(stop_gate, "park_looping_run", return_value=None):
            out = printed(lambda: stop_gate.block("same sentence", "ignored"))
        self.assertEqual("block", out["decision"])
        self.assertIn("AskUserQuestion", out["reason"])
        self.assertIn("Decision owed by you", out["systemMessage"])
        self.assertNotIn("AskUserQuestion", out["systemMessage"])


class ParkedRunsAreAnnouncedTest(unittest.TestCase):
    """The release at the end of decide() used to print nothing, so a run parked
    on the User was silent. advance.py's own output only reaches the model."""

    def parked(self, awaiting: str, **extra) -> dict | None:
        return decide_output_with(
            [run(stage="ready", awaiting_human=awaiting, **extra)], pipeline=BUILD_PIPELINE
        )

    def test_a_commit_checkpoint_says_what_is_owed_and_rings_commit(self):
        out = self.parked("commit")
        self.assertNotIn("decision", out)  # no decision: the stop is still allowed
        self.assertIn("task-0001", out["systemMessage"])
        self.assertIn("approve or reject the commit", out["systemMessage"])
        self.assertEqual(stop_gate.terminal_sequence("commit"), out["terminalSequence"])

    def test_a_push_checkpoint_rings_push(self):
        out = self.parked("push")
        self.assertIn("approve or reject the push", out["systemMessage"])
        self.assertEqual(stop_gate.terminal_sequence("push"), out["terminalSequence"])

    def test_a_plan_approval_and_a_merge_wait_are_announced_without_a_bell(self):
        # Neither is one of the four events, so they get the sentence and no
        # terminal sequence: a fifth treatment would be a fifth thing to tell apart.
        plan = decide_output_with([plan_run(stage="approval", awaiting_human="plan")], pipeline=PLAN_PIPELINE)
        self.assertIn("approve or reject the plan", plan["systemMessage"])
        self.assertNotIn("terminalSequence", plan)
        merge = self.merge_wait(BUILD_PIPELINE)
        self.assertIn("merge the pull request into main", merge["systemMessage"])
        self.assertNotIn("terminalSequence", merge)

    def merge_wait(self, pipeline: dict, task: str = "task-0001") -> dict | None:
        """The state advance.py _merge_wait really writes: stage `done`, status
        gate_passed, awaiting_human `merge`. A `ready` run with this marker never
        exists, and a fixture built that way passes whatever the code does."""
        return decide_output_with(
            [run(task=task, stage="done", status=state.ST_GATE_PASSED, awaiting_human="merge")],
            pipeline=pipeline,
            backlog=(),
        )

    def test_in_solo_mode_the_merge_wait_is_not_the_ceos(self):
        # The pipeline merges locally there, so "owed by you" would be false.
        solo = {**BUILD_PIPELINE, "workflow": {"mode": "solo"}}
        self.assertIsNone(self.merge_wait(solo))

    def test_a_finished_run_with_any_other_marker_is_not_announced(self):
        out = decide_output_with(
            [run(stage="done", status=state.ST_GATE_PASSED, awaiting_human="commit")],
            pipeline=BUILD_PIPELINE,
            backlog=(),
        )
        self.assertIsNone(out)

    def test_an_unknown_park_reason_is_still_announced(self):
        out = self.parked("something-new")
        self.assertIn("something-new", out["systemMessage"])

    def test_two_parked_runs_are_one_message_naming_both(self):
        out = decide_output_with(
            [
                run(task="task-0001", stage="ready", awaiting_human="commit"),
                run(task="task-0002", stage="done", status=state.ST_GATE_PASSED, awaiting_human="merge"),
            ],
            pipeline=BUILD_PIPELINE,
        )
        self.assertIn("task-0001", out["systemMessage"])
        self.assertIn("task-0002", out["systemMessage"])
        self.assertEqual(stop_gate.terminal_sequence("commit"), out["terminalSequence"])

    def test_an_approved_checkpoint_owes_the_ceo_nothing(self):
        # A fresh busy marker keeps the approved-action block quiet, which leaves
        # the release: the approval is recorded, so there is nothing to announce.
        out = decide_output_with(
            [run(stage="ready", awaiting_human="commit", commit_approved=1)],
            pipeline=BUILD_PIPELINE,
            busy=True,
        )
        self.assertIsNone(out)

    def test_a_blocked_run_is_not_repeated_after_it_was_surfaced(self):
        # The once-only contract of the blocked surfacing must survive: the
        # release does not nag about a run it already raised.
        out = decide_output_with(
            [run(stage="implement", status=state.ST_BLOCKED, awaiting_human=stop_gate.AWAITING_BLOCKED)],
            pipeline=BUILD_PIPELINE,
            backlog=(),
        )
        self.assertIsNone(out)

    def test_nothing_parked_means_a_silent_release(self):
        self.assertIsNone(decide_output_with([], pipeline=BUILD_PIPELINE, backlog=()))


class ChargeParkIsAnnouncedTest(unittest.TestCase):
    """charge() parks a run BLOCKED when its continuation ceiling is spent and
    decide() falls through to other work. That stop used to release with no
    output at all, so the human heard nothing until the next stop."""

    CEILING = 30
    VARIANTS = (
        ("editing stage in flight", lambda c: run(continuations=c), BUILD_PIPELINE),
        ("gate failed", lambda c: run(status=state.ST_GATE_FAILED, continuations=c), BUILD_PIPELINE),
        (
            "approved commit awaiting the git action",
            lambda c: run(stage="ready", awaiting_human="commit", commit_approved=1, continuations=c),
            BUILD_PIPELINE,
        ),
        (
            "stage the flow does not list",
            lambda c: plan_run(stage="draft", continuations=c),
            PLAN_PIPELINE_UNSET,
        ),
    )

    def test_the_stop_that_spends_the_last_continuation_still_blocks(self):
        # The control: one below the ceiling is an ordinary nag, so the park
        # below is a change of state and not a different fixture.
        for name, make, pipeline in self.VARIANTS:
            with self.subTest(variant=name):
                out = decide_output_with([make(self.CEILING - 1)], pipeline=pipeline, backlog=())
                self.assertEqual("block", out["decision"])

    def test_the_stop_that_parks_the_run_tells_the_human_and_rings_blocked(self):
        for name, make, pipeline in self.VARIANTS:
            with self.subTest(variant=name):
                out = decide_output_with([make(self.CEILING)], pipeline=pipeline, backlog=())
                self.assertIsNotNone(out, "the park is silent")
                self.assertNotIn("decision", out)  # the stop is still allowed
                self.assertIn("task-0001", out["systemMessage"])
                self.assertIn("Decision owed by you", out["systemMessage"])
                for word in MODEL_ONLY:
                    self.assertNotIn(word, out["systemMessage"])
                self.assertEqual(stop_gate.terminal_sequence("blocked"), out["terminalSequence"])

    def test_the_park_is_announced_even_when_a_later_branch_blocks_the_same_stop(self):
        # Documentation debt blocks at step 5, after the park. The block carries
        # its own sentence and the park is added to it rather than lost.
        out = decide_output_with(
            [run(continuations=self.CEILING)], pipeline=BUILD_PIPELINE, backlog=(), debt=DEBT
        )
        self.assertEqual("block", out["decision"])
        self.assertIn("parked BLOCKED", out["systemMessage"])
        self.assertIn("Handoff debt", out["systemMessage"])
        self.assertEqual(stop_gate.terminal_sequence("blocked"), out["terminalSequence"])

    def test_a_run_surfaced_earlier_is_not_announced_a_second_time(self):
        out = decide_output_with([run(stage="implement", status=state.ST_BLOCKED)], pipeline=BUILD_PIPELINE)
        self.assertEqual(1, out["systemMessage"].count("parked BLOCKED"))
        self.assertNotIn("ran out of continuations", out["systemMessage"])

    def test_the_announcement_does_not_outlive_the_stop(self):
        decide_output_with([run(continuations=self.CEILING)], pipeline=BUILD_PIPELINE, backlog=())
        buf = io.StringIO()
        with redirect_stdout(buf):
            stop_gate.emit("a real demand", "a sentence")
        self.assertEqual("a sentence", json.loads(buf.getvalue())["systemMessage"])


class TerminalSequenceTest(unittest.TestCase):
    def test_there_are_exactly_four_events_and_their_sequences_differ(self):
        self.assertEqual(set(EVENTS), set(stop_gate.TERMINAL_EVENTS))
        sequences = {stop_gate.terminal_sequence(e) for e in EVENTS}
        self.assertEqual(4, len(sequences))
        titles = {re.search(r"\x1b\]2;([^\x07]*)\x07", s).group(1) for s in sequences}
        self.assertEqual(4, len(titles))  # distinct to the eye, not only in bell count

    def test_every_sequence_is_inside_the_allowlist_claude_code_applies(self):
        for event in EVENTS:
            with self.subTest(event=event):
                self.assertTrue(accepted_by_claude_code(stop_gate.terminal_sequence(event)))

    def test_the_mirror_of_the_allowlist_can_actually_fail(self):
        # Proof the check above is not vacuous: OSC 52 (clipboard) is not in the
        # allowlist, a digit-led OSC 9 body is refused, and so is an unterminated
        # sequence and an empty string.
        for bad in ("\x1b]52;c;AAAA\x07", "\x1b]9;5 minutes\x07", "\x1b]2;title", "", "text\x07"):
            with self.subTest(sequence=bad):
                self.assertFalse(accepted_by_claude_code(bad))

    def test_an_event_nobody_defined_degrades_to_a_plain_block(self):
        # NFR-4. The hook still blocks, still tells the human, exits 0, and
        # simply has no terminal sequence to send.
        self.assertIsNone(stop_gate.terminal_sequence("no-such-event"))
        self.assertIsNone(stop_gate.terminal_sequence(None))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = stop_gate.emit("do the thing", "a sentence for the human", "no-such-event")
        out = json.loads(buf.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("block", out["decision"])
        self.assertEqual("a sentence for the human", out["systemMessage"])
        self.assertNotIn("terminalSequence", out)

    def test_a_caller_with_no_human_sentence_still_gets_a_non_empty_one(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            stop_gate.emit("a real demand")
        out = json.loads(buf.getvalue())
        self.assertEqual("block", out["decision"])
        self.assertEqual(stop_gate.NOTHING_OWED, out["systemMessage"])
        self.assertTrue(out["systemMessage"].strip())


class SessionStartBlockedRunTest(unittest.TestCase):
    """The state.py blocked-run line is an asking site (row 11 of the task-0024
    enumeration). SessionStart stdout is plain context unless it starts with `{`,
    in which case it is ONE JSON document, so the blocked case switches shape and
    must lose none of what the plain shape printed."""

    def start(self, runs: list[dict], mode_line: str = "") -> str:
        """session_start.main() with the three steps that touch the host replaced
        by fixed lines; the resume step is the real one over `runs`."""
        buf = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.object(state, "all_runs", return_value=runs))
            stack.enter_context(unittest.mock.patch.object(state, "connect", return_value=_FakeConn()))
            stack.enter_context(unittest.mock.patch.object(session_start, "cleanup_nul"))
            stack.enter_context(
                unittest.mock.patch.object(
                    session_start,
                    "pipeline_mode",
                    side_effect=lambda: print(mode_line) if mode_line else None,
                )
            )
            stack.enter_context(
                unittest.mock.patch.object(
                    session_start, "codegraph_sync", side_effect=lambda: print("codegraph: synced")
                )
            )
            stack.enter_context(unittest.mock.patch.object(session_start, "codebase_memory_check"))
            stack.enter_context(redirect_stdout(buf))
            self.assertEqual(0, session_start.main())
        return buf.getvalue()

    def test_a_blocked_run_becomes_one_envelope_with_a_system_message(self):
        raw = self.start(
            [run(stage="implement", status=state.ST_BLOCKED)], mode_line="pipeline mode: plan - planning"
        )
        self.assertTrue(raw.startswith("{"))
        out = json.loads(raw)  # the WHOLE stdout is one document
        human = out["systemMessage"]
        self.assertIn("task-0001", human)
        self.assertIn("Decision owed by you", human)
        for word in MODEL_ONLY:
            self.assertNotIn(word, human)
        self.assertEqual("SessionStart", out["hookSpecificOutput"]["hookEventName"])

    def test_nothing_the_plain_shape_printed_is_lost_in_the_envelope(self):
        raw = self.start(
            [run(stage="implement", status=state.ST_BLOCKED)], mode_line="pipeline mode: plan - planning"
        )
        context = json.loads(raw)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("pipeline mode: plan - planning", context)
        self.assertIn("Pipeline state (resume in-flight work", context)
        self.assertIn("BLOCKED, needs the User", context)  # the model still gets its line
        self.assertIn("codegraph: synced", context)

    def test_without_a_blocked_run_the_output_is_the_same_plain_text_as_before(self):
        raw = self.start([run(stage="implement")])
        self.assertFalse(raw.startswith("{"))
        self.assertIn("Pipeline state (resume in-flight work", raw)
        self.assertIn("codegraph: synced", raw)
        self.assertNotIn("systemMessage", raw)

    def test_a_step_that_escapes_still_lets_earlier_output_through(self):
        buf = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.object(session_start, "cleanup_nul"))
            stack.enter_context(
                unittest.mock.patch.object(
                    session_start, "pipeline_mode", side_effect=lambda: print("pipeline mode: plan")
                )
            )
            stack.enter_context(
                unittest.mock.patch.object(session_start, "pipeline_resume", side_effect=RuntimeError("boom"))
            )
            stack.enter_context(redirect_stdout(buf))
            stack.enter_context(self.assertRaises(RuntimeError))
            session_start.main()
        self.assertIn("pipeline mode: plan", buf.getvalue())

    def test_a_failure_computing_the_human_sentence_keeps_the_resume_text(self):
        with unittest.mock.patch.object(state, "blocked_notice", side_effect=RuntimeError("boom")):
            raw = self.start([run(stage="implement", status=state.ST_BLOCKED)])
        self.assertFalse(raw.startswith("{"))
        self.assertIn("BLOCKED, needs the User", raw)  # the model's line survived

    def test_a_quiet_start_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.object(state, "all_runs", return_value=[]))
            stack.enter_context(unittest.mock.patch.object(state, "connect", return_value=_FakeConn()))
            for step in ("cleanup_nul", "pipeline_mode", "codegraph_sync", "codebase_memory_check"):
                stack.enter_context(unittest.mock.patch.object(session_start, step))
            stack.enter_context(redirect_stdout(buf))
            session_start.main()
        self.assertEqual("", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
