#!/usr/bin/env python3
"""Advance a task through the execution pipeline FSM.

The orchestrator calls this when it believes the current stage is finished. The
script - not the model - runs the stage's exit gate and decides whether to move
on. On a human-checkpoint stage (`ready`) it parks the task for commit, then push
approval. This is the deterministic transition authority.

Workflow mode narrows that tail: `pr` mode keeps both checkpoints, `solo` mode
has no push at all (the approved branch is merged into the trunk locally), so it
parks for the commit and then goes straight to the merge check - see
stage_checkpoints().

Registration backstop: a task may only ENTER the pipeline when its file exists
in tasks/active/, its status is released by the CEO (`active`/`review`), its
Acceptance Criteria are non-empty, and - when it names a spec - that spec is
`approved`. Spec-driven discipline enforced in code, not prose.

Handoff gate: registration is also refused while ANY completed task above the
handoff baseline lacks a valid handoff doc (see tools/pipeline/handoff.py) -
the incoming task's assignee must document the previous task first.

Memory gate: and then refused again while any completed task above the memory
baseline lacks its stamp (see tools/memory/update.py). The two are one sentence
in rules/pipeline.md and only the first half was enforced here - this file
contained the word `memory` zero times, so two tasks ran the whole pipeline and
closed undistilled with nothing objecting (task-0066). Handoff first, memory
second: the rows are distilled FROM the doc.

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
import sys
import time

import approvals
import git_state
import mode
import pretool_gate
import state
from gate import run_gate

GATE_TIMEOUT = 900

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


def handoff_debt() -> list:
    """Completed tasks lacking a valid handoff doc (tools/pipeline/handoff.py).

    Same reason for its own handler as memory_debt below, and it is not
    hypothetical: this call sat bare under check_task_ready's blanket
    `except Exception: return ""` for its whole life, so a broken handoff.py
    did not merely switch off the handoff gate - it returned "allow" past the
    acceptance-criteria check and the spec-approval gate too, which are the
    pipeline's floor. One checker's failure may only disable that checker."""
    try:
        import handoff
        return handoff.uncovered_done_tasks()
    except Exception:
        return []


def memory_debt() -> list:
    """Completed tasks whose memory review was never stamped (tools/memory/
    update.py). Carries its OWN try/except instead of riding on the caller's:
    check_task_ready's blanket handler returns "allow" on any exception, so an
    import error here would silently take the spec and criteria checks down with
    it. Fail-open on its own terms - no debt reported."""
    try:
        p = str(state.ROOT / ".claude" / "tools" / "memory")
        if p not in sys.path:
            sys.path.insert(0, p)
        import update as memory_update
        return memory_update.unstamped_done_tasks()
    except Exception:
        return []


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

        debt = handoff_debt()
        if debt:
            d = debt[0]
            return (f"{task}: handoff debt blocks registration - completed task {d['task']} "
                    f"lacks a valid handoff doc ({d['reason']}). Dispatch {task}'s assignee to: "
                    f"1) scaffold it: python .claude/tools/pipeline/handoff.py --for {d['task']}, "
                    f"2) fill every section of .agentry/tasks/handoffs/{d['task']}.md in its own "
                    f"words from tasks/done/{d['task']}.md, its merge diff on main, and the gate "
                    f"log, 3) read .agentry/project/project-context.md and this task's spec first. "
                    f"Then re-run advance.py --task {task}.")

        # The second half of the same guarantee, and the half that was enforced
        # nowhere. It sits here rather than only in the Stop hook because a
        # registration is the moment the rule names, and the Stop hook's own
        # memory check could not see one: it ran only while nothing was in
        # flight. Handoff above, memory here, in the order the work happens.
        mem = memory_debt()
        if mem:
            d = mem[0]
            return (f"{task}: memory debt blocks registration - completed task {d['task']} "
                    f"has no memory stamp ({d['reason']}). Distill its handoff doc "
                    f".agentry/tasks/handoffs/{d['task']}.md into the store: python "
                    f".claude/tools/memory/memory.py --record --kind lesson --signature <tag> "
                    f"--trigger <when> --what <mistake> --why <cause> --fix <rule> (reusable "
                    f"code shapes: --kind pattern; touched modules: --kind module). Then stamp "
                    f"it: python .claude/tools/memory/update.py --stamp --task {d['task']} - "
                    f"refused unless the store gained a row, so pass --none if the review "
                    f"genuinely found nothing worth recording. Then re-run advance.py --task "
                    f"{task}.")

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
            specs_dir = state.ROOT / ".agentry" / "specs"
            matches = sorted(specs_dir.glob(f"{spec_id}*.md")) if specs_dir.is_dir() else []
            if not matches:
                return (f"{task}: frontmatter names spec '{spec_id}' but no such file "
                        f"exists in .agentry/specs/.")
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


# Which run.db flag records each checkpoint's approval.
APPROVAL_FIELD = {"commit": "commit_approved", "push": "push_approved"}


def stage_checkpoints(stage_def: dict) -> list:
    """The checkpoints this stage really has, narrowed by the workflow mode.

    In `solo` mode nothing is pushed: the approved branch is merged into the
    trunk locally and the CEO pushes the trunk himself later. A push checkpoint
    there gates a step that never happens, which is why the first task of an
    unattended run reached `ready` and stopped for the night. The mode drives
    the list rather than a branch inside the checkpoint block, so there is one
    answer to 'does this project push at all'.

    Read through pretool_gate.workflow_mode() on purpose - that is the single
    accessor deciding the mode, and the gate that enforces the local merge must
    not be able to disagree with the FSM that authorizes it.

    Only the push is dropped. push_needs_approval is untouched: if a push does
    happen in solo mode it still needs its recorded approval, and a push to a
    protected branch is still refused outright."""
    checkpoints = [str(c).lower() for c in (stage_def.get("checkpoints") or [])]
    if pretool_gate.workflow_mode() == pretool_gate.WORKFLOW_SOLO:
        checkpoints = [c for c in checkpoints if c != approvals.PUSH]
    return [c for c in checkpoints if c in APPROVAL_FIELD]


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

    # A stage name the config does not define is a dead end, not a pass. Such a
    # stage has no exit_gate, and gate.py reports a missing gate as configured
    # AND passed, so the run used to be written stage=NULL under an "advanced"
    # message - after which next_stage(None) is also None and every later call
    # advanced from nothing to nothing. Worse, NULL is in neither the editing
    # set nor awaiting_human, so the Stop hook read a free slot and branched new
    # work onto the stranded task's dirty tree. Writing NULL and reporting
    # success is not failing open (NFR-4), it is failing silently: refuse to
    # guess, name the unknown stage, and point at the way back.
    if cur not in names:
        state.set_fields(conn, args.task, stage_status=state.ST_BLOCKED)
        run = state.get_run(conn, args.task)
        conn.close()
        return result("blocked", args.task, run,
                      f"stage '{cur}' is not part of the {which} flow, so no gate was run. "
                      f"Configured stages: {', '.join(names)}. The run is stranded - a "
                      f"pipeline.json stage list that changed under a live run, or an earlier "
                      f"NULL write. Recover with: python .claude/tools/pipeline/approve.py "
                      f"--task {args.task} --reject (sends it back to '{first}'), or restore "
                      f"'{cur}' in pipelines.{which}.stages.")

    if run["stage_status"] == state.ST_BLOCKED:
        conn.close()
        return result("blocked", args.task, run,
                      "task is BLOCKED - needs CEO. Do not auto-retry.")

    stage_def = state.get_stage(pipeline, cur, which) or {}

    # 2. Human-checkpoint tail stage (commit, then push). A checkpoint listed in
    # the stage's optional "auto_approve" array is approved by the script itself
    # (no CEO parking) - for flows where human participation is optional. The
    # approval FLAG is still set, so pretool_gate / stop_gate work unchanged.
    # Default (key absent / empty) keeps both checkpoints human-approved.
    checkpoints = stage_checkpoints(stage_def)
    if checkpoints:
        auto = {str(c).lower() for c in (stage_def.get("auto_approve") or [])}
        solo = pretool_gate.workflow_mode() == pretool_gate.WORKFLOW_SOLO
        # The first checkpoint still unapproved is the one to park on; none left
        # means the tail of the stage is complete, so route to the merge check -
        # in solo mode the local merge is what satisfies it, in pr mode the
        # CEO's merge of the pushed branch. Same check either way.
        pending = next((c for c in checkpoints if not run[APPROVAL_FIELD[c]]), None)
        if pending is None:
            state.set_fields(conn, args.task, stage="done", awaiting_human="",
                             stage_status=state.ST_GATE_PASSED)
        else:
            fields = {"awaiting_human": pending}
            # auto_approve proposes, approvals.granted() disposes: NEVER_GRANTED
            # refuses the push at every level and for every stage list, so the
            # config key cannot re-grant it.
            if pending in auto and approvals.granted(pending, sorted(auto)):
                fields[APPROVAL_FIELD[pending]] = 1
            state.set_fields(conn, args.task, **fields)
        run = state.get_run(conn, args.task)
        conn.close()
        # What follows the commit differs by mode, and the orchestrator has to be
        # told: in solo mode nothing is pushed, so waiting for a push checkpoint
        # that will never arrive is exactly the stall this fixed.
        after_commit = ("merge the branch into the trunk locally (solo workflow mode) "
                        "and re-run advance.py - the trunk carrying the task is what "
                        "closes it." if solo else
                        "re-run advance.py for the push checkpoint.")
        msgs = {
            "commit": ("Checkpoint 'commit' auto-approved (auto_approve in pipeline.json). "
                       f"Stage files and perform the git commit now, then {after_commit}"
                       if "commit" in auto else
                       "Review passed. Stage files, surface the full diff, and wait for "
                       "CEO commit approval (approve.py --gate commit). git commit is "
                       f"hook-blocked until then. After the commit: {after_commit}"),
            "push": ("Checkpoint 'push' auto-approved. Perform the git push now, "
                     "then re-run advance.py."
                     if approvals.granted(approvals.PUSH, sorted(auto)) else
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
        # The guard above proved `cur` is configured, so an absent successor
        # means this flow's last stage is not `done` - the only stage allowed to
        # end a run (it is handled before the gate). Block instead of writing
        # NULL: same silent-death path as an unknown stage, one step later.
        if nxt is None:
            state.set_fields(conn, args.task, stage_status=state.ST_BLOCKED)
            run = state.get_run(conn, args.task)
            conn.close()
            return result("blocked", args.task, run,
                          f"stage '{cur}' passed its gate, but the {which} flow defines no "
                          f"stage after it and '{cur}' is not the terminal 'done' stage. Fix "
                          f"pipelines.{which}.stages in .agentry/pipeline.json (the list must "
                          f"end with 'done'), then re-run advance.py --task {args.task}.")
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
