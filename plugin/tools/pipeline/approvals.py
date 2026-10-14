#!/usr/bin/env python3
"""How much the pipeline may decide without asking.

Separate axis from the mode: the mode picks WHICH stages run, this picks how
many of their checkpoints the User still has to answer. Two dials rather than one
matrix, because at three in the morning a matrix is unreadable.

Levels, each a superset of the one before (default grants, what passes WITHOUT
asking the User):

  manual    ask at every checkpoint. Which task to take, the commit, the push.
            The default, and the right setting while someone is at the keyboard.
  assisted  commit without asking. The User starts each task, and the conveyor
            stops after the current one. `take` is not granted here: taking
            the next task is the one thing that tells `auto` apart from
            `assisted` (task-0050).
  auto      the same, plus taking the next ready backlog task on its own once
            the current one is done (the Stop hook's pickup reads `take`), plus
            the push of the WORK branch. For a night run.

What is withheld from every level, whatever the table above says:

  - everything listed in pipeline.json `approvals_never_granted`, read by
    never_granted(). This project lists `push` and `trunk_push`, so `auto` here
    stops at the push exactly as it always did: the User asked for that decision
    to stay theirs, every time, at every level (rules/git-workflow.md, "Push is
    never automatic"). A push is the moment work leaves the machine. A config
    that omits the list, or cannot be read, withholds both too (fail-safe).
  - `trunk_push` and `plan`, always, configured or not (ALWAYS_WITHHELD). The
    trunk push publishes the trunk and is recorded per push by the User in chat
    (approve.py --trunk-push), and a plan is the User's signature: every task
    below an approved plan inherits its mistakes.
  - the merge into the main branch. No level grants it; who performs it follows
    `workflow.mode`. In `pr` mode the agent stops at a pushed branch; the User
    opens and merges the PR in the web UI. In `solo` mode the pipeline merges
    locally once the commit is approved; the approved commit permits that
    merge, not the level. At `assisted` and `auto` the level sets the commit
    approval that permits the solo local merge (rules/git-workflow.md,
    "Workflow modes").
  - moving a task to done. That follows the merge above and is confirmed by git,
    not by an approval: the human's merge in `pr` mode, the local merge in `solo`.
  - approving a plan, which is what lets the breakdown into tasks begin.
  - answering the questions raised while planning. That information exists only
    in the User's head.

Because of these, an overnight run is bounded by the dependency chain:
independent tasks run all night, a chain of dependent ones advances by exactly
one, since the next task needs its predecessor in the main branch. In `solo`
mode an unattended session is refused the local merge (`check_unattended`), so
the bound is the same.

A per-stage `auto_approve` list in pipeline.json is layered on top and wins for
every checkpoint EXCEPT the withheld ones - listing `push` there does nothing
while `approvals_never_granted` lists it, by design.

Fail-open: anything unreadable resolves to `manual`, the strictest level. A
broken file can never hand the agent more authority than it had.
"""

from __future__ import annotations

import sys

import state

ROOT = state.ROOT
# Suffixed per lane from the single accessor in state.py - see state.LANE. One
# conveyor per lane means one approval level per lane: a planning session set to
# `auto` must not hand the building session its commit approvals.
APPROVALS_PATH = ROOT / ".agentry" / "state" / f"approvals{state.LANE_SUFFIX}"

MANUAL = "manual"
ASSISTED = "assisted"
AUTO = "auto"
LEVELS = (MANUAL, ASSISTED, AUTO)

# Checkpoint names, matching the awaiting_human values and stage semantics.
TAKE = "take"              # move a task out of the queue into work
COMMIT = "commit"
PUSH = "push"
PLAN = "plan"              # the User signs off a plan (plan flow, stage `approval`)
TRUNK_PUSH = "trunk_push"   # solo mode: publish the trunk itself, once

GRANTS = {
    MANUAL: frozenset(),
    ASSISTED: frozenset({COMMIT}),
    AUTO: frozenset({TAKE, COMMIT, PUSH}),
}

