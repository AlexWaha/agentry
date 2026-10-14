#!/usr/bin/env python3
"""SubagentStart hook - delivers the rule files an agent declares in `rules:`.

Wired in plugin/hooks/hooks.json (SubagentStart, no matcher: it fires for every
agent type, including ones added later, and is a no-op for an agent without the
key). Contract C-7 fixes the field: a list of paths relative to .claude/rules/.
Design record: .agentry/plans/2026-09-14-task-0013-rule-injection-contract.md
(FR-28, spec-0001 line 149).

Identity comes from the payload's `agent_type` - the key the SubagentStart
event actually carries, and the one its own matcher queries. `subagent_type`
and `agent_name` follow it only to mirror subagent_stop.py and pretool_gate.py
across harness versions. The name is the agent definition's `name:`, which is
the file stem for all 31 files. Never `agent_id`: that is a run id, not a
definition name.

Output is the hookSpecificOutput envelope rather than raw stdout, because
SubagentStart drops plain stdout on this build. json.dumps keeps its default
ensure_ascii=True on purpose: a cp1252 stdout on Windows raises
UnicodeEncodeError on the arrows and Cyrillic the rule files contain, and that
error would die in main()'s blanket handler as a silent, permanent no-op.

CHUNKED, and the number that forces it. Claude Code runs a hook's
additionalContext through the SAME persist path as its stdout - a single
`gce(text, id, label, {threshold: PCs})` with `PCs = 1e4`, called as
`gce(hook.additionalContext, id, "additionalContext", ...)` (2.1.296 names; they
were `Zhe` and `NEr` in 2.1.269, so anchor on the strings and the value) - so a
block over 10000 CHARACTERS is written to `hook-<id>-<n>-additionalContext.txt`
and replaced by a 2000-char preview. There is no separate cap for SubagentStart
and no 8000-char variant anywhere in the build. Re-measured live on 2.1.296 on
2026-10-13: a SubagentStart additionalContext of 9900 characters reached the
agent whole and one of 10100 arrived as the preview plus the file path (see
docs/technical/claude-code-2.1.296-delta.md, section 4). Every agent declaring more than 10000
chars of rules therefore received a preview, not its rules, from task-0014 until
task-0091. Delivery is now one SubagentStart entry per chunk (`--chunk N`) under
CHUNK_BUDGET, plus an `--index` entry printing the K-of-K line, the same shape
main_thread_rules.py uses on SessionStart. The number of entries wired in
hooks.json is set by the LARGEST declaring agent (reviewer today), so adding a
rule to any agent can require more entries - ChunkBudgetTest fails when it does.

No byte budget, per decision D4 of the contract. After FR-29 this block is a
strict subset of what leaves the core @import set, so it cannot raise any
agent's context. A runtime cap would drop a whole policy at the moment it is
needed and hide the drop behind exit 0; size is decided where the declarations
are written (task-0014) and measured by FR-35.

An agent with `omitClaudeMd: true` (task-0106) runs without any CLAUDE.md, so the
core @rules/ set does not reach it through the file walk. It declares the core
rules in its own `rules:` key and this hook delivers them like any other; the
block then carries the core set too, but the CLAUDE.md files it dropped were
larger (measured on 2.1.296, docs/technical/claude-code-2.1.296-delta.md,
section 5). test_agent_frontmatter.py pins which agents do this and that each
declares the whole core set.

Fail-open by contract (NFR-4): no `rules:` key prints nothing and exits 0, a
declared file that does not resolve under .claude/rules/ becomes a diagnostic
line and exits 0. Rule delivery never blocks a dispatch.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# tools/paths.py decides the project root; tools/ is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paths

HERE = Path(__file__).resolve()
ROOT = paths.PROJECT_ROOT
AGENTS_DIR = ROOT / ".claude" / "agents"
RULES_DIR = ROOT / ".claude" / "rules"

# main_thread_rules.py owns the deterministic splitter; duplicating it would let
# the two budgets drift. Two inserts, LIFO: this file's own dir lands at index 0,
# ahead of tools/ - which holds paths.py and payload.py, nothing this dir shadows.
sys.path.insert(0, str(HERE.parent))
from main_thread_rules import pack  # noqa: E402

# Chars, not bytes, and per hook command: PCs = 1e4 (NEr in 2.1.269; re-verified
# on 2.1.296). 10 percent margin, the same number main_thread_rules.py uses for
# the identical threshold on SessionStart.
CHUNK_BUDGET = 9000

# This module imports only paths.py and main_thread_rules.py. If a later change
# needs state.py, copy memory/inject.py:73-75 verbatim - pipeline dir FIRST, own dir
# SECOND, so the own dir ends at index 0 - and add the re-exec order test from
# InjectSysPathOrderTest.

IDENTITY_KEYS = ("agent_type", "subagent_type", "agent_name")
# Same shape as state.LANE_RE. A name that cannot address a file never reaches
# the filesystem.
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# The frontmatter block, the same regex advance.py:64 and handoff.py:47 use.
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
RULES_KEY_RE = re.compile(r"^rules:[ \t]*(.*)$", re.MULTILINE)
BLOCK_ITEM_RE = re.compile(r"^[ \t]+-[ \t]*(.+?)[ \t]*$")
# A relative markdown path: no segment starting with a dot, no drive letter, no
# absolute root, no traversal. Checked before any stat, so garbage yields a
# readable diagnostic instead of a filesystem probe.
RULE_PATH_RE = re.compile(
    r"^(?:[A-Za-z0-9_-][A-Za-z0-9._-]*/)*[A-Za-z0-9_-][A-Za-z0-9._-]*\.md$")


def agent_type(payload: dict) -> str:
    """The dispatched agent's type: first non-empty string among the known
    spellings, stripped. Empty when the payload carries no identity at all."""
    for key in IDENTITY_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def declared_rules(text: str) -> list[str]:
    """The `rules:` entries an agent file declares, in declared order.

    Three value forms, because YAML allows all three and task-0014 will most
    naturally write the block form that the neighbouring `skills:` and
    `mcpServers:` keys already use in every agent file:

        rules: [a.md, b.md]     flow
        rules: a.md             scalar, or `a.md, b.md`
        rules:                  block
          - a.md

    Only the block between the opening and closing `---` is searched, and only a
    `rules:` at column 0 inside it: a `rules:` in the agent's prose body is
    documentation, not a declaration. No block, no key, or an empty value gives
    an empty list - the silent path all 31 files take today.

    Duplicates are kept as declared. A truncated flow list (`rules: [a.md` with
    no closing bracket) yields the raw value as ONE unreadable entry rather than
    its readable prefix: it fails the path check downstream and surfaces as a
    diagnostic, where a partial list would have injected some rules silently and
    looked like success.
    """
    block = FRONTMATTER_RE.match(text)
    if not block:
        return []
    body = block.group(1)
    key = RULES_KEY_RE.search(body)
    if not key:
        return []
    value = key.group(1).strip()
    if value.startswith("["):
        if not value.endswith("]"):
            return [value]
        items = value[1:-1].split(",")
    elif value:
        items = value.split(",")
    else:
        items = []
        for line in body[key.end():].splitlines():
            if not line.strip():
                continue
            item = BLOCK_ITEM_RE.match(line)
            if not item:
                break  # the next key at column 0 ends the block list
            items.append(item.group(1))
    return [clean for clean in (i.strip().strip("'\"").strip() for i in items) if clean]


def resolve_rule(entry: str, rules_dir: Path):
    """The file a declared entry names, or None when it names nothing legal.

    Both halves are required. The regex refuses traversal, absolute paths and
    non-markdown before any stat; is_relative_to catches whatever survives it,
    including a symlink inside the rules directory whose target is outside.
    """
    if not RULE_PATH_RE.match(entry):
        return None
    candidate = (rules_dir / entry).resolve()
    if not candidate.is_relative_to(rules_dir.resolve()) or not candidate.is_file():
        return None
    return candidate


def rel(path: Path) -> str:
    """A repo-relative POSIX path for the labels. An absolute path in an
    injected block is noise the agent cannot act on."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def render(name: str, agents_dir: Path, rules_dir: Path) -> str:
    """The injected block for one agent, or "" when there is nothing to inject.

    The `Contents of <path>:` label mirrors the framing Claude Code itself uses
    for imported files, so the agent reads a rule with the same standing whether
    it arrived through the core @import set or through here. Every declared
    entry failing still emits a block: the diagnostic is the whole point of that
    case, and swallowing it would make a misdeclared rule indistinguishable from
    no rule.
    """
    agent_file = agents_dir / f"{name}.md"
    if not agent_file.is_file():
        return ""  # a built-in or user-level agent has no project definition
    try:
        text = agent_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""  # is_file() passed, the read did not: nothing to declare from
    entries = declared_rules(text)
    if not entries:
        return ""
    sections, failures = [], []
    for entry in entries:
        path = resolve_rule(entry, rules_dir)
        if path is None:
            failures.append(f"- not injected: {entry} (declared, but not a file under "
                            f"{rel(rules_dir)}/) - report this to the orchestrator")
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace").rstrip()
        except OSError:
            # A failed entry is one diagnostic line and the loop continues. An
            # escape here would reach main()'s blanket handler and discard the
            # whole block - every section already collected and every
            # diagnostic - at exit 0.
            failures.append(f"- not injected: {entry} (declared, but unreadable)"
                            " - report this to the orchestrator")
            continue
        sections.append(f"Contents of {rel(path)}:\n\n{body}")
    head = (f"## Agent rules: {len(sections)} of {len(entries)} declared file(s), "
            f"from {rel(agent_file)}")
    return "\n\n".join([head, *failures, *sections])


