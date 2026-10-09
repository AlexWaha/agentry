#!/usr/bin/env python3
"""Swarm flag - the third dial, next to the mode and the approval level.

Build mode only. `on` runs each multi-subtask epic as a supervised parallel agent
swarm under ONE umbrella task (.claude/skills/swarm/SKILL.md, adapter in
.claude/pipeline-adapter.md); `off` runs one task at a time. Stored as one word
in .agentry/state/swarm, suffixed per lane from the single accessor in state.py
exactly like mode and approvals: one conveyor per lane means one swarm flag per
lane.

The flag changes nothing in advance.py or the gates. It tells the orchestrator
and SessionStart which procedure is in force; the umbrella task still walks the
ordinary stages.

Fail-open: a missing or unreadable file means `off`, the sequential flow.
"""

from __future__ import annotations

import sys
from pathlib import Path

import state

ROOT = Path(__file__).resolve().parents[3]
SWARM_PATH = ROOT / ".agentry" / "state" / f"swarm{state.LANE_SUFFIX}"

ON = "on"
OFF = "off"
VALUES = (ON, OFF)

DESCRIPTIONS = {
    ON: "build runs each epic as a parallel agent swarm (skills/swarm/SKILL.md)",
    OFF: "build runs one task at a time",
}


def read() -> str:
    try:
        value = SWARM_PATH.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return OFF
    return value if value in VALUES else OFF


def write(value: str) -> None:
    SWARM_PATH.parent.mkdir(parents=True, exist_ok=True)
    SWARM_PATH.write_text(value + "\n", encoding="utf-8")


def _main() -> int:
    args = [a for a in sys.argv[1:] if a.strip()]

    if not args or args[0] in ("--show", "show"):
        value = read()
        print(f"{value}: {DESCRIPTIONS[value]}")
        return 0

    requested = args[0].lstrip("-").lower()
    if requested not in VALUES:
        print(f"unknown value '{requested}'. Use one of: {', '.join(VALUES)}")
        return 1

    previous = read()
    write(requested)
    print(f"{previous} -> {requested}: {DESCRIPTIONS[requested]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
