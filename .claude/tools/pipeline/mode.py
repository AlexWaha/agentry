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

The set of modes is a property of the PROJECT, not of this file: everything
except `talk` is whatever pipeline.json declares under `pipelines`, so a
workspace that declares a `research` flow gets a `research` mode for free. The
three names above are the fallback for a config that declares nothing.

Fail-open by design: an unreadable or missing file means the default flow, so a
broken mode file can never silently disable the pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import state

ROOT = Path(__file__).resolve().parents[3]
# Suffixed per lane from the single accessor in state.py - see state.LANE.
MODE_PATH = ROOT / ".claude" / "state" / f"mode{state.LANE_SUFFIX}"

BUILD = "build"
PLAN = "plan"
TALK = "talk"
MODES = (BUILD, PLAN, TALK)

# One line per mode name a project might declare. A name that is not here still
# works - describe() falls back to "<name> flow" - so this table is wording,
# never the list of permitted modes.
KNOWN = {
    BUILD: "implementation flow",
    PLAN: "design flow",
    TALK: "no flow at all - discussion and hypotheses, nothing tracked, no code written",
}


def _pipelines() -> dict:
    """The declared flows. Fails open to {} so a broken config falls back to the
    hardcoded MODES rather than leaving the session with no mode at all."""
    try:
        return state.load_pipeline().get("pipelines") or {}
    except Exception:
        return {}


def modes() -> tuple[str, ...]:
    """Every mode this project has. `talk` is not a flow, so it exists whatever
    the config says; the rest come from `pipelines`."""
    declared = [m for m in _pipelines() if m != TALK]
    return (*declared, TALK) if declared else MODES


def default() -> str:
    """The mode a project falls back to: `build` whenever it is declared, else
    the first declared flow. Reading the first key made the default depend on
    JSON key ORDER - reordering pipelines.json would have silently moved every
    unmarked task onto another stage machine."""
    available = [m for m in modes() if m != TALK]
    if BUILD in available:
        return BUILD
    return available[0] if available else BUILD


def describe(name: str) -> str:
    """The mode's one-liner, with its real stage list when the config has one."""
    block = _pipelines().get(name) or {}
    stages = [s.get("name") for s in block.get("stages", []) if isinstance(s, dict)]
    label = KNOWN.get(name, f"{name} flow")
    return f"{label}: {', '.join(stages)}" if stages else label


def read() -> str:
    """Current mode. Anything unreadable or unknown resolves to the default."""
    try:
        value = MODE_PATH.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return default()

    return value if value in modes() else default()


def write(mode: str) -> None:
    MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODE_PATH.write_text(mode + "\n", encoding="utf-8")


def conveyor_runs() -> bool:
    """Whether the Stop hook drives tasks at all. Discussion has no pipeline."""
    return read() != TALK


def pipeline_for_mode() -> str:
    """Which stage machine a newly registered task should follow."""
    return PLAN if read() == PLAN else BUILD


def main() -> int:
    args = [a for a in sys.argv[1:] if a.strip()]

    if not args or args[0] in ("--show", "show"):
        mode = read()
        print(f"{mode}: {describe(mode)}")
        return 0

    requested = args[0].lstrip("-").lower()

    if requested not in modes():
        print(f"unknown mode '{requested}'. Use one of: {', '.join(modes())}")
        return 1

    previous = read()
    write(requested)
    print(f"{previous} -> {requested}: {describe(requested)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
