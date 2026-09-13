#!/usr/bin/env python3
"""External supervisor - a process with its own clock, outside every session.

`stop_gate.py` is a reactive GATE: it runs inside the session's lifecycle, on
the `Stop` event, and only when the model emits a stop. Three failures are
therefore invisible to it and unfixable inside it - a model stuck in a loop
never emits a stop, a killed session never emits anything, and a hook has no
clock between triggers. This script is the other half: it runs as its own
process, polls `run.db` and the task folders on a wall clock, classifies every
in-flight run from observed facts, and acts.

It complements the Stop hook and does not duplicate it. The hook decides what
the LIVE session does next; the supervisor decides whether a session is still
there at all.

FAIL-SAFE, WHICH IS THE OPPOSITE OF THE HOOKS
---------------------------------------------
Every hook in this directory fails OPEN: a bug in a gate allows the action
rather than bricking the agent (rules/orchestration.md). This process fails
SAFE, which is the opposite default, and a reader who knows the hooks will
expect the wrong one. The reason for the flip: a hook's failure costs one
denied tool call, while this process's failure would mutate pipeline state with
nobody watching. So any error here leaves the pipeline exactly as it was and
notifies. There is no code path that advances a stage, closes a run, moves a
task file, records an approval, pushes, or merges - see the forbidden-token test
in tools/tests/test_supervisor.py, which pins that by reading this file.

CLASSIFICATION (exactly one label per run, checked in this order)
-----------------------------------------------------------------
LOOPING  any of three readings of the same evidence, `continuations` growing
         while `stage` does not: climbing on consecutive polls (one quiet poll
         resets that count), reaching the pipeline's own continuation_ceiling,
         or having climbed at all within a stage that then became relaunchable -
         the slow loop. Why it takes three readings rather than one, with the
         measured trace: observe()'s else branch, which is the single copy of
         it; do not restate it here. Parked `blocked` and surfaced. NEVER
         relaunched: relaunching a looping session amplifies the exact failure
         this process exists to catch. Enforced three ways and by construction -
         classify() decides the label before EITHER relaunchable answer, LOOPING
         is absent from RELAUNCHABLE, and relaunch() refuses any label outside
         that set before it can spawn.
DEAD     a session THIS supervisor spawned has exited while its run still sits
         in the stage it was spawned for. Process liveness, not a clock.
         Note honestly what this cannot see: a session the supervisor did not
         spawn has no known owner pid, so it can never be classified DEAD and
         falls through to STALLED instead. The busy marker is no help here -
         `advance.py --busy` records the pid of the advance.py process itself,
         which has already exited by the time the marker is read, so the
         marker's pid is dead for every healthy run. Hence our own registry.
STALLED  in flight, nothing above applies, the STAGE has not changed for
         `stall_seconds`, and NOTHING moved within it - a stage frozen with
         continuations growth behind it is the slow loop above, not this.
         This is the one label with a clock in it, which is
         why it is one of four rather than the whole detector. The clock reads
         the supervisor's own record of when the stage changed, NOT the run
         row's `updated` column - see stage_age() for the defect that
         distinction fixes.
HEALTHY  anything else, including a run parked on a human by design (a task
         waiting for the CEO's commit approval is not stalled, it is correct).

Config: the `supervisor` block in .agentry/pipeline.json (see DEFAULTS).
Lane: resolved through state.py, the single place PIPELINE_LANE is read. The
supervisor operates on one lane and never crosses lanes.

HOW IT IS STARTED, AND THE ONE PLACE THAT FAILS OPEN INSTEAD OF SAFE
--------------------------------------------------------------------
The CEO never starts this by hand. A `SessionStart` hook runs
`--ensure-running`, which spawns the poll loop DETACHED and as a singleton per
lane. Detached is not a preference: a watcher that dies with the session cannot
report that session's death, and DEAD is half of what this process is for.

Note the asymmetry, because it is deliberate and a future reader will assume one
rule covers both. The supervisor's own internals fail SAFE (an error changes no
pipeline state). Its LAUNCHER fails OPEN, like every other hook in this tree: if
the supervisor cannot be started, `--ensure-running` says so once and exits 0,
because a session that refuses to start because its watchdog will not start is a
worse failure than having no watchdog.

CLI (the flags are for tests and the orchestrator; the hook is the product):
    python supervisor.py                     # poll forever (the daemon itself)
    python supervisor.py --ensure-running    # start it detached if not running
    python supervisor.py --stop              # stop it and release the lock
    python supervisor.py --once              # a single pass (cron / a test)
    python supervisor.py --status            # classify and print, act on nothing
    python supervisor.py --interval 120      # override the poll interval
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import mode
import state

# The four classifications. Exactly one is assigned per run.
HEALTHY = "HEALTHY"
STALLED = "STALLED"
LOOPING = "LOOPING"
DEAD = "DEAD"
CLASSIFICATIONS = (HEALTHY, STALLED, LOOPING, DEAD)

# The ONLY labels a relaunch may ever act on. LOOPING is absent on purpose and
# must stay absent: it is the one classification where another session is the
# wrong answer. relaunch() checks membership here before doing anything, so the
# guard cannot be bypassed by a caller that forgets it, and it is not a config
# key because no project setting should be able to turn it off.
RELAUNCHABLE = frozenset({STALLED, DEAD})

# The mark every spawned session carries, set in its environment by relaunch().
#
# WHY A MARK AND NOT A SENTENCE. What relaunch() starts is `claude -p`, which is
# a new MAIN session, so agent_gate.py's profiles - which bind subagents - never
# apply to it. With approvals at `auto` and workflow.mode `solo` the rest of the
# chain closes with no human in it: the spawned session drives the task,
# advance.py clears the commit checkpoint by itself at that level, and the local
# trunk write needs only a matching branch name, a run row and that flag. So the
# trunk could be written overnight by a process the CEO never watches.
#
# The prompt in RELAUNCH_PROMPT asks the session not to do any of it, and a
# request is not a control: the model is free to read it differently at three in
# the morning. pretool_gate.py refuses the three irreversible steps outright
# while this variable is set, which makes the property structural rather than
# advisory. Keep the name in step with pretool_gate.UNATTENDED_ENV - the two are
# pinned equal by a test, because a rename on one side alone would silently
# unmark every spawn.
UNATTENDED_ENV = "AGENTRY_UNATTENDED"

# Actions the supervisor can take. Both are counted against both caps.
ACTION_RELAUNCH = "relaunch"
ACTION_PARK = "park_blocked"

# Windows kernel32 constants for pid_alive(). STILL_ACTIVE is the exit code
# GetExitCodeProcess reports for a process that has not exited. The two error
# codes are how an OpenProcess failure is told apart: 87 means the pid does not
# exist, 5 means it exists and is not ours to ask about. See pid_alive().
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87

# Windows process-creation flags for spawn_detached(). DETACHED_PROCESS gives
# the child no console to inherit, so closing the session's terminal cannot take
# it down with it; CREATE_NEW_PROCESS_GROUP keeps a Ctrl+C in that terminal from
# reaching it. The POSIX equivalent is start_new_session=True.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200

# Singleton lock states, as owner() reports them.
FREE = "free"
HELD = "held"
CLAIMING = "claiming"

# How long a lock file that has been claimed but has no pid in it yet is
# respected. It covers exactly one window: a launcher has won the exclusive
# create and has not finished spawning. Anything older is a launcher that died
# mid-start, and a lock nobody can release is worse than one stolen too early.
CLAIM_GRACE = 60.0

# How long --stop waits for a signalled daemon to actually go away before it
# releases the lock regardless.
STOP_WAIT = 5.0

# The daemon rewrites its lock after every poll, so the lock carries a heartbeat
# as well as a pid. A lock whose heartbeat is older than this is not our daemon,
# whatever the pid probe says about the number in it.
#
# This exists because a pid alone cannot identify a process. pid_alive() is the
# one liveness probe (reused here on purpose rather than joined by a second one),
# and it reports "cannot tell" as ALIVE - so a pid recycled by an unrelated
# process reads as a live owner. Without the heartbeat that has two consequences,
# and the second is the bad one: no supervisor would ever start on this lane
# again, and --stop would signal whatever now owns that number. With it, a
# recycled pid goes stale and the lock is stolen and released instead.
#
# Ten minutes, which is ten default poll intervals. The window widens for a
# project that polls more slowly than every 200 seconds (see owner's callers).
HEARTBEAT_STALE = 600.0

# The same idea with a much shorter fuse, for --stop only. Signalling a pid is
# irreversible for whatever holds it, so the kill decision demands a heartbeat
# written within a couple of poll intervals rather than ten. See stop().
SIGNAL_STALE = 120.0

# The two NORMAL outcomes of --ensure-running in which nothing is started: this
# lane already has a supervisor, or the project turned it off. Both are correct
# behaviour, so they go to the log and not to the session's stdout, which is
# injected into the model's context. The strings are constants because main()
# decides whether to print by testing for them, and a reworded message must not
# quietly turn a silent path into a noisy one.
ALREADY = "supervisor already running"
DISABLED = "supervisor is disabled"

DEFAULTS = {
    "enabled": True,
    "poll_seconds": 60,
    # No state change for this long, with no other evidence, is a stall. Chosen
    # longer than a slow exit gate (advance.py's own GATE_TIMEOUT is 900) so a
    # legitimately long test run is not mistaken for a dead session.
    "stall_seconds": 1200,
    # How many CONSECUTIVE polls must show `continuations` climbing on an
    # unchanged stage before it counts as a loop. Two is the smallest number
    # that cannot be one slow turn. Consecutive, not cumulative - observe()'s
    # else branch explains why, and why this stays at 2 rather than being scaled
    # up towards continuation_ceiling.
    "loop_ticks": 2,
    "max_actions_per_task": 3,
    "max_actions_per_hour": 6,
    # Consecutive polls with nothing in flight after which the daemon exits and
    # releases its lock, rather than sitting on a machine forever with no work.
    # The next SessionStart starts it again, so stopping costs nothing. 0 means
    # never exit on idle.
    "idle_exit_polls": 30,
    # Argv, never a shell string: a shell here would be one string-interpolated
    # task id away from command injection. Placeholders: {task} {stage} {prompt}
    "spawn_cmd": ["claude", "-p", "{prompt}"],
}

RELAUNCH_PROMPT = (
    "Resume {task}. Its pipeline run sits at stage '{stage}' and the session "
    "that owned it ended without finishing ({why}). Read "
    ".agentry/tasks/active/{task}.md, absorb context per rules/pipeline.md, "
    "finish the stage work, then run: python .claude/tools/pipeline/advance.py "
    "--task {task}. Do not commit without the CEO's approval, and do not "
    "attempt to send work to the remote, merge a branch, or record any "
    "approval - those are the CEO's, always."
)


# --- paths (functions, not constants: tests repoint state.STATE_DIR) --------

def registry_path():
    return state.STATE_DIR / f"supervisor{state.LANE_SUFFIX}.json"


def log_path():
    return state.STATE_DIR / f"supervisor{state.LANE_SUFFIX}.log"


def run_log_dir():
    return state.STATE_DIR / f"supervisor-runs{state.LANE_SUFFIX}"


def lock_path():
    return state.STATE_DIR / f"supervisor{state.LANE_SUFFIX}.lock"


# --- config -----------------------------------------------------------------

def config() -> dict:
    """DEFAULTS overlaid with the `supervisor` block from pipeline.json, plus
    the pipeline's own continuation_ceiling. An unreadable config yields the
    defaults rather than raising."""
    cfg = dict(DEFAULTS)
    cfg["continuation_ceiling"] = 30
    try:
        pipeline = state.load_pipeline()
        cfg["continuation_ceiling"] = int(pipeline.get("continuation_ceiling", 30))
        block = pipeline.get("supervisor") or {}
        for key in DEFAULTS:
            if key in block:
                cfg[key] = block[key]
    except Exception:
        pass
    return cfg


# --- process liveness -------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """Whether a pid is still running. On Windows, asked through kernel32 rather
    than os.kill - for the access right, NOT because signal 0 is dangerous.

    MEASURED on this host (CPython 3.12.4, Windows 11), which is the evidence so
    the next reader does not have to derive it again:

        own child,  os.kill(pid, 0):  raised=None  exit_after=None  killed=False
        own child,  os.kill(pid, 9):  raised=None  exit_after=9     killed=True
        not-owned live pids 4 (System) and 1836 (services.exe):
            os.kill(pid, 0)  -> SystemError: <class 'OSError'> returned a result
                                with an exception set
            OpenProcess(QUERY_LIMITED_INFORMATION) -> fails, GetLastError 5
        pid that never existed (999999):
            OpenProcess -> fails, GetLastError 87

    So the folklore is half right, and the wrong half is the half that matters.
    os.kill on Windows does route to TerminateProcess - signal 9 above really
    killed the child, with exit code 9 - but signal 0, the one a liveness probe
    actually uses, is special-cased and does NOT kill. Do not repeat the claim
    that os.kill(pid, 0) kills the process it asks about. It does not.

    Two reasons to use kernel32 regardless. The first is measured, the second is
    about guarantees rather than behaviour:

    1. ACCESS. os.kill opens the target with PROCESS_ALL_ACCESS, which fails for
       a process the caller does not own - measured above, and messily: a
       SystemError, which is not even an OSError subclass, so an `except OSError`
       around the call does not catch it. Code that reads that failure as "no
       such process" calls a LIVE session dead, and for a supervisor dead means
       relaunch, so it would start a second writer on a tree that already has
       one.
    2. GUARANTEE. Signal 0 being harmless is a CPython implementation detail
       with no documented promise. Betting on the one special case inside an API
       whose general path terminates processes is a bad bet at any price, and
       here the price is four lines.

    Had os.kill been used and been wrong, the failure would have been silent and
    TOTAL rather than partial: this function probes every in-flight run on every
    poll, from a process with nobody watching it.

    That same access measurement is why an OpenProcess failure is not simply
    read as dead - the bug this docstring warns about was in this very function
    until the numbers above were taken, and it reported System and services.exe
    as dead. ERROR_INVALID_PARAMETER means the pid does not exist; anything
    else, ERROR_ACCESS_DENIED included, means we cannot tell, and cannot-tell
    reports alive. The fail-safe direction is always "do less": a missed
    relaunch costs one poll interval, a wrong one costs a duplicate session.

    A recycled pid reads as alive. Nothing here solves that; the window is one
    poll interval wide and the consequence is a relaunch not happening."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                err = ctypes.get_last_error()
                # 87: no such pid, genuinely dead. 5 (and anything else): the
                # process exists but is not ours to inspect, or the reason is
                # unknown - either way we cannot tell, so report alive.
                return err != _ERROR_INVALID_PARAMETER
            try:
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == _STILL_ACTIVE
            finally:
                k32.CloseHandle(handle)
        except Exception:
            # Cannot tell -> report alive, which suppresses a relaunch.
            return True
    # POSIX, where signal 0 is the documented liveness probe rather than an
    # implementation detail. PermissionError is the access case from reason 1
    # above and means alive, never dead.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


