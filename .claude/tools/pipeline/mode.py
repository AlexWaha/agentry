#!/usr/bin/env python3
"""Session mode switch - decides whether the execution conveyor runs at all.

The pipeline exists to drive coding tasks autonomously. It is the wrong tool
when the human wants to think out loud, probe a hypothesis or review an idea:
the Stop hook keeps shoving the next task forward and every reply drags the
backlog along with it.

The mode picks WHICH stage machine runs, it is not an on/off switch. Planning
is no less formal than building: it has its own stages, its own owners and its
own checkpoints. Stored as one word in .claude/state/mode:

  build  implementation. Drives a task from code to a pushed branch.
  plan   design. Turns a request into an approved plan, a spec and task files
         waiting in tasks/backlog/. Writes documents, never product code.
  talk   discussion and hypotheses. No stage machine at all, nothing tracked.

Both build and plan drive the conveyor, over different stages. Only talk has
no flow behind it.

Fail-open by design: an unreadable or missing file means `build`, so a broken
mode file can never silently disable the pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODE_PATH = ROOT / ".claude" / "state" / "mode"

BUILD = "build"
PLAN = "plan"
TALK = "talk"
MODES = (BUILD, PLAN, TALK)

DESCRIPTIONS = {
    BUILD: "implementation flow: implement, test, review, ready, done",
    PLAN: "design flow: formalize, draft, plan-review, approval, breakdown, done",
    TALK: "no flow at all - discussion and hypotheses, nothing tracked, no code written",
}


def read() -> str:
    """Current mode. Anything unreadable or unknown resolves to build."""
    try:
        value = MODE_PATH.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return BUILD

    return value if value in MODES else BUILD


def write(mode: str) -> None:
    MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODE_PATH.write_text(mode + "\n", encoding="utf-8")


def conveyor_runs() -> bool:
    """Whether the Stop hook drives tasks at all. Discussion has no pipeline."""
    return read() in (BUILD, PLAN)


def pipeline_for_mode() -> str:
    """Which stage machine a newly registered task should follow."""
    return PLAN if read() == PLAN else BUILD


def main() -> int:
    args = [a for a in sys.argv[1:] if a.strip()]

    if not args or args[0] in ("--show", "show"):
        mode = read()
        print(f"{mode}: {DESCRIPTIONS[mode]}")
        return 0

    requested = args[0].lstrip("-").lower()

    if requested not in MODES:
        print(f"unknown mode '{requested}'. Use one of: {', '.join(MODES)}")
        return 1

    previous = read()
    write(requested)
    print(f"{previous} -> {requested}: {DESCRIPTIONS[requested]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
