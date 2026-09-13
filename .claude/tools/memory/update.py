#!/usr/bin/env python3
"""Post-task memory-update gate - mirrors the handoff-debt machinery.

Every completed task must get a memory review before the next task starts:
distill the task's handoff doc into the three layers (Gotchas -> lessons.md,
reusable code -> patterns.md + patterns/, touched modules -> codebase.md), then
stamp. "Nothing to record" is a legal outcome (--none) - the gate forces the
REVIEW, not fabricated content.

Stamps live in .claude/state/memory/<task>.json (gitignored with state/).
Config: pipeline.json "memory": {"enabled": bool, "baseline": "task-NNNN"}.
Missing block = feature OFF. Enforced by stop_gate.py (blocks starting the
next task while memory debt exists).

CLI:
    python update.py --check                       JSON debt report, exit 1 on debt
    python update.py --stamp --task task-0007 [--lessons N --patterns N --l1-rows N | --none]

Fail-open: internal errors report "no debt".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
CLAUDE_DIR = HERE.parents[2]
sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))

import state

STAMP_DIR = state.STATE_DIR / "memory"
DONE_DIR = state.ROOT / ".claude" / "tasks" / "done"
TASK_NUM_RE = re.compile(r"(\d+)$")


def cfg() -> dict:
    c = state.load_pipeline().get("memory", {})
    return c if isinstance(c, dict) else {}


def enabled() -> bool:
    try:
        return bool(cfg().get("enabled"))
    except Exception:
        return False


def _num(task_id: str):
    m = TASK_NUM_RE.search(str(task_id).strip())
    return int(m.group(1)) if m else None


def covered_required(task: str) -> bool:
    """Same baseline semantics as handoff.covered_required: tasks at or below
    the configured baseline are grandfathered; unparseable ids are exempt."""
    if not enabled():
        return False
    n = _num(task)
    if n is None:
        return False
    baseline = str(cfg().get("baseline", "") or "").strip()
    if not baseline:
        return True
    b = _num(baseline)
    if b is None:
        return False
    return n > b


def stamp_path(task: str) -> Path:
    return STAMP_DIR / f"{task}.json"


def unstamped_done_tasks() -> list:
    """[{'task','reason'}] for done tasks above baseline lacking a memory stamp."""
    try:
        if not enabled() or not DONE_DIR.is_dir():
            return []
        out = []
        for f in sorted(DONE_DIR.glob("task-*.md")):
            task = f.stem
            if not covered_required(task):
                continue
            if not stamp_path(task).is_file():
                out.append({"task": task,
                            "reason": "memory layers not reviewed after completion"})
        return out
    except Exception:
        return []


def stamp(task: str, lessons: int, patterns: int, l1_rows: int, none: bool) -> str:
    STAMP_DIR.mkdir(parents=True, exist_ok=True)
    p = stamp_path(task)
    p.write_text(json.dumps({
        "task": task,
        "stamped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lessons": lessons,
        "patterns": patterns,
        "l1_rows": l1_rows,
        "none": none,
    }, indent=2) + "\n", encoding="utf-8")
    try:
        return str(p.relative_to(state.ROOT).as_posix())
    except ValueError:
        return str(p)


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-task memory review gate")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--stamp", action="store_true")
    parser.add_argument("--task", default="")
    parser.add_argument("--lessons", type=int, default=0)
    parser.add_argument("--patterns", type=int, default=0)
    parser.add_argument("--l1-rows", type=int, default=0, dest="l1_rows")
    parser.add_argument("--none", action="store_true",
                        help="reviewed, nothing worth recording")
    args = parser.parse_args()

    try:
        if args.stamp:
            if not args.task:
                print("refused: --stamp requires --task")
                return 1
            if not args.none and not (args.lessons or args.patterns or args.l1_rows):
                print("refused: pass --lessons/--patterns/--l1-rows counts, or --none "
                      "if the review found nothing worth recording")
                return 1
            rel = stamp(args.task, args.lessons, args.patterns, args.l1_rows, args.none)
            print(f"memory: stamped {args.task} ({rel})")
            return 0
        debt = unstamped_done_tasks()
        print(json.dumps({
            "enabled": enabled(),
            "baseline": str(cfg().get("baseline", "")),
            "memory_debt": debt,
        }, indent=2))
        return 1 if debt else 0
    except Exception as exc:
        print(json.dumps({"enabled": False, "error": str(exc), "memory_debt": []}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
