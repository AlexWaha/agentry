#!/usr/bin/env python3
"""Record a human checkpoint decision for a task.

The orchestrator runs this only after the CEO explicitly approves in chat. It
flips the deterministic flag that pretool_gate.py checks before allowing
`git commit` / `git push`. `--reject` sends the task back: to `implement` from
the commit checkpoint, to the plan step while the plan is still unapproved. It
needs `--reason`: the reason is what the event journal records against the
rejection, and a rejection nobody explained cannot be learned from.

Trust note: nothing binds this to a real user message; it prevents accidental
skips and out-of-order commits, not a model that deliberately self-approves. The
hard stop is the Stop hook surfacing the diff and parking the task.

CLI:
    python approve.py --task task-0007 --gate commit
    python approve.py --task task-0007 --gate push
    python approve.py --task task-0007 --gate plan
    python approve.py --task task-0007 --reject --reason "diff touches an unrelated file"
    python approve.py --task task-0007 --pause "CEO force majeure: task-0058 first"
    python approve.py --task task-0007 --resume
    python approve.py --task task-0007 --abandon --reason "CEO dropped the work"
    python approve.py --trunk-push [--remote NAME]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import approvals
import git_state
import leak_guard
import pretool_gate
import state
import stop_gate

REASON_MAX = 500
# Control, format, line and paragraph separator, private use, unassigned and
# surrogate characters: none belongs in text echoed into a later agent's context.
UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cn", "Cs"})
PAUSE_CLEAN = "clean"
PAUSE_COMMITTED = "committed"
GIT_TIMEOUT = 20
# Pipeline bookkeeping, not task work: the run store and the task files move
# while a task is being set aside, so they never count as uncommitted work.
BOOKKEEPING_PREFIX = ".agentry/"


def clean_reason(text: str | None) -> str:
    """A reason as it is stored and journaled: control and format characters
    (newlines, escapes, bidi overrides) become spaces and at most REASON_MAX
    characters are kept. The text is echoed into later agents' context, so it
    must be bounded and carry no line breaks or terminal sequences."""
    flat = "".join(" " if unicodedata.category(ch) in UNSAFE_CATEGORIES else ch for ch in text or "")
    return flat.strip()[:REASON_MAX].strip()


def trunk_name() -> str:
    """The configured trunk, with the template placeholder resolved - the same
    reader the leak guard compares against (leak_guard.trunk_name)."""
    return leak_guard.trunk_name()


def record_trunk_push(remote: str = pretool_gate.TRUNK_PUSH_DEFAULT_REMOTE) -> int:
    """Record the CEO's approval to publish the trunk once, to `remote`.

    The remote is part of the approval: a push of the trunk to any other remote
    name or URL is a different push and does not spend or use it.

    Solo mode only. In `pr` mode the trunk is reached by a pull request, so a
    marker there would be an approval for a step that has no legitimate
    spelling - it is refused at write time rather than written and ignored."""
    mode = pretool_gate.workflow_mode()
    if mode != pretool_gate.WORKFLOW_SOLO:
        print(json.dumps({"error": (
            f"workflow mode is '{mode}' - a push to the trunk is refused there in every "
            f"case, because the trunk is reached by a pull request. Only "
            f"'{pretool_gate.WORKFLOW_SOLO}' mode can publish the trunk by push.")}))
        return 2
    trunk = trunk_name()
    if trunk not in pretool_gate.PUSH_PROTECTED_ALWAYS:
        print(json.dumps({"error": (
            f"the configured trunk is '{trunk}', and the gate grants this approval for "
            f"{' / '.join(pretool_gate.PUSH_PROTECTED_ALWAYS)} only. Writing the marker "
            f"would record an approval no push can ever use.")}))
        return 2
    if not remote.strip() or remote != remote.strip() or remote.startswith("-"):
        print(json.dumps({"error": f"'{remote}' is not a remote name the gate can match: give "
                                   f"the remote exactly as the push command will name it."}))
        return 2
    path = pretool_gate.trunk_push_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "trunk": trunk,
        "remote": remote,
        "lane": state.LANE,
        "approved": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    state.emit("approved", "", outcome="trunk_push", detail={"trunk": trunk, "remote": remote})
    print(json.dumps({"ok": True, "message": (
        f"trunk push approved - ONE push to '{trunk}' on remote '{remote}' is now permitted "
        f"in lane '{state.LANE or 'default'}'; that push consumes the approval."),
        "marker": str(path)},
        indent=2))
    return 0


def _git(repo: Path, *args: str) -> tuple[int, str]:
    """(exit code, stdout) of one git call. Unlike git_state._git the output is
    NOT stripped: the first `status --porcelain` line starts with a space."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, encoding="utf-8",
            errors="replace", timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout


