#!/usr/bin/env python3
"""SubagentStart retrieval - queries the memory store with the dispatched task.

Wired in .claude/settings.json (SubagentStart hooks, matcher per agent type).
Whatever this prints lands in the dispatched subagent's context:

  --kinds lesson,pattern,module   planning agents (map + patterns + lessons)
  --kinds lesson,pattern          spec-writing agents
  --kinds lesson                  implementing / reviewing agents

This used to print the HEAD of a markdown layer file under a line cap, so an
agent received whatever sat at the top of the file - on a real project 37k
tokens of lessons before it read a line of code. Now the dispatch text (the
subagent prompt, plus the active task file as a fallback) is the query, the
store ranks rows against it with FTS5/bm25, and only the top matches go in,
under a hard byte budget.

The injected block states its row count: a thin result must be visible, not
silent. Zero matches print nothing at all.

Fail-open by contract: a missing, empty or corrupt store injects nothing and
exits 0. Memory retrieval must never block a dispatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
CLAUDE_DIR = HERE.parents[2]
ACTIVE_DIR = CLAUDE_DIR / "tasks" / "active"
sys.path.insert(0, str(HERE.parent))

# Sibling module; the dir is not on sys.path by default, hence the insert above.
import memory

BUDGET_BYTES = 3800          # the whole injected block, hard ceiling
ROW_CHARS = 700              # per-row cap before the budget trims rows
QUERY_KEYS = ("prompt", "description", "task", "message", "input", "instructions",
              "agent_prompt", "subagent_prompt")
TASK_TEXT_CHARS = 4000

KIND_LABEL = {"lesson": "lesson", "pattern": "pattern", "module": "module"}


def payload_text(payload: dict) -> str:
    """Dispatch text from the hook payload. Field spelling differs across
    harness versions, so try every known key rather than one."""
    parts = []
    for key in QUERY_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
        elif isinstance(val, dict):
            for v in val.values():
                if isinstance(v, str) and v.strip():
                    parts.append(v)
    return "\n".join(parts)


def active_task_text() -> str:
    """Fallback query source: the in-flight task file(s). A payload without the
    prompt would otherwise query on nothing and inject nothing useful."""
    try:
        chunks = []
        for path in sorted(ACTIVE_DIR.glob("task-*.md")):
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[:TASK_TEXT_CHARS])
        return "\n".join(chunks)
    except OSError:
        return ""


def head_line(shown: int, matched: int, terms: list) -> str:
    count = (f"{shown} row(s) matched this dispatch" if shown == matched
             else f"{shown} of {matched} matching row(s) (byte budget)")
    return (f"## Project memory: {count}\n"
            f"Ranked from .claude/memory/memory.db (query terms: "
            f"{', '.join(terms[:8])}). Apply a matching FIX before acting; "
            f"record new lessons with .claude/tools/memory/memory.py --record.")


def render(hits: list, terms: list) -> str:
    rows = []
    for h in hits:
        text = h["text"].strip()
        if len(text) > ROW_CHARS:
            text = text[:ROW_CHARS].rstrip() + " ..."
        rows.append(f"- [{KIND_LABEL.get(h['kind'], h['kind'])}] {h['title']}: {text}")
    while True:
        block = "\n".join([head_line(len(rows), len(hits), terms), *rows])
        if len(block.encode("utf-8")) <= BUDGET_BYTES or len(rows) <= 1:
            return block
        rows.pop()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Query project memory for a subagent dispatch")
    parser.add_argument("--kinds", default="lesson", help="comma-separated: lesson,pattern,module")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--db", default="")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except ValueError:
        payload = {}

    try:
        text = payload_text(payload) or active_task_text()
        if not text.strip():
            return 0
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
        conn = memory.connect_readonly(args.db or None)
        if conn is None:
            return 0  # no store yet, or unreadable - inject nothing
        try:
            hits = memory.query(text, limit=max(1, args.limit), kinds=kinds, conn=conn)
        finally:
            conn.close()
        if hits:
            print(render(hits, memory.fts_terms(text)))
    except Exception:
        pass  # fail-open: retrieval never blocks a dispatch
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
