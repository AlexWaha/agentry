#!/usr/bin/env python3
"""SessionStart hook - delivers the orchestrator-only rules to the main thread.

Wired in .claude/settings.json (SessionStart, fourth entry). The two files it
carries describe work only the orchestrator does - the operating loop, the busy
marker, force majeure, steering, the branch/commit/merge/push procedure - and
task-0017 measured them at 48,574 of the core set's 93,321 bytes, paid by every
agent dispatch for nothing. They left the core @import set and are named in
claudeMdExcludes; this hook is the channel that keeps the main thread served.
Design record: .agentry/plans/2026-09-16-task-0088-main-thread-rules.md.

Two measurements decide the shape (M1, M2 of that record, do not re-derive):
SessionStart pushes each hook's raw stdout into the opening message list and
re-fires on /compact, so delivery is plain stdout and survives by re-delivery -
the hookSpecificOutput envelope inject_rules.py needs is DROPPED on this event,
and the two are not interchangeable. And nothing a SessionStart hook injects
reaches a subagent (57 of 57 attachments on the main thread, 0 on a sidechain),
so the agent_type guard below should never fire. It costs two lines and is the
one thing that would silently defeat the task.

Bytes, not str: the rule files contain U+2192 and U+26A0, and stdout is cp1252
on this host, so print() would raise UnicodeEncodeError, land in the handler,
and become a permanent silent no-op. Same trap inject_rules.py avoids with
ensure_ascii.

Fail-open (NFR-4), loud (the state.checker_failed pattern from task-0077): any
failure is one `[main-thread-rules] <ExcType>: <msg>` line on stderr and exit 0.
A session start is never blocked by a rule that would not load. An unreadable
payload is reported and then treated as the main thread, because refusing to
deliver on a malformed payload is the silent non-delivery this hook exists to
prevent.

CHUNKING, and the number that forces it. Build 2.1.269 persists a hook's stdout
to a file and replaces it with a 2000-char preview whenever it exceeds a
threshold: `Zhe(ms.stdout.trim(), hookId, "stdout")`, `if (e.length <= s) return
e`, `s = NEr = 1e4`. So 10000 CHARACTERS (a JS string length, not bytes), applied
per hook COMMAND's stdout, not per event. Delivering both files from one entry
was 52514 chars and spilled - proof 2 of the design record caught it. Delivery is
therefore split: one SessionStart entry per chunk, `--chunk N`, each chunk under
CHUNK_BUDGET, plus a final `--index` entry printing one line so proof 2 can check
completeness. Chunking is deterministic (split at `## ` headings, greedily packed,
an oversized section split at line boundaries) so the same tree always yields the
same N.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]                # project root, as in inject_rules.py
RULES_DIR = ROOT / ".claude" / "rules"

# Not a config key: the whole point is one recorded decision per file, written
# in .claude/CLAUDE.md's main-thread table and pinned to this tuple by
# RealTreeTest. A runtime key would let the two drift without a test noticing.
RULES = ("git-workflow.md", "orchestration.md")

HEADER = ("## Orchestrator-only rules, delivered to the main thread by "
          ".claude/tools/hooks/main_thread_rules.py - these two are not in the "
          "core @import set and reach no agent dispatch.")

# Chars, not bytes, and per hook command's stdout: build 2.1.269 spills anything
# over 10000 (NEr) to a file and leaves a 2000-char preview. 10 percent margin.
CHUNK_BUDGET = 9000


def warn(exc: BaseException) -> None:
    sys.stderr.write(f"[main-thread-rules] {type(exc).__name__}: {exc}\n")


def sections(body: str) -> list[str]:
    """The file split at its `## ` headings, in order, nothing dropped."""
    out: list[str] = []
    cur: list[str] = []
    for line in body.splitlines(keepends=True):
        if line.startswith("## ") and cur:
            out.append("".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        out.append("".join(cur))
    return out


def pack(body: str, budget: int) -> list[str]:
    """Sections greedily packed into pieces of at most `budget` chars. A single
    section over budget (orchestration.md has one at 9696) is cut at line
    boundaries - never mid-line, so no chunk ends inside a sentence."""
    pieces: list[str] = []
    cur = ""
    for section in sections(body):
        while len(section) > budget:
            head, cut = "", 0
            for line in section.splitlines(keepends=True):
                if cut + len(line) > budget and head:
                    break
                head += line
                cut += len(line)
            if not head:  # one line longer than the budget; hard cut
                head, cut = section[:budget], budget
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(head)
            section = section[cut:]
        if len(cur) + len(section) > budget:
            pieces.append(cur)
            cur = ""
        cur += section
    if cur:
        pieces.append(cur)
    return pieces


def chunks(rules_dir: Path) -> list[str]:
    """Every chunk of every rule, in order, each headed by its file and part.
    Built for the whole rule set before anything is written, so a missing file is
    a reported non-delivery rather than half a rule set that reads like success."""
    out: list[str] = []
    for name in RULES:
        body = (rules_dir / name).read_text(encoding="utf-8", errors="replace").rstrip()
        pieces = pack(body, CHUNK_BUDGET - len(HEADER) - 200)
        for i, piece in enumerate(pieces, 1):
            head = f"--- .claude/rules/{name} (part {i} of {len(pieces)}) ---"
            out.append(f"{head}\n\n{piece.rstrip()}\n")
    if out:
        out[0] = f"{HEADER}\n\n{out[0]}"
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Deliver the orchestrator-only rules")
    parser.add_argument("--chunk", type=int, default=0, help="1-based chunk to print")
    parser.add_argument("--index", action="store_true", help="print the completeness line")
    parser.add_argument("--rules-dir", default="", help="test seam; defaults to .claude/rules")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception as exc:  # reported, then delivery continues
        warn(exc)
        raw = ""
    payload = {}
    try:
        if raw.strip():
            payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"payload is {type(payload).__name__}, not an object")
    except ValueError as exc:
        warn(exc)
        payload = {}

    try:
        agent = payload.get("agent_type")
        if isinstance(agent, str) and agent.strip():
            return 0  # M2 says this never happens; if it does, deliver nothing
        parts = chunks(Path(args.rules_dir) if args.rules_dir else RULES_DIR)
        if args.index:
            text = f"[main-thread-rules] delivered {len(parts)} of {len(parts)} chunks\n"
        elif 1 <= args.chunk <= len(parts):
            text = parts[args.chunk - 1]
        else:
            raise ValueError(f"--chunk {args.chunk} outside 1..{len(parts)}")
        sys.stdout.buffer.write(text.encode("utf-8"))
    except Exception as exc:  # fail-open, never block a session
        warn(exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
