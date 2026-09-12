#!/usr/bin/env python3
"""Stop-hook brain - the engine that keeps the agent from idling mid-pipeline.

Wired to Claude Code's `Stop` event. On every attempt to stop it inspects the run
state and either:
  - blocks the stop and feeds back a concrete next instruction (continue an
    in-flight task, or start the next ready backlog task), or
  - allows the stop when every task is parked at a human checkpoint, blocked, or
    done and no ready backlog remains.

Autonomy is pipelined but edit-serialized: at most one task occupies an editing
stage at a time; parked tasks wait for the human while the next task runs.

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

BACKLOG_DIR = state.BACKLOG_DIR
ACTIVE_DIR = state.ACTIVE_DIR
DONE_DIR = state.DONE_DIR


def block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def allow() -> int:
    # No output -> Claude Code stops normally.
    return 0


def read_backlog() -> list[dict]:
    """Queued task files: [{id, deps}]. Empty on any error.

    The queue is a folder, not a field. A file in backlog/ is waiting to be
    picked; there is no `status:` to read, and therefore nothing that can
    disagree with where the file actually sits."""
    out: list[dict] = []
    if not BACKLOG_DIR.is_dir():
        return out
    for path in sorted(BACKLOG_DIR.glob("task-*.md")):
        m = TASK_ID_RE.search(path.name)
        if not m:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
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


def dep_satisfied(dep: str, runs_by_task: dict) -> bool:
    run = runs_by_task.get(dep)
    if run and run["stage"] == "done":
        return True
    return (DONE_DIR / f"{dep}.md").exists()


def handoff_debt() -> list:
    """Completed tasks still lacking a valid handoff doc. Lazy import + fail-open
    so a partially synced clone missing handoff.py cannot break the Stop hook."""
    try:
        import handoff
        return handoff.uncovered_done_tasks()
    except Exception:
        return []


def memory_debt() -> list:
    """Completed tasks whose memory layers were not reviewed (tools/memory/
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
                r = runs.get(task)
                if r and r["stage"] != "done":
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
        ceiling = int(state.load_pipeline().get("continuation_ceiling", 30))

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
            # Interactive CEO diff-review stage.
            if stage == "diff-review":
                verdict = state.STATE_DIR / "review" / f"{r['task']}.json"
                if verdict.is_file():
                    return block(
                        f"{r['task']}: CEO diff-review verdict recorded. Consume it: python "
                        f".claude/tools/pipeline/advance.py --task {r['task']}. Do not ask the user.")
                if aw == "diff-review":
                    continue  # parked awaiting the CEO - do not nag
                if busy_marker_fresh(r["task"]):
                    continue  # review UI currently serving
                return block(
                    f"{r['task']} is at stage 'diff-review'. Run: python "
                    f".claude/tools/pipeline/advance.py --task {r['task']} - it opens the "
                    f"CEO review UI in the browser and waits for the verdict. Do not ask the user.")
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

        # 2. No in-flight editing task -> start the next ready backlog task.
        editing_in_flight = any(
            r["stage"] in editing and r["stage_status"] != state.ST_BLOCKED for r in runs
        )
        if not editing_in_flight:
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
                        f".claude/tasks/handoffs/{d['task']}.md in its own words, reading "
                        f".claude/project/project-context.md and {t['id']}'s spec first. Then "
                        f"run: python .claude/tools/pipeline/advance.py --task {t['id']}. "
                        f"Do not ask the user.")
                # Memory chain: after the handoff doc exists, distill it into the
                # shared memory layers BEFORE new work (tools/memory/update.py).
                if mem_debt:
                    d = mem_debt[0]
                    return block(
                        f"Before starting {t['id']}: completed task {d['task']} has not been "
                        f"distilled into the memory layers. From its handoff doc: Gotchas -> "
                        f".claude/memory/lessons.md; reusable code patterns -> "
                        f".claude/memory/patterns.md (+ patterns/P-NNN-<name>.md); touched "
                        f"modules -> .claude/memory/codebase.md rows. Then stamp: python "
                        f".claude/tools/memory/update.py --stamp --task {d['task']} "
                        f"--lessons N --patterns N --l1-rows N (or --none if nothing to "
                        f"record), and refresh L1: python "
                        f".claude/tools/memory/codebase_sync.py --stamp. Then run: python "
                        f".claude/tools/pipeline/advance.py --task {t['id']}. Do not ask the user.")
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
                            f"every section of .claude/tasks/handoffs/{latest['task']}.md in "
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
