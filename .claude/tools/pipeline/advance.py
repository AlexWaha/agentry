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
import re
import sys

import approvals
import busy
import diff_checkpoint
import git_state
import mode
import pretool_gate
import state
import worktree
from gate import run_gate

GATE_TIMEOUT = 1800

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
CRITERIA_RE = re.compile(r"##\s*Acceptance Criteria\s*\n(.*?)(\n##\s|\Z)", re.DOTALL)
CHECKBOX_RE = re.compile(r"-\s*\[[ x]\]")

# The one refusal that waits on the CEO rather than on an agent (task-0114,
# FR-50): the spec is not approved. Every other refusal is work for an agent.
# The marker is shared by the refusal text and refusal_human(), so the two
# cannot drift apart.
SPEC_NOT_APPROVED = "only an approved spec may be implemented"
SPEC_NOT_APPROVED_HUMAN = ("{task} cannot start: its spec is not approved. Read the spec "
                           "and approve it, or send the task back to planning.")


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
    except Exception as exc:
        return state.checker_failed("advance.handoff_debt", exc, [])


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
    except Exception as exc:
        return state.checker_failed("advance.memory_debt", exc, [])


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
                        f"'{spec_fm.get('status', '?')}' - {SPEC_NOT_APPROVED}. "
                        f"Surface it to the CEO.")
        return ""
    except Exception:
        return ""  # fail-open


def _register_tree(task: str, task_type: str, which: str) -> dict | None:
    """Cut the task's branch and worktree at registration: worktree.add()'s result, or
    None when this task gets none - the flag is off, or it is not a build-flow task
    or it is a swarm umbrella (umbrellas run in the repo itself, exclusive both ways).
    The caller has already passed check_task_ready, so nothing is created for a task
    that was going to be refused."""
    if which != state.BUILD or not worktree.enabled():
        return None
    frozen = state.frozen_frontmatter(task)
    if frozen["umbrella"] == "true":
        return None
    return worktree.add(task, task_type, frozen["repo"])


def refusal_human(task: str, refusal: str) -> str:
    """The sentence for the CEO when a refusal waits on him, else "".

    The model reads `message` in the Bash tool result; the person reads this,
    through hooks/refusal_notice.py (PostToolUse on Bash). NEVER raises: a message
    that cannot be built is dropped and the refusal stands - the task is still
    not registered (supervisor decision on task-0114, over the NFR-4 wording)."""
    try:
        if SPEC_NOT_APPROVED in refusal:
            return SPEC_NOT_APPROVED_HUMAN.format(task=task)
    except Exception:
        pass
    return ""


def _task_frontmatter(task: str) -> dict:
    try:
        where = state.task_dir(task) or "active"
        path = state.TASK_DIRS[where] / f"{task}.md"
        return _frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def _merge_wait(task: str, run: dict, waiting: str) -> dict:
    """Record - or clear - 'this task is still waiting on a merge'. The only
    clearing call (waiting == "") follows a confirmed merge, so it is the one that
    journals `merged`; the write that sets the wait journals `parked`.

    A park nobody writes down is invisible: stop_gate.py sees an idle slot and
    hands the working tree to the next backlog task while the CEO is still
    reading the diff on this branch. Opens its own connection because callers
    have already closed theirs. Fail-open: bookkeeping never breaks the run."""
    try:
        conn = state.connect()
        try:
            state.set_fields(conn, task, awaiting_human=waiting,
                             event="" if waiting else "merged")
        finally:
            conn.close()
    except Exception:
        return run
    return {**run, "awaiting_human": waiting}


