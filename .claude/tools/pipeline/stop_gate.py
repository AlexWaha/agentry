#!/usr/bin/env python3
"""Stop-hook brain - the engine that keeps the agent from idling mid-pipeline.

Wired to Claude Code's `Stop` event. On every attempt to stop it inspects the run
state and either:
  - blocks the stop and feeds back a concrete next instruction (continue an
    in-flight task, or start the next ready backlog task), or
  - allows the stop when every task is parked at a human checkpoint, blocked, or
    done and no ready backlog remains.

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

Fail-open: any error allows the stop (never trap the user in a loop).

Hook output: print {"decision":"block","reason":...} to block; print nothing to
allow.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time

import approvals
import mode
import state

_SKIP_DIRS = {".git", "node_modules", "vendor", ".claude", "storage",
              "dist", "build", "data", "logs"}

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

BACKLOG_DIR = state.BACKLOG_DIR
ACTIVE_DIR = state.ACTIVE_DIR
DONE_DIR = state.DONE_DIR


def block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def allow() -> int:
    # No output -> Claude Code stops normally.
    return 0


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
    """Queued task files: [{id, deps}]. Empty on any error.

    The queue is a folder, not a field. A file in backlog/ is waiting to be
    picked; there is no `status:` to read, and therefore nothing that can
    disagree with where the file actually sits.

    Two frontmatter fields do take a task OUT of the ready set without making it
    done: `superseded_by:` (the work was folded into another task) and a
    non-empty `blocked_on:` (waiting on a human or an outside party). Both mean
    skip, never finish."""
    out: list[dict] = []
    if not BACKLOG_DIR.is_dir():
        return out
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
        out.append({"id": m.group(1), "deps": deps})
    return out


def busy_marker_fresh(task: str) -> bool:
    """True when tools/pipeline wrote a busy-marker for this task (a long gate is
    running, or the orchestrator dispatched a subagent for the stage) and it has
    not gone stale. Lets the Stop hook stay quiet instead of nagging while real
    work is in flight. Fail-open: any error -> not fresh (nag as before)."""
    try:
        p = state.busy_marker_path(task)
        if not p.is_file():
            return False
        d = json.loads(p.read_text(encoding="utf-8"))
        started = float(d.get("started", 0))
        timeout = float(d.get("timeout", 900))
        return (time.time() - started) < timeout
    except Exception:
        return False


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
        return any(busy_marker_fresh(p.name[len("gate-"):-len(".json")])
                   for p in state.STATE_DIR.glob("gate-*.json"))
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
    except Exception:
        return []


def memory_debt() -> list:
    """Completed tasks whose memory review was not stamped (tools/memory/
    update.py). Lazy import + fail-open like handoff_debt."""
    try:
        p = str(state.ROOT / ".claude" / "tools" / "memory")
        if p not in sys.path:
            sys.path.insert(0, p)
        import update as memory_update
        return memory_update.unstamped_done_tasks()
    except Exception:
        return []


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
    safe to charge a second branch - the `ready` stage's budget is its own."""
    cont = r["continuations"] + 1
    if cont > ceiling:
        state.set_fields(conn, r["task"], stage_status=state.ST_BLOCKED)
        return False
    state.set_fields(conn, r["task"], continuations=cont)
    return True


def bounded_block(key: str, subject: str, reason: str) -> int:
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
            f"situation, the options - pay it, waive it (handoff.py --waive / update.py "
            f"--stamp --none), or change the baseline in pipeline.json - and your "
            f"recommendation. This hook will not raise it again, so if you drop it now "
            f"nothing else will catch it. The instruction that is not working: {reason}")
    return block(reason)


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
    except Exception:
        return None


def _git_repos() -> list:
    """Every git repo in the workspace (root + up to 2 levels down), skipping
    heavy/irrelevant dirs. Works for single-repo and multi-repo workspaces
    without hardcoding paths, so the same gate ships to any project."""
    repos = []
    try:
        roots = [state.ROOT]
        for d1 in state.ROOT.iterdir():
            if d1.is_dir() and d1.name not in _SKIP_DIRS and not d1.name.startswith("."):
                roots.append(d1)
                for d2 in d1.iterdir():
                    if d2.is_dir() and d2.name not in _SKIP_DIRS and not d2.name.startswith("."):
                        roots.append(d2)
        for r in roots:
            if (r / ".git").exists():
                repos.append(r)
    except Exception:
        pass
    return repos


def _main_commit_subjects() -> str:
    """Recent `main` commit subjects across all workspace repos. Lets the gate
    detect tasks merged out-of-band (CEO merges the PR in the web UI) whose file
    was never moved to done/. Fail-open: '' on any error."""
    parts = []
    for repo in _git_repos():
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), "log", "main", "--format=%s", "-n", "500"],
                capture_output=True, text=True, timeout=8)
            if out.returncode == 0:
                parts.append(out.stdout)
        except Exception:
            continue
    return "\n".join(parts)


