#!/usr/bin/env python3
"""SubagentStop hook - progressive-learning trigger (fires in the orchestrator).

When a dispatched subagent finishes:
  1. If the agent has a project memory dir, inject a one-line reminder to
     record any lesson from its report (skills/self-learning, record mode).
  2. If that agent's MEMORY.md nears the native injection window
     (200 lines / 25KB), inject a curation nudge.

stdout is context injection; exit 0 always (this hook never blocks - lesson
recording is a discipline, stopping the loop over it would cost more than it
saves). Fail-open on any error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
CLAUDE_DIR = HERE.parents[2]
MEMORY_DIR = CLAUDE_DIR / "agent-memory"
MEMORY_LINE_LIMIT = 180
MEMORY_BYTE_LIMIT = 22 * 1024


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
        mem = MEMORY_DIR / name / "MEMORY.md"
        if not mem.is_file():
            return 0

        print(f"Subagent {name} finished. If its report contains a mistake, "
              f"surprise, or CEO correction, record the lesson now per "
              f"skills/self-learning (tier: agent MEMORY.md vs "
              f".claude/memory/lessons.md vs shared rule; reusable code -> "
              f".claude/memory/patterns.md). Do not skip.")

        data = mem.read_bytes()
        lines = data.count(b"\n") + 1
        if len(data) > MEMORY_BYTE_LIMIT or lines > MEMORY_LINE_LIMIT:
            print(f"memory: {name}/MEMORY.md at {lines} lines / "
                  f"{len(data) // 1024}KB - nearing the 200-line/25KB "
                  f"injection window. Create a curation task "
                  f"(skills/self-learning, mode: curate).")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
