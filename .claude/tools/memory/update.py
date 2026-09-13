#!/usr/bin/env python3
"""Post-task memory-update gate - mirrors the handoff-debt machinery.

Every completed task must get a memory review before the next task starts:
distill the task's handoff doc into the store (Gotchas -> lessons, reusable code
-> patterns, touched modules -> module rows) with
`.claude/tools/memory/memory.py --record`, then stamp. "Nothing to record" is a
legal outcome (--none) - the gate forces the REVIEW, not fabricated content.

The stamp counts ROWS IN THE STORE (.agentry/memory/memory.db), not lines in a
markdown file and not a number the caller asserts: a stamp without --none is
refused unless the store gained at least one row since the previous stamp. So
"I distilled it" has to be true before it can be recorded.

Stamps live in .agentry/state/memory/<task>.json (gitignored with state/).
Config: pipeline.json "memory": {"enabled": bool, "baseline": "task-NNNN"}.
Missing block = feature OFF. Enforced by stop_gate.py (blocks starting the
next task while memory debt exists).

CLI:
    python update.py --check                       JSON debt report, exit 1 on debt
    python update.py --stamp --task task-0007 [--none]

Fail-open: internal errors report "no debt"; a store that does not exist yet
cannot block a stamp.
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
sys.path.insert(0, str(HERE.parent))

import memory
import state

STAMP_DIR = state.STATE_DIR / "memory"
DONE_DIR = state.ROOT / ".agentry" / "tasks" / "done"
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


def store_counts() -> dict:
    """Row counts per kind, straight out of memory.db. Zeros when the store is
    missing or unreadable (fail-open, same contract as the rest of the gates)."""
    try:
        return memory.counts()
    except Exception:
        return {k: 0 for k in memory.KINDS}


def store_exists() -> bool:
    try:
        return memory.DB_PATH.is_file()
    except Exception:
        return False


def previous_counts() -> tuple:
    """(counts, task) from the most recent stamp - the baseline a new stamp's
    row delta is measured against. ({}, '') when nothing was stamped yet."""
    try:
        best, best_at, best_task = {}, "", ""
        for f in STAMP_DIR.glob("task-*.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            at = str(d.get("stamped_at", ""))
            if at >= best_at:
                best, best_at, best_task = d.get("counts", {}) or {}, at, str(d.get("task", ""))
        return best, best_task
    except Exception:
        return {}, ""


def rows_added(current: dict, previous: dict) -> int:
    return sum(max(0, int(current.get(k, 0)) - int(previous.get(k, 0))) for k in memory.KINDS)


def stamp(task: str, none: bool) -> str:
    STAMP_DIR.mkdir(parents=True, exist_ok=True)
    p = stamp_path(task)
    p.write_text(json.dumps({
        "task": task,
        "stamped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": store_counts(),
        "none": none,
    }, indent=2) + "\n", encoding="utf-8")
    try:
        return str(p.relative_to(state.ROOT).as_posix())
    except ValueError:
        return str(p)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Post-task memory review gate")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--stamp", action="store_true")
    parser.add_argument("--task", default="")
    # Accepted and ignored: the counts now come from the store, not from the
    # caller. Kept so the instruction stop_gate.py prints still parses.
    parser.add_argument("--lessons", type=int, default=0)
    parser.add_argument("--patterns", type=int, default=0)
    parser.add_argument("--l1-rows", type=int, default=0, dest="l1_rows")
    parser.add_argument("--none", action="store_true",
                        help="reviewed, nothing worth recording")
    args = parser.parse_args(argv)

    try:
        if args.stamp:
            if not args.task:
                print("refused: --stamp requires --task")
                return 1
            current = store_counts()
            previous, prev_task = previous_counts()
            added = rows_added(current, previous)
            if not args.none and store_exists() and added == 0:
                print("refused: .agentry/memory/memory.db gained no rows since the last stamp"
                      + (f" ({prev_task})" if prev_task else "")
                      + f" - it holds {current.get('lesson', 0)} lessons, "
                        f"{current.get('pattern', 0)} patterns, "
                        f"{current.get('module', 0)} module rows. Record the distillation "
                        f"with: python .claude/tools/memory/memory.py --record --kind lesson "
                        f"--signature <tag> --trigger <when> --what <mistake> --why <cause> "
                        f"--fix <rule>  (or pass --none if the review found nothing).")
                return 1
            rel = stamp(args.task, args.none)
            print(f"memory: stamped {args.task} ({rel}) - {added} new row(s) in the store, "
                  f"now {current.get('lesson', 0)} lessons / {current.get('pattern', 0)} "
                  f"patterns / {current.get('module', 0)} module rows")
            return 0
        debt = unstamped_done_tasks()
        print(json.dumps({
            "enabled": enabled(),
            "baseline": str(cfg().get("baseline", "")),
            "store": store_counts(),
            "memory_debt": debt,
        }, indent=2))
        return 1 if debt else 0
    except Exception as exc:
        print(json.dumps({"enabled": False, "error": str(exc), "memory_debt": []}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