def finish_or_wait_for_merge(task: str, run: dict) -> int:
    """A task is done when the main branch carries it, not when it was pushed.

    Who merges follows `workflow.mode`: in `pr` mode the human, in the web UI,
    after the agent pushes; in `solo` mode the pipeline, locally, once the
    commit is approved. Treating the push as completion is how a branch sat
    unmerged for a day while the task file said done. So the file only moves to
    done/ once git confirms the merge - and until then the task keeps saying it
    is waiting for one."""
    # The branch recorded at registration, not the file's current `branch:`: the file
    # is not in the CEO's diff, and a line added to it after the commit approval
    # must not point this check at a branch that is already merged.
    declared = state.task_branch(task)
    report = git_state.task_report(task)

    if not report:
        # Absence of evidence is not evidence of a merge. Neither signal was
        # found, so git cannot tell - park and name both. A task moved to done/
        # on this path once hid unmerged code on a sibling's branch for a day.
        # A declared `repo:` that names no known repo searches nothing at all
        # (git_state.repos_for_task), which is a different cause from "searched
        # and found nothing" - say so, and show the value either way.
        repo = state.task_repo(task) or ""
        scope = (f"the task's repo: '{repo}' did not resolve to any known repo, so no "
                 f"repo was searched (check repos in pipeline.json); "
                 if repo and not git_state.repos_for_task(task) else "")
        searched = f" (task repo: '{repo}')" if repo and not scope else ""
        missing = (f"{scope}the declared branch 'origin/{declared}' (and local "
                   f"'{declared}') does not exist in any repo{searched}" if declared else
                   f"{scope}no branch matching this task id exists in any repo{searched}, "
                   f"and the task declares no 'branch:' in its frontmatter")
        run = _merge_wait(task, run, "merge")
        return result("park", task, run,
                      f"Cannot confirm a merge - both signals are missing: (1) carrier "
                      f"branch: {missing}; (2) tagged commit: the main branch carries no "
                      f"'[{task}]' commit. This is UNKNOWN, not merged, so the task stays "
                      f"out of tasks/done/. If its work rides on another task's branch, "
                      f"registration did not record that branch and editing the file now "
                      f"changes nothing (it is frozen in the run row). The CEO decides: "
                      f"the orchestrator re-registers the task with 'branch: <type>/task-NNNN' "
                      f"declared, or retags the carrying commit '[{task}]'; otherwise push "
                      f"the branch.")

    unmerged = [r for r in report if r["in_main"] is not True]
    if unmerged:
        stale = _stale_before_merge(task, run)
        if stale is not None:
            return stale
        where = ", ".join(f"{r['repo']} ({r['branch'] or 'no branch'})" for r in unmerged)
        run = _merge_wait(task, run, "merge")
        who = ("Merge the approved branch into the trunk locally (solo workflow mode)"
               if pretool_gate.workflow_mode() == pretool_gate.WORKFLOW_SOLO else
               "The CEO merges the PR in the web UI")
        return result("park", task, run,
                      f"The main branch does not carry this task yet: {where}. "
                      f"{who}; re-run advance.py afterwards and the file "
                      f"moves to tasks/done/. Do not call this task done meanwhile.")

    moved = state.move_task(task, "done")
    if moved:
        note = "moved to tasks/done/"
    else:
        left = state.task_dir(task)
        if left in ("backlog", "active"):
            # move_task refuses an existing destination, so a leftover copy means
            # BOTH files exist: say so instead of claiming the move happened.
            note = (f"NOT moved: tasks/{left}/{task}.md and tasks/done/{task}.md both "
                    f"exist, so the move was refused; compare them and delete the "
                    f"{left}/ copy by hand")
        else:
            note = "already in tasks/done/"
    try:
        swept = state.move_swarm_subtasks(task)
    except Exception:
        swept = []
    if swept:
        note += f"; swarm subtasks moved too: {', '.join(swept)}"
    repos = ", ".join(r["repo"] for r in report)
    run = _merge_wait(task, run, "")
    return result("done", task, run,
                  f"Merged into main ({repos}). Task done - file {note}."
                  f"{_worktree_cleanup(task)}")


def _worktree_cleanup(task: str) -> str:
    """Remove the merged task's worktree and say what happened ('' when it had none).
    Runs only after the merge is confirmed, the file moved and the run closed, so a
    failed merge never touches the tree. A tree git refuses to remove stays where it
    is and is reported; the task is done either way, and the branch is kept."""
    try:
        gone = worktree.remove(task)
    except Exception as exc:
        return f" Worktree cleanup failed ({type(exc).__name__}: {exc}); remove it by hand."
    if gone["removed"]:
        return " Worktree removed (the branch is kept)."
    if gone["left"]:
        return f" Worktree LEFT in place: {gone['reason']}"
    return ""