# --- registry ---------------------------------------------------------------

def empty_registry() -> dict:
    return {"tasks": {}, "actions": []}


def load_registry() -> dict:
    """The supervisor's own memory: per-task observations, the pids it spawned,
    and the action log the caps are counted from. A missing or corrupt file
    yields an empty registry - worst case the supervisor forgets and observes
    the loop again, which costs one poll interval."""
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_registry()
    if not isinstance(data, dict):
        return empty_registry()
    tasks = data.get("tasks")
    actions = data.get("actions")
    if isinstance(tasks, dict):
        # Per-ENTRY validation, not just the container: observe() and classify()
        # call .get() on an entry, so a string or a list in there raises and the
        # run is reported as a supervisor error instead of being classified. A
        # bad entry is dropped, which re-seeds that task's clock - the same cost
        # as a lost registry, and the alternative is not classifying it at all.
        tasks = {task: entry for task, entry in tasks.items() if isinstance(entry, dict)}
    else:
        tasks = {}
    return {
        "tasks": tasks,
        "actions": actions if isinstance(actions, list) else [],
    }


def save_registry(reg: dict) -> bool:
    """Persist the registry. Returns False on failure instead of raising: the
    registry is the supervisor's own bookkeeping and losing it must never
    interrupt a poll, let alone touch pipeline state."""
    try:
        state.STATE_DIR.mkdir(parents=True, exist_ok=True)
        reg["actions"] = reg.get("actions", [])[-500:]
        registry_path().write_text(json.dumps(reg, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def entry_for(reg: dict, task: str) -> dict:
    return reg["tasks"].setdefault(task, {})


# --- observation ------------------------------------------------------------

def parse_updated(updated: str) -> float | None:
    """The run row's `updated` stamp as an epoch seconds value, or None when it
    is missing or unparseable (in which case age is unknown, so no stall can be
    claimed from it)."""
    try:
        dt = datetime.strptime(str(updated), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return dt.timestamp()


def idle_seconds(run: dict, now_ts: float) -> float | None:
    """Seconds since the run row was last WRITTEN, or None when unknown.

    Read the name as "row-touch age", not "idle time". `state.set_fields` stamps
    `updated` on every write, so this is refreshed by bookkeeping that is not
    progress. It is kept because it is a valid LOWER BOUND on the stage's age
    (a stage change is itself a write, so `updated` is never earlier than the
    stage change), and stage_age() uses it as a conservative seed. It is NOT the
    stall clock. See stage_age()."""
    at = parse_updated(run.get("updated", ""))
    if at is None:
        return None
    return max(0.0, now_ts - at)


def stage_since_of(entry: dict, now_ts: float) -> float | None:
    """The entry's recorded stage-change time, or None when it cannot be used.

    The one place a value loaded from the registry is validated. Unusable means
    either not a number, or LATER THAN NOW - a future stamp would make
    `now - since` negative, which clamps to an age of zero on every poll and
    switches the stall clock off permanently. observe() clamps the seed it
    writes; this clamps everything read back, including a value some other
    version, a clock correction or a corrupt file put there."""
    raw = entry.get("stage_since")
    if raw is None:
        return None
    try:
        since = float(raw)
    except (TypeError, ValueError):
        return None
    if since != since or since > now_ts:  # NaN, or a stamp from the future
        return None
    return since


def stage_age(run: dict, entry: dict, now_ts: float) -> tuple[float | None, str]:
    """Seconds since the STAGE last changed, plus how that was established.

    MEASURED DEFECT THIS EXISTS TO FIX (task-0058, second round). The stall
    clock used to be `now - run.updated`. That column is a row-TOUCH time:
    `state.set_fields` (state.py) stamps `fields["updated"] = now()` on EVERY
    write, and `stop_gate.py` calls it with `continuations=...` on every single
    Stop-hook nag. `retries`, `stage_status` and `awaiting_human` writes do the
    same. So the Stop hook nagging about a task was what hid that task from the
    supervisor: each nag reset the only clock STALLED had. Observed live on
    task-0010 - roughly forty minutes at one stage, reported HEALTHY with
    "changed 44s ago".

    WHY THE NEW SIGNAL CANNOT BE RESET BY BOOKKEEPING: `stage_since` lives in
    the supervisor's OWN registry file (supervisor[.lane].json), and observe()
    is the only writer of it. It moves for exactly one reason - a poll observed
    a `stage` value different from the one recorded at the previous poll, which
    is the definition of progress through the pipeline. No writer of run.db can
    reach it: a continuations bump, a retry count, a status flip and an
    awaiting_human change all leave `stage` alone, so they all leave the clock
    alone. The signal is a stage TRANSITION rather than a timestamp anybody
    stamps.

    The returned source string is part of the fix, not decoration. The old
    HEALTHY reason asserted "stage 'X' changed Ns ago" while measuring a row
    touch, and a log line that lies during an incident is part of the defect. So
    each answer says which of the three it is:

    1. observed - we watched the stage change and know when.
    2. seeded - first sight of this task, so the transition was never observed;
       the row's last write is used as a lower bound. It UNDER-states the age
       (the stage may have been sat at for hours before the last write), which
       errs towards HEALTHY, which is the fail-safe direction: a missed relaunch
       costs one poll interval, a wrong one costs a duplicate session.
    3. unknown - no parseable timestamp at all, so no stall can be claimed.

    A FUTURE `stage_since` is discarded rather than clamped to zero, and this is
    the second defect of this round, found in review. `max(0.0, now - since)` on
    a future value returns 0 forever: the run reads HEALTHY for good, and the
    reason string claims "the stage change this supervisor observed" about a
    number nothing observed. It takes no corruption to get there - a laptop
    resuming with a clock correction, or an NTP step backwards, is enough, and
    the registry is JSON that anything could have written. So a stamp later than
    now is not evidence of anything: it falls through to the row's lower bound,
    it says out loud that it was discarded, and observe() re-seeds it."""
    since = stage_since_of(entry, now_ts)
    if since is not None:
        age = max(0.0, now_ts - since)
        return age, ("since the stage change this supervisor observed"
                     if entry.get("stage_since_observed")
                     else "at least, seeded from the row's last write when first seen")
    if entry.get("stage_since") is not None:
        # There WAS a recorded value and it was unusable. Never report this as
        # observed, whatever the entry's flag says: the flag describes the value
        # that has just been thrown away.
        idle = idle_seconds(run, now_ts)
        detail = ("the recorded stage-change time was unusable (in the future or not a "
                  "number) and was discarded")
        if idle is None:
            return None, f"age unknown: {detail}"
        return idle, f"at least, from the row's last write: {detail}"

    # No registry entry: classify() was called without an observe() before it.
    # Fall back to the lower bound rather than to nothing, so a single-shot
    # --status pass still reports an obviously ancient run.
    idle = idle_seconds(run, now_ts)
    if idle is None:
        return None, "age unknown"
    return idle, "at least, from the row's last write (no prior observation)"


def observe(entry: dict, run: dict, now_ts: float) -> dict:
    """Fold this poll's facts into the task's registry entry, in place.

    Two derived facts, and both are derived here rather than in classify() so
    that the evidence accumulates in exactly one place:

    `loop_ticks` - the number of consecutive polls in which `continuations`
    climbed while `stage` stayed put. Two things reset it, and both are
    load-bearing: a stage change, because that is real progress, and a poll that
    saw no climb, because the count claims adjacency and has to measure it (see
    the else branch).

    `stage_climbs` - the number of polls in which `continuations` climbed since
    the stage last changed, adjacent or not. Only a stage change resets it. It is
    the CUMULATIVE reading of the same evidence, and it exists for one decision:
    classify()'s stall branch consults it before calling a frozen stage STALLED,
    because STALLED is relaunchable and "something is turning over" must never be
    relaunched however slowly it turns. See classify().

    `stage_since` - when the stage last changed. This is the stall clock, and it
    is kept here, in the supervisor's own registry, precisely because nothing
    that writes run.db can touch it (see stage_age)."""
    stage = run.get("stage")
    cont = int(run.get("continuations") or 0)
    prev_stage = entry.get("stage")
    prev_cont = entry.get("continuations")

    if prev_stage is not None and prev_stage != stage:
        entry["loop_ticks"] = 0
        entry["stage_climbs"] = 0
        # Observed transition: the one event allowed to move the clock.
        entry["stage_since"] = now_ts
        entry["stage_since_observed"] = True
    elif prev_cont is not None and cont > int(prev_cont):
        entry["loop_ticks"] = int(entry.get("loop_ticks", 0)) + 1
        entry["stage_climbs"] = int(entry.get("stage_climbs", 0)) + 1
    else:
        # A poll that saw no climb BREAKS the run. This branch is the whole
        # difference between consecutive and cumulative, and it was missing:
        # climbs at poll 1 and poll 9 with eight quiet polls between them
        # accumulated to loop_ticks=2 and classify() then reported "2
        # consecutive polls", which nothing had observed - the same defect class
        # as the old stall clock's reason string.
        #
        # CONSECUTIVE with a threshold of 2 was chosen over cumulative with a
        # threshold near continuation_ceiling, deliberately, because the two
        # signals answer different questions and the ceiling branch below
        # already owns the cumulative one:
        #   - here: `continuations` climbing on adjacent polls is a session
        #     turning over without the stage moving, which is a live loop. Two
        #     adjacent polls cannot be one slow turn, and at a 60s interval this
        #     catches a repeating branch within about two minutes.
        #   - the `cont >= ceiling` branch: total continuations regardless of
        #     when they happened, which is the cumulative reading, already
        #     tuned to the pipeline's own budget.
        # What escapes this branch: a loop slow enough to climb on alternate
        # polls keeps loop_ticks oscillating 0/1, so it is never caught HERE.
        #
        # MEASURED (task-0058, fourth round) - the backstop this comment used to
        # name was the ceiling, and the ceiling does not get the chance. With the
        # defaults, a frozen stage and continuations climbing every other poll:
        #     poll 16 cont= 9 ticks=1 -> HEALTHY
        #     poll 20 cont=11 ticks=1 -> STALLED  action=relaunch
        # The ceiling needs 60 polls to reach 30; STALLED fires at poll 20,
        # because a frozen stage is exactly what a slow loop looks like, and
        # STALLED is relaunchable. So the slow loop got RELAUNCHED - the one
        # outcome this module exists to prevent.
        # The real backstop is `stage_climbs` above, consulted by classify()'s
        # stall branch: a frozen stage with any climb behind it is LOOPING and
        # parks. This branch therefore costs latency only (a slow loop is caught
        # at stall_seconds instead of at two polls), never an escape.
        # The risk refused is still the other one: cumulative-at-2 parked a
        # HEALTHY task for two Stop-hook nags anywhere inside one stage, and
        # LOOPING parks `blocked`, which costs a CEO intervention.
        entry["loop_ticks"] = 0

    if stage_since_of(entry, now_ts) is None:
        # First sight of this task, a registry that was lost, or a recorded value
        # that cannot be used (a future stamp from a clock correction, a corrupt
        # number). All three are the same situation: no observed transition, so
        # seed from the row's last write - a lower bound on the stage's real age -
        # and mark it unobserved so the reason string does not overclaim.
        if entry.get("stage_since") is not None:
            log(f"discarded an unusable stage_since ({entry.get('stage_since')!r}) for "
                f"{run.get('task')}: re-seeding the stall clock from the run row")
        seed = parse_updated(run.get("updated", ""))
        entry["stage_since"] = min(seed, now_ts) if seed is not None else None
        entry["stage_since_observed"] = False

    entry["stage"] = stage
    entry["continuations"] = cont
    entry["seen"] = now_ts
    return entry


# --- classification ---------------------------------------------------------

def classify(run: dict, entry: dict, cfg: dict, now_ts: float) -> tuple[str, str]:
    """Exactly one of the four labels, plus the fact it was derived from.

    Call observe() first: this reads `loop_ticks` off the entry rather than
    recomputing it, so the loop evidence is accumulated in one place."""
    stage = run.get("stage")
    status = run.get("stage_status")
    ceiling = int(cfg.get("continuation_ceiling", 30))
    cont = int(run.get("continuations") or 0)

    # Not in flight: a finished run, or one already parked for the CEO. Neither
    # is the supervisor's business, and a blocked run in particular must never
    # be poked again - it was surfaced to a human on purpose.
    if stage == "done":
        return HEALTHY, "run finished (stage 'done')"
    if status == state.ST_BLOCKED:
        return HEALTHY, "already parked blocked for the CEO"
    if run.get("awaiting_human"):
        return HEALTHY, f"parked on a human by design (awaiting_human={run['awaiting_human']})"

    # LOOPING is checked BEFORE anything relaunchable, so no loop can ever be
    # relabelled into something a relaunch would act on. This ordering is the
    # first of the two guards; relaunch()'s RELAUNCHABLE check is the second.
    ticks = int(entry.get("loop_ticks", 0))
    if ticks >= int(cfg.get("loop_ticks", 2)):
        return LOOPING, (f"continuations climbed on {ticks} consecutive polls while stage "
                         f"stayed '{stage}' (now {cont})")
    if cont >= ceiling:
        return LOOPING, (f"continuations {cont} reached the pipeline's continuation_ceiling "
                         f"({ceiling}) at stage '{stage}'")

    # Every remaining fact is gathered BEFORE any of them is answered, because
    # the order the answers used to be returned in was itself the defect.
    #
    # MEASURED DEFECT THIS SHAPE FIXES (task-0058, fifth round, found in
    # review). The pid branch used to return DEAD before `stage_climbs` was ever
    # read, so entry={loop_ticks:1, stage_climbs:5, pid:<dead>,
    # spawn_stage=='implement'} classified DEAD - which IS in RELAUNCHABLE. That
    # is the fourth path onto a loop, and it is reached by an ordinary sequence:
    # the supervisor relaunches a stall, the spawned session loops while
    # stop_gate.py bumps `continuations`, the session exits, and every later poll
    # reads DEAD and relaunches again to the per-task cap.
    #
    # Two rounds had certified "no fourth path" from the fact that relaunch()
    # has one call site and one RELAUNCHABLE check. That verified the structure
    # of the decision and not the ORDER of the classifier feeding it: a loop
    # relabelled DEAD needs no second call site to be relaunched.
    #
    # `age` is the clock, and the ONLY clock. It measures time since the stage
    # changed, not since the row was touched - `source` says which of the two
    # ways that was established, so the reason string can never overclaim.
    climbs = int(entry.get("stage_climbs", 0))
    pid = int(entry.get("pid") or 0)
    alive = pid_alive(pid) if pid else False
    dead = bool(pid) and not alive and stage == entry.get("spawn_stage")
    age, source = stage_age(run, entry, now_ts)
    stall = float(cfg.get("stall_seconds", 1200))
    stalled = age is not None and age >= stall

    # THE BOUNDARY between a loop and a plain stall, and the third guard on
    # LOOPING (RELAUNCHABLE and handle_run's park branch are the other two; all
    # three agree because this one answers with the LABEL rather than with an
    # action of its own). It sits above BOTH relaunchable answers, so whichever
    # evidence arrives first - a dead owner or the stall clock - any climb
    # within the current stage wins. Nothing moving at all is a plain stall,
    # which stays relaunchable: this must not become a switch that turns
    # relaunching off. Why `stage_climbs` is the distinction, and the measured
    # trace of getting it wrong: observe()'s else branch.
    if climbs and (dead or stalled):
        evidence = (f"its supervisor-spawned session pid {pid} exited" if dead
                    else f"it has not changed for {int(age)}s")
        return LOOPING, (f"stage '{stage}' would otherwise be relaunched ({evidence}), but "
                         f"continuations climbed on {climbs} polls within it (now {cont}): a "
                         f"slow loop, not a stall, so it is parked rather than relaunched")
    if alive:
        return HEALTHY, f"supervisor-spawned session pid {pid} is alive"
    if dead:
        return DEAD, (f"supervisor-spawned session pid {pid} exited with the run still at "
                      f"stage '{stage}' (spawned for that stage)")
    if stalled:
        return STALLED, (f"stage '{stage}' unchanged for {int(age)}s with no continuations "
                         f"growth within it ({source}; stall_seconds={int(stall)})")

    return HEALTHY, (f"stage '{stage}' unchanged for {int(age)}s ({source})"
                     if age is not None else f"stage '{stage}', age unknown")


# --- notification -----------------------------------------------------------

def log(text: str) -> None:
    """Append one line to the lane's supervisor log, and print nothing.

    The log file is the primary channel by design: the daemon is detached, so
    its stdout goes to a file nobody has open, and the session's stdout is read
    by nobody either. Never raises - losing a log line must not stop a poll."""
    try:
        state.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"{state.now()} {text}\n")
    except Exception:
        pass


def notify(text: str) -> None:
    """Surface something to the human: the log file AND stdout, because the CEO
    may be reading either. Never raises."""
    log(text)
    print(f"{state.now()} {text}", flush=True)


# --- actions ----------------------------------------------------------------

def caps_exceeded(reg: dict, task: str, cfg: dict, now_ts: float) -> str:
    """'' when one more action is within both caps, else the reason to STOP.

    Two caps, both hard: per task (lifetime) and global per wall-clock hour
    (rolling). Exceeding either stops the supervisor and notifies; it does not
    skip the action and carry on, because a supervisor that keeps polling after
    hitting its budget is a supervisor nobody looks at."""
    per_task = int(cfg.get("max_actions_per_task", 3))
    per_hour = int(cfg.get("max_actions_per_hour", 6))

    done_for_task = int(entry_for(reg, task).get("actions", 0))
    if done_for_task >= per_task:
        return (f"per-task action cap reached: {done_for_task}/{per_task} supervisor actions "
                f"on {task}. Stopping rather than acting again - this task needs a human.")

    recent = [a for a in reg.get("actions", [])
              if isinstance(a, dict) and (now_ts - float(a.get("at", 0))) < 3600]
    if len(recent) >= per_hour:
        return (f"global hourly action cap reached: {len(recent)}/{per_hour} supervisor actions "
                f"in the last hour. Stopping rather than acting again.")
    return ""


def record_action(reg: dict, task: str, kind: str, now_ts: float) -> None:
    entry = entry_for(reg, task)
    entry["actions"] = int(entry.get("actions", 0)) + 1
    reg.setdefault("actions", []).append({"at": now_ts, "task": task, "kind": kind})


def park_blocked(task: str, reason: str) -> bool:
    """Park a run `blocked` so a human sees it. The supervisor's ONLY write to
    run.db, made through state.py (the pipeline's own accessor) and never with
    raw SQL. It sets stage_status and nothing else: no stage change, no
    approval flag, no task-file move. Returns False on any failure, having
    changed nothing."""
    try:
        conn = state.connect()
        try:
            if state.get_run(conn, task) is None:
                return False
            state.set_fields(conn, task, stage_status=state.ST_BLOCKED)
        finally:
            conn.close()
    except Exception:
        return False
    notify(f"{task} parked BLOCKED by the supervisor: {reason}")
    return True


def spawn_argv(cfg: dict, task: str, stage: str, why: str) -> list[str]:
    """The exact argv for a relaunch. A headless `claude -p` run, because an
    external process cannot inject a turn into a running interactive session -
    the only lever that reaches a NEW session is starting one."""
    prompt = RELAUNCH_PROMPT.format(task=task, stage=stage, why=why)
    raw = cfg.get("spawn_cmd") or DEFAULTS["spawn_cmd"]
    if isinstance(raw, str):
        raw = [raw]
    return [str(part).format(task=task, stage=stage, prompt=prompt) for part in raw]


def relaunch(classification: str, task: str, stage: str, why: str,
             cfg: dict) -> tuple[bool, list, int]:
    """Spawn a headless session to continue this run.

    Returns (spawned, argv, pid) - pid is 0 when nothing was spawned.

    THE GUARD: a classification outside RELAUNCHABLE returns immediately,
    before the argv is even built. LOOPING is not in that set, so a looping run
    cannot be relaunched through this function even if a caller asks for it.
    The refusal lives here rather than at the call site on purpose - one
    function can be audited, every future caller cannot.

    THE SPAWN IS MARKED: UNATTENDED_ENV is set in the child's environment, and
    pretool_gate.py refuses the three irreversible steps for as long as it is
    set. What this function starts is a new MAIN session, which no agent profile
    governs, so without the mark the prompt below was the only thing standing
    between an unwatched session and a written trunk. See UNATTENDED_ENV."""
    if classification not in RELAUNCHABLE:
        notify(f"refused to relaunch {task}: classification {classification} is not relaunchable "
               f"(relaunchable: {', '.join(sorted(RELAUNCHABLE))})")
        return False, [], 0

    argv = spawn_argv(cfg, task, stage, why)
    try:
        run_log_dir().mkdir(parents=True, exist_ok=True)
        out = run_log_dir() / f"{task}-{int(time.time())}.log"
    except Exception as exc:
        notify(f"could not prepare a spawn log for {task}: {exc!r} - nothing was spawned")
        return False, argv, 0

    try:
        with out.open("w", encoding="utf-8") as handle:
            proc = subprocess.Popen(argv, cwd=str(state.ROOT), stdin=subprocess.DEVNULL,
                                    stdout=handle, stderr=subprocess.STDOUT, shell=False,
                                    env={**os.environ, UNATTENDED_ENV: "1"})
    except Exception as exc:
        notify(f"spawn FAILED for {task} ({classification}): {exc!r}. Command was: {argv}. "
               f"Pipeline state is untouched.")
        return False, argv, 0

    notify(f"relaunched {task} ({classification}: {why}) as pid {proc.pid}. "
           f"Command: {argv}. Output: {out}")
    return True, argv, proc.pid


# --- the poll ---------------------------------------------------------------

def handle_run(run: dict, reg: dict, cfg: dict, now_ts: float, act: bool) -> dict:
    """Classify one run and, when acting, take at most one action on it.

    Returns an event dict. Raises nothing the caller has to fear: a failure in
    here is caught in poll_once() per run, so one unclassifiable row cannot
    stop the supervisor from seeing the others."""
    task = run["task"]
    entry = entry_for(reg, task)
    observe(entry, run, now_ts)
    label, why = classify(run, entry, cfg, now_ts)

    event = {"task": task, "stage": run.get("stage"), "classification": label,
             "why": why, "action": "", "stop": ""}

    # Notify on a CHANGE of label only, so a stalled run does not write a log
    # line every poll for the rest of the night.
    if entry.get("last_label") != label:
        if label != HEALTHY:
            notify(f"{task} is {label}: {why}")
        entry["last_label"] = label

    if label == HEALTHY:
        return event

    if not act:
        event["action"] = f"would {'park blocked' if label == LOOPING else 'relaunch'}"
        return event

    stop = caps_exceeded(reg, task, cfg, now_ts)
    if stop:
        event["stop"] = stop
        return event

    if label == LOOPING:
        # Never a relaunch. Park it and let a human look.
        if park_blocked(task, why):
            record_action(reg, task, ACTION_PARK, now_ts)
            event["action"] = ACTION_PARK
        else:
            event["action"] = "park failed (pipeline state untouched)"
        return event

    # STALLED / DEAD. The task file must still sit in active/: a run whose file
    # has left it is not ours to restart.
    where = state.task_dir(task)
    if where != "active":
        event["action"] = f"skipped (task file is in {where or 'no'} folder, not active/)"
        return event

    spawned, argv, pid = relaunch(label, task, str(run.get("stage")), why, cfg)
    if spawned:
        record_action(reg, task, ACTION_RELAUNCH, now_ts)
        entry["pid"] = pid
        entry["spawn_stage"] = run.get("stage")
        entry["spawned"] = now_ts
        event["action"] = ACTION_RELAUNCH
        event["argv"] = argv
        event["pid"] = pid
    else:
        event["action"] = "relaunch refused or failed (pipeline state untouched)"
    return event


def poll_once(cfg: dict, act: bool = True) -> dict:
    """One pass over every run in this lane's run.db.

    Returns {"events": [...], "stop": "<reason or ''>"}. The `stop` reason is
    set when a cap is hit or the pass itself failed; main() then stops the
    supervisor and notifies rather than polling on."""
    reg = load_registry()
    events: list[dict] = []
    stop = ""
    try:
        conn = state.connect()
        try:
            runs = state.all_runs(conn)
        finally:
            conn.close()
    except Exception as exc:
        # Cannot read the state: nothing is classified and nothing is touched.
        return {"events": [], "stop": f"could not read the run store: {exc!r}"}

    for run in runs:
        if run.get("stage") == "done":
            # A finished run can never change again, and walking it cost three
            # things at once, all measured: 2466 bytes of daemon log per poll
            # (3.4MB a day, about 80% of it these lines), a registry entry per
            # done task that nothing ever reclaimed (13 of 16 entries), and the
            # work itself. classify() keeps its own `done` branch for direct
            # callers; this is where the daemon stops paying for them.
            continue
        try:
            event = handle_run(run, reg, cfg, time.time(), act)
        except Exception as exc:
            # Fail SAFE: this run is reported and skipped. No write happened on
            # this path - park_blocked and relaunch are the only writers and
            # both are past this point.
            event = {"task": run.get("task"), "stage": run.get("stage"),
                     "classification": "", "why": f"supervisor error: {exc!r}",
                     "action": "none (pipeline state untouched)", "stop": ""}
            notify(f"{run.get('task')}: supervisor error while classifying: {exc!r}. "
                   f"Pipeline state untouched.")
        events.append(event)
        if event.get("stop"):
            stop = event["stop"]
            break

    prune_registry(reg, runs)
    if not save_registry(reg):
        # LOUD, and every time. The registry holds the stall clock, so a
        # supervisor that cannot write it silently degrades to the row-touch
        # clock this round replaced: `stage_since` is re-seeded from the run row
        # on every poll, which under-reports the age without bound and is exactly
        # how task-0010 stayed HEALTHY for forty minutes. A watchdog whose own
        # state will not persist has to say so, not carry on looking healthy.
        notify(f"COULD NOT WRITE the supervisor registry ({registry_path()}). The stall "
               f"clock cannot persist, so every poll re-seeds it from the run row and a "
               f"stalled run may read HEALTHY indefinitely. Fix the path or the permissions; "
               f"detection is degraded until then.")
    return {"events": events, "stop": stop}


def prune_registry(reg: dict, runs: list[dict]) -> int:
    """Drop entries for tasks that are done or no longer in the run store.

    Keyed on done-or-absent and NEVER on age or size. An in-flight run's entry
    holds its `stage_since`, and dropping it re-seeds the clock from the run
    row - which is this task's own defect wearing a different coat. Returns the
    number of entries removed."""
    live = {r.get("task") for r in runs if r.get("stage") != "done"}
    tasks = reg.get("tasks")
    if not isinstance(tasks, dict):
        return 0
    stale = [task for task in tasks if task not in live]
    for task in stale:
        del tasks[task]
    return len(stale)


def print_pass(result: dict) -> None:
    for event in result["events"]:
        print(json.dumps(event), flush=True)


# --- the singleton lock -----------------------------------------------------
#
# One supervisor per lane, or every session that starts adds another watcher and
# within a week several of them are relaunching work for each other. The lock is
# a small JSON file holding the daemon's pid, and the arbitration between two
# racing launchers is an O_EXCL create of that file - not a "is it there?" check,
# which both would pass.
#
# Ownership is deliberately NOT a bare pid. A pid is a recycled number: a dead
# daemon's 9184 can be some unrelated process a day later, and reading that as a
# live owner would mean no supervisor ever starts again on this machine. Liveness
# is asked of the operating system through pid_alive(), which is the SAME probe
# the DEAD classification uses - one liveness answer in this file, not two that
# can disagree.


def read_lock() -> dict | None:
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def owner(now_ts: float, stale_after: float = HEARTBEAT_STALE) -> tuple[str, int, str]:
    """Who holds this lane's lock: (FREE | HELD | CLAIMING, pid, reason).

    HELD     a recorded pid that the operating system says is running AND a
             heartbeat younger than `stale_after`. Both are required: the pid
             says a process exists, the heartbeat says it is OUR process rather
             than whatever recycled that number (see HEARTBEAT_STALE).
    CLAIMING a lock claimed within CLAIM_GRACE whose pid is not written yet, so
             a launcher is mid-spawn right now. Treated as busy: backing off
             costs one session with no watcher, spawning costs two watchers.
    FREE     no lock, a recorded pid that is gone, a live pid that stopped
             writing its heartbeat, or a claim older than the grace period (a
             launcher that died mid-start)."""
    if not lock_path().exists():
        return FREE, 0, "no lock file"
    data = read_lock() or {}
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    try:
        at = float(data.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    if not at:
        # No usable timestamp in the file, which is exactly what a claim looks
        # like in the instant between the exclusive create and the write into
        # it: the file exists and is still empty. Its mtime is the only claim
        # time available, and reading a missing one as 0 made a just-won claim
        # look ancient - so a racing launcher stole it and two supervisors
        # started. Caught by the concurrency test, not by reasoning.
        try:
            at = lock_path().stat().st_mtime
        except OSError:
            at = 0.0
    age = now_ts - at

    if pid:
        if not pid_alive(pid):
            return FREE, pid, f"the recorded owner pid {pid} is gone"
        if age > stale_after:
            # A live pid that has not refreshed its heartbeat. Either the number
            # was recycled by an unrelated process, or a daemon is wedged and no
            # longer polling. Both mean nobody is watching this lane, and
            # neither is a process this tool may signal.
            return FREE, 0, (f"the lock names pid {pid}, which is alive but has not been "
                             f"refreshed for {int(age)}s: that is not this lane's supervisor")
        return HELD, pid, f"pid {pid} is alive"
    if age < CLAIM_GRACE:
        return CLAIMING, 0, "another session claimed the lock and is starting one now"
    return FREE, 0, "a stale claim with no live owner"


def heartbeat_window(cfg: dict) -> float:
    """How long a lock may go unrefreshed before it is nobody's. Never shorter
    than a few poll intervals, or a slow-polling project would keep stealing its
    own supervisor's lock."""
    return max(HEARTBEAT_STALE, poll_seconds(cfg) * 3)


def signal_window(cfg: dict) -> float:
    """How recently the lock must have been refreshed before --stop will signal
    the pid in it. Strict on purpose - see stop()."""
    return max(SIGNAL_STALE, poll_seconds(cfg) * 2)


def poll_seconds(cfg: dict) -> float:
    try:
        return float(cfg.get("poll_seconds", 60))
    except (TypeError, ValueError):
        return 60.0


def write_lock(pid: int | None, now_ts: float) -> bool:
    """Overwrite the lock with a pid (or None for a claim in progress)."""
    try:
        state.STATE_DIR.mkdir(parents=True, exist_ok=True)
        lock_path().write_text(json.dumps(
            {"pid": pid, "at": now_ts, "lane": state.LANE or "default"}), encoding="utf-8")
        return True
    except OSError:
        return False


def take_stale_lock(expected: dict | None) -> bool:
    """Take a stale lock away and report whether we really got THAT lock.

    Two mechanisms, because the first one alone was not enough and the test
    proved it. Unlinking is not arbitration at all: two launchers can both
    unlink successfully, and the loser's unlink removes the winner's fresh lock,
    so both create one and both spawn. Renaming is better - a rename of one path
    succeeds exactly once, and the destination carries this pid so two stealers
    cannot collide there either - but rename is path-based, and the verdict that
    the lock was stale was made BEFORE it. MEASURED: with a launcher placed
    inside the window, both won (A=True B=True), because B took the stale file,
    wrote its own claim, and A's rename then succeeded on B's brand new lock.

    So `expected` is the content the FREE verdict was about, and what the rename
    actually took is compared against it. Anything else means we moved a live
    lock: hand it straight back and lose the race. That is compare-and-swap by
    content, with the rename providing the atomic part."""
    victim = lock_path()
    taken = victim.parent / f"{victim.name}.stale-{os.getpid()}-{int(time.time() * 1000)}"
    try:
        os.rename(str(victim), str(taken))
    except OSError:
        return False

    try:
        got = json.loads(taken.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = None
    if got != expected:
        try:
            # Put it back. It is somebody's current lock, not the stale one.
            os.rename(str(taken), str(victim))
        except OSError:
            # A lock already exists at the path again, so somebody holds one
            # either way. Drop the copy we took rather than leaving litter.
            try:
                taken.unlink()
            except OSError:
                pass
        return False

    try:
        taken.unlink()
    except OSError:
        # A harmless `.stale-*` file is left rather than rolling the steal back:
        # the lock itself is gone, which is the part that matters.
        pass
    return True


def claim_lock(now_ts: float) -> bool:
    """Win the right to spawn, or return False.

    O_CREAT|O_EXCL is the whole mechanism: of two launchers reaching this line
    together, exactly one creates the file. One retry follows a FREE verdict from
    owner(), which is the dead-owner and stale-claim case - the leftover file is
    taken away and the exclusive create is attempted once more. Two attempts,
    never a loop: a launcher that keeps trying is a launcher that never returns
    to the session it is supposed to be letting start.

    The retry used to unlink the stale file, and that was a check-then-act hole
    found in review: a dead owner's lock is the NORMAL state after a crash, and
    two launchers inside the unlink window both saw FREE, both unlinked, and both
    created a lock - so both spawned. take_stale_lock() closes it by renaming the
    file away instead, which only one process can do."""
    for attempt in (1, 2):
        try:
            state.STATE_DIR.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path()), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Read the content the verdict is about BEFORE asking for the
            # verdict, so the steal can check that it took the same file.
            stale = read_lock()
            kind, _, _ = owner(now_ts)
            if attempt == 2 or kind != FREE or not take_stale_lock(stale):
                return False
            continue
        except OSError:
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": None, "at": now_ts, "lane": state.LANE or "default"}, fh)
        except OSError:
            return False
        return True
    return False


def release_lock_if_ours() -> bool:
    """Drop the lock when it names THIS process. Called as the daemon exits, so
    a clean shutdown does not leave the next session stealing a stale lock. A
    lock naming somebody else is left strictly alone."""
    data = read_lock() or {}
    try:
        if int(data.get("pid") or 0) != os.getpid():
            return False
        lock_path().unlink()
        return True
    except (OSError, TypeError, ValueError):
        return False


# --- detached start / stop --------------------------------------------------

def daemon_argv() -> list[str]:
    """The poll loop, as a command. No flags: the daemon's defaults come from
    pipeline.json, and the lane comes from the environment it inherits from the
    session that started it (PIPELINE_LANE, read only by state.py)."""
    return [sys.executable, str(Path(__file__).resolve())]


def spawn_detached(argv: list[str], stdout) -> int:
    """Start argv so that it OUTLIVES this process, and return its pid.

    The reason this cannot be an ordinary child: a watcher that dies with the
    session cannot report that session's death, and DEAD is half of what this
    supervisor is for. A session-bound watcher would only ever catch a hung
    session, never a killed one."""
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": stdout, "stderr": subprocess.STDOUT,
              "cwd": str(state.ROOT), "close_fds": True, "shell": False}
    if os.name == "nt":
        kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs).pid


def ensure_running() -> tuple[bool, str]:
    """The SessionStart entry point: start a detached daemon unless this lane has
    one. Returns (started, message) and NEVER raises.

    This is the one FAIL-OPEN path in this file (the rest fails safe). Every
    failure here is reported and swallowed, because the session must start even
    when its watchdog will not."""
    try:
        cfg = config()
        if not cfg.get("enabled", True):
            return False, f"{DISABLED} in pipeline.json (supervisor.enabled=false)"
        lane = state.LANE or "default"
        now_ts = time.time()

        kind, _pid, why = owner(now_ts, heartbeat_window(cfg))
        if kind == HELD:
            return False, f"{ALREADY} on lane '{lane}' ({why})"
        if kind == CLAIMING:
            return False, f"supervisor not started on lane '{lane}': {why}"
        if not claim_lock(now_ts):
            return False, (f"supervisor not started on lane '{lane}': another session took "
                           f"the lock first")

        argv = daemon_argv()
        try:
            run_log_dir().mkdir(parents=True, exist_ok=True)
            handle = (run_log_dir() / f"daemon-{int(now_ts)}.log").open("w", encoding="utf-8")
        except OSError as exc:
            release_lock(f"could not open a daemon log: {exc!r}")
            return False, f"supervisor not started: could not open a daemon log ({exc!r})"
        try:
            with handle:
                child = spawn_detached(argv, handle)
        except Exception as exc:
            release_lock(f"spawn failed: {exc!r}")
            return False, f"supervisor not started: {exc!r}. Command was: {argv}"

        write_lock(child, now_ts)
        return True, f"supervisor started detached on lane '{lane}' (pid {child})"
    except Exception as exc:
        return False, f"supervisor not started: {exc!r}"


def release_lock(reason: str) -> None:
    """Give up a claim this process made but could not use."""
    log(f"releasing the lock without a daemon: {reason}")
    try:
        lock_path().unlink()
    except OSError:
        pass


def stop() -> tuple[bool, str]:
    """Stop this lane's supervisor and release the lock, so the next session
    starts a fresh one. The documented command is `--stop`.

    It signals ONLY a pid whose heartbeat was written in the last couple of poll
    intervals, which is a tighter window than the one owner() uses to decide
    whether to START a supervisor, and deliberately so. The two decisions carry
    different risks:

      start: a window too short spawns a second daemon over a live one (a
             suspended laptop stops the heartbeat without stopping the daemon),
             so it is tolerant - HEARTBEAT_STALE, ten minutes by default.
      kill:  a window too long signals whatever now owns a recycled pid, which
             is somebody else's process, so it is strict - two poll intervals.

    A lock is only refreshed by the process that is polling, so "written two
    minutes ago" is evidence about IDENTITY and not merely liveness. It is the
    strongest identity this design has, and the pid alone proves nothing.

    CORRECTING AN EARLIER CLAIM in this file's own history: narrowing the window
    does NOT need an OS lock handle or a process start time. The width is a
    consequence of WHERE the heartbeat is written - poll_loop() writes it once
    per pass, so it can legitimately be one poll interval plus a pass old, and
    the window has to cover that. A heartbeat on its own short timer, decoupled
    from the poll, would narrow this to seconds with no new probe at all.
    The window stays two intervals regardless, because that is the size the
    CURRENT heartbeat needs, and under-sizing it only makes --stop decline to
    signal - the fail-safe direction, and recoverable by hand."""
    lane = state.LANE or "default"
    cfg = config()
    kind, pid, why = owner(time.time(), signal_window(cfg))
    if kind != HELD:
        released = lock_path().exists()
        try:
            lock_path().unlink()
        except OSError:
            released = False
        tail = "; a stale lock was released" if released else ""
        return False, f"no supervisor is running on lane '{lane}' ({why}){tail}"

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        return False, f"could not stop pid {pid} on lane '{lane}': {exc!r}. The lock is untouched."

    deadline = time.time() + STOP_WAIT
    while pid_alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    gone = not pid_alive(pid)
    try:
        lock_path().unlink()
    except OSError:
        pass
    log(f"stopped the supervisor on lane '{lane}' (pid {pid}, exited={gone})")
    return True, (f"stopped the supervisor on lane '{lane}' (pid {pid}), lock released"
                  + ("" if gone else f". Note: pid {pid} had not exited after {STOP_WAIT}s"))


def main() -> int:
    parser = argparse.ArgumentParser(description="External pipeline supervisor")
    parser.add_argument("--once", action="store_true", help="a single pass, then exit")
    parser.add_argument("--status", action="store_true",
                        help="classify and print, take no action (implies --once)")
    parser.add_argument("--interval", type=float, help="override poll_seconds")
    parser.add_argument("--ensure-running", action="store_true",
                        help="start the daemon detached unless this lane already has one "
                             "(the SessionStart hook; always exits 0)")
    parser.add_argument("--stop", action="store_true",
                        help="stop this lane's daemon and release its singleton lock")
    args = parser.parse_args()

    if args.ensure_running:
        # FAIL OPEN, unlike everything else in this file: the session starts
        # whatever happened here, and it says so exactly once. Silence on the
        # happy path is deliberate - stdout is injected into the session's
        # context, and the log file is where the detail belongs.
        started, message = ensure_running()
        log(message)
        # One line, and only when something actually went wrong. "Already
        # running" and "disabled" are the normal not-started outcomes: printing
        # them spends session context on a non-event every single start.
        if not started and not message.startswith((ALREADY, DISABLED)):
            print(f"supervisor: {message}", flush=True)
        return 0

    if args.stop:
        ok, message = stop()
        notify(message)
        return 0 if ok else 1

    cfg = config()
    if args.interval:
        cfg["poll_seconds"] = args.interval
    act = not args.status

    if not cfg.get("enabled", True):
        notify("supervisor is disabled in pipeline.json (supervisor.enabled=false)")
        return 0
    if act and not mode.conveyor_runs():
        notify(f"session mode is '{mode.read()}' - no conveyor runs, so there is nothing to "
               f"supervise. Use --status to look anyway.")
        return 0

    lane = state.LANE or "default"
    # Only the long-running form is a singleton. A single pass (--once,
    # --status) is a look, and a look must never be refused because a daemon
    # happens to be watching the same lane.
    daemon = not (args.once or args.status)
    if daemon:
        kind, held_pid, why = owner(time.time(), heartbeat_window(cfg))
        if kind == HELD and held_pid != os.getpid():
            notify(f"a supervisor already holds lane '{lane}' ({why}). Exiting rather than "
                   f"watching one conveyor twice. Stop it with --stop to replace it.")
            return 4
        # FREE, or the pid-less claim our own launcher made on our behalf: take
        # it and record OUR pid, which is what makes --stop and the next
        # --ensure-running able to find us.
        # The one window this does not close: a hand-started daemon landing
        # inside another launcher's CLAIM_GRACE adopts a claim meant for a
        # different process, and two daemons can result. It takes a manual start
        # timed inside a session start; --stop and a restart clears it.
        write_lock(os.getpid(), time.time())

    notify(f"supervisor started on lane '{lane}' (pid {os.getpid()}, "
           f"poll {cfg['poll_seconds']}s, stall {cfg['stall_seconds']}s, "
           f"caps {cfg['max_actions_per_task']}/task and {cfg['max_actions_per_hour']}/hour, "
           f"acting={act})")

    try:
        return poll_loop(cfg, args, act, daemon)
    finally:
        if daemon:
            release_lock_if_ours()


def poll_loop(cfg: dict, args, act: bool, daemon: bool = False) -> int:
    """Poll until told otherwise. Exit codes: 0 normal, 2 the pass failed, 3 a
    cap was reached."""
    idle_limit = int(cfg.get("idle_exit_polls", 0) or 0)
    idle_polls = 0
    while True:
        if daemon:
            # Before anything else: do we still hold this lane? Checked every
            # poll, not just at startup, so "one supervisor per lane" is a
            # standing property rather than a start-up test. Two cases:
            #   - the lock is gone: --stop released it (or something did), so
            #     this daemon is no longer wanted and leaves quietly. This is
            #     what makes --stop work even if its signal does not land.
            #   - the lock names another LIVE pid: a launcher decided our lock
            #     had gone stale (a long suspend does that) and started a
            #     replacement. The newcomer keeps the lane; we exit rather than
            #     both of us relaunching the same run.
            held = read_lock()
            if held is None:
                notify("the lock for this lane is gone, so this supervisor is no longer "
                       "the one watching it. Exiting.")
                return 0
            other = int(held.get("pid") or 0)
            if other and other != os.getpid() and pid_alive(other):
                notify(f"lane {state.LANE or 'default'!r} is now held by pid {other}. "
                       f"Exiting rather than watching one conveyor twice.")
                return 4
            # And is there still a conveyor to watch? Re-read every poll for
            # the same reason the lock is: the daemon is DETACHED and outlives
            # the session that started it, so a mode read once at startup goes
            # stale the moment the lane is switched to `talk` or `plan`. Until
            # this was here, a daemon started in `build` kept relaunching build
            # work for a lane that no longer runs any.
            if act and not mode.conveyor_runs():
                notify(f"session mode is now '{mode.read()}' - this lane runs no conveyor, "
                       f"so there is nothing to supervise. Exiting and releasing the lock; "
                       f"the next session start brings a supervisor back.")
                return 0
            # The heartbeat, written before each pass: it is what tells the next
            # launcher that the pid in the lock is still this daemon and not
            # whatever recycled that number. See HEARTBEAT_STALE.
            write_lock(os.getpid(), time.time())
        try:
            result = poll_once(cfg, act=act)
        except Exception as exc:
            # The last line of fail-safe defence: stop, say so, change nothing.
            notify(f"supervisor pass FAILED: {exc!r}. Stopping. Pipeline state is untouched.")
            return 2
        print_pass(result)
        if result["stop"]:
            notify(f"STOPPING: {result['stop']}")
            return 3
        if args.once or args.status:
            return 0

        # Stop on its own rather than sit on the machine forever with nothing to
        # watch, and say so when it does: the next SessionStart starts a fresh
        # one, so exiting costs nothing and an unattended process nobody
        # remembers starting costs confusion later.
        idle_polls = 0 if result["events"] else idle_polls + 1
        if idle_limit and idle_polls >= idle_limit:
            notify(f"nothing in flight for {idle_polls} consecutive polls "
                   f"(idle_exit_polls={idle_limit}). Exiting and releasing the lock - the "
                   f"next session start will start a fresh supervisor.")
            return 0

        try:
            time.sleep(float(cfg["poll_seconds"]))
        except KeyboardInterrupt:
            notify("supervisor stopped by the operator")
            return 0


if __name__ == "__main__":
    sys.exit(main())