def render_chunks(name: str, agents_dir: Path, rules_dir: Path) -> list[str]:
    """The agent's block split into pieces of at most CHUNK_BUDGET chars.

    Every piece is headed with its part number, because the pieces arrive as
    separate attachments in an order nothing else guarantees, and a rule body cut
    across two of them is unreadable without the marker. Empty block, no chunks:
    an agent without the key still injects nothing.
    """
    block = render(name, agents_dir, rules_dir)
    if not block:
        return []
    pieces = pack(block, CHUNK_BUDGET - 80)
    return [f"[agent-rules part {i} of {len(pieces)}]\n{piece.rstrip()}\n"
            for i, piece in enumerate(pieces, 1)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inject an agent's declared rule files")
    parser.add_argument("--agents-dir", default="", help="test seam; defaults to .claude/agents")
    parser.add_argument("--rules-dir", default="", help="test seam; defaults to .claude/rules")
    parser.add_argument("--chunk", type=int, default=0, help="1-based chunk to inject")
    parser.add_argument("--index", action="store_true", help="inject the K-of-K line")
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
        name = agent_type(payload)
        if not name:
            return 0
        if not AGENT_NAME_RE.match(name):
            sys.stderr.write(f"inject_rules: ignoring unusable agent name {name!r}\n")
            return 0
        agents_dir = Path(args.agents_dir) if args.agents_dir else AGENTS_DIR
        rules_dir = Path(args.rules_dir) if args.rules_dir else RULES_DIR
        if args.chunk or args.index:
            parts = render_chunks(name, agents_dir, rules_dir)
            if not parts:
                return 0  # nothing declared; every chunk entry stays silent
            if args.index:
                block = f"[agent-rules] delivered {len(parts)} of {len(parts)} chunks"
            elif 1 <= args.chunk <= len(parts):
                block = parts[args.chunk - 1]
            else:
                return 0  # this agent has fewer chunks than the wired entries
        else:
            # Unchunked, for a by-hand run and the unit tests. NEVER wire this
            # form in hooks.json: over 10000 chars it is persisted, not
            # delivered, which is the bug task-0091 fixed.
            block = render(name, agents_dir, rules_dir)
        if block:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": block,
            }}))
    except Exception:
        pass  # fail-open: rule delivery never blocks a dispatch
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
