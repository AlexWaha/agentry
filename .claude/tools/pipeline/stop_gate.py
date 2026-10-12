#!/usr/bin/env python3
"""Stop-hook brain - the engine that keeps the agent from idling mid-pipeline.

Wired to Claude Code's `Stop` event. On every attempt to stop it inspects the run
state and either:
  - blocks the stop and feeds back a concrete next instruction (continue an
    in-flight task, or start the next ready backlog task), or
  - allows the stop when every task is parked at a human checkpoint, blocked, or
    done and no ready backlog remains.

There is a third outcome and it is not a decision: this script CRASHING. It then
allows the stop like the second case, because failing open is not negotiable,
but it exits non-zero so the harness raises its `stop-hook-error` notification
instead of letting the crash pass for a considered release. See CRASH_EXIT.

A blocked run is surfaced once before it is left alone (it is the CEO's to
unblock, and nothing else in the harness tells him it exists) - see decide().

Work is serialized on the working tree. A new backlog task is offered only when
nothing holds it: no run in an editing stage (blocked included - a blocked task
still owns its dirty tree) and no run awaiting a human, because the CEO may be
reading the diff on that very branch.

Documentation owed by COMPLETED tasks - a missing handoff doc, an unstamped
memory review - is read on every stop and blocks it, regardless of what is in
flight. Both checks used to sit inside the free-slot branch, which switched them
off whenever any task was parked on the CEO, which is the normal state of a
pipeline with a human in it (task-0072). Only the free-slot question reads the
free slot now.

No block repeats for ever. Every branch that blocks ON A RUN goes through
charge(), which spends one of that stage's `continuation_ceiling` continuations
and parks the run BLOCKED when the budget is gone. Every branch that blocks on
something with NO run row - documentation owed by a completed task, a backlog
task not yet registered - goes through bounded_block() instead, because
`continuations` is a column of a run and there is none to charge. The two
exceptions are deliberate and are the two blocks that cannot repeat: surfacing
a BLOCKED run (one-shot, marked by `awaiting_human`) and the drift
reconciliation (it performs the reconciliation before blocking, so the next stop
finds nothing left to reconcile).

Behind both of those sits one backstop that trusts neither: block() counts
CONSECUTIVE BLOCKS CARRYING THE SAME REASON and stops repeating itself at
`stop_hook_repeat_ceiling`, whatever branch produced them. It catches the
repeats no per-branch budget can see - a site bounded by an argument rather
than by a counter, a budget zeroed faster than it is spent, and a twelfth
branch nobody classified. It is armed only while the harness's own
`stop_hook_active` flag says this hook is already inside a stop loop.

Fail-open: any error allows the stop (never trap the user in a loop).

Hook output (contract C-8, one field per audience): print
{"decision":"block","reason":...} to block - `reason` is for the model - and ALWAYS
alongside it a `systemMessage` for the human saying what the CEO owes the run
(nothing, a decision, an approval). A block may add a `terminalSequence`, the
terminal's own channel. A stop that is allowed but has something to tell the
human - a run parked on the CEO, a run just parked BLOCKED, the repeat backstop
tripping - prints {"systemMessage":...} with no decision, which still allows the
stop; every other release prints nothing.
"""

from __future__ import annotations

import json
import re
import sys
import traceback

import approvals
import busy
import git_state
import mode
import pretool_gate
import state
import swarm