def assess_pause(task: str) -> tuple[str, str]:
    """(kind, problem) for setting `task` aside.

    `problem` is non-empty when the pause must be refused: uncommitted work in a
    repo the task lives in, or a git state this cannot read (a pause that cannot
    show the tree is clean would be a guess). Otherwise `kind` is PAUSE_COMMITTED
    when the task's branch already carries commits beyond the trunk, and
    PAUSE_CLEAN when it carries none - the two cases a later reader must be able
    to tell apart ("nothing to commit" versus "forgot to commit")."""
    kind = PAUSE_CLEAN
    for repo in git_state.repos_for_task(task) or [state.ROOT]:
        code, out = _git(repo, "status", "--porcelain")
        if code != 0:
            return "", (f"the git state of {repo} could not be read, so it cannot be shown "
                        f"that {task} has no uncommitted work")
        dirty = [path for path in (line[3:].split(" -> ")[-1].strip('"')
                                   for line in out.splitlines() if line.strip())
                 if not path.replace("\\", "/").startswith(BOOKKEEPING_PREFIX)]
        if dirty:
            shown = ", ".join(dirty[:5]) + (f" (+{len(dirty) - 5} more)" if len(dirty) > 5 else "")
            return "", (f"{task} has uncommitted work in {repo}: {shown}. Commit it first - "
                        f"force-majeure step 1 in rules/orchestration.md (the CEO's declaration "
                        f"is the commit approval: approve.py --task {task} --gate commit) - then "
                        f"pause again.")
        ref = git_state.branch_for(repo, task)
        if not ref:
            continue
        code, count = _git(repo, "rev-list", "--count", f"{git_state.trunk(repo)}..{ref}")
        if code != 0 or not count.strip().isdigit():
            return "", (f"the commits of {ref} in {repo} could not be counted, so the pause "
                        f"record could not say whether {task} has any")
        if int(count) > 0:
            kind = PAUSE_COMMITTED
    return kind, ""


def pause_run(conn, run: dict, reason: str) -> tuple[str, str]:
    """Set a run aside. (message, "") on success, ("", error) on refusal.

    Touches stage_status and the three pause columns only: `stage`, `retries`,
    `continuations` and every approval flag are left exactly as they are, so a
    task paused after its commit checkpoint is not asked to re-earn it and one
    paused before it does not look approved."""
    task = run["task"]
    reason = clean_reason(reason)
    if not reason:
        return "", "a pause needs a reason, so a later reader can tell a preemption from a failure"
    if run["stage"] == "done":
        return "", f"{task} is already done - there is nothing to pause."
    if state.is_paused(run):
        return "", (f"{task} is already paused ({run.get('pause_kind')}: "
                    f"{run.get('pause_reason')}).")
    kind, problem = assess_pause(task)
    if problem:
        return "", problem
    state.set_fields(conn, task, stage_status=state.ST_PAUSED,
                     paused_status=run["stage_status"] or state.ST_IN_PROGRESS,
                     pause_reason=reason, pause_kind=kind)
    detail = ("it has no commits and no edits - nothing was written yet" if kind == PAUSE_CLEAN
              else "its branch carries commits and the tree is clean")
    return (f"{task} paused at '{run['stage']}': {detail}. It is no longer driven and does "
            f"not hold the working tree. Resume: approve.py --task {task} --resume."), ""


def resume_run(conn, run: dict) -> tuple[str, str]:
    """Bring a paused run back at the stage it was set aside at. (message, "") on
    success, ("", error) on refusal. `retries`, `continuations` and the approval
    flags are untouched."""
    task = run["task"]
    if not state.is_paused(run):
        return "", f"{task} is not paused (stage_status={run['stage_status']})."
    pipeline = state.load_pipeline()
    which = state.run_pipeline(run)
    if run["stage"] in state.editing_stages(pipeline, which):
        for other in state.all_runs(conn):
            if (other["task"] != task and other["stage"] != "done"
                    and not state.is_paused(other)
                    and other["stage"] in state.editing_stages(pipeline, state.run_pipeline(other))):
                return "", (f"{other['task']} holds an editing stage ('{other['stage']}'), and at "
                            f"most one task may. Finish or pause {other['task']} first, then "
                            f"resume {task}.")
    restored = run.get("paused_status") or state.ST_IN_PROGRESS
    # An approval flag on a run that has not reached its checkpoint can only be
    # the force-majeure commit approval (a normal one is recorded AT the
    # checkpoint). Left set, the finished work would skip the CEO's read of the
    # diff, so it is cleared here. A run paused at a checkpoint keeps its flags.
    stale = {}
    if (run["stage"] in state.editing_stages(pipeline, which)
            and run["stage"] not in state.checkpoint_stages(pipeline, which)):
        stale = {f: 0 for f in ("commit_approved", "push_approved") if run[f]}
    state.set_fields(conn, task, stage_status=restored, paused_status="", pause_reason="",
                     pause_kind="", **stale)
    note = (f" Cleared {', '.join(stale)}: it was recorded before the checkpoint, so the "
            f"finished work needs its own approval." if stale else "")
    return (f"{task} resumed at '{run['stage']}' (status {restored}); retries and "
            f"continuations were kept.{note} Re-run advance.py when the stage work is "
            f"done."), ""


