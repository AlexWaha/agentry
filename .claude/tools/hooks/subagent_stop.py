#!/usr/bin/env python3
"""SubagentStop hook - progressive-learning trigger (fires in the orchestrator).

When a dispatched subagent finishes, inject a one-line reminder to record any
lesson from its report into the memory store, with the exact command. The
reminder used to also carry a curation nudge when the agent's MEMORY.md neared
the native 200-line injection window; the store replaced those files and does
not overflow, so there is nothing left to curate.

stdout is context injection; exit 0 always (this hook never blocks - lesson
recording is a discipline, stopping the loop over it would cost more than it
saves). Fail-open on any error.
"""

from __future__ import annotations

import json
import sys


def agent_name(payload: dict) -> str:
    # Field name differs across harness versions; try the known spellings.
    for key in ("agent_name", "subagent_name", "agent_type", "subagent_type"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    try:
        name = agent_name(payload)
        if not name:
            return 0
        print(f"Subagent {name} finished. If its report contains a mistake, surprise, or "
              f"CEO correction, record it now (skills/self-learning): python "
              f".claude/tools/memory/memory.py --record --kind lesson --signature <tag> "
              f"--trigger <when it applies> --what <one sentence> --why <root cause> "
              f"--fix <checkable rule> --agent {name} --task <task-id>. A reusable code "
              f"shape goes in as --kind pattern. A universal policy goes in "
              f".claude/rules/ instead. Do not skip.")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
