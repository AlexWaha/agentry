#!/usr/bin/env python3
"""How much the pipeline may decide without asking.

Separate axis from the mode: the mode picks WHICH stages run, this picks how
many of their checkpoints the CEO still has to answer. Two dials rather than one
matrix, because at three in the morning a matrix is unreadable.

Levels, each a superset of the one before:

  manual    ask at every checkpoint. Which task to take, the commit, the push.
            The default, and the right setting while someone is at the keyboard.
  assisted  commit without asking. The CEO starts each task, and the conveyor
            stops after the current one. Still asks before pushing.
  auto      the same, plus taking the next ready backlog task on its own once
            the current one is done (the Stop hook's pickup reads `take`). For
            a night run. Still stops at the push.

What no level ever grants:

  - the push. The CEO asked for that decision to stay his, every time, at every
    level (rules/git-workflow.md, "Push is never automatic"). A push is the
    moment work leaves the machine.
  - merging into the main branch. That happens in the web UI, by a human. The
    agent stops at a pushed branch and a merge-request link, always.
  - moving a task to done. That needs the merge above, so it needs the human.
  - approving a plan, which is what lets the breakdown into tasks begin.
  - answering the questions raised while planning. That information exists only
    in the CEO's head.

Because of these, an overnight run is bounded by the dependency chain:
independent tasks run all night, a chain of dependent ones advances by exactly
one, since the next task needs its predecessor in the main branch.

A per-stage `auto_approve` list in pipeline.json is layered on top and wins for
every checkpoint EXCEPT the ones in NEVER_GRANTED - listing `push` there does
nothing, by design.

Fail-open: anything unreadable resolves to `manual`, the strictest level. A
broken file can never hand the agent more authority than it had.
"""

from __future__ import annotations

import sys
from pathlib import Path

import state

ROOT = Path(__file__).resolve().parents[3]
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
PLAN = "plan"              # the CEO signs off a plan (plan flow, stage `approval`)
TRUNK_PUSH = "trunk_push"   # solo mode: publish the trunk itself, once

GRANTS = {
    MANUAL: frozenset(),
    ASSISTED: frozenset({COMMIT}),
    AUTO: frozenset({TAKE, COMMIT}),
}

# Checkpoints no level and no per-stage auto_approve may ever grant. The push
# was in GRANTS[AUTO] while git-workflow.md forbade it - the rule was written
# and not enforced, so the orchestrator followed the permissive document.
# TRUNK_PUSH joins it for the same reason and one more: it is the one step that
# publishes the trunk, so it is recorded per push by the CEO in chat
# (approve.py --trunk-push), never handed over wholesale by a level. PLAN is the
# CEO's signature on a plan: closer to a push than to a commit, because every
# task below an approved plan inherits its mistakes.
NEVER_GRANTED = frozenset({PUSH, TRUNK_PUSH, PLAN})

# Which run.db column records each checkpoint's approval. One table for every
# reader: advance.py parks on it, approve.py writes it.
APPROVAL_FIELD = {COMMIT: "commit_approved", PUSH: "push_approved", PLAN: "plan_approved"}

DESCRIPTIONS = {
    MANUAL: "ask at every checkpoint: which task, the commit, the push",
    ASSISTED: "commit unasked, but you start each task and the next one waits for you. "
              "The push is never granted",
    AUTO: "commit unasked and take the next ready task on its own. The push is never "
          "granted",
}


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
    """Whether this checkpoint may pass without the CEO.

    `stage_auto` is the stage's own `auto_approve` list from pipeline.json; it
    is layered on top of the level, so a single checkpoint can be automated
    without raising the dial for everything else. NEVER_GRANTED wins over both."""
    if checkpoint in NEVER_GRANTED:
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