def live_dependents(task: str) -> list[str]:
    """Backlog tasks whose `depends_on:` names `task`, sorted. Empty on any error."""
    found: list[str] = []
    try:
        for path in sorted(state.TASK_DIRS["backlog"].glob("task-*.md")):
            head = stop_gate.frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            raw = stop_gate.DEPENDS_RE.search(head)
            if raw and task in stop_gate.TASK_ID_RE.findall(raw.group(1)):
                found.append(path.stem)
    except OSError:
        pass
    return found


def abandon_run(conn, run: dict, reason: str) -> tuple[str, str]:
    """Retire a run on the CEO's decision, without a merge. (message, "") on success,
    ("", error) on refusal; every refusal comes before any write.

    The run goes to stage `done` with stage_status `abandoned`, the task file moves
    to done/ stamped `abandoned: <reason>`, and the commit, push and plan approvals
    are cleared (the gates read them; an abandoned run must not leave a standing
    permission). A run parked at `done` awaiting its merge (file still in active/)
    can be abandoned; a merged one cannot. No git command runs here - the branch,
    its commits and the working tree stay for the CEO to deal with."""
    task = run["task"]
    reason = clean_reason(reason)
    if not reason:
        return "", "--abandon needs --reason \"<why>\": the journal records why the work was dropped"
    if run["stage"] == "done" and run.get("awaiting_human") != "merge":
        how = "already abandoned" if run.get("stage_status") == state.ST_ABANDONED else "already merged"
        return "", f"{task} is {how} - there is nothing to abandon."
    if state.task_dir(task) in ("backlog", "active") and not state.move_task(
            task, "done", abandoned=reason):
        return "", (f"{task}.md could not be moved to tasks/done/: a different file already sits "
                    f"there. Compare the two and remove one by hand, then abandon again.")
    state.set_fields(conn, task, stage="done", stage_status=state.ST_ABANDONED,
                     awaiting_human="", paused_status="", pause_reason="", pause_kind="",
                     commit_approved=0, push_approved=0, plan_approved=0,
                     abandon_reason=reason, event="abandoned", reason=reason)
    dependents = live_dependents(task)
    after = (f"Dependents are ready at once: {', '.join(dependents)} (at the auto level they "
             f"start unattended)." if dependents else "No backlog task depends on it.")
    return (f"{task} abandoned at '{run['stage']}': the run is closed and its file is in "
            f"tasks/done/. Git was not touched: the branch, its commits and the working tree "
            f"are left as they were - clean the tree before the next task. {after}"), ""


