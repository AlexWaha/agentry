#!/usr/bin/env python3
"""Stop-hook brain - the engine that keeps the agent from idling mid-pipeline.

Wired to Claude Code's `Stop` event. On every attempt to stop it inspects the run
state and either:
  - blocks the stop and feeds back a concrete next instruction (continue an
    in-flight task, or start the next ready backlog task), or
  - allows the stop when every task is parked at a human checkpoint, blocked, or
    done and no ready backlog remains.

Work is serialized on the working tree. A new backlog task is offered only when
nothing holds it: no run in an editing stage (blocked included - a blocked task
still owns its dirty tree) and no run awaiting a human, because the CEO may be
reading the diff on that very branch.

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
        p = state.STATE_DIR / f"gate-{task}.json"
        if not p.is_file():
            return False
        d = json.loads(p.read_text(encoding="utf-8"))
        started = float(d.get("started", 0))
        timeout = float(d.get("timeout", 900))
        return (time.time() - started) < timeout
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
        sys.path.insert(0, str(state.ROOT / ".claude" / "tools" / "memory"))
        import update as memory_update
        return memory_update.unstamped_done_tasks()
    except Exception:
        return []


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

        # 1. Continue an in-flight, actionable run.
        for r in runs:
            stage, status, aw = r["stage"], r["stage_status"], r["awaiting_human"]
            if stage == "done" or status == state.ST_BLOCKED:
                continue
            # Approved checkpoint action still pending (commit/push the agent must do).
            if stage == "ready" and ((aw == "commit" and r["commit_approved"])
                                     or (aw == "push" and r["push_approved"])):
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
                cont = r["continuations"] + 1
                if cont > ceiling:
                    state.set_fields(conn, r["task"], stage_status=state.ST_BLOCKED)
                    continue  # stuck -> blocked, fall through to other work
                state.set_fields(conn, r["task"], continuations=cont)
                if status == state.ST_GATE_FAILED:
                    return block(
                        f"{r['task']} stage '{stage}' gate FAILED. Fix the cause, then re-run: "
                        f"python .claude/tools/pipeline/advance.py --task {r['task']}. Do not ask the user.")
                return block(
                    f"{r['task']} is at stage '{stage}'. Finish the stage work (dispatch the owning "
                    f"subagent if needed), then run: python .claude/tools/pipeline/advance.py "
                    f"--task {r['task']}. Do not ask the user whether to continue.")

        # 2. Nothing holding the working tree -> start the next ready backlog task.
        # Three states hold it, and a new task on top of any of them switches the
        # branch out from under someone:
        #   - an editing stage in flight;
        #   - an editing stage parked BLOCKED (a blocked task still owns its dirty
        #     tree; it is surfaced to the CEO in step 1, not retired);
        #   - ANY run awaiting a human - the CEO may be reading the diff on that
        #     very branch. A task parked on a human is unfinished work, not a
        #     free slot, so go quiet and wait instead;
        #   - a run stranded on a stage its flow does not define (see stranded()).
        occupied = any(
            (r["stage"] in editing) or r["awaiting_human"] or stranded(r, pipeline)
            for r in runs
        )
        if not occupied:
            debt = handoff_debt()
            mem_debt = memory_debt()
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
                    return block(
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
                    return block(
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
                        return block(
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

        # 3. Nothing to drive -> enforce status-management drift before idling:
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

        # 4. Everything is parked / blocked / done and nothing ready -> release.
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