DEPENDS_RE = re.compile(r"^depends_on:\s*\[(.*?)\]", re.MULTILINE)
TASK_ID_RE = re.compile(r"(task-\d+)")
# A task whose work was folded into another task stays on disk as a pointer to
# its successor - it must never be offered as ready, and it will never reach
# done/ either, which is why dep_satisfied resolves through the pointer.
SUPERSEDED_RE = re.compile(r"^superseded_by:[ \t]*(task-\d+)", re.MULTILINE)
# A task waiting on something no other task can satisfy - a CEO ruling, a vendor
# answer, an external decision - records it in blocked_on. depends_on cannot
# express that, so without this the queue re-offers the task every turn and an
# unattended run never settles. The negative lookahead is what keeps the blank
# `blocked_on:` the template ships on every task from meaning "blocked".
BLOCKED_RE = re.compile(r"^blocked_on:[ \t]*(?!\s*$)\S", re.MULTILINE)
# The `repo:` a task is bound to, read the way state.task_repo() reads it: the
# rest of the line, quotes stripped. Empty means the default lane (FR-45).
REPO_RE = re.compile(r"^repo:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
# An umbrella task (skills/swarm/SKILL.md) is one task file carrying
# `umbrella: true` and the `epic:` whose subtasks the swarm runs together.
EPIC_RE = re.compile(r"^epic:[ \t]*(epic-[\w-]+)", re.MULTILINE)
UMBRELLA_RE = re.compile(r"^umbrella:[ \t]*true\b", re.MULTILINE | re.IGNORECASE)

# What `awaiting_human` is set to when a blocked run is surfaced to the CEO. It
# doubles as the surfaced-once marker, so a blocked run is raised on one stop
# rather than on every one. See decide(), step 2.
AWAITING_BLOCKED = "blocked"

# How many consecutive stops may be blocked on the SAME outstanding debt before
# the hook stops demanding and hands it to the CEO instead (decide(), step 5).
# The debt block is the file's only unbounded one, and it is bounded because the
# argument for letting it repeat - the agent can clear it - has a degenerate
# case: a handoff doc that keeps failing min_section_chars, or a store that
# keeps refusing the stamp. Then the demand is unsatisfiable and repeating it is
# exactly the stop loop line 26 promises never to create.
DEBT_NAG_CEILING = 3
DEBT_NAG_FILE = "debt-nag.json"

# The key namespaces the top-of-decide() reconciliation actually evaluates: it
# is handed the live `handoff:` and `memory:` demands, and nothing else. A key
# outside these was NOT LOOKED AT, which is not the same as dead, and clearing
# it would pin its count at 1 for ever - no escalation and no mute. Measured on
# the `main-handoff:` key: eight consecutive stops, all count 1. Adding a
# namespace here without also feeding its live keys to clear_debt_nags()
# reintroduces exactly that.
RECONCILED_PREFIXES = ("handoff:", "memory:")

# Where the reason-repeat backstop keeps its count, and what it defaults to when
# `stop_hook_repeat_ceiling` is missing from pipeline.json. Three, not two, by
# CEO decision RQ-7: two identical blocks are a legitimate retry, and a backstop
# that trips on a retry cuts off work that was about to succeed.
STOP_REPEAT_FILE = "stop-repeat.json"
STOP_REPEAT_CEILING_DEFAULT = 3

# Set by main() from the hook payload's `stop_hook_active`, which Claude Code
# sends as a plain boolean: true on every Stop invocation that is a RE-ENTRY
# after this hook already blocked once in the current turn.
#
# Measured in the 2.1.269 binary rather than assumed, because four documented
# harness behaviours in this project turned out to differ in the build:
#
#   - The payload schema (offset 187320454) is `hook_event_name:"Stop",
#     stop_hook_active:<bool>, last_assistant_message?, background_tasks?,
#     session_crons?`. There is no count in it. The count exists internally as
#     `stopHookBlockingCount` and is never sent, which is why the bound below
#     has to persist its own - the same reason debt_nags() states.
#   - Claude Code has a cap of its own (offset 193551378):
#     `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP ?? 8`, after which it prints "A hook
#     blocked the turn from ending N consecutive times, overriding and ending
#     turn". DO NOT read that as "the loop is already bounded at 8, so this file
#     need not be" - it is not, and deleting the backstop on that reading would
#     leave nothing. The cap compares against `stopHookBlockingCount`, and the
#     `next_turn` transition (offset 193561819, verbatim
#     `transition:{reason:"next_turn"}`) re-enters with `stopHookBlockingCount:
#     0` after every tool round. So the vendor cap counts only blocks with NO
#     intervening tool use, and the conveyor's actual shape - block, the model
#     runs advance.py, stop, block - zeroes it on every iteration and never
#     approaches 8. The backstop below is therefore both the park AND the
#     record, and the only boundary that fires on a real loop.
#   - The flag is STICKIER than that internal count, and this is the fact that
#     decides where it may be read. The same `next_turn` path, and the other
#     non-block continuations (offsets 193546604, 193547939, 193548553,
#     193549335, 193550215), all carry `stopHookActive: yo` through unchanged
#     while resetting the count. So once anything has blocked in a turn, the flag
#     stays true for every later Stop in that turn even after the model has done
#     real work in between - which is precisely the case the conveyor lives in.
#     Treating it as "stay quiet" would mute this hook for the rest of every turn
#     after its first nudge. It is a qualifier on parking, never a mute.
STOP_HOOK_ACTIVE = False

# What decide() is holding when block() is reached, so the backstop can park the
# looping run without re-opening the database or threading a parameter through
# eleven call sites. A single-shot CLI process with exactly one decide() call is
# the only reason a module global is honest here; an empty context simply means
# "nothing to park", which is the case for every block() outside decide().
_CTX: dict = {"conn": None, "runs": ()}

BACKLOG_DIR = state.BACKLOG_DIR
ACTIVE_DIR = state.ACTIVE_DIR
DONE_DIR = state.DONE_DIR


# FR-51, the four terminal treatments. The terminal is the channel for a CEO who
# is not looking at the session, so each event is told apart by what it does to a
# window that is out of sight: a distinct bell count and a distinct title, plus a
# desktop notification carrying the same label. Zero bells for a gate failure on
# purpose - the agent is already retrying, so it is the one event that must not
# ring.
#
# What Claude Code accepts, measured in the 2.1.295 binary (the hook schema
# describes the field and the allowlist lives in the function that validates it):
# a string of at most 4096 bytes made only of BEL and OSC sequences, OSC numbers
# 0, 1, 2, 9, 99 and 777, each ended by BEL or ESC \, and an OSC 9 body that does
# not start with a digit. Anything else is DROPPED WHOLE and logged at debug
# level - never an error, never a failed hook. Colour is not in the list, so the
# plan's "colour" is not available through this field.
TERMINAL_EVENTS = {
    "commit": (1, "commit approval needed"),
    "push": (2, "push approval needed"),
    "blocked": (3, "task blocked, your decision needed"),
    "gate-failure": (0, "gate failed, the agent is retrying"),
}

# What the human is told when a caller gave no sentence of its own. Only emit()
# can reach it (block() requires one), so every block says SOMETHING to the human.
NOTHING_OWED = "The Stop hook is keeping the agent working. Nothing is owed by you."


BEL = "\x07"


def terminal_sequence(event: str | None) -> str | None:
    """The terminalSequence for one of the four events, or None for any other
    name. None is the NFR-4 half: an event nobody defined costs the human the
    bell, not the block."""
    treatment = TERMINAL_EVENTS.get(event)
    if treatment is None:
        return None
    bells, label = treatment
    return f"{BEL * bells}\x1b]2;Agentry: {label}{BEL}\x1b]9;Agentry: {label}\x1b\\"


def parked_now(runs: list[dict]) -> list[dict]:
    """Runs charge() parked BLOCKED during THIS stop: it marks the row in the
    stop's own snapshot, which is the only thing that tells them from a run
    parked earlier (that one is surfaced by decide() step 2 instead)."""
    return [r for r in runs if r.get("parked_now")]


def human_fields(human: str | None, event: str | None = None) -> dict:
    """The human-audience half of the hook's JSON: always a non-empty
    systemMessage, plus a terminalSequence when the event has one.

    A run that charge() parked on this very stop is announced here, whichever
    branch ends the stop, because the park is silent otherwise: it falls through
    to other work and the next stop is the first that could speak of it."""
    parked = parked_now(_CTX["runs"])
    if parked:
        tasks = ", ".join(r["task"] for r in parked)
        sentence = (f"{tasks} parked BLOCKED: the agent ran out of continuations and "
                    f"nothing is driving it. Decision owed by you: fix the blocker so it "
                    f"can re-run, or send the task back to its first stage.")
        human = f"{sentence} Separately: {human}" if human else sentence
        event = "blocked"
    fields = {"systemMessage": human or NOTHING_OWED}
    sequence = terminal_sequence(event)
    if sequence:
        fields["terminalSequence"] = sequence
    return fields


def emit(reason: str, human: str | None = None, event: str | None = None) -> int:
    """Actually block the stop. The only writer of the hook's one model-facing
    channel.

    `reason` is for the model. `human` is for the person reading the terminal and
    says what they owe the run; it is never the same sentence, because the model's
    instruction ("run advance.py") is noise to a CEO and the CEO's ask is noise to
    the model."""
    print(json.dumps({"decision": "block", "reason": reason,
                      **human_fields(human, event)}))
    return 0


def release(human: str, event: str | None = None) -> int:
    """Allow the stop, but say to the human why the run is not moving. No
    `decision`, so Claude Code lets the turn end exactly as it does for silence
    (a Stop hook's {"systemMessage": ...} alone is the documented way to show a
    message without blocking)."""
    print(json.dumps(human_fields(human, event)))
    return allow()


def allow() -> int:
    # No output -> Claude Code stops normally.
    #
    # Unless a debt checker was skipped this stop. Then this release is
    # uninformed, not clean, and saying nothing is the exact defect task-0077
    # was filed for. CRASH_EXIT still ALLOWS the stop - measured, see its
    # comment below - but the harness keeps the stderr line checker_failed
    # wrote and raises `stop-hook-error` over it. Fail-open, out loud.
    #
    # In allow() and not in emit(): a stop that is genuinely being blocked
    # already reaches the model through the hook's own channel, and exiting
    # non-zero there would discard the block, which is the one direction this
    # fix must never move.
    return CRASH_EXIT if state.CHECKS_SKIPPED else 0


# The exit code for "this hook crashed". NOT 0 and NOT 2, and both halves were
# measured on build 2.1.269 rather than read off the documentation:
#
#   0 - the stop is allowed and the harness shows NOTHING. Measured with a Stop
#       hook that raised, caught its own exception and returned 0, which is
#       exactly the shape main() had: the run produced no notification, no
#       stderr and no stream event. Nothing anywhere said the hook had failed.
#   2 - the stop is BLOCKED and stderr goes to the model. That is the opposite
#       of failing open; a crashing hook would trap the session in a stop loop.
#   1 - the stop is allowed AND the harness raises its own notification, keyed
#       `stop-hook-error`, priority `immediate`: "Stop hook error occurred,
#       ctrl+o to see". Measured twice, with a raised traceback and with a bare
#       non-zero exit, and once more on a stop carrying stop_hook_active, which
#       the minified source suggested might suppress it and did not. The turn
#       ended normally in all three (`stop_reason: end_turn`).
#
# So the channel this file's own comments wanted already exists in the harness,
# and exiting 0 was the one thing that hid it. See main().
CRASH_EXIT = 1


def stop_repeats(reason: str) -> int:
    """How many consecutive blocks have now carried this exact reason, counting
    the current one. A different reason starts the count again, so any progress
    at all - a different branch, a different task, a different stage - restores
    the full budget.

    Consecutive BLOCKS, not consecutive stops: an allowed stop in between leaves
    the count alone. That matches debt_nags(), and it is the conservative
    direction, because a demand that alternates with silence is still a demand
    nobody is satisfying.

    The count has to outlive the process for the same reason debt_nags() gives -
    a fresh interpreter per event, and `stop_hook_active` carries no count. It is
    a file of its own rather than a second key in debt-nag.json: that file holds
    ONE key and clear_debt_nags() unlinks it, so sharing it would have the two
    counters evicting each other and the debt bounds are not mine to weaken.

    An unreadable file is overwritten by the next write, in its own try, which is
    the bug debt_nags() had: while json.loads shared the outer handler a file
    left corrupt by a killed process returned 1 for ever. The outer fail-open
    returns 1 too, which keeps this hook reporting and loses only the backstop -
    the right way round, since silence is the failure this file exists to end.

    Both halves of that guard are needed and the second one is easy to forget:
    the first version of this function had the inner try and not the isinstance
    check, and a file holding `[1, 2, 3]` or `42` parsed fine, then raised
    AttributeError on .get() into the outer handler - measured 1, 1, 1 against a
    healthy 1, 2, 3. Valid JSON of the wrong shape is just an absent file."""
    try:
        state.STATE_DIR.mkdir(parents=True, exist_ok=True)
        p = state.STATE_DIR / STOP_REPEAT_FILE
        prev = {}
        if p.is_file():
            try:
                prev = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prev = {}
            if not isinstance(prev, dict):
                prev = {}
        n = int(prev.get("count", 0)) + 1 if prev.get("reason") == reason else 1
        p.write_text(json.dumps({"reason": reason, "count": n}), encoding="utf-8")
        return n
    except Exception:
        return 1


def park_looping_run(reason: str) -> str | None:
    """Park the run this reason keeps being emitted about, and name it.

    The task is read out of the reason text because that is where it already is:
    every run-bearing block in decide() names its task first, and the
    alternative was passing a task through eleven call sites to be discarded at
    ten of them. A reason whose first task id has no run - a completed task's
    documentation, a backlog task not yet registered, a run already retired to
    `done` - parks nothing and returns None, which is what makes the caller
    escalate instead of going quiet.

    Fail-open: any error parks nothing, so the caller speaks."""
    try:
        m = TASK_ID_RE.search(reason)
        if not m:
            return None
        task = m.group(1)
        r = next((x for x in _CTX["runs"] if x["task"] == task), None)
        if r is None or r["stage"] == "done" or state.is_paused(r):
            return None
        state.set_fields(_CTX["conn"], task, stage_status=state.ST_BLOCKED,
                         reason="the stop hook repeated one demand to no effect")
        # Forget the count now the park has replaced the loop. Without this, a
        # CEO who spots the park in `state.py --show` and runs approve.py
        # --reject WITHOUT waiting for the surfacing stop leaves the key at its
        # tripped value, and this hook stays mute about that instruction until
        # the turn ends and the flag disarms. Bounded either way, but a state
        # nobody can see should not outlive the thing it described.
        try:
            (state.STATE_DIR / STOP_REPEAT_FILE).unlink()
        except OSError:
            pass
        return task
    except Exception:
        return None


def block(reason: str, human: str, event: str | None = None) -> int:
    """Block the stop unless this exact reason has already been repeated to no
    effect, in which case park the loop and let the session end.

    Every one of the eleven block sites in decide() reaches the hook's channel
    through here, including bounded_block()'s two, so the bound is structural
    rather than enumerated: a twelfth branch inherits it by existing.

    Two conditions, and both matter:

    `stop_hook_active` - the harness telling us this invocation is a re-entry
    after a block earlier in the same turn. False means the turn has not looped
    yet (a human just spoke, or the previous block was answered), and the demand
    gets said out loud whatever its history, without even being counted. That is
    deliberate twice over: it guarantees every demand is uttered at least once
    per turn, so the backstop can defer a report but never permanently swallow
    one - and it keeps the counter file untouched on the ordinary first-nudge
    stop, which is most of them.

    The count reaching the ceiling - by then the same sentence has been printed
    twice and changed nothing.

    What happens on the trip is the whole safety argument, and it is never
    "return allow() and hope". If the reason names a live run, that run is
    parked BLOCKED, which is louder than the block it replaces: the next stop
    surfaces it through decide() step 2 with a different reason, naming the CEO.
    If it names no run - both documentation demands, and the start-the-next-task
    branch task-0018 left unbounded on purpose - there is nothing to park, so it
    escalates in the same breath, once, naming the card. Only after that does it
    go quiet, and only about that exact sentence.

    `human` is the sentence for the person at the terminal (see emit()), and every
    caller must supply one: that is how the "every block says what the CEO owes"
    rule is structural too. The two outcomes of the trip speak to the human as
    well, because they are the moments a run is handed to him."""
    if not STOP_HOOK_ACTIVE:
        return emit(reason, human, event)
    try:
        n = stop_repeats(reason)
        ceiling = int(state.load_pipeline().get(
            "stop_hook_repeat_ceiling", STOP_REPEAT_CEILING_DEFAULT))
    except Exception:
        return emit(reason, human, event)
    if ceiling < 1 or n < ceiling:
        return emit(reason, human, event)
    if n > ceiling:
        return allow()  # parked or escalated on the trip stop; the CEO owns it
    task = park_looping_run(reason)
    if task:
        # Step 2 surfaces it to the model on the next stop, but the next stop may
        # never come, so the human hears it now.
        return release(
            f"{task} was parked BLOCKED: the same instruction was repeated {n} times "
            f"and nothing changed. Decision owed by you: fix the blocker and let the "
            f"agent re-run it, or send the task back to its first stage.", "blocked")
    return emit(
        f"The same instruction has now been repeated {n} times with nothing changing, and "
        f"there is no run to park. Stop re-attempting it and raise it with the CEO now "
        f"(AskUserQuestion): the situation, the options, and your recommendation. This hook "
        f"will not raise it again. The instruction that is not working: {reason}",
        f"The agent has been told the same thing {n} times with nothing changing and "
        f"there is no run to park. Decision owed by you: the agent is about to ask you "
        f"how to proceed.", "blocked")


def frontmatter(text: str) -> str:
    """The leading `---` block, or the whole text when there is none. Field
    regexes run against this and not the body: task files quote field names in
    their prose, and a false positive there would silently drop a ready task
    out of the queue."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[:end] if end != -1 else text


def read_backlog() -> list[dict]:
    """Queued task files: [{id, deps, epic, repo, umbrella}]. Empty on any error.

    The queue is a folder, not a field. A file in backlog/ is waiting to be
    picked; there is no `status:` to read, and therefore nothing that can
    disagree with where the file actually sits.

    Two frontmatter fields do take a task OUT of the ready set without making it
    done: `superseded_by:` (the work was folded into another task) and a
    non-empty `blocked_on:` (waiting on a human or an outside party). Both mean
    skip, never finish.

    LANES (FR-45). With `lanes{}` configured, a lane is offered only the tasks
    bound to its repo, so two lanes sharing one backlog cannot both take the
    same task: a task whose `repo:` equals the lane's repo is offered, one with
    an empty `repo:` is offered to the lane named `default`, anything else
    belongs to another lane. A lane `lanes{}` does not declare is offered
    nothing. A `repo:` that is not a key of `repos{}` is offered to NO lane and
    comes back flagged `unknown_repo`, because dropping it here would be the
    silent fall-through; decide() raises it through unknown_repo_block(). An
    absent or unreadable `lanes{}` offers the whole backlog and flags nothing
    (NFR-4), which is also the behaviour before this existed."""
    out: list[dict] = []
    if not BACKLOG_DIR.is_dir():
        return out
    routes = state.lanes()
    lane = state.lane_name()
    lane_repo = routes.get(lane)
    if routes and lane_repo is None:
        print(f"lane {lane!r} is not declared in pipeline.json lanes{{}}; "
              f"offering it no backlog task", file=sys.stderr)
        return out
    entries = git_state.repo_entries()
    for path in sorted(BACKLOG_DIR.glob("task-*.md")):
        m = TASK_ID_RE.search(path.name)
        if not m:
            continue
        try:
            text = frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if SUPERSEDED_RE.search(text) or BLOCKED_RE.search(text):
            continue
        deps_raw = DEPENDS_RE.search(text)
        deps = [d.strip() for d in (deps_raw.group(1).split(",") if deps_raw else []) if d.strip()]
        epic = EPIC_RE.search(text)
        repo_raw = REPO_RE.search(text)
        repo = repo_raw.group(1).strip().strip("'\"") if repo_raw else ""
        entry = {"id": m.group(1), "deps": deps,
                 "epic": epic.group(1) if epic else "", "repo": repo,
                 "umbrella": bool(UMBRELLA_RE.search(text))}
        if routes:
            if not repo:
                offered = lane == state.DEFAULT_LANE
            elif entries:
                key = git_state.resolve_key(entries, repo)
                if not key:
                    entry["unknown_repo"] = True
                    offered = True  # returned flagged so decide() can name it
                else:
                    offered = key == lane_repo
            else:
                offered = repo == lane_repo
            if not offered:
                continue
        out.append(entry)
    return out


def swarm_on() -> bool:
    """The swarm dial. Fail-open: anything unreadable means off."""
    try:
        return swarm.read() == swarm.ON
    except Exception:
        return False


def swarm_tail(epic: str) -> str:
    """What replaces `run: advance.py --task <subtask>` when the next ready task
    is a subtask of a swarm epic: start the epic's umbrella task instead."""
    return (
        f"start {epic} as a swarm per .claude/skills/swarm/SKILL.md. An umbrella "
        f"task file with `umbrella: true` and `epic: {epic}` must exist in "
        f".agentry/tasks/backlog/ (create it if it is missing, listing the "
        f"subtasks in `subtasks: [...]`); then run: python "
        f".claude/tools/pipeline/advance.py --task <umbrella task id>.")


def swarm_demand(task_id: str, epic: str) -> tuple[str, str]:
    """(model reason, human systemMessage) telling the orchestrator to start an
    epic as a swarm instead of its subtask `task_id` as a single task."""
    reason = (
        f"Swarm mode is on and {task_id} belongs to {epic}: do not start it "
        f"as a single task. Swarm it: {swarm_tail(epic)} "
        f"Drive it through the pipeline without asking the user.")
    human = (
        f"Nothing is parked on you, so the agent is starting {epic} as a "
        f"swarm under one umbrella task. Nothing is owed by you.")
    return reason, human


def umbrella_epics(runs: list[dict]) -> set[str]:
    """Epics owned by an umbrella run registered and not yet in done/."""
    epics: set[str] = set()
    for r in runs:
        p = task_file(r["task"])
        if p is None or p.parent == DONE_DIR:
            continue
        try:
            text = frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        epic = EPIC_RE.search(text)
        if epic and UMBRELLA_RE.search(text):
            epics.add(epic.group(1))
    return epics


def busy_marker_fresh(task: str) -> bool:
    """True when tools/pipeline wrote a busy-marker for this task (a long gate is
    running, or the orchestrator dispatched a subagent for the stage) and it is
    still live: its owning session is running and its timeout has not run out.
    Lets the Stop hook stay quiet instead of nagging while real work is in
    flight. Fail-open: any error -> not fresh (nag as before)."""
    try:
        return busy.read(task).fresh
    except Exception:
        return False


def busy_marker_note(task: str) -> str:
    """What to append to a nag when the task's marker exists but no longer
    silences anything, so a stale marker is told apart from no marker. Empty for
    a live marker, an absent one, or any error.

    BYTE-STABLE for a given marker: no elapsed time, only the recorded timeout
    and owner. The reason-repeat backstop compares nag text, and a sentence that
    changed every stop would hand the run a fresh budget on every one."""
    try:
        m = busy.read(task)
        clear = (f"clear it with: python .claude/tools/pipeline/advance.py --task {task} "
                 f"--idle")
        if m.status == busy.EXPIRED:
            return (f" (The busy marker for {task} EXPIRED after its {int(m.timeout)}s "
                    f"timeout and no longer silences this hook; {clear}.)")
        if m.status == busy.OWNER_GONE:
            return (f" (The busy marker for {task} is ignored: its owner, session pid "
                    f"{m.owner_pid}, is gone; {clear}.)")
        if m.status == busy.INVALID:
            return (f" (A busy marker for {task} exists but is unreadable, names another "
                    f"task or could not be checked, so it is ignored; {clear}.)")
    except Exception:
        pass
    return ""


def any_busy_marker_fresh() -> bool:
    """True when ANY task has a fresh busy marker.

    The per-task reader above answers "is work in flight on THIS run", which is
    all the in-flight branch needs. Every other branch is about a task that has
    no run of its own - a completed task's documentation, the next backlog task,
    a drift reconciliation - and the question there is "is anything in flight at
    all", because the agent that would clear it is the one that was dispatched.

    Deliberately routed through busy_marker_fresh() rather than re-reading the
    files: one definition of fresh, and one name for a test to patch.

    Fail-open: any error -> nothing is busy, which nags rather than going
    silent."""
    try:
        return any(busy_marker_fresh(task) for task in busy.tasks())
    except Exception:
        return False


def task_file(task: str):
    """The task's file wherever it currently sits, or None."""
    for d in (DONE_DIR, ACTIVE_DIR, BACKLOG_DIR):
        p = d / f"{task}.md"
        if p.is_file():
            return p
    return None


def superseded_by(task: str) -> str | None:
    """The successor task id declared in this task's frontmatter, or None."""
    p = task_file(task)
    if p is None:
        return None
    try:
        m = SUPERSEDED_RE.search(frontmatter(p.read_text(encoding="utf-8")))
    except OSError:
        return None
    return m.group(1) if m else None


def dep_satisfied(dep: str, runs_by_task: dict) -> bool:
    """A dependency is met when its file sits in tasks/done/ (merged, not merely
    pushed) - except when the dependency was superseded, which it can never be.

    A superseded dependency resolves THROUGH its successor: the work moved, so
    the dependent task waits for where it moved, not for a file that will never
    reach done/. A pointer that leads nowhere - successor file missing, or a
    supersede cycle - is unsatisfiable by anything, so it counts as met rather
    than parking the dependent task forever."""
    seen: set[str] = set()
    while dep not in seen:
        seen.add(dep)
        run = runs_by_task.get(dep)
        if (run and run["stage"] == "done") or (DONE_DIR / f"{dep}.md").exists():
            return True
        nxt = superseded_by(dep)
        if nxt is None:
            return False  # ordinary dependency, still unfinished
        if task_file(nxt) is None:
            return True  # dangling pointer - nothing can ever satisfy it
        dep = nxt
    return True  # supersede cycle - same reasoning as a dangling pointer


def stranded(r: dict, pipeline: dict) -> bool:
    """True when a live run sits on a stage its own flow does not define.

    advance.py blocks such a run and names the recovery, but its working tree is
    still dirty and its branch still checked out, so the slot is NOT free: an
    unknown stage is in neither the editing set nor awaiting_human, which let a
    new backlog task branch on top of it - defects 1 and 3 through another door.
    An empty stage list (unreadable pipeline.json) disables the check rather
    than declaring every run stranded."""
    names = state.stage_names(pipeline, state.run_pipeline(r))
    return bool(names) and r["stage"] != "done" and r["stage"] not in names


def handoff_debt() -> list:
    """Completed tasks still lacking a valid handoff doc. Lazy import + fail-open
    so a partially synced clone missing handoff.py cannot break the Stop hook."""
    try:
        import handoff
        return handoff.uncovered_done_tasks()
    except Exception as exc:
        return state.checker_failed("stop_gate.handoff_debt", exc, [])


def memory_debt() -> list:
    """Completed tasks whose memory review was not stamped (tools/memory/
    update.py). Lazy import + fail-open like handoff_debt."""
    try:
        p = str(state.ROOT / ".claude" / "tools" / "memory")
        if p not in sys.path:
            sys.path.insert(0, p)
        import update as memory_update
        return memory_update.unstamped_done_tasks()
    except Exception as exc:
        return state.checker_failed("stop_gate.memory_debt", exc, [])


def debt_nags(key: str) -> int:
    """How many consecutive stops have now been blocked on this exact debt,
    counting the current one. A different `key` - another task, or the other
    kind of debt - starts the count again, so progress resets the budget.

    The count has to outlive the process: the Stop hook is a fresh interpreter
    per event, `stop_hook_active` in the payload says only that a hook already
    ran this turn, and `continuations` belongs to a run while this debt belongs
    to a finished task. Hence a file beside the other state.

    An unreadable file is treated as an absent one and OVERWRITTEN by the next
    write, in its own try. That is the difference between a bound and no bound:
    while `json.loads` shared the outer handler, a file left corrupt by a
    process killed mid-write returned 1 for ever and never got rewritten, so
    the nag became unbounded again with no escalation - the exact failure this
    counter exists to remove. Measured 1, 1, 1, 1 against a healthy 1, 2, 3, 4.

    The outer fail-open returns 1 too, keeping the report and losing the bound,
    which is the right way round because silence is what task-0072 fixed. Note
    it is close to unreachable: decide() opens run.db through state.connect()
    long before it gets here, so a state dir that cannot be written has already
    failed the hook open in main(). The corrupt-file case above needs no such
    thing - the directory is perfectly writable."""
    try:
        state.STATE_DIR.mkdir(parents=True, exist_ok=True)
        p = state.STATE_DIR / DEBT_NAG_FILE
        prev = {}
        if p.is_file():
            try:
                prev = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prev = {}
            if not isinstance(prev, dict):
                prev = {}  # valid JSON of the wrong shape - also just absent
        count = int(prev.get("count", 0)) + 1 if prev.get("key") == key else 1
        p.write_text(json.dumps({"key": key, "count": count}), encoding="utf-8")
        return count
    except Exception:
        return 1


def charge(conn, r: dict, ceiling: int) -> bool:
    """Spend one continuation on this run, and say whether the caller may block.

    False means the budget is gone: the run is parked BLOCKED and the caller
    must fall through to other work rather than blocking on it again.

    EVERY branch that blocks on a RUN goes through here - that is the whole
    invariant, and it is enforced by there being no other writer of
    `continuations` in this file. The branches that block on something with no
    run row cannot: `continuations` is a column of a run, and a completed task's
    unpaid documentation or an unregistered backlog task has none. Those are
    bounded by bounded_block() instead, on the nag file.

    The counter is per STAGE, not per run: advance.py zeroes it on every stage
    transition and approve.py --reject zeroes it too, so a stage starts with the
    full ceiling however long the stages before it took. That is what makes it
    safe to charge a second branch - the `ready` stage's budget is its own.

    The park is written to the DATABASE AND TO `r`, and the second write is not
    housekeeping. `r` comes from the state.all_runs() snapshot decide() took at
    the top, and every later reader in this stop - the free-slot test above all -
    reads that snapshot, not the database. Without the in-memory write the run
    still says `in_progress` to everything downstream on the very stop that
    parked it, so a caller that relies on the BLOCKED status to hold the working
    tree is reading a value that is already false. Measured on the plan flow: the
    hook answered "Start the next ready task" on the parking stop while the run
    held its branch. Build never showed it because its callers key on the stage
    name, which this does not touch."""
    cont = r["continuations"] + 1
    if cont > ceiling:
        state.set_fields(conn, r["task"], stage_status=state.ST_BLOCKED,
                         reason=f"continuation ceiling of {ceiling} spent at '{r['stage']}'")
        r["stage_status"] = state.ST_BLOCKED
        r["parked_now"] = True
        return False
    state.set_fields(conn, r["task"], continuations=cont)
    return True


# The ways out of a spent bound, worded for the model and for the human. Debt is
# the default; a caller whose demand is not documentation passes its own pair.
DEBT_OPTIONS = (
    ("pay it, waive it (handoff.py --waive / update.py --stamp --none), "
     "or change the baseline in pipeline.json"),
    "pay it, waive it, or change the baseline in pipeline.json")


def bounded_block(key: str, subject: str, reason: str,
                  options: tuple[str, str] = DEBT_OPTIONS) -> int:
    """Block with `reason`, but not forever.

    The run-less counterpart of charge(): same contract, different counter,
    because there is no run row to charge. `key` identifies the demand, so a
    different one starts its own budget and progress restores it.

    Past the ceiling it escalates ONCE, naming the card, then goes quiet. Going
    quiet is the lesser evil only because the escalation said so out loud - an
    unbounded block on an unsatisfiable demand is the stop loop this module's
    docstring promises never to create."""
    n = debt_nags(key)
    if n > DEBT_NAG_CEILING + 1:
        return allow()  # escalated already; the CEO owns it now
    if n > DEBT_NAG_CEILING:
        return block(
            f"{subject} has survived {n - 1} stops and is not being cleared. Stop "
            f"re-attempting it and raise it with the CEO now (AskUserQuestion): the "
            f"situation, the options - {options[0]} - and your "
            f"recommendation. This hook will not raise it again, so if you drop it now "
            f"nothing else will catch it. The instruction that is not working: {reason}",
            f"{subject} has survived {n - 1} stops and is not being cleared. Decision "
            f"owed by you: {options[1]}.",
            "blocked")
    return block(reason, f"{subject}. The agent has been told to clear it; nothing is "
                         f"owed by you unless it keeps failing.")


def unknown_repo_block(t: dict) -> int:
    """Name a backlog task whose `repo:` is no key of `repos{}` (FR-45).

    read_backlog() offers such a task to no lane, so without this it would sit
    in backlog/ for ever and nothing would say why. It has no run row to
    charge(), so it spends the run-less counter, bounded_block(), keyed by the
    task: that is the FR-37 invariant (no blocking branch bypasses a counter)
    kept for a demand with nothing to park. It lives in a helper so the block
    sites counted in decide() stay what test_conveyor_gaps pins."""
    keys = ", ".join(sorted(git_state.repo_entries())) or "none"
    fix = "fix the task's repo:, or add the repo to repos{} in pipeline.json"
    return bounded_block(
        f"repo:{t['id']}",
        f"{t['id']} names repo '{t['repo']}', which is not a key of repos{{}}",
        f"{t['id']} in the backlog names `repo: {t['repo']}`, which is not a key of "
        f"repos{{}} in .agentry/pipeline.json (keys: {keys}), so no lane is offered it "
        f"and it will not start. Correct its `repo:` frontmatter to one of those keys, "
        f"or add the repo to repos{{}}. Do not ask the user.",
        (fix, fix))


def done_conflict_block(task: str) -> int:
    """Name a task whose file is in done/ and still in backlog/ or active/ with
    different text. state.move_task() refuses that for good, so loops A and B of
    reconcile_status_drift() skip it without a word; this is the word. Bounded
    like every run-less demand, so it is said, escalated once, then left to the
    CEO. In a helper for the same reason as unknown_repo_block()."""
    fix = (f"compare tasks/done/{task}.md with its twin in backlog/ or active/ and "
           f"delete the one that is wrong")
    return bounded_block(
        f"conflict:{task}",
        f"{task} exists in tasks/done/ and in backlog/ or active/ with different text",
        f"{task} is in tasks/done/ and also in backlog/ or active/, and the two files "
        f"differ, so the status move refuses it and reconciliation skips it. A person "
        f"must {fix}. Do not ask the user.",
        (fix, fix))


def clear_debt_nags(live_keys=()) -> None:
    """Forget the count unless it belongs to a demand still outstanding, so the
    next demand gets its full budget rather than inheriting a spent one.

    Keyed rather than all-or-nothing: the file holds ONE key, and clearing only
    when EVERY debt was absent left a spent key alive whenever any other debt
    happened to be outstanding at the same moment. If that same key then
    recurred - a handoff doc edited back below `min_section_chars`, say -
    `debt_nags` resumed above the ceiling and `bounded_block` allowed
    immediately, so the demand was never uttered even once. A stale key is a
    spent budget for a demand nobody is making.

    `live_keys` carries only the demands the caller EVALUATED, and a key is
    cleared only when its namespace was evaluated and it was not among them.
    An empty `live_keys` therefore means "no handoff or memory debt", never
    "nothing is owed anywhere": the caller does not evaluate `main-handoff:`
    (site 6), whose source costs a `git log` on every stop, and reading its
    absence as death unlinked that key on every stop it spoke on - measured, the
    count sat at 1 through eight stops, so it could neither escalate nor go
    quiet. See RECONCILED_PREFIXES before adding a namespace."""
    try:
        p = state.STATE_DIR / DEBT_NAG_FILE
        try:
            key = json.loads(p.read_text(encoding="utf-8")).get("key")
        except (OSError, ValueError):
            key = None  # absent or corrupt: nothing worth preserving
        if isinstance(key, str) and (key in live_keys
                                     or not key.startswith(RECONCILED_PREFIXES)):
            return
        p.unlink()
    except OSError:
        pass


def latest_undocumented():
    """Latest task-tagged commit on main lacking a handoff doc (single-task
    backlog case - see handoff.latest_main_task_undocumented). Fail-open."""
    try:
        import handoff
        return handoff.latest_main_task_undocumented()
    except Exception as exc:
        return state.checker_failed("stop_gate.latest_undocumented", exc, None)


def reconcile_status_drift(conn) -> tuple[list, list]:
    """Deterministic status-management enforcement (replaces the manual
    'move file to done/' step the orchestrator kept skipping).

    Closes ONLY what cannot be a skipped checkpoint:

      A. A task file in tasks/active/ whose run is already at stage 'done', or
         that has NO run row at all, and whose work main carries - judged by
         git_state.task_tagged_in_main(): a commit on the trunk naming the task,
         over the task's OWN repo(s) only. A run at any other stage is never
         touched here, whatever git says.
      B. A run still marked in-flight whose file already sits in tasks/done/ (or
         vanished) is closed to 'done' - but only on the same evidence AND only
         when its commit checkpoint was approved: a run that never passed the
         commit approval cannot have been merged by the pipeline, so a trunk
         commit naming it is somebody else's text.

    Never decided from `branch:` in the task file or from a branch whose name
    holds the id: the agent being judged can write both, and `merged_into_main`
    answers True for any ref merged at some point in the past. Moving the file
    also sweeps an umbrella's subtasks, so a forgeable signal here skipped every
    checkpoint of every one of them.

    Returns (moved, closed) task-id lists. Fail-open: never raises."""
    moved, closed = [], []
    try:
        runs = {r["task"]: r for r in state.all_runs(conn)}
        active_files = list(ACTIVE_DIR.glob("task-*.md")) if ACTIVE_DIR.is_dir() else []
        for f in active_files:
            task = f.stem
            run = runs.get(task)
            if run is not None and run["stage"] != "done":
                continue
            # No fetch: this runs on every Stop and must not wait on the network.
            if not git_state.task_tagged_in_main(task):
                continue
            if state.move_task_to_done(task):
                moved.append(task)
                moved.extend(state.move_swarm_subtasks(task, conn))
                if run is not None:
                    # The merge is what a run at 'done' was waiting for; leaving
                    # awaiting_human set would freeze the queue on a finished task.
                    state.set_fields(conn, task, stage="done", awaiting_human="",
                                     stage_status=state.ST_GATE_PASSED, event="merged")
        for task, r in runs.items():
            # A run whose file left active/ for done/ - or vanished - is stale;
            # close it. A file put BACK into backlog/ is a deliberate unqueue,
            # so leave that run alone rather than silently marking it finished.
            if (r["stage"] != "done"
                    and r.get("commit_approved")
                    and state.task_dir(task) in (None, "done")
                    and git_state.task_tagged_in_main(task)):
                state.set_fields(conn, task, stage="done", awaiting_human="",
                                 stage_status=state.ST_GATE_PASSED, event="closed",
                                 reason="its task file left active/ and main carries its tag")
                closed.append(task)
    except Exception:
        pass
    return moved, closed


# What each `awaiting_human` value advance.py writes means to the person the run
# is parked on, and which of the four terminal events it is. "blocked" is absent
# on purpose: decide() step 2 surfaces a blocked run once and then leaves it alone.
OWED_TEXT = {
    "commit": "read the diff (/diff) and approve or reject the commit",
    "push": "approve or reject the push",
    "plan": "approve or reject the plan",
    "merge": "merge the pull request into main",
}
OWED_EVENT = {"commit": "commit", "push": "push"}
# Once the approval is recorded nothing is owed; the git action is the agent's.
APPROVED_FLAG = {"commit": "commit_approved", "push": "push_approved",
                 "plan": "plan_approved"}


def owed_to_ceo(runs: list[dict]) -> list[tuple[str, str | None]]:
    """[(sentence for the human, terminal event or None)] for every run parked on
    him. Any value of `awaiting_human` not named above still produces a sentence,
    so a new park reason is announced rather than silently dropped.

    Two exceptions to "a done run is finished". The merge wait is written ONLY at
    stage `done` (advance.py _merge_wait: the checkpoints passed and the main
    branch does not carry the task yet), so skipping done would skip the only
    place it exists. And it is the human's only in `pr` mode, where he merges the
    pull request; in `solo` the pipeline merges locally and he owes nothing."""
    owed = []
    for r in runs:
        aw = r["awaiting_human"]
        if state.is_paused(r):
            continue  # set aside by the CEO himself: nothing is owed until resume
        if not aw or aw == AWAITING_BLOCKED or r.get(APPROVED_FLAG.get(aw, "")):
            continue
        if r["stage"] == "done" and aw != "merge":
            continue
        if aw == "merge" and pretool_gate.workflow_mode() == pretool_gate.WORKFLOW_SOLO:
            continue
        what = OWED_TEXT.get(aw, f"resolve '{aw}'")
        sentence = f"{r['task']} is parked at stage '{r['stage']}'. Owed by you: {what}."
        owed.append((sentence, OWED_EVENT.get(aw)))
    return owed


def decide() -> int:
    if not mode.conveyor_runs():
        return allow()

    conn = state.connect()
    try:
        runs = state.all_runs(conn)
        runs_by_task = {r["task"]: r for r in runs}
        # What block()'s backstop needs to park a looping run. Set once, here,
        # rather than threaded through every branch that might block.
        _CTX["conn"], _CTX["runs"] = conn, runs
        pipeline = state.load_pipeline()
        ceiling = int(pipeline.get("continuation_ceiling", 30))

        # 1. Documentation owed by COMPLETED tasks, read BEFORE anything else.
        #
        # Not inside the free-slot branch, which is where it used to live: that
        # condition - "some task is waiting for the CEO" - is the normal state of
        # a pipeline with a human in it, and it switched the only report of
        # undocumented work off for as long as it held (task-0072). Whether a
        # finished task is documented is true or false regardless of what is in
        # flight.
        #
        # And not after the run loop below either, which is where it sat until
        # the nag bound was added. Step 2 returns on every stop that has a run to
        # drive, so a spent nag key was never revisited while any task was in
        # flight: measured, a key spent to the ceiling survived three stops with
        # an editing run in flight, and the same demand recurring afterwards was
        # allowed without being uttered once. The count is bookkeeping, so it is
        # reconciled on EVERY stop, before any branch can return.
        debt = handoff_debt()
        mem_debt = memory_debt()
        clear_debt_nags({f"handoff:{d['task']}" for d in debt}
                        | {f"memory:{d['task']}" for d in mem_debt})

        # 2. Continue an in-flight, actionable run.
        #
        # ORDERING DECISION (task-0059), recorded because the eleven block sites
        # below are read in this order and the order is the policy:
        #   1. documentation debt is only READ above, never returned on: it is
        #      raised from step 3 and step 5, after the runs have been driven;
        #   2. this loop returns on the first run that needs driving, so while
        #      one does, steps 3 to 6 do not run at all;
        #   3. a PAUSED run is skipped before any other test (below), so it is
        #      never driven, never charged a continuation and never needs a busy
        #      marker to stay quiet. It is also excluded from `occupied` in step
        #      3, so the next task may start while it waits.
        # The drift reconciler (step 4) is deliberately NOT reachable while a run
        # is in flight. Its mutations are file moves and run closures, and the
        # moving of a merged task into done/ is what creates handoff debt; doing
        # either underneath a run that an agent is editing is the wrong time.
        # The incident that asked for reachability (a task file moved back to
        # backlog/ while its run stayed in flight) was never a reconciler job
        # anyway: step B leaves a backlog/ file alone as a deliberate unqueue.
        # The supported way to set a run aside is `approve.py --pause`.
        for r in runs:
            stage, status, aw = r["stage"], r["stage_status"], r["awaiting_human"]
            if stage == "done":
                continue
            if state.is_paused(r):
                continue
            # THE RUN'S OWN FLOW, not the build flow (FR-39). This was
            # state.EDITING_STAGES, a module constant naming three build stages,
            # so every branch below was blind to the `plan` pipeline: a plan run
            # mid-flight matched no branch, nothing drove it, and step 3 read a
            # free slot and started a backlog task on top of its tree. Resolved
            # per run because `runs` can hold one of each.
            which = state.run_pipeline(r)
            editing = state.editing_stages(pipeline, which)
            # Who owns this stage: an agent (editing) or a person (checkpoint).
            # Both come from the run's own flow, and both are needed, because a
            # checkpoint stage must be left alone by the drive branch AND must
            # still hold the working tree in the free-slot test below.
            checkpoints = state.checkpoint_stages(pipeline, which)
            if status == state.ST_BLOCKED:
                # A BLOCKED RUN IS SURFACED HERE, EXACTLY ONCE (task-0067).
                # It is not autonomously advanceable, so this hook used to skip
                # it silently; the supervisor answers HEALTHY ("already parked
                # blocked") and takes no action, so it is silent too. Neither was
                # wrong on its own, and between them a task nobody was driving
                # produced no signal at all for ninety minutes.
                #
                # This hook owns the surfacing because its output is the only one
                # of the two that lands in the live session the CEO reads - the
                # supervisor is detached and logs to a file nobody has open.
                #
                # ONCE, not every stop: `awaiting_human` is the marker, which is
                # not new state but the harness's existing "a human must act on
                # this" flag - true of a blocked run by definition. An unbounded
                # nag would be a stop loop, which is the opposite failure. The
                # only exit from `blocked` is approve.py --reject, and it clears
                # `awaiting_human`, so the surfacing re-arms itself.
                #
                # The test is "was it surfaced", NOT "is anything awaited", and
                # the difference is a silent halt. `if not aw` read any pending
                # checkpoint as a surfacing that already happened: charge() parks
                # the `ready` branch below while `awaiting_human` is necessarily
                # 'commit' or 'push' (that branch cannot fire otherwise), so the
                # run went BLOCKED with `aw` non-empty and was never raised here.
                # Measured: two stops, both silent, no writes on the second - and
                # since `ready` now counts as occupied, the queue was held too.
                # An editing run still arrives with `aw` empty, so nothing about
                # that path changes.
                if aw != AWAITING_BLOCKED:
                    state.set_fields(conn, r["task"], awaiting_human=AWAITING_BLOCKED)
                    return block(
                        f"{r['task']} is parked BLOCKED at stage '{stage}' and nothing is "
                        f"driving it: the pipeline will not advance it and the supervisor "
                        f"will not touch it. Raise it with the CEO now (AskUserQuestion): "
                        f"what the blocker is, and the options - fix it and re-run "
                        f"advance.py, or send the task back with python "
                        f".claude/tools/pipeline/approve.py --task {r['task']} --reject --reason "
                        f"\"<why>\". "
                        f"Do not silently move on to other work.",
                        f"{r['task']} is parked BLOCKED at stage '{stage}' and nothing is "
                        f"driving it. Decision owed by you: fix the blocker so the agent can "
                        f"re-run it, or send the task back to its first stage.", "blocked")
                continue
            # Approved checkpoint action still pending (commit/push the agent must do).
            # Charged like the editing branch and for the same reason: it repeats
            # on every stop until the git action happens, so without a counter it
            # is unbounded. It was, until task-0018.
            if stage == "ready" and ((aw == "commit" and r["commit_approved"])
                                     or (aw == "push" and r["push_approved"])):
                if busy_marker_fresh(r["task"]):
                    continue
                if not charge(conn, r, ceiling):
                    continue
                return block(
                    f"{r['task']} checkpoint approved (awaiting_human={aw}). Perform the "
                    f"git {aw} now, then run: python .claude/tools/pipeline/advance.py --task {r['task']}. "
                    f"Do not ask the user.{busy_marker_note(r['task'])}",
                    f"{r['task']}: the {aw} approval is recorded and the agent is doing the "
                    f"git {aw} now. Nothing is owed by you.")
            # Parked, unapproved checkpoint -> not autonomously advanceable.
            if stage == "ready" and aw in ("commit", "push"):
                continue
            # Editing stage in flight (in_progress or gate_failed retry).
            #
            # A CHECKPOINT STAGE IS NEVER DRIVEN, even if a flow lists it in its
            # editing_stages. This branch's sentence is "finish the stage work",
            # and the work of a checkpoint stage is a human's approval, so the
            # demand is addressed to nobody - and since task-0019 a byte-stable
            # sentence parks the run on the third armed repeat, which at a
            # checkpoint costs an approval that was already given. The free-slot
            # test below has the other half: a stage that stops being nagged must
            # still hold the tree, or the queue takes it from under the human.
            # Those two halves are the pair that produced task-0018's Critical,
            # so they move together or not at all.
            if stage in editing and stage not in checkpoints:
                # A human owns the run - same guard the branch below carries, and
                # a guard rather than a fix: no writer of `awaiting_human`
                # produces this state today, because the two branches above claim
                # every `ready` run and no other build stage sets the column. It
                # is written down because the set is config now. Give
                # `awaiting_human` a third writer and without this line the
                # conveyor starts nagging over a human.
                if aw:
                    continue
                # A fresh busy-marker means a long gate or a dispatched subagent
                # is legitimately working this stage - stay quiet, do not nag or
                # burn the continuation ceiling until it finishes or the marker
                # goes stale.
                if busy_marker_fresh(r["task"]):
                    continue
                if not charge(conn, r, ceiling):
                    continue  # stuck -> blocked, fall through to other work
                if status == state.ST_GATE_FAILED:
                    return block(
                        f"{r['task']} stage '{stage}' gate FAILED. Fix the cause, then re-run: "
                        f"python .claude/tools/pipeline/advance.py --task {r['task']}. Do not ask the user."
                        f"{busy_marker_note(r['task'])}",
                        f"{r['task']} failed the exit gate of stage '{stage}'. The agent is "
                        f"fixing it and re-running; nothing is owed by you unless the retries "
                        f"run out.", "gate-failure")
                return block(
                    f"{r['task']} is at stage '{stage}'. Finish the stage work (dispatch the owning "
                    f"subagent if needed), then run: python .claude/tools/pipeline/advance.py "
                    f"--task {r['task']}. Do not ask the user whether to continue."
                    f"{busy_marker_note(r['task'])}",
                    f"{r['task']} is at stage '{stage}' and the agent is still working on it. "
                    f"Nothing is owed by you.")
            # A LIVE RUN ON A STAGE ITS OWN FLOW DEFINES AND editing_stages DOES
            # NOT NAME (FR-39). Before this branch such a run matched nothing in
            # the loop and the hook said not one word about it. That was
            # invisible while the set was a build-only constant, because every
            # build stage is covered by a branch above; the moment the set comes
            # from config it is a live hole, since leaving a stage out of
            # editing_stages then means "the conveyor never mentions this run
            # again", which is not what an operator editing that key is asking
            # for. The plan flow reaches it today at `approval`.
            #
            # THREE EXCLUSIONS, each preserving a behaviour the build flow
            # already had, which is why they are guards and not oversights:
            #
            #   - a stage the flow does not define at all. That is stranded(),
            #     which advance.py already blocks by name; it holds the queue
            #     through the occupied test below and stays silent here. A
            #     pipeline.json too broken to read names no stages, so this also
            #     keeps an unreadable config from nagging about every run.
            #   - a checkpoint stage, resolved by state.checkpoint_stages():
            #     `ready` on build, `approval` on plan, and whatever a later flow
            #     declares. Same reason as the drive branch above - the work is a
            #     human's - stated once for every flow instead of once per stage
            #     name. The key is `checkpoints`, the only spelling any reader accepts.
            #   - `awaiting_human` set: a human owns the run and there is
            #     nothing to demand of the orchestrator. Same reason the
            #     checkpoint branches above continue rather than block.
            if (stage not in state.stage_names(pipeline, which)
                    or stage in checkpoints
                    or aw or busy_marker_fresh(r["task"])):
                continue
            if not charge(conn, r, ceiling):
                continue
            return block(
                f"{r['task']} is at stage '{stage}' of the '{which}' flow, which "
                f"pipelines.{which}.editing_stages does not list, so no branch of the Stop "
                f"hook drives it. Run: python .claude/tools/pipeline/advance.py "
                f"--task {r['task']} - it advances the stage or names the recovery. If the "
                f"stage is real work, add it to editing_stages. Do not ask the user."
                f"{busy_marker_note(r['task'])}",
                f"{r['task']} is at stage '{stage}', which the '{which}' flow does not list as "
                f"work for the agent. The agent is being told to advance it; nothing is owed "
                f"by you.")

        # Nothing above was driveable. If ANY task has a fresh busy marker, a
        # dispatched agent is working right now and every branch below would be
        # talking over it: demanding a handoff doc from the agent writing it
        # (observed on tasks 0004, 0005, 0007, 0008 and three times on 0009 -
        # a closed loop, since the instruction was to do what was already being
        # done), offering a new task on top of its tree, or reconciling drift
        # underneath it.
        #
        # This is NOT the mistake task-0072 fixed, and the difference is the
        # only reason it is safe. `occupied` silenced the debt on a condition
        # with no clock in it - a task parked on the CEO stays parked - so the
        # silence was permanent. A marker expires (state.BUSY_TIMEOUT), so the
        # debt is deferred by at most one marker's life and is then raised again
        # by the branches below.
        if any_busy_marker_fresh():
            return allow()

        # 3. Nothing holding the working tree -> start the next ready backlog task.
        # Three states hold it, and a new task on top of any of them switches the
        # branch out from under someone:
        #   - an editing stage in flight;
        #   - an editing stage parked BLOCKED (a blocked task still owns its dirty
        #     tree; it is surfaced to the CEO in step 2, not retired);
        #   - ANY run awaiting a human - the CEO may be reading the diff on that
        #     very branch. A task parked on a human is unfinished work, not a
        #     free slot, so go quiet and wait instead;
        #   - a run on a CHECKPOINT STAGE, approved or not. Missing until
        #     task-0018: with every checkpoint auto-approved, `awaiting_human` is
        #     empty and a checkpoint stage is not an editing stage, so a task
        #     holding a dirty tree and a checked-out branch read as a free slot
        #     and the next backlog task was started on top of it;
        #   - a run stranded on a stage its flow does not define (see stranded()).
        #
        # THE CHECKPOINT CLAUSE IS RESOLVED FROM CONFIG, not from a stage name,
        # and that is the root fix rather than a tidy-up. This read
        # `r["stage"] == "ready"`, which is true of the build flow because the
        # build flow happens to spell its checkpoint that way. Every other flow
        # fell through it: the plan flow's `approval` matched nothing here and
        # nothing in the run loop, so the conveyor drove the CEO's own approval
        # stage and read its tree as free at the same time. Special-casing
        # `approval` would have been a second literal of the same shape, and the
        # next flow a third. state.checkpoint_stages() asks the definition
        # instead, so a stage is a checkpoint because its config says so.
        #
        # It pairs with the exclusion in the drive branch above and must move
        # with it: what is not nagged must still occupy, or the queue takes the
        # tree from under the human the nag was withheld for. That pair is
        # task-0018's Critical restated, which is why both directions are tested
        # for both flows.
        #
        # This hook only stops the conveyor driving `approval`. What stops a
        # hand-run `advance.py --task X` walking it to `breakdown` is the plan
        # checkpoint itself (task-0089): run.db plan_approved, parked on by
        # advance.py, recorded by approve.py --gate plan. An absent `exit_gate`
        # returns configured=True AND passed=True from gate.py, so that
        # checkpoint is the only thing that holds the stage.
        #
        # The BLOCKED clause is the same "a blocked task still owns its dirty
        # tree" the second bullet states, spelled without going through a stage
        # set. It fires on exactly one stop, the one charge() parks on: the
        # caller continues with `awaiting_human` still empty, and the NEXT stop
        # is covered by the surfacing branch, which returns before this test is
        # reached. While the set covered every parkable stage that window was
        # covered too; now that any stage can be parked by the branch above, it
        # needs saying. A misconfigured editing_stages therefore costs driving,
        # never tree safety.
        #
        # It reads `r["stage_status"]` out of the SNAPSHOT, so it depends on
        # charge() writing the park into `r` as well as into the database. That
        # coupling is the whole clause: with the in-memory write removed this
        # reads `in_progress` on the only stop it exists for and the queue hands
        # the tree away. Verified by deleting each half in turn.
        #
        # A PAUSED run holds nothing (task-0059): `approve.py --pause` refuses
        # while uncommitted work exists, so its tree is clean and its commits sit
        # on its own branch. It is therefore left out of this test whole, whatever
        # stage it was set aside at. This is the paused half of the pair whose
        # other half is the `continue` at the top of the step 2 loop.
        occupied = any(
            not state.is_paused(r) and (
                r["stage"] in state.editing_stages(pipeline, state.run_pipeline(r))
                or r["stage"] in state.checkpoint_stages(pipeline, state.run_pipeline(r))
                or r["awaiting_human"]
                or (r["stage"] != "done" and r["stage_status"] == state.ST_BLOCKED)
                or stranded(r, pipeline)
            )
            for r in runs
        )
        if not occupied:
            # Taking work on is itself a checkpoint. Below `auto` the CEO says
            # which task to start, so an empty in-flight set means "stop and
            # ask", not "help yourself to the queue".
            queue = read_backlog()
            # Tasks read_backlog() flagged as naming a repo that is no key of
            # repos{} are offered to no lane; they are named after the ready
            # tasks below, never started.
            unrouted = [t for t in queue if t.get("unknown_repo")]
            queue = [t for t in queue if not t.get("unknown_repo")]
            take = approvals.granted(approvals.TAKE)
            ready = ([t for t in queue
                      if t["id"] not in runs_by_task
                      and all(dep_satisfied(d, runs_by_task) for d in t["deps"])]
                     if take else [])
            # Swarm ON: an epic's subtasks are the umbrella's work list, not
            # queue entries. Hide those of an epic whose umbrella is registered.
            swarm_ready = any(t.get("epic") for t in ready) and swarm_on()
            if swarm_ready:
                held = umbrella_epics(runs)
                ready = [t for t in ready if t.get("umbrella") or t.get("epic") not in held]
            for t in ready:
                swarm_epic = ""
                if swarm_ready and t.get("epic") and not t.get("umbrella"):
                    # A queued umbrella for this epic is started in its subtask's
                    # place (or waited for, when its own dependencies are open).
                    # Without one, an epic with two or more subtasks is demanded
                    # as a swarm; a lone subtask stays an ordinary task.
                    umbrella = next((u for u in queue
                                     if u.get("umbrella") and u.get("epic") == t["epic"]), None)
                    if umbrella:
                        if umbrella not in ready:
                            continue
                        t = umbrella
                    elif sum(1 for u in queue
                             if u.get("epic") == t["epic"] and not u.get("umbrella")) >= 2:
                        swarm_epic = t["epic"]
                # What the demands below end in: the advance.py command for this
                # task, or the swarm start when t is a subtask of a swarm epic.
                tail = (swarm_tail(swarm_epic) if swarm_epic else
                        f"run: python .claude/tools/pipeline/advance.py --task {t['id']}.")
                # Handoff chain: the next task's assignee documents the previous
                # completed task BEFORE registering new work (forced context
                # absorption - see tools/pipeline/handoff.py).
                if debt:
                    d = debt[0]
                    return bounded_block(
                        f"handoff:{d['task']}",
                        f"Handoff debt on completed task {d['task']}",
                        f"Before starting {t['id']}: completed task {d['task']} has no valid "
                        f"handoff doc ({d['reason']}). Dispatch {t['id']}'s assignee (see its "
                        f"frontmatter) to scaffold it: python .claude/tools/pipeline/handoff.py "
                        f"--for {d['task']}, then fill every section of "
                        f".agentry/tasks/handoffs/{d['task']}.md in its own words, reading "
                        f".agentry/project/project-context.md and {t['id']}'s spec first. Then "
                        f"{tail} Do not ask the user.")
                # Memory chain: after the handoff doc exists, distill it into the
                # memory store BEFORE new work (tools/memory/update.py).
                if mem_debt:
                    d = mem_debt[0]
                    return bounded_block(
                        f"memory:{d['task']}",
                        f"Memory debt on completed task {d['task']}",
                        f"Before starting {t['id']}: completed task {d['task']} has not been "
                        f"distilled into the memory store. From its handoff doc, record one row "
                        f"per item with python .claude/tools/memory/memory.py --record: Gotchas "
                        f"-> --kind lesson (--signature, --trigger, --what, --why, --fix); "
                        f"reusable code shapes -> --kind pattern (--name, --use-when, --body); "
                        f"touched modules -> --kind module (--path, --responsibility). Then "
                        f"stamp: python .claude/tools/memory/update.py --stamp --task "
                        f"{d['task']} (refused unless the store gained a row, so pass --none if "
                        f"the review found nothing worth recording), and refresh the module-map "
                        f"heads: python .claude/tools/memory/codebase_sync.py --stamp. Then "
                        f"{tail} Do not ask the user.")
                # Single-task backlog: the previous work's documentation is the
                # ONLY context source - the latest task-tagged commit on main
                # must be documented (read it, or generate it) before implementing.
                if len(ready) == 1:
                    latest = latest_undocumented()
                    if latest:
                        # Its OWN namespace, not `handoff:`. This demand comes
                        # from latest_undocumented(), which the reconciliation
                        # above does not call, so a `handoff:` key here would be
                        # cleared on every stop as an unevaluated stranger and
                        # its count would never leave 1.
                        return bounded_block(
                            f"main-handoff:{latest['task']}",
                            f"Handoff debt on merged task {latest['task']}",
                            f"Before starting {t['id']} (the only ready task): the latest "
                            f"task commit on main - {latest['task']} "
                            f"({latest['sha'][:12]}) - has {latest['reason']}. Dispatch "
                            f"{t['id']}'s assignee to generate it first: python "
                            f".claude/tools/pipeline/handoff.py --for {latest['task']}, fill "
                            f"every section of .agentry/tasks/handoffs/{latest['task']}.md in "
                            f"its own words from the commit diff (git show "
                            f"{latest['sha'][:12]}), then {tail} Do not ask the user.")
                if swarm_epic:
                    reason, human = swarm_demand(t["id"], swarm_epic)
                else:
                    reason = (
                        f"Start the next ready task {t['id']}: create its branch per "
                        f"git-workflow.md, then run: python "
                        f".claude/tools/pipeline/advance.py --task {t['id']}. "
                        f"Drive it through the pipeline without asking the user.")
                    human = (
                        f"Nothing is parked on you, so the agent is starting the next ready "
                        f"task, {t['id']}. Nothing is owed by you.")
                return block(reason, human)
            # Nothing ready for this lane, so the unroutable task is what is
            # left to say. Only under TAKE, like the queue itself: below `auto`
            # the CEO picks the task and this hook offers nothing.
            if take and unrouted:
                return unknown_repo_block(unrouted[0])

        # 4. Nothing to drive -> enforce status-management drift before idling:
        #    move merged-but-active task files to done/, close stale runs.
        moved, closed = reconcile_status_drift(conn)
        if moved or closed:
            bits = []
            if moved:
                bits.append(f"moved to tasks/done/: {', '.join(moved)}")
            if closed:
                bits.append(f"closed stale run(s): {', '.join(closed)}")
            return block(
                "Reconciled task-status drift (" + "; ".join(bits) + "). These were "
                "already merged but left in tasks/active/ or run.db. This is bookkeeping "
                "only - do not re-open them; confirm and stop.",
                "Bookkeeping: " + "; ".join(bits) + ". These tasks were already merged. "
                "Nothing is owed by you.")
        conflicts = state.move_conflicts()
        if conflicts:
            return done_conflict_block(conflicts[0])

        # 5. Documentation debt that step 3 had no ready task to gate - the last
        # thing before the session is allowed to end. It BLOCKS rather than
        # merely mentioning, for three reasons worth keeping in the code:
        #
        #   - A Stop hook has exactly one channel to the MODEL, the block reason.
        #     An allowed stop reaches the model with nothing (release() speaks
        #     only to the human), so "report without blocking" is not a softer
        #     block, it is silence toward the model - which is the failure this
        #     branch exists to end.
        #   - It cannot fight edit-serialization. Step 2 returns first for every
        #     run that is actually being driven, so nothing reaching here is
        #     mid-advance, and paying the debt writes to
        #     .agentry/tasks/handoffs/ and the memory store, never to the
        #     working tree a parked task still owns.
        #   - It may repeat where the surfacing of a BLOCKED run may not (step 2
        #     raises that one ONCE), because the agent reading this message can
        #     clear the debt itself - write the doc, record the rows, or --waive
        #     it with the CEO's approval - while a blocked run is clearable only
        #     by the CEO.
        #
        # "May repeat" is not "may repeat forever", and the difference is
        # DEBT_NAG_CEILING. The argument above assumes the demand is
        # satisfiable, and it has a degenerate case: a doc that keeps failing
        # min_section_chars, or a store that keeps refusing the stamp. After the
        # ceiling the hook says so once, hands the decision to the CEO, and goes
        # quiet - a bound, not a retreat to the silence this branch ended.
        #
        # After the drift reconciliation above, not before: moving a merged task
        # into done/ is what CREATES debt, so paying first would only raise it
        # again on the next stop.
        #
        # Same bound as the step 3 copies of these two demands, through the same
        # key: it is one debt, and which branch happens to raise it depends only
        # on whether a backlog task existed to gate. Two budgets for one demand
        # would mean paying twice as many stops to escalate it.
        if debt:
            d = debt[0]
            return bounded_block(
                f"handoff:{d['task']}",
                f"Handoff debt on completed task {d['task']}",
                f"Documentation debt is outstanding and there is no other work to drive: "
                f"completed task {d['task']} has no valid handoff doc ({d['reason']}). "
                f"Nothing else in the harness can act on this - the supervisor only logs "
                f"it, and cannot reach this session. Dispatch the assignee of the next task to scaffold it: "
                f"python .claude/tools/pipeline/handoff.py --for {d['task']}, then fill every "
                f"section of .agentry/tasks/handoffs/{d['task']}.md in its own words from "
                f".agentry/tasks/done/{d['task']}.md, its merge diff on main and the gate log. "
                f"If the CEO has decided the doc is not owed, he waives it: handoff.py --waive "
                f"{d['task']} --reason \"...\". Do not ask the user whether to write it.")
        if mem_debt:
            d = mem_debt[0]
            return bounded_block(
                f"memory:{d['task']}",
                f"Memory debt on completed task {d['task']}",
                f"Memory debt is outstanding and there is no other work to drive: completed "
                f"task {d['task']} has not been distilled into the memory store. From "
                f".agentry/tasks/handoffs/{d['task']}.md record one row per item with python "
                f".claude/tools/memory/memory.py --record: Gotchas -> --kind lesson "
                f"(--signature, --trigger, --what, --why, --fix); reusable code shapes -> "
                f"--kind pattern (--name, --use-when, --body); touched modules -> --kind "
                f"module (--path, --responsibility). Then stamp: python "
                f".claude/tools/memory/update.py --stamp --task {d['task']} (refused unless "
                f"the store gained a row, so pass --none if the review found nothing worth "
                f"recording). Do not ask the user whether to do it.")

        # 6. Everything is parked / blocked / done and nothing ready -> release.
        # A run parked on the CEO is the one release that has something to say to
        # him: this is where the commit and push checkpoints, the plan approval
        # and the merge wait reach a person, since the advance.py output that
        # parked them only goes to the model.
        owed = owed_to_ceo(runs)
        if owed or parked_now(runs):
            return release(" ".join(text for text, _ in owed),
                           next((ev for _, ev in owed if ev), None))
        return allow()
    finally:
        # The snapshot is this stop's alone: left behind, a later call outside
        # decide() would announce parks that were already announced.
        _CTX.update(conn=None, runs=())
        conn.close()


def main() -> int:
    global STOP_HOOK_ACTIVE
    # Cleared FIRST, so the flag is defined by this payload and never inherited.
    # Assigning only on success left it sticky: armed once, an unreadable payload
    # afterwards kept the backstop live on no evidence at all. It costs one line
    # to make "we could not read the payload" and "the payload said false" the
    # same state, which is what an untrusted input deserves.
    STOP_HOOK_ACTIVE = False
    try:
        payload = json.load(sys.stdin)
        STOP_HOOK_ACTIVE = bool(payload.get("stop_hook_active"))
    except Exception:
        # Unreadable payload -> disarmed -> every branch reports as it did before
        # this task. Losing the bound is the correct failure; losing the report
        # is the silent halt.
        pass
    try:
        return decide()
    except Exception:
        # FAIL OPEN, AND SAY SO. Failing open is not in question - a crash here
        # must never trap the user in a stop loop, and it still does not: exit 1
        # allows the stop exactly as exit 0 did. What changed is that the crash
        # is no longer indistinguishable from a decision to release.
        #
        # Until this task the handler returned allow(), so a crashed hook and a
        # hook that had examined every run and found nothing to do produced the
        # identical result: exit 0, no output, session ends. The whole conveyor
        # would stop advancing and the only evidence would be that it had gone
        # quiet - the failure mode this file exists to prevent, in the file
        # itself.
        #
        # The traceback goes to stderr because that is what the harness shows.
        # It is not printed for its own sake: on a non-zero exit Claude Code
        # keeps the stderr and raises the `stop-hook-error` notification that
        # points at it (ctrl+o). On exit 0 it keeps nothing, which is why moving
        # off 0 is the fix and printing alone would not have been.
        traceback.print_exc(file=sys.stderr)
        return CRASH_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