# Withheld from every level and every per-stage auto_approve list, in code, so
# configuration can only add to it. TRUNK_PUSH is the one step that publishes
# the trunk, so it is recorded per push by the User in chat (approve.py
# --trunk-push), never handed over wholesale by a level. PLAN is the User's
# signature on a plan: closer to a push than to a commit, because every task
# below an approved plan inherits its mistakes.
ALWAYS_WITHHELD = frozenset({TRUNK_PUSH, PLAN})

# What a config that names no usable list withholds. The push was in GRANTS[AUTO]
# once while git-workflow.md forbade it - the rule was written and not enforced,
# so the orchestrator followed the permissive document. A missing or unreadable
# list therefore resolves to the strict answer, never to "grant everything".
DEFAULT_NEVER_GRANTED = frozenset({PUSH, TRUNK_PUSH})

# The only names `approvals_never_granted` may carry.
CHECKPOINTS = frozenset({TAKE, COMMIT, PUSH, PLAN, TRUNK_PUSH})

# Which run.db column records each checkpoint's approval. One table for every
# reader: advance.py parks on it, approve.py writes it.
APPROVAL_FIELD = {COMMIT: "commit_approved", PUSH: "push_approved", PLAN: "plan_approved"}

DESCRIPTIONS = {
    MANUAL: "ask at every checkpoint: which task, the commit, the push",
    ASSISTED: "commit unasked, but you start each task and the next one waits for you. "
              "The push is never granted",
    AUTO: "commit unasked and take the next ready task on its own. The push of the work "
          "branch is granted only where pipeline.json approvals_never_granted omits it",
}


def never_granted() -> frozenset:
    """Checkpoints this project never grants at any level: ALWAYS_WITHHELD plus
    pipeline.json `approvals_never_granted`. Stricter only, never looser.

    Fail-SAFE: a missing key, a value that is not a list, a list with an entry that
    is not exactly one checkpoint name (a typo, `"push,trunk_push"`, null, a nested
    list), or a config that cannot be read all fall back to DEFAULT_NEVER_GRANTED
    (state.load_pipeline() returns {} for an unreadable file). A broken config therefore never hands the agent
    more authority than the shipped one. Only an explicit list changes the
    answer, and an explicit empty list is the one way to let `auto` push the work
    branch."""
    configured = state.load_pipeline().get("approvals_never_granted")
    if not isinstance(configured, list) or not all(
            isinstance(c, str) and c in CHECKPOINTS for c in configured):
        return ALWAYS_WITHHELD | DEFAULT_NEVER_GRANTED
    return ALWAYS_WITHHELD | frozenset(configured)


def read() -> str:
    try:
        value = APPROVALS_PATH.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return MANUAL
    return value if value in LEVELS else MANUAL


def write(level: str) -> None:
    APPROVALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS_PATH.write_text(level + "\n", encoding="utf-8")


def granted(checkpoint: str, stage_auto: list | None = None) -> bool:
    """Whether this checkpoint may pass without the User.

    `stage_auto` is the stage's own `auto_approve` list from pipeline.json; it
    is layered on top of the level, so a single checkpoint can be automated
    without raising the dial for everything else. never_granted() wins over both."""
    if checkpoint in never_granted():
        return False
    if stage_auto and checkpoint in {str(c).lower() for c in stage_auto}:
        return True
    return checkpoint in GRANTS[read()]


def _main() -> int:
    args = [a for a in sys.argv[1:] if a.strip()]

    if not args or args[0] in ("--show", "show"):
        level = read()
        print(f"{level}: {DESCRIPTIONS[level]}")
        return 0

    requested = args[0].lstrip("-").lower()
    if requested not in LEVELS:
        print(f"unknown level '{requested}'. Use one of: {', '.join(LEVELS)}")
        return 1

    previous = read()
    write(requested)
    print(f"{previous} -> {requested}: {DESCRIPTIONS[requested]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