def reconcile_status_drift(conn) -> tuple[list, list]:
    """Deterministic status-management enforcement (replaces the manual
    'move file to done/' step the orchestrator kept skipping):

      A. A task file still in tasks/active/ whose work is already merged - its
         run reached 'done', OR main carries its '[task-id]' commit tag - is
         moved to tasks/done/ with status stamped.
      B. A run still marked in-flight whose file already sits in tasks/done/
         has its run closed to 'done' (clears stale run.db drift, no hand-edit).

    Returns (moved, closed) task-id lists. Fail-open: never raises."""
    moved, closed = [], []
    try:
        runs = {r["task"]: r for r in state.all_runs(conn)}
        active_files = list(ACTIVE_DIR.glob("task-*.md")) if ACTIVE_DIR.is_dir() else []
        subjects = _main_commit_subjects() if active_files else ""
        for f in active_files:
            task = f.stem
            # Only the main branch may declare a task finished. The run reaching
            # stage 'done' just means it was pushed and every checkpoint passed;
            # treating that as merged is what once moved an unmerged task into
            # done/ and hid it for a day.
            merged = f"[{task}]" in subjects
            if merged and state.move_task_to_done(task):
                moved.append(task)
                # Unconditionally, including a run already at 'done': the merge
                # is what it was waiting for, and leaving awaiting_human set
                # would freeze the queue on a task that is finished.
                state.set_fields(conn, task, stage="done", awaiting_human="",
                                 stage_status=state.ST_GATE_PASSED)
        for task, r in runs.items():
            # A run whose file left active/ for done/ - or vanished - is stale;
            # close it. A file put BACK into backlog/ is a deliberate unqueue,
            # so leave that run alone rather than silently marking it finished.
            if (r["stage"] != "done"
                    and state.task_dir(task) in (None, "done")):
                state.set_fields(conn, task, stage="done", awaiting_human="",
                                 stage_status=state.ST_GATE_PASSED)
                closed.append(task)
    except Exception:
        pass
    return moved, closed


def decide() -> int:
    if not mode.conveyor_runs():
        return allow()

    conn = state.connect()
    try:
        runs = state.all_runs(conn)
        runs_by_task = {r["task"]: r for r in runs}
        editing = state.EDITING_STAGES
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
        for r in runs:
            stage, status, aw = r["stage"], r["stage_status"], r["awaiting_human"]
            if stage == "done":
                continue
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
                        f".claude/tools/pipeline/approve.py --task {r['task']} --reject. "
                        f"Do not silently move on to other work.")
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
                    f"Do not ask the user.")
            # Parked, unapproved checkpoint -> not autonomously advanceable.
            if stage == "ready" and aw in ("commit", "push"):
                continue
            # Editing stage in flight (in_progress or gate_failed retry).
            if stage in editing:
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
                        f"python .claude/tools/pipeline/advance.py --task {r['task']}. Do not ask the user.")
                return block(
                    f"{r['task']} is at stage '{stage}'. Finish the stage work (dispatch the owning "
                    f"subagent if needed), then run: python .claude/tools/pipeline/advance.py "
                    f"--task {r['task']}. Do not ask the user whether to continue.")

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
        #   - a run at `ready`, approved or not. This one was missing until
        #     task-0018: with every checkpoint auto-approved, `awaiting_human` is
        #     empty and `ready` is not an editing stage, so a task holding a
        #     dirty tree and a checked-out branch read as a free slot and the
        #     next backlog task was started on top of it. The stage name is a
        #     literal here as it is twice above; FR-39 (task-0020) is what reads
        #     the stage sets from config;
        #   - a run stranded on a stage its flow does not define (see stranded()).
        occupied = any(
            (r["stage"] in editing) or r["stage"] == "ready" or r["awaiting_human"]
            or stranded(r, pipeline)
            for r in runs
        )
        if not occupied:
            # Taking work on is itself a checkpoint. Below `auto` the CEO says
            # which task to start, so an empty in-flight set means "stop and
            # ask", not "help yourself to the queue".
            ready = ([t for t in read_backlog()
                      if t["id"] not in runs_by_task
                      and all(dep_satisfied(d, runs_by_task) for d in t["deps"])]
                     if approvals.granted(approvals.TAKE) else [])
            for t in ready:
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
                        f"run: python .claude/tools/pipeline/advance.py --task {t['id']}. "
                        f"Do not ask the user.")
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
                        f"heads: python .claude/tools/memory/codebase_sync.py --stamp. Then run: "
                        f"python .claude/tools/pipeline/advance.py --task {t['id']}. "
                        f"Do not ask the user.")
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
                            f"{latest['sha'][:12]}), then run: python "
                            f".claude/tools/pipeline/advance.py --task {t['id']}. "
                            f"Do not ask the user.")
                return block(
                    f"Start the next ready task {t['id']}: create its branch per git-workflow.md, "
                    f"then run: python .claude/tools/pipeline/advance.py --task {t['id']}. "
                    f"Drive it through the pipeline without asking the user.")

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
                "only - do not re-open them; confirm and stop.")

        # 5. Documentation debt that step 3 had no ready task to gate - the last
        # thing before the session is allowed to end. It BLOCKS rather than
        # merely mentioning, for three reasons worth keeping in the code:
        #
        #   - A Stop hook has exactly one channel, the block reason. An allowed
        #     stop prints nothing at all, so "report without blocking" is not a
        #     softer block, it is silence - which is the failure this branch
        #     exists to end.
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
                f"Nothing else in the harness reports this - the supervisor does not read "
                f"handoff debt at all. Dispatch the assignee of the next task to scaffold it: "
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
        return allow()
    finally:
        conn.close()


def main() -> int:
    try:
        json.load(sys.stdin)  # consume hook payload (stop_hook_active etc.)
    except (ValueError, OSError):
        pass
    try:
        return decide()
    except Exception:
        # Fail open: never trap the user in a stop loop.
        return allow()


if __name__ == "__main__":
    raise SystemExit(main())