def reject_target(pipeline: dict, run: dict) -> tuple[str, bool]:
    """(stage a rejected run goes back to, whether its plan approval is cleared).

    Every flow sends a rejection to its first stage and clears the plan approval,
    with one exception that exists for the build flow's plan step (task-0045): a
    build run that is already PAST its plan checkpoint was rejected on its code,
    at the commit checkpoint, not on its plan. It goes to the stage after the
    checkpoint (`implement`) and keeps the approval, so the CEO is not asked to
    sign the same approach twice. A run still in the plan phase stays there and
    loses nothing but the plan. A flow without a plan checkpoint, or a stage the
    config no longer knows, takes the old answer."""
    which = state.run_pipeline(run)
    names = state.stage_names(pipeline, which)
    gate = pretool_gate.plan_checkpoint_index(pipeline, which)
    if which != state.BUILD or gate is None or run["stage"] not in names:
        return names[0], True
    if names.index(run["stage"]) <= gate:
        return names[0], True
    return (names[gate + 1] if gate + 1 < len(names) else names[0]), False


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a checkpoint decision")
    parser.add_argument("--task")
    parser.add_argument("--gate", choices=list(approvals.APPROVAL_FIELD))
    parser.add_argument("--reject", action="store_true",
                        help="send the task back (implement, or plan while the plan is "
                             "unapproved), clearing approvals")
    parser.add_argument("--reason", metavar="TEXT",
                        help="with --reject or --abandon: why the CEO sent the task back or "
                             "dropped it (required); "
                             "recorded in the event journal, capped at 500 characters, "
                             "control characters become spaces")
    parser.add_argument("--pause", metavar="REASON",
                        help="set the task aside (CEO force majeure): refused while it has "
                             "uncommitted work; records REASON and whether it had commits")
    parser.add_argument("--resume", action="store_true",
                        help="bring a paused task back at the stage it was paused at")
    parser.add_argument("--abandon", action="store_true",
                        help="retire the task without a merge (CEO decision): run closed, file "
                             "moved to done/, git untouched; needs --reason")
    parser.add_argument("--trunk-push", action="store_true",
                        help="solo mode only: allow ONE push of the trunk to the remote")
    parser.add_argument("--remote", default=pretool_gate.TRUNK_PUSH_DEFAULT_REMOTE,
                        help="with --trunk-push: the remote name the push will use "
                             "(default origin); a push naming another remote or a URL is refused")
    args = parser.parse_args()

    if args.trunk_push:
        return record_trunk_push(args.remote)
    if not args.task:
        print(json.dumps({"error": "need --task task-XXXX (or --trunk-push)"}))
        return 2

    if args.abandon and (args.reject or args.gate or args.pause is not None or args.resume):
        print(json.dumps({"error": "--abandon stands alone: combine it with none of --gate, "
                                   "--reject, --pause, --resume"}))
        return 2

    conn = state.connect()
    run = state.get_run(conn, args.task)
    if run is None:
        conn.close()
        print(json.dumps({"error": f"no active run for {args.task}"}))
        return 1

    if run.get("stage_status") == state.ST_ABANDONED and (args.reject or args.gate):
        conn.close()
        print(json.dumps({"error": f"{args.task} was abandoned - it takes no further decision."}))
        return 2

    if args.abandon:
        msg, err = abandon_run(conn, run, args.reason)
        if err:
            conn.close()
            print(json.dumps({"error": err}))
            return 2
        run = state.get_run(conn, args.task)
        conn.close()
        print(json.dumps({"ok": True, "message": msg, "run": run}, indent=2))
        return 0

    if args.pause is not None or args.resume:
        if args.reject or args.gate or (args.pause is not None and args.resume):
            conn.close()
            print(json.dumps({"error": "--pause and --resume stand alone: combine neither "
                                       "with each other, --gate nor --reject"}))
            return 2
        msg, err = (resume_run(conn, run) if args.resume
                    else pause_run(conn, run, args.pause))
        if err:
            conn.close()
            print(json.dumps({"error": err}))
            return 2
        run = state.get_run(conn, args.task)
        conn.close()
        print(json.dumps({"ok": True, "message": msg, "run": run}, indent=2))
        return 0

    if args.reject:
        reason = clean_reason(args.reason)
        if not reason:
            conn.close()
            print(json.dumps({"error": "--reject needs --reason \"<why>\": the reason is "
                                       "recorded against the rejection, and a task sent back "
                                       "without one cannot be learned from"}))
            return 2
        # The run's own flow: a rejected plan goes back to its first planning
        # stage, not to the build flow's `implement`.
        first, clear_plan = reject_target(state.load_pipeline(), run)
        plan_reset = {"plan_approved": 0} if clear_plan else {}
        state.set_fields(conn, args.task, stage=first, stage_status=state.ST_IN_PROGRESS,
                         awaiting_human="", commit_approved=0, push_approved=0, **plan_reset,
                         retries=0, continuations=0,
                         paused_status="", pause_reason="", pause_kind="",
                         event="rejected", reason=reason)
        msg = f"rejected - {args.task} sent back to '{first}'."
    elif args.gate in approvals.APPROVAL_FIELD and run[approvals.APPROVAL_FIELD[args.gate]]:
        # Recorded once, durably. A second call changes nothing, so a repeated
        # approval cannot be mistaken for a second decision.
        msg = f"{args.gate} already approved for {args.task} - nothing to do."
    elif args.gate == "commit":
        state.set_fields(conn, args.task, commit_approved=1)
        msg = f"commit approved for {args.task} - git commit now permitted."
    elif args.gate == "push":
        state.set_fields(conn, args.task, push_approved=1)
        msg = f"push approved for {args.task} - git push now permitted."
    elif args.gate == "plan":
        state.set_fields(conn, args.task, plan_approved=1)
        msg = (f"plan approved for {args.task} - edits outside .agentry/ are allowed again. "
               f"Re-run advance.py and the run leaves its approval stage.")
    else:
        conn.close()
        print(json.dumps({"error": "need --gate commit|push|plan or --reject"}))
        return 2

    run = state.get_run(conn, args.task)
    conn.close()
    print(json.dumps({"ok": True, "message": msg, "run": run}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
