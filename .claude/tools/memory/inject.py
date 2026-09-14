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

Both size caps are configuration, not literals: `memory.inject_budget_bytes`
and `memory.inject_row_chars` in .agentry/pipeline.json (contract C-5). The
values below are the defaults used when a key is absent or unusable - the dial
FR-35's measurement turns must be turnable without a code edit.

Fail-open by contract: a missing, empty or corrupt store injects nothing and
exits 0. A missing, non-integer or absurd budget value falls back to the
default and STILL emits the block (NFR-4). Memory retrieval must never block a
dispatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
ACTIVE_DIR = ROOT / ".agentry" / "tasks" / "active"
sys.path.insert(0, str(HERE.parents[2] / "tools" / "pipeline"))
sys.path.insert(0, str(HERE.parent))

# Siblings; neither dir is on sys.path by default, hence the inserts above.
# The inserts are LIFO, so the pipeline dir goes first and this file's OWN dir
# lands at index 0 - matching update.py and codebase_sync.py. Reversed, a future
# pipeline/memory.py would shadow the sibling below and `memory.connect_readonly`
# would fail inside main()'s blanket except, silently killing injection.
import memory
import state

DEFAULT_BUDGET_BYTES = 3800  # the whole injected block, hard ceiling
DEFAULT_ROW_CHARS = 700      # per-row cap before the budget trims rows
QUERY_KEYS = ("prompt", "description", "task", "message", "input", "instructions",
              "agent_prompt", "subagent_prompt")
TASK_TEXT_CHARS = 4000

KIND_LABEL = {"lesson": "lesson", "pattern": "pattern", "module": "module"}


def cfg_int(key: str, default: int) -> int:
    """One `memory.<key>` integer from .agentry/pipeline.json, or `default`.

    Fail-open per NFR-4, and the three refusals are deliberate. A bool is
    rejected before the int check because `True` is an int in Python and would
    silently mean a one-byte budget. A string is rejected rather than coerced:
    the spec says non-integer falls back. Zero and negative are absurd (they
    describe no block at all) and fall back too.

    A small-but-positive value is HONOURED rather than overridden. render()
    never drops the last row, so the floor is the head line plus one capped
    row: no budget can silence memory injection, which is what NFR-4 protects,
    so there is nothing left for a minimum to defend against.
    """
    try:
        block = state.load_pipeline().get("memory")
        raw = block.get(key) if isinstance(block, dict) else None
    except Exception:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return default
    return raw


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
            f"Ranked from .agentry/memory/memory.db (query terms: "
            f"{', '.join(terms[:8])}). Apply a matching FIX before acting; "
            f"record new lessons with .claude/tools/memory/memory.py --record.")


def render(hits: list, terms: list) -> str:
    budget_bytes = cfg_int("inject_budget_bytes", DEFAULT_BUDGET_BYTES)
    row_chars = cfg_int("inject_row_chars", DEFAULT_ROW_CHARS)
    rows = []
    for h in hits:
        text = h["text"].strip()
        if len(text) > row_chars:
            text = text[:row_chars].rstrip() + " ..."
        rows.append(f"- [{KIND_LABEL.get(h['kind'], h['kind'])}] {h['title']}: {text}")
    while True:
        block = "\n".join([head_line(len(rows), len(hits), terms), *rows])
        if len(block.encode("utf-8")) <= budget_bytes or len(rows) <= 1:
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
