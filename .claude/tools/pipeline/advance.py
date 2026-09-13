#!/usr/bin/env python3
"""Advance a task through the execution pipeline FSM.

The orchestrator calls this when it believes the current stage is finished. The
script - not the model - runs the stage's exit gate and decides whether to move
on. On a human-checkpoint stage (`ready`) it parks the task for commit, then push
approval. This is the deterministic transition authority.

Registration backstop: a task may only ENTER the pipeline when its file exists
in tasks/active/, its status is released by the CEO (`active`/`review`), its
Acceptance Criteria are non-empty, and - when it names a spec - that spec is
`approved`. Spec-driven discipline enforced in code, not prose.

Handoff gate: registration is also refused while ANY completed task above the
handoff baseline lacks a valid handoff doc (see tools/pipeline/handoff.py) -
the incoming task's assignee must document the previous task first.

Interactive diff-review stage: a stage carrying "interactive": "diff_review"
is not gated by a command but by a CEO verdict file written by
tools/review/diff_review.py (browser UI). approved -> advance;
changes_requested -> comments are appended to the task file and the task
resets to the first stage.

Merge evidence: the `done` stage resolves through git_state.py, and only a
POSITIVE signal finishes a task. No branch and no tagged commit is unknown, so
the task parks naming the missing signals. A task whose work rides on another
task's branch declares it as `branch: <type>/task-NNNN` in its frontmatter and
is resolved against that.

Busy marker: dispatching a subagent for a stage must silence the Stop hook the
same way a long gate does, so mark the stage busy around the dispatch instead of
hand-writing the marker JSON.

CLI:
    python advance.py --task task-0007 --type feature
    python advance.py --task task-0007 --busy implement   # subagent dispatched
    python advance.py --task task-0007 --idle             # subagent returned
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import approvals
import git_state
import mode
import state
from gate import run_gate

GATE_TIMEOUT = 900
REVIEW_DIR = state.STATE_DIR / "review"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
CRITERIA_RE = re.compile(r"##\s*Acceptance Criteria\s*\n(.*?)(\n##\s|\Z)", re.DOTALL)
CHECKBOX_RE = re.compile(r"-\s*\[[ x]\]")


def gate_marker_path(task: str):
    return state.STATE_DIR / f"gate-{task}.json"


def write_gate_marker(task: str, stage: str) -> None:
    """Heartbeat so stop_gate.py knows a long gate is legitimately running and
    must not nag or trip the continuation ceiling while it waits."""
    state.STATE_DIR.mkdir(parents=True, exist_ok=True)
    gate_marker_path(task).write_text(json.dumps({
        "task": task, "stage": stage, "pid": os.getpid(),
        "started": time.time(), "timeout": GATE_TIMEOUT,
    }), encoding="utf-8")


def clear_gate_marker(task: str) -> None:
    try:
        gate_marker_path(task).unlink()
    except OSError:
        pass


def _frontmatter(text: str) -> dict:
    """Tiny YAML-less frontmatter reader: top-level 'key: value' pairs between
    the first pair of --- lines. Good enough for task/spec files."""
    fields: dict[str, str] = {}
    m = FRONTMATTER_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t", "-")):
                k, v = line.split(":", 1)
                fields[k.strip()] = v.strip()
    return fields


def check_task_ready(task: str) -> str:
    """Deterministic registration backstop. Returns '' when the task may enter
    the pipeline, else a one-line refusal reason. Fail-open: an internal error
    allows registration (a backstop bug must not block real work)."""
    try:
        where = state.task_dir(task)
        if where is None:
            return (f"{task}: no task file in tasks/backlog, active or done - create it "
                    f"via skills/new-task before starting the pipeline.")
        if where == "done":
            return (f"{task}: the file sits in tasks/done, so this task is already "
                    f"merged and closed. Reopen it deliberately if that is wrong.")
        path = state.TASK_DIRS[where] / f"{task}.md"

        # Lazy import + the surrounding try/except keep this fail-open on a
        # partially synced clone that lacks handoff.py.
        import handoff
        debt = handoff.uncovered_done_tasks()
        if debt:
            d = debt[0]
            return (f"{task}: handoff debt blocks registration - completed task {d['task']} "
                    f"lacks a valid handoff doc ({d['reason']}). Dispatch {task}'s assignee to: "
                    f"1) scaffold it: python .claude/tools/pipeline/handoff.py --for {d['task']}, "
                    f"2) fill every section of .claude/tasks/handoffs/{d['task']}.md in its own "
                    f"words from tasks/done/{d['task']}.md, its merge diff on main, and the gate "
                    f"log, 3) read .claude/project/project-context.md and this task's spec first. "
                    f"Then re-run advance.py --task {task}.")

        text = path.read_text(encoding="utf-8", errors="replace")
        fm = _frontmatter(text)

        m = CRITERIA_RE.search(text)
        criteria = m.group(1) if m else ""
        if not CHECKBOX_RE.search(criteria):
            return (f"{task}: Acceptance Criteria section is empty - a task without "
                    f"testable criteria cannot enter the pipeline (quality-standard).")

        spec_id = fm.get("spec", "")
        if not spec_id:
            return (f"{task}: no 'spec:' field in frontmatter. Every task must derive "
                    f"from an approved spec (formalize -> plan -> spec -> task) so the "
                    f"implementer follows the spec, not improvised logic. Link it "
                    f"(spec: <id>), or - for trivial work with genuinely no spec - set "
                    f"'spec: none' with a one-line reason in Notes.")
        if spec_id.lower() != "none":
            specs_dir = state.ROOT / ".claude" / "specs"
            matches = sorted(specs_dir.glob(f"{spec_id}*.md")) if specs_dir.is_dir() else []
            if not matches:
                return (f"{task}: frontmatter names spec '{spec_id}' but no such file "
                        f"exists in .claude/specs/.")
            spec_fm = _frontmatter(matches[0].read_text(encoding="utf-8", errors="replace"))
            if spec_fm.get("status") != "approved":
                return (f"{task}: spec '{spec_id}' has status "
                        f"'{spec_fm.get('status', '?')}' - only an approved spec may "
                        f"be implemented. Surface it to the CEO.")
        return ""
    except Exception:
        return ""  # fail-open


def _task_frontmatter(task: str) -> dict:
    try:
        where = state.task_dir(task) or "active"
        path = state.TASK_DIRS[where] / f"{task}.md"
        return _frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def _merge_wait(task: str, run: dict, waiting: str) -> dict:
    """Record - or clear - 'this task is still waiting on a merge'.

    A park nobody writes down is invisible: stop_gate.py sees an idle slot and
    hands the working tree to the next backlog task while the CEO is still
    reading the diff on this branch. Opens its own connection because callers
    have already closed theirs. Fail-open: bookkeeping never breaks the run."""
    try:
        conn = state.connect()
        try:
            state.set_fields(conn, task, awaiting_human=waiting)
        finally:
            conn.close()
    except Exception:
        return run
    return {**run, "awaiting_human": waiting}


def finish_or_wait_for_merge(task: str, run: dict) -> int:
    """A task is done when the main branch carries it, not when it was pushed.

    Pushing is the last thing the agent can do on its own; merging is the CEO's,
    in the web UI. Treating the push as completion is how a branch sat unmerged
    for a day while the task file said done. So the file only moves to done/
    once git confirms the merge - and until then the task keeps saying it is
    waiting for one."""
    declared = _task_frontmatter(task).get("branch", "")
    report = git_state.task_report(task, branch=declared)

    if not report:
        # Absence of evidence is not evidence of a merge. Neither signal was
        # found, so git cannot tell - park and name both. A task moved to done/
        # on this path once hid unmerged code on a sibling's branch for a day.
        missing = (f"the declared branch 'origin/{declared}' (and local "
                   f"'{declared}') does not exist in any repo" if declared else
                   "no branch matching this task id exists in any repo, and the task "
                   "declares no 'branch:' in its frontmatter")
        run = _merge_wait(task, run, "merge")
        return result("park", task, run,
                      f"Cannot confirm a merge - both signals are missing: (1) carrier "
                      f"branch: {missing}; (2) tagged commit: the main branch carries no "
                      f"'[{task}]' commit. This is UNKNOWN, not merged, so the task stays "
                      f"out of tasks/done/. If its work rides on another task's branch, "
                      f"add 'branch: <type>/task-NNNN' to the frontmatter and re-run; "
                      f"otherwise push the branch or surface it to the CEO.")

    unmerged = [r for r in report if r["in_main"] is not True]
    if unmerged:
        where = ", ".join(f"{r['repo']} ({r['branch'] or 'no branch'})" for r in unmerged)
        run = _merge_wait(task, run, "merge")
        return result("park", task, run,
                      f"Pushed, but the main branch does not carry this task yet: {where}. "
                      f"The CEO merges the MR; re-run advance.py afterwards and the file "
                      f"moves to tasks/done/. Do not call this task done meanwhile.")

    moved = state.move_task(task, "done")
    note = "moved to tasks/done/" if moved else "already in tasks/done/"
    repos = ", ".join(r["repo"] for r in report)
    run = _merge_wait(task, run, "")
    return result("done", task, run,
                  f"Merged into main ({repos}). Task done - file {note}.")


def stage_owner(pipeline: dict, name: str, task: str = "", which: str = state.BUILD) -> str:
    """Owner 'dev' is generic - resolve it to the task's assignee so frontend
    work routes to senior-frontend-dev, not whoever the pipeline hardcodes."""
    s = state.get_stage(pipeline, name, which) or {}
    owner = s.get("owner", "orchestrator")
    if owner == "dev" and task:
        assignee = _task_frontmatter(task).get("assignee", "")
        if assignee and assignee != "agent-name":
            return assignee
        return "dev (set the task's assignee: senior-backend-dev / senior-frontend-dev / ...)"
    return owner


def _diff_review_repo(pipeline: dict, stage_def: dict) -> Path:
    """Resolve the diff-review git repo dir from stage/pipeline cwd, mirroring
    gate.py's cwd resolution so multi-repo workspaces (repo in a subdir, root
    not a repo) target the actual git repo."""
    cwd = stage_def.get("cwd") or pipeline.get("cwd") or "."
    return state.ROOT if cwd == "." else (state.ROOT / cwd)


def _repo_head(repo) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=8)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def _read_verdict(task: str) -> dict:
    try:
        d = json.loads((REVIEW_DIR / f"{task}.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _archive_verdict(task: str, verdict: dict) -> None:
    try:
        rnd = int(verdict.get("round", 1))
        src = REVIEW_DIR / f"{task}.json"
        if src.is_file():
            src.replace(REVIEW_DIR / f"{task}.round{rnd}.json")
    except Exception:
        pass


def _append_review_feedback(task: str, verdict: dict) -> bool:
    """Append the CEO comments to the END of the active task file (append-only
    at EOF so the Acceptance Criteria parsing is untouched). The dev absorbs
    them via the context-absorption chain (rules/pipeline.md)."""
    try:
        path = state.ROOT / ".claude" / "tasks" / "active" / f"{task}.md"
        if not path.is_file():
            return False
        rnd = int(verdict.get("round", 1))
        today = state.today()  # local date - this line is read by the dev and the CEO
        lines = [f"\n## CEO Review Feedback (round {rnd})\n",
                 (f"> Recorded {today} via diff-review. Address "
                  f"EVERY comment before the task can pass diff-review again.\n")]
        for c in verdict.get("comments", []):
            loc = f"{c.get('file', '?')}:{c.get('start_line', '?')}"
            if c.get("end_line") and c.get("end_line") != c.get("start_line"):
                loc += f"-{c['end_line']}"
            side = c.get("side", "after")
            lines.append(f"- [ ] `{loc}` [{side}]: {c.get('text', '').strip()}\n")
        with path.open("a", encoding="utf-8") as fh:
            fh.writelines(lines)
        return True
    except Exception:
        return False


def handle_diff_review(conn, task: str, pipeline: dict, run: dict, cur: str,
                       which: str = state.BUILD) -> int:
    """Interactive CEO checkpoint. Consume a fresh verdict when present;
    otherwise launch the review UI (blocking, gate-marker protected); park when
    no verdict arrives."""
    stage_def = state.get_stage(pipeline, cur, which) or {}
    repo = _diff_review_repo(pipeline, stage_def)
    head = _repo_head(repo)
    verdict = _read_verdict(task)
    stale = bool(verdict) and head and verdict.get("head") != head

    if not verdict or stale:
        script = state.ROOT / ".claude" / "tools" / "review" / "diff_review.py"
        write_gate_marker(task, cur)
        try:
            subprocess.run([sys.executable, str(script), "--task", task, "--repo", str(repo)],
                           cwd=str(state.ROOT), timeout=GATE_TIMEOUT + 60)
        except Exception:
            pass
        finally:
            clear_gate_marker(task)
        verdict = _read_verdict(task)
        if head and verdict.get("head") != head:
            verdict = {}

    if not verdict:
        state.set_fields(conn, task, awaiting_human="diff-review")
        run = state.get_run(conn, task)
        conn.close()
        return result("park", task, run,
                      "CEO diff-review pending - no verdict recorded (browser closed or "
                      "timeout). Re-run advance.py when the CEO is available.")

    if verdict.get("verdict") == "approved":
        nxt = state.next_stage(pipeline, cur, which)
        state.set_fields(conn, task, stage=nxt, awaiting_human="",
                         stage_status=state.ST_IN_PROGRESS, retries=0, continuations=0)
        _archive_verdict(task, verdict)
        run = state.get_run(conn, task)
        conn.close()
        return result("advanced", task, run,
                      f"CEO approved the diff (round {verdict.get('round', 1)}): "
                      f"'{cur}' -> '{nxt}'. Proceed.")

    # changes_requested -> feedback into the task file, reset to first stage.
    appended = _append_review_feedback(task, verdict)
    _archive_verdict(task, verdict)
    first = state.stage_names(pipeline, which)[0]
    state.set_fields(conn, task, stage=first, awaiting_human="",
                     stage_status=state.ST_IN_PROGRESS, retries=0, continuations=0,
                     commit_approved=0, push_approved=0)
    run = state.get_run(conn, task)
    conn.close()
    note = ("comments appended to the task file" if appended
            else "WARNING: could not append comments to the task file - read the "
                 "archived verdict in .claude/state/review/")
    return result("rejected", task, run,
                  f"CEO requested changes ({len(verdict.get('comments', []))} comment(s); "
                  f"{note}). Task reset to '{first}'. Dispatch the assignee to address "
                  f"EVERY comment in '## CEO Review Feedback', then re-run advance.py.")


def result(action: str, task: str, run: dict, message: str) -> int:
    print(json.dumps({
        "action": action,
        "task": task,
        "stage": run.get("stage"),
        "stage_status": run.get("stage_status"),
        "awaiting_human": run.get("awaiting_human", ""),
        "message": message,
    }, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Advance a task through the pipeline")
    parser.add_argument("--task", required=True)
    parser.add_argument("--type", default="feature")
    parser.add_argument("--pipeline", choices=[state.BUILD, state.PLAN],
                        help="which flow to start this task on; defaults to the session mode")
    parser.add_argument("--busy", metavar="STAGE",
                        help="mark this stage busy (a subagent was dispatched for it) so the "
                             "Stop hook stops nagging while it works")
    parser.add_argument("--idle", action="store_true",
                        help="clear the busy marker (the subagent returned)")
    args = parser.parse_args()

    # Busy marker bookkeeping - no FSM transition, so handle it and leave.
    # advance.py already brackets its own long gates with these two calls; a
    # dispatched subagent is the same situation seen from the orchestrator's
    # side, and it had no call to make until now.
    if args.busy:
        write_gate_marker(args.task, args.busy)
        print(json.dumps({
            "action": "busy", "task": args.task, "stage": args.busy,
            "message": f"Busy marker written for stage '{args.busy}' (expires in "
                       f"{GATE_TIMEOUT}s). The Stop hook stays quiet on {args.task} until "
                       f"then. Clear it when the subagent returns: python "
                       f".claude/tools/pipeline/advance.py --task {args.task} --idle.",
        }, indent=2))
        return 0
    if args.idle:
        clear_gate_marker(args.task)
        print(json.dumps({
            "action": "idle", "task": args.task,
            "message": "Busy marker cleared - the Stop hook drives this task again.",
        }, indent=2))
        return 0

    pipeline = state.load_pipeline()

    conn_probe = state.connect()
    existing = state.get_run(conn_probe, args.task)
    conn_probe.close()
    # An in-flight run keeps the flow it was registered on; a new one follows the
    # explicit flag, else the session mode.
    which = (state.run_pipeline(existing) if existing
             else (args.pipeline or mode.pipeline_for_mode()))

    names = state.stage_names(pipeline, which)
    if not names:
        print(json.dumps({"action": "error", "task": args.task,
                          "message": "pipeline.json has no stages - run onboarding"}))
        return 0
    first = names[0]
    budget = int(pipeline.get("retry_budget", 3))

    conn = state.connect()
    run = state.get_run(conn, args.task)

    # 1. Start a task that is not yet in a run - registration backstop first.
    if run is None:
        refusal = check_task_ready(args.task)
        if refusal:
            conn.close()
            print(json.dumps({"action": "refused", "task": args.task,
                              "message": refusal}, indent=2))
            return 0
        run = state.create_run(conn, args.task, args.type, first, which)
        conn.close()
        # Taking a task on IS the move out of the queue - there is no separate
        # status to flip, and nothing lands in active/ that is not really moving.
        state.move_task(args.task, "active")
        return result("started", args.task, run,
                      f"started at '{first}' on the {which} flow "
                      f"(owner: {stage_owner(pipeline, first, args.task, which)}). "
                      f"Do the stage work, then re-run advance.py --task {args.task}.")

    cur = run["stage"]

    if cur == "done":
        conn.close()
        return finish_or_wait_for_merge(args.task, run)
    if run["stage_status"] == state.ST_BLOCKED:
        conn.close()
        return result("blocked", args.task, run,
                      "task is BLOCKED - needs CEO. Do not auto-retry.")

    stage_def = state.get_stage(pipeline, cur, which) or {}

    # 1.5 Interactive CEO diff-review stage - verdict-file gated, not command-gated.
    if stage_def.get("interactive") == "diff_review":
        return handle_diff_review(conn, args.task, pipeline, run, cur, which)

    # 2. Human-checkpoint tail stage (commit, then push). A checkpoint listed in
    # the stage's optional "auto_approve" array is approved by the script itself
    # (no CEO parking) - for flows where human participation is optional. The
    # approval FLAG is still set, so pretool_gate / stop_gate work unchanged.
    # Default (key absent / empty) keeps both checkpoints human-approved.
    if stage_def.get("checkpoints"):
        auto = {str(c).lower() for c in (stage_def.get("auto_approve") or [])}
        # The push is routed through approvals.granted() rather than read out of
        # auto_approve directly: approvals.NEVER_GRANTED refuses it at every
        # level and for every stage list, so the config key cannot re-grant it.
        push_auto = approvals.granted(approvals.PUSH, sorted(auto))
        aw = run["awaiting_human"]
        if aw == "":
            fields = {"awaiting_human": "commit"}
            if "commit" in auto:
                fields["commit_approved"] = 1
            state.set_fields(conn, args.task, **fields)
        elif aw == "commit" and run["commit_approved"]:
            fields = {"awaiting_human": "push"}
            if push_auto:
                fields["push_approved"] = 1
            state.set_fields(conn, args.task, **fields)
        elif aw == "push" and run["push_approved"]:
            state.set_fields(conn, args.task, stage="done", awaiting_human="",
                             stage_status=state.ST_GATE_PASSED)
        run = state.get_run(conn, args.task)
        conn.close()
        msgs = {
            "commit": ("Checkpoint 'commit' auto-approved (auto_approve in pipeline.json). "
                       "Stage files and perform the git commit now, then re-run advance.py."
                       if "commit" in auto else
                       "Review passed. Stage files, surface the full diff, and wait for "
                       "CEO commit approval (approve.py --gate commit). "
                       "git commit is hook-blocked until then."),
            "push": ("Checkpoint 'push' auto-approved. Perform the git push now, "
                     "then re-run advance.py."
                     if push_auto else
                     "Committed. Wait for CEO push approval (approve.py --gate push). "
                     "git push is hook-blocked until then."),
        }
        if run["stage"] == "done":
            return finish_or_wait_for_merge(args.task, run)
        return result("park", args.task, run, msgs.get(run["awaiting_human"], "awaiting human."))

    # 3. Gated autonomous stage - run the exit gate deterministically.
    write_gate_marker(args.task, cur)
    try:
        gate = run_gate(pipeline, cur, args.task, which)
    finally:
        clear_gate_marker(args.task)

    if not gate["configured"]:
        state.set_fields(conn, args.task, stage_status=state.ST_BLOCKED)
        run = state.get_run(conn, args.task)
        conn.close()
        return result("blocked", args.task, run,
                      f"stage '{cur}' gate is not configured: {gate['output']}")

    if gate["passed"]:
        nxt = state.next_stage(pipeline, cur, which)
        state.set_fields(conn, args.task, stage=nxt, stage_status=state.ST_IN_PROGRESS,
                         retries=0, continuations=0)
        run = state.get_run(conn, args.task)
        conn.close()
        owner = stage_owner(pipeline, nxt, args.task, which)
        return result("advanced", args.task, run,
                      f"gate passed: '{cur}' -> '{nxt}' (owner: {owner}). Proceed.")

    retries = run["retries"] + 1
    if retries >= budget:
        state.set_fields(conn, args.task, stage_status=state.ST_BLOCKED, retries=retries)
        run = state.get_run(conn, args.task)
        conn.close()
        return result("blocked", args.task, run,
                      f"gate failed {retries}/{budget} times - BLOCKED. Surface to CEO, stop retrying.")

    state.set_fields(conn, args.task, stage_status=state.ST_GATE_FAILED, retries=retries)
    run = state.get_run(conn, args.task)
    conn.close()
    return result("gate_failed", args.task, run,
                  f"gate failed (retry {retries}/{budget}). Fix the cause and re-run advance.py. "
                  f"Gate output tail:\n{gate['output'][-600:]}")


if __name__ == "__main__":
    raise SystemExit(main())
