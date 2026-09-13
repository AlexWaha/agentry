#!/usr/bin/env python3
"""How much the pipeline may decide without asking.

Separate axis from the mode: the mode picks WHICH stages run, this picks how
many of their checkpoints the CEO still has to answer. Two dials rather than one
matrix, because at three in the morning a matrix is unreadable.

Levels, each a superset of the one before:

  manual    ask at every checkpoint. Which task to take, the diff, the commit,
            the push. The default, and the right setting while someone is at the
            keyboard.
  assisted  take the task and commit without asking, and skip the diff review
            when the code review came back with nothing serious. Still asks
            before pushing.
  auto      everything up to the commit, then pick up the next ready task. For
            a night run. Still stops at the push.

What no level ever grants:

  - the push. The CEO asked for that decision to stay his, every time, at every
    level (rules/git-workflow.md, "Push is never automatic"). A push is the
    moment work leaves the machine.
  - merging into the main branch. That happens in the web UI, by a human. The
    agent stops at a pushed branch and a merge-request link, always.
  - moving a task to done. That needs the merge above, so it needs the human.
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

ROOT = Path(__file__).resolve().parents[3]
APPROVALS_PATH = ROOT / ".claude" / "state" / "approvals"

MANUAL = "manual"
ASSISTED = "assisted"
AUTO = "auto"
LEVELS = (MANUAL, ASSISTED, AUTO)

# Checkpoint names, matching the awaiting_human values and stage semantics.
TAKE = "take"              # move a task out of the queue into work
DIFF_REVIEW = "diff-review"  # the CEO reads the diff in the browser
COMMIT = "commit"
PUSH = "push"
PLAN_APPROVAL = "approval"  # the CEO signs off a plan

GRANTS = {
    MANUAL: frozenset(),
    ASSISTED: frozenset({TAKE, COMMIT, DIFF_REVIEW}),
    AUTO: frozenset({TAKE, COMMIT, DIFF_REVIEW}),
}

# Checkpoints no level and no per-stage auto_approve may ever grant. The push
# was in GRANTS[AUTO] while git-workflow.md forbade it - the rule was written
# and not enforced, so the orchestrator followed the permissive document.
NEVER_GRANTED = frozenset({PUSH})

DESCRIPTIONS = {
    MANUAL: "ask at every checkpoint: which task, the diff, the commit, the push",
    ASSISTED: "take tasks and commit unasked, skip the diff review when the code "
              "review is clean, still ask before pushing",
    AUTO: "everything through the commit, then take the next ready task. The push, "
          "the merge and done stay with the CEO",
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


def diff_review_auto(has_critical_or_high: bool) -> bool:
    """The diff review is skipped only when the code review found nothing
    serious. A clean review plus green gates is evidence; a review carrying a
    Critical or High finding is exactly what the CEO must see."""
    if has_critical_or_high:
        return False
    return granted(DIFF_REVIEW)


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