# Which run.db flag records each checkpoint's approval (one table, in approvals.py,
# shared with approve.py so the reader and the writer cannot disagree).
APPROVAL_FIELD = approvals.APPROVAL_FIELD


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
    protected branch is still refused, except the trunk itself under the
    one-shot approval recorded by approve.py --trunk-push.

    Every declared checkpoint is returned, in any pipeline. This used to filter
    to the names in APPROVAL_FIELD, which silently dropped the plan flow's `plan`
    and let a hand-run advance.py walk `approval` to `breakdown` (task-0089). A
    name with no approval field is not dropped now - the park branch refuses it."""
    checkpoints = [str(c).lower() for c in (stage_def.get("checkpoints") or [])]
    if pretool_gate.workflow_mode() == pretool_gate.WORKFLOW_SOLO:
        checkpoints = [c for c in checkpoints if c != approvals.PUSH]
    return checkpoints


def start_stage(pipeline: dict, which: str, task_type: str) -> str:
    """The stage a new run of `task_type` starts at: the first one whose
    `skip_for_types` does not name the type (case-insensitive).

    This is where small work skips the build flow's plan step (task-0045), as
    configuration and not as a per-task flag: the CEO's rule is "bugfix and
    techdebt skip it, feature always asks", and approvals.never_granted() still
    refuses the plan at every level. Read once, at registration - a run in
    flight keeps the stage it is on. A stage list that skips everything, or one
    nobody can read, starts at the first stage, which is the strict answer."""
    wanted = str(task_type).strip().lower()
    stage_defs = state.stages(pipeline, which)
    for stage_def in stage_defs:
        skip = stage_def.get("skip_for_types")
        if isinstance(skip, list) and wanted in {str(t).strip().lower() for t in skip}:
            continue
        return str(stage_def.get("name") or "")
    return state.stage_names(pipeline, which)[0]


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
        # Present only when a debt checker raised, so a reader that has never
        # seen this key still parses; an automated reader tells "the gates were
        # checked and clear" from "a gate was skipped" by its presence alone.
        **({"checks_skipped": list(state.CHECKS_SKIPPED)} if state.CHECKS_SKIPPED else {}),
    }, indent=2))
    return 0


def _commits_behind(pipeline: dict, nxt: str, task: str, which: str) -> int:
    """How many trunk commits the task's worktree lacks when `nxt` is the stage that
    asks for the commit approval; 0 everywhere else. Only a task with a usable tree of
    its own is checked (a LEGACY run keeps today's behaviour), and "cannot tell" is
    not stale - the merge gate repeats the check."""
    try:
        if approvals.COMMIT not in stage_checkpoints(state.get_stage(pipeline, nxt, which) or {}):
            return 0
        tree = worktree.work_dir(task)
        return (git_state.behind_trunk(tree) or 0) if tree is not None else 0
    except Exception:
        return 0


def _stale_send_back(conn, task: str, run: dict, behind: int, back: str, budget: int,
                     was: str) -> int:
    """The trunk moved on after this branch was cut: what the gates judged (or what
    would merge) is not the code on the trunk. Send the run back to `back`, the stage
    that runs the suite, keeping the commit approval (the CEO approved the work, not
    the base). `was` says where the run stood. The sends are counted per run: an agent
    that never merges the trunk would loop test, review and stale forever, so the
    `retry_budget`-th one BLOCKS the run for the CEO. Closes `conn`."""
    sent = int(run.get("stale_sendbacks") or 0) + 1
    if sent >= budget:
        state.set_fields(conn, task, stale_sendbacks=sent, stage_status=state.ST_BLOCKED,
                         reason=f"trunk moved {behind} commits; sent back {sent}/{budget} times")
        run = state.get_run(conn, task)
        conn.close()
        return result("blocked", task, run,
                      f"{was}, but {worktree.stale_message(behind)}. The run was already sent "
                      f"back {sent - 1} times for this and the worktree still lacks trunk "
                      f"commits - BLOCKED, needs CEO. Do not auto-retry.")
    state.set_fields(conn, task, stage=back, stage_status=state.ST_IN_PROGRESS,
                     awaiting_human="", retries=0, continuations=0, stale_sendbacks=sent)
    run = state.get_run(conn, task)
    conn.close()
    return result("stale", task, run,
                  f"{was}, but {worktree.stale_message(behind)}. Commit or stash uncommitted "
                  f"work in the worktree first, then merge the trunk there. Back to '{back}' "
                  f"(send-back {sent}/{budget}); re-run advance.py --task {task} afterwards. "
                  f"{worktree.dispatch_line(task)}")


def _commit_binding(task: str, run: dict) -> tuple[str, str]:
    """(binding, "") of the change set a run in its own worktree shows at the commit
    checkpoint, ("", why) when it cannot be read, ("", "") for a run with no tree of its
    own (nothing to bind: it holds the checkout alone)."""
    if not run.get("worktree"):
        return "", ""
    try:
        return pretool_gate.task_binding(task), ""
    except Exception as exc:
        return "", str(exc) or type(exc).__name__


def _tip_in_trunk(task: str) -> bool:
    """The tip of the task's tree is already contained in the trunk: its work is merged
    (the solo flow commits, merges and THEN re-runs advance.py at the commit stage), so
    there is no unmerged change left for an approval to cover. False on any doubt."""
    try:
        tree = worktree.work_dir(task)
        return tree is not None and git_state._is_ancestor(tree, "HEAD", git_state.trunk(tree))
    except Exception:
        return False


def _stale_before_merge(task: str, run: dict) -> int | None:
    """A task that waits for its merge on a tree that lacks trunk commits cannot merge
    (the merge gate refuses it), and nothing else would ever re-test it: send it back
    to `test`, the same way review to ready does. None when it is not stale, cannot be
    told, or the flow has no `test` stage."""
    try:
        tree = worktree.work_dir(task)
        behind = (git_state.behind_trunk(tree) or 0) if tree is not None else 0
        pipeline = state.load_pipeline()
        which = state.run_pipeline(run)
        if not behind or "test" not in state.stage_names(pipeline, which):
            return None
        budget = int(pipeline.get("retry_budget", 3))
    except Exception:
        return None
    return _stale_send_back(state.connect(), task, run, behind, "test", budget,
                            f"{task} is waiting for its merge")


def main() -> int:
    parser = argparse.ArgumentParser(description="Advance a task through the pipeline")
    parser.add_argument("--task", required=True)
    parser.add_argument("--type", default="feature")
    parser.add_argument("--pipeline", choices=[state.BUILD, state.PLAN],
                        help="which flow to start this task on; defaults to the session mode")
    parser.add_argument("--busy", metavar="STAGE",
                        help="mark this stage busy (a subagent was dispatched for it) so the "
                             "Stop hook stops nagging while it works. Free-form, but avoid "
                             "the literal 'handoff': handoff.py --check clears markers with "
                             "that stage once the doc validates, and would clear this one too")
    parser.add_argument("--idle", action="store_true",
                        help="clear the busy marker (the subagent returned)")
    args = parser.parse_args()

    # Busy marker bookkeeping - no FSM transition, so handle it and leave.
    # advance.py brackets its own long gates with busy.hold(); a dispatched
    # subagent is the same situation seen from the orchestrator's side, and it
    # outlives this process, so it is acquired here and released by --idle.
    if args.busy:
        try:
            busy.acquire(args.task, args.busy, GATE_TIMEOUT)
        except (ValueError, OSError) as exc:
            print(json.dumps({
                "action": "error", "task": args.task,
                "message": f"Busy marker NOT written: {exc}. The Stop hook keeps driving "
                           f"this task.",
            }, indent=2))
            return 1
        owner = busy.read(args.task).owner_pid
        ends = (f"when session pid {owner} exits, or in {GATE_TIMEOUT}s" if owner else
                f"in {GATE_TIMEOUT}s only (no session pid in the environment, so nothing "
                f"else can end it)")
        print(json.dumps({
            "action": "busy", "task": args.task, "stage": args.busy,
            "message": f"Busy marker written for stage '{args.busy}'; it ends {ends}. The "
                       f"Stop hook stays quiet on {args.task} until then. Clear it when the "
                       f"subagent returns: python .claude/tools/pipeline/advance.py --task "
                       f"{args.task} --idle.",
        }, indent=2))
        return 0
    if args.idle:
        busy.release(args.task)
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
            human = refusal_human(args.task, refusal)
            print(json.dumps({"action": "refused", "task": args.task,
                              "message": refusal, **({"human": human} if human else {})},
                             indent=2))
            return 0
        begin = start_stage(pipeline, which, args.type)
        tree = _register_tree(args.task, args.type, which)
        if tree is not None and not tree["ok"]:
            conn.close()
            print(json.dumps({"action": "refused", "task": args.task,
                              "message": f"{args.task}: worktree not created - {tree['error']}"
                                         + (f". Run by hand: {tree['manual']}" if tree["manual"] else "")},
                             indent=2))
            return 0
        try:
            run = state.create_run(conn, args.task, args.type, begin, which,
                                   worktree=tree["path"] if tree else "")
        except Exception:
            if tree is not None:
                worktree.discard(tree)
            raise
        finally:
            conn.close()
        # Taking a task on IS the move out of the queue - there is no separate
        # status to flip, and nothing lands in active/ that is not really moving.
        state.move_task(args.task, "active")
        skipped = (f" ({first}..{begin} skipped for type '{args.type}': skip_for_types in "
                   f"pipeline.json)" if begin != first else "")
        frozen = ", ".join(f"{k}={run.get(k) or '-'}" for k in state.FROZEN_FIELDS)
        refused = state.invalid_frozen_refs(args.task)
        if refused:
            frozen += (f". NOT frozen, stored empty because the value is not a plain name "
                       f"([A-Za-z0-9._/-], no leading '-', no '..'): {', '.join(refused)}")
        return result("started", args.task, run,
                      f"started at '{begin}' on the {which} flow{skipped} "
                      f"(owner: {stage_owner(pipeline, begin, args.task, which)}). "
                      f"Task-file fields frozen at registration, read from the run row "
                      f"from now on ('-' = none): {frozen}. "
                      f"Do the stage work, then re-run advance.py --task {args.task}."
                      + (f" {worktree.dispatch_line(args.task)}" if tree else ""))

    cur = run["stage"]

    if cur == "done":
        conn.close()
        if run.get("stage_status") == state.ST_ABANDONED:
            # Dropped by the CEO: nothing merged and nothing is owed, so no git read
            # and no merge park.
            return result("done", args.task, run,
                          f"{args.task} was abandoned ({run.get('abandon_reason') or 'no reason'})"
                          f" - nothing to merge.")
        return finish_or_wait_for_merge(args.task, run)

    # A paused run is not advanced: running its gate here would rewrite its
    # stage_status and silently end the pause. The record says why it was set
    # aside and the only way out is the explicit resume.
    if state.is_paused(run):
        conn.close()
        return result("paused", args.task, run,
                      f"{args.task} is PAUSED at stage '{cur}' ({run.get('pause_kind') or '?'}: "
                      f"{run.get('pause_reason') or 'no reason recorded'}). Nothing is advanced "
                      f"while it is paused. Resume it first: python "
                      f".claude/tools/pipeline/approve.py --task {args.task} --resume")

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
        state.set_fields(conn, args.task, stage_status=state.ST_BLOCKED,
                         reason=f"stage '{cur}' is not part of the {which} flow")
        run = state.get_run(conn, args.task)
        conn.close()
        return result("blocked", args.task, run,
                      f"stage '{cur}' is not part of the {which} flow, so no gate was run. "
                      f"Configured stages: {', '.join(names)}. The run is stranded - a "
                      f"pipeline.json stage list that changed under a live run, or an earlier "
                      f"NULL write. Recover with: python .claude/tools/pipeline/approve.py "
                      f"--task {args.task} --reject --reason \"<why>\" (sends it back to "
                      f"'{first}'), or restore '{cur}' in pipelines.{which}.stages.")

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
        bound = refused_auto = unbound = ""
        # The first checkpoint still unapproved is the one to park on; none left
        # means the tail of the stage is complete, so route to the merge check -
        # in solo mode the local merge is what satisfies it, in pr mode the
        # CEO's merge of the pushed branch. Same check either way.
        # A checkpoint name with no approval field can never be approved, so it
        # stays pending (fail-closed) and the park message names it.
        pending = next((c for c in checkpoints
                        if not run.get(APPROVAL_FIELD.get(c, ""))), None)
        if (pending is None and approvals.COMMIT in checkpoints and run.get("worktree")
                and run.get("commit_approved")):
            # The commit approval is bound to the change set the CEO read. A tree that
            # no longer holds that change set (an overlapping sync, a late edit) or
            # cannot be read voids the approval and parks the run again, so the diff
            # is shown a second time.
            current, why = _commit_binding(args.task, run)
            # Merged and clean (the solo flow commits, merges and then re-runs this at the
            # commit stage): the trunk carries the work, so nothing is left to cover.
            merged = current == pretool_gate.EMPTY_BINDING and _tip_in_trunk(args.task)
            if not merged and (why or current != str(run.get("approved_tree") or "")):
                state.set_fields(conn, args.task, commit_approved=0, approved_tree="",
                                 awaiting_human="")
                run = state.get_run(conn, args.task)
                pending = approvals.COMMIT
        nxt = state.next_stage(pipeline, cur, which) or "done"
        if pending is None:
            # The stage after the checkpoints: `done` for build's `ready`, the
            # breakdown for the plan flow's `approval`. Both used to be `done`.
            fields = {"stage": nxt, "awaiting_human": "",
                      "stage_status": (state.ST_GATE_PASSED if nxt == "done"
                                       else state.ST_IN_PROGRESS)}
            if nxt != "done":
                fields.update(retries=0, continuations=0)
            state.set_fields(conn, args.task, **fields)
        else:
            fresh_park = run.get("awaiting_human") != pending
            fields = {"awaiting_human": pending}
            if pending == approvals.COMMIT and run.get("worktree"):
                # What the CEO is shown is what the approval will be bound to: recorded
                # at every park, and a park whose tree moved since the last one counts
                # as fresh, so the diff is delivered again.
                bound, unbound = _commit_binding(args.task, run)
                fields["approved_tree"] = bound
                fresh_park = fresh_park or bound != str(run.get("approved_tree") or "")
            # approvals.granted() answers the whole question: the level's GRANTS
            # set OR the stage list approves, and never_granted() refuses the
            # plan, the trunk push and (here) the push ahead of both. Do NOT
            # re-test `pending in auto` in front of it - that made the stage
            # list mandatory, so the level dial could never approve anything on
            # its own and manual/assisted/auto behaved identically at `ready`
            # with the shipped (empty) config.
            if pending in APPROVAL_FIELD and approvals.granted(pending, sorted(auto)):
                if pending == approvals.COMMIT and run.get("worktree") and not bound:
                    # Fail closed: the level never grants an approval it cannot bind.
                    refused_auto = unbound
                else:
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
            "commit": ("Checkpoint 'commit' auto-approved (approvals level, or auto_approve "
                       "in pipeline.json). "
                       f"Stage files and perform the git commit now, then {after_commit}"
                       if approvals.granted(approvals.COMMIT, sorted(auto)) and not refused_auto
                       else
                       "Review passed. Stage files, surface the full diff, and wait for "
                       "CEO commit approval (approve.py --gate commit). git commit is "
                       f"hook-blocked until then. After the commit: {after_commit}"),
            "push": ("Checkpoint 'push' auto-approved. Perform the git push now, "
                     "then re-run advance.py."
                     if approvals.granted(approvals.PUSH, sorted(auto)) else
                     "Committed. Wait for CEO push approval (approve.py --gate push). "
                     "git push is hook-blocked until then."),
            "plan": ("Plan awaiting CEO approval. Present it, and wait for the CEO to "
                     "approve (approve.py --gate plan). advance.py will not leave "
                     f"'{cur}' until that is recorded, at any approvals level."),
        }
        if run["stage"] == "done":
            return finish_or_wait_for_merge(args.task, run)
        if pending is None:
            return result("advanced", args.task, run,
                          f"checkpoint(s) approved: '{cur}' -> '{nxt}' "
                          f"(owner: {stage_owner(pipeline, nxt, args.task, which)}). Proceed.")
        message = msgs.get(
            run["awaiting_human"],
            f"awaiting human: checkpoint '{pending}' has no approval field and cannot "
            f"be recorded - fix the stage's checkpoints in pipeline.json.")
        if refused_auto:
            message += (f" The level would approve this commit, but the change set of the "
                        f"worktree could not be read ({refused_auto}), so it was NOT approved: "
                        f"an approval is never recorded without the tree it covers. The CEO "
                        f"approves it.")
        elif run["awaiting_human"] == "commit" and bound:
            message += (f" The approval binds change set {bound[:12]}: a byte changed after the "
                        f"diff is shown (formatter, late edit) voids it, and advance.py parks "
                        f"the task again.")
        # The one place the CEO is handed the commit diff (FR-53/54). deliver()
        # answers "" unless the run is genuinely parked on an unapproved commit,
        # so an auto-approved commit gets nothing here.
        if run["awaiting_human"] == "commit":
            message += diff_checkpoint.deliver(args.task, inject=fresh_park)
        return result("park", args.task, run, message)

    # 3. Gated autonomous stage - run the exit gate deterministically.
    with busy.hold(args.task, cur, GATE_TIMEOUT):
        gate = run_gate(pipeline, cur, args.task, which)

    if not gate["configured"]:
        state.set_fields(conn, args.task, stage_status=state.ST_BLOCKED,
                         reason=f"stage '{cur}' gate is not configured")
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
            state.set_fields(conn, args.task, stage_status=state.ST_BLOCKED,
                             reason=f"stage '{cur}' passed its gate but the {which} flow has no next stage")
            run = state.get_run(conn, args.task)
            conn.close()
            return result("blocked", args.task, run,
                          f"stage '{cur}' passed its gate, but the {which} flow defines no "
                          f"stage after it and '{cur}' is not the terminal 'done' stage. Fix "
                          f"pipelines.{which}.stages in .agentry/pipeline.json (the list must "
                          f"end with 'done'), then re-run advance.py --task {args.task}.")
        behind = _commits_behind(pipeline, nxt, args.task, which)
        if behind:
            return _stale_send_back(conn, args.task, run, behind,
                                    "test" if "test" in names else cur, budget,
                                    f"gate passed at '{cur}'")
        state.set_fields(conn, args.task, stage=nxt, stage_status=state.ST_IN_PROGRESS,
                         retries=0, continuations=0)
        run = state.get_run(conn, args.task)
        conn.close()
        owner = stage_owner(pipeline, nxt, args.task, which)
        # A checkpoint stage is not driven by the Stop hook, so nothing would ever
        # tell the orchestrator to park the plan for the CEO. Say it here.
        nxt_def = state.get_stage(pipeline, nxt, which) or {}
        then = (f" '{nxt}' awaits the CEO's plan approval: re-run advance.py --task "
                f"{args.task} to park it." if approvals.PLAN in stage_checkpoints(nxt_def) else "")
        return result("advanced", args.task, run,
                      f"gate passed: '{cur}' -> '{nxt}' (owner: {owner}). Proceed.{then}")

    retries = run["retries"] + 1
    if retries >= budget:
        state.set_fields(conn, args.task, stage_status=state.ST_BLOCKED, retries=retries,
                         reason=f"gate failed {retries}/{budget} times at '{cur}'")
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
