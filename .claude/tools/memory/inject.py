#!/usr/bin/env python3
"""SubagentStart retrieval - queries the memory store with the active task text.

Wired in .claude/settings.json (SubagentStart hooks, matcher per agent type):

  --kinds lesson,pattern,module   planning agents (map + patterns + lessons)
  --kinds lesson,pattern          spec-writing agents
  --kinds lesson                  implementing / reviewing agents

This used to print the HEAD of a markdown layer file under a line cap, so an
agent received whatever sat at the top of the file - on a real project 37k
tokens of lessons before it read a line of code. Now the store ranks rows
against the task text with FTS5/bm25 and only the top matches go in, under a
hard byte budget. The injected block states its row count: a thin result must
be visible, not silent. Zero matches print nothing at all.

Delivery is the JSON envelope, not plain stdout (task-0081):

  {"hookSpecificOutput": {"hookEventName": "SubagentStart",
                          "additionalContext": "<block>"}}

Claude Code DISCARDS plain stdout on SubagentStart - only
`hookSpecificOutput.additionalContext` reaches the dispatched agent (binary
2.1.269 declares exactly that key for this event, and the ponytail plugin's
own hook carries the same comment). This file printed the block raw from the
day it was written, so retrieval delivered nothing to anybody: measured by a
probe agent that reported `## Project memory:` absent from its own context
while a sibling hook using the envelope was present. `json.dumps` runs with
its default ensure_ascii=True on purpose - the store holds arrows and
Cyrillic, and a cp1252 stdout on Windows would raise UnicodeEncodeError into
main()'s blanket handler, recreating exactly this silent no-op.

The QUERY IS THE ACTIVE TASK FILE(S), never the dispatch prompt. The live
SubagentStart payload is {session_id, transcript_path, cwd, prompt_id?,
permission_mode?, hook_event_name, agent_id, agent_type} - the binary builds
it from the session fields plus those three, and there is no prompt,
description or task field in it at all. A previous `payload_text()` scanned
eight candidate key spellings and therefore always returned "", falling
through to the task file silently; it is gone rather than kept as
forward-compatible decoration, because that fallback is what hid the missing
envelope. The parent transcript at transcript_path does hold the dispatch
text, but parsing an undocumented internal JSONL inside a 10s hook to
paraphrase the task file was rejected as cost without a measured gain.

Both size caps are configuration, not literals: `memory.inject_budget_bytes`
and `memory.inject_row_chars` in .agentry/pipeline.json (contract C-5). The
values below are the defaults used when a key is absent or unusable - the dial
FR-35's measurement turns must be turnable without a code edit.
`inject_budget_bytes` caps the BLOCK INSIDE the envelope; the JSON scaffolding
and escaping sit outside it, so the emitted object is always somewhat larger
than the budget. The alternative (capping the whole object) would make the
delivered context shrink by an amount that depends on how many newlines and
non-ASCII characters the matched rows happen to hold, and would silently
invalidate task-0011's measurement of 3565 against the 3800-byte ceiling.

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

DEFAULT_BUDGET_BYTES = 3800  # the block INSIDE the envelope, hard ceiling
DEFAULT_ROW_CHARS = 700      # per-row cap before the budget trims rows
TASK_TEXT_CHARS = 4000
HOOK_EVENT = "SubagentStart"

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


def active_task_text() -> str:
    """The query source: the in-flight task file(s) under .agentry/tasks/active/.

    This is the ONLY query text available to this hook - see the module
    docstring on what the SubagentStart payload actually carries. Each file is
    read up to TASK_TEXT_CHARS, which is well past the point where fts_terms()
    stops taking terms."""
    try:
        chunks = []
        for path in sorted(ACTIVE_DIR.glob("task-*.md")):
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[:TASK_TEXT_CHARS])
        return "\n".join(chunks)
    except OSError:
        return ""


def head_line(shown: int, matched: int, terms: list) -> str:
    count = (f"{shown} row(s) matched the active task" if shown == matched
             else f"{shown} of {matched} matching row(s) (byte budget)")
    return (f"## Project memory: {count}\n"
            f"Ranked from .agentry/memory/memory.db against the active task "
            f"file(s) (query terms: {', '.join(terms[:8])}). Apply a matching "
            f"FIX before acting; record new lessons with "
            f".claude/tools/memory/memory.py --record.")


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

    # Drain the payload and discard it. It is read only so the parent's write
    # completes; nothing in it is a query (module docstring), and parsing it
    # would only re-create the fallback that hid the missing envelope.
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:
        pass

    try:
        text = active_task_text()
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
            # The envelope, not raw stdout: plain text is discarded on this
            # event. ensure_ascii stays at its default - see the docstring.
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": HOOK_EVENT,
                "additionalContext": render(hits, memory.fts_terms(text))}}))
    except Exception:
        pass  # fail-open: retrieval never blocks a dispatch
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
