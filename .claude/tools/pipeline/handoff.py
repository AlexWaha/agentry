#!/usr/bin/env python3
"""Handoff documentation chain - forced context absorption between tasks.

Before any NEW task registers in the pipeline, every completed task above the
configured baseline must have a valid handoff doc in .agentry/tasks/handoffs/.
The doc is written by the NEXT task's assignee IN ITS OWN WORDS - the act of
writing forces the incoming agent to absorb the previous task's context (task
file, merge diff, gate history) plus the project context and its own task's
spec, before implementing anything.

Enforced deterministically in three places (never by prompt goodwill):
  - advance.py refuses registration of a new task while handoff debt exists;
  - stop_gate.py blocks "start next task" until the doc is written;
  - pretool_gate.py (config flag handoff.hard_edit_gate) freezes code edits
    while debt exists and no task is mid-stage.

Config lives in pipeline.json under the top-level "handoff" key:
  enabled (bool), dir (str), baseline (task id; lower/equal ids are exempt),
  hard_edit_gate (bool), min_section_chars (int), min_total_chars (int),
  max_total_bytes (int), max_bytes_exempt (list of task ids), thread_max_bytes
  (int).
Missing block = feature OFF. Every public function is fail-open: an internal
error reports "no debt" rather than deadlocking the pipeline. The two byte caps
fail open INDIVIDUALLY too: a missing, malformed or non-positive value disables
that cap instead of rejecting every document, because this module is called by
advance.py, stop_gate.py and pretool_gate.py and a cap that rejects everything
would block registration and freeze edits tree-wide.

CLI:
    python handoff.py --check                    JSON debt report, exit 1 on debt
    python handoff.py --for task-0016 [--force]  scaffold a pre-filled doc
    python handoff.py --inject                   SubagentStart chain injection
    python handoff.py --waive task-0016 --reason "..."   orchestrator-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import state

REQUIRED_SECTIONS = ("What was done", "Key decisions", "Files touched",
                     "Gotchas and lessons", "Impact on next tasks", "Context loaded")
THREAD_SECTIONS = ("Where the chain stands", "What is decided", "What remains")
FILL_MARKER = "FILL-ME"
DEFAULT_DIR = ".agentry/tasks/handoffs"
EPICS_DIR = ".agentry/tasks/epics"
EPIC_ID_RE = re.compile(r"^(epic-\d+)")
TEMPLATE_PATH = state.ROOT / ".agentry" / "tasks" / "templates" / "handoff-template.md"

TASK_NUM_RE = re.compile(r"(\d+)$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Minimal embedded skeleton used when templates/handoff-template.md is missing,
# so the scaffold never fails on a partially synced clone.
FALLBACK_TEMPLATE = """---
task: {{TASK}}
title: {{TITLE}}
author: agent-name
date: {{DATE}}
merge_commit: {{MERGE_COMMIT}}
---

# Handoff: {{TASK}} - {{TITLE}}

## What was done
FILL-ME

## Key decisions
FILL-ME

## Files touched
{{FILES}}

## Gotchas and lessons
FILL-ME

## Impact on next tasks
FILL-ME

## Context loaded
FILL-ME:
- .agentry/project/project-context.md - <takeaway>
- spec for my upcoming task (<spec-id>, or none with reason) - <takeaway>
- .agentry/tasks/done/{{TASK}}.md and its diff on main - <takeaway>
"""


def _frontmatter(text: str) -> dict:
    """Tiny YAML-less frontmatter reader (same shape as advance.py's)."""
    fields: dict[str, str] = {}
    m = FRONTMATTER_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t", "-")):
                k, v = line.split(":", 1)
                fields[k.strip()] = v.strip()
    return fields


def cfg() -> dict:
    c = state.load_pipeline().get("handoff", {})
    return c if isinstance(c, dict) else {}


def enabled() -> bool:
    try:
        return bool(cfg().get("enabled"))
    except Exception:
        return False


def cap(key: str) -> int | None:
    """A byte cap from config, or None when there is no usable one.

    None means "do not enforce", and every unusable value maps to it: absent,
    null, a string, a float, a bool (True is an int to Python and 1 byte is not
    a cap anyone meant), zero and negatives. This is the NFR-4 fail-open
    direction and it is the whole reason this helper exists instead of an inline
    int(): the existing thin-checks read their config with a bare int(), which
    raises on 'abc', and an exception escaping validate_handoff would be caught
    by uncovered_done_tasks as "no debt" for EVERY task at once. A cap that
    silently switches the debt check off is worse than a cap that is off."""
    try:
        raw = cfg().get(key)
    except Exception:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw > 0 else None


def cap_exempt(task: str) -> bool:
    """True when this task's doc predates the cap and is grandfathered.

    An explicit id list, not a numeric baseline, and the difference is measured
    rather than stylistic: on 2026-09-15 the 18 over-cap documents ran from
    task-0004 to task-0081 while the next document to be written was task-0016,
    so no numeric threshold separates "already written" from "written from now
    on". handoff.baseline cannot express this decision; a list can, and it
    shrinks to nothing as those tasks age out."""
    try:
        raw = cfg().get("max_bytes_exempt")
        if raw is None:
            return False  # no list configured: the cap applies to everything
        if not isinstance(raw, list):
            return True  # malformed list = exempt, never reject on a config bug
        return str(task) in [str(x) for x in raw]
    except Exception:
        return True


def handoff_dir() -> Path:
    d = str(cfg().get("dir", DEFAULT_DIR) or DEFAULT_DIR)
    p = Path(d)
    return p if p.is_absolute() else state.ROOT / p


def handoff_path(task: str) -> Path:
    return handoff_dir() / f"{task}.md"


def rel(p: Path) -> str:
    try:
        return p.relative_to(state.ROOT).as_posix()
    except ValueError:
        return str(p)


def _num(task_id: str) -> int | None:
    m = TASK_NUM_RE.search(str(task_id).strip())
    return int(m.group(1)) if m else None


def covered_required(task: str) -> bool:
    """True when this done task must have a handoff doc. Unparseable ids and
    an unparseable baseline are exempt (fail-open, never deadlock)."""
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


def _section_body(text: str, name: str) -> str | None:
    m = re.search(r"##\s*" + re.escape(name) + r"\s*\n(.*?)(?=\n##\s|\Z)",
                  text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else None


def validate_handoff(path: Path, task: str) -> list[str]:
    """Return a list of problems; empty list = the doc is valid."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ["file unreadable"]

    fm = _frontmatter(text)

    # CEO waiver short-circuit (recorded in the doc itself, auditable in git).
    if fm.get("waived", "").lower() in ("true", "yes") and fm.get("reason", "").strip():
        return []

    problems: list[str] = []
    if fm.get("task", "") != task:
        problems.append(f"frontmatter 'task:' is '{fm.get('task', '')}', expected '{task}'")

    min_section = int(cfg().get("min_section_chars", 40))
    min_total = int(cfg().get("min_total_chars", 500))

    body = FRONTMATTER_RE.sub("", text, count=1)
    for name in REQUIRED_SECTIONS:
        sec = _section_body(body, name)
        if sec is None:
            problems.append(f"missing section '## {name}'")
            continue
        if FILL_MARKER in sec:
            problems.append(f"section '{name}' still contains the {FILL_MARKER} scaffold marker")
            continue
        dense = "".join(sec.split())
        if len(dense) < min_section:
            problems.append(f"section '{name}' is too thin ({len(dense)} chars, need {min_section})")

    if len("".join(body.split())) < min_total:
        problems.append(f"document body is too thin (need {min_total} non-space chars)")

    # The FR-32 cap. Raw file bytes, not the whitespace-stripped characters the
    # two checks above count - a handoff doc is paid for on the wire, so the
    # number that matters is the one on disk.
    max_bytes = cap("max_total_bytes")
    if max_bytes is not None and not cap_exempt(task):
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        if size is not None and size > max_bytes:
            problems.append(f"document is too large ({size} bytes, cap {max_bytes}) - "
                            f"trim it or add {task} to handoff.max_bytes_exempt")

    ctx = _section_body(body, "Context loaded") or ""
    if ctx and FILL_MARKER not in ctx:
        low = ctx.lower()
        if "project-context.md" not in low:
            problems.append("Context loaded must confirm reading .agentry/project/project-context.md")
        if "spec" not in low and "none" not in low:
            problems.append("Context loaded must confirm reading the upcoming task's spec (or state 'none')")

    return problems


def uncovered_done_tasks() -> list[dict]:
    """[{'task','reason'}] for every done task above baseline lacking a valid
    handoff doc, ordered by task id. Scans tasks/done/ only (folder state is the
    single source of truth; runs closed by reconcile cannot create orphan debt).
    Fail-open: [] on any internal error."""
    try:
        if not enabled():
            return []
        done_dir = state.ROOT / ".agentry" / "tasks" / "done"
        if not done_dir.is_dir():
            return []
        out: list[dict] = []
        for f in sorted(done_dir.glob("task-*.md")):
            task = f.stem
            if not covered_required(task):
                continue
            path = handoff_path(task)
            if not path.is_file():
                out.append({"task": task, "reason": f"no handoff doc at {rel(path)}"})
                continue
            problems = validate_handoff(path, task)
            if problems:
                out.append({"task": task, "reason": "; ".join(problems)})
        return out
    except Exception:
        return []


def latest_main_task_undocumented() -> dict | None:
    """The newest '[task-XXXX]'-tagged commit on main whose task lacks a valid
    handoff doc - INCLUDING tasks grandfathered by the baseline and tasks merged
    out-of-band. Used when the ready backlog holds a single task: the incoming
    agent must have the previous work documented (read it if it exists, generate
    it if it does not) before implementing. Config flag:
    handoff.document_latest_on_start. Fail-open: None on any error."""
    try:
        if not enabled() or not cfg().get("document_latest_on_start"):
            return None
        log = subprocess.run(
            ["git", "-C", str(state.ROOT), "log", _main_branch(),
             "--format=%H%x09%s", "-n", "200"],
            capture_output=True, text=True, timeout=8)
        if log.returncode != 0:
            return None
        task_tag = re.compile(r"\[(task-[a-z0-9-]+)\]", re.IGNORECASE)
        for line in log.stdout.splitlines():
            sha, _, subject = line.partition("\t")
            m = task_tag.search(subject)
            if not m:
                continue
            task = m.group(1)
            path = handoff_path(task)
            if path.is_file() and not validate_handoff(path, task):
                return None  # latest task-tagged commit is documented - all good
            return {"task": task, "sha": sha,
                    "reason": ("no handoff doc" if not path.is_file()
                               else "handoff doc invalid")}
        return None  # no task-tagged commits on main yet
    except Exception:
        return None


def _doc_field(path: Path, name: str) -> str:
    try:
        return _frontmatter(path.read_text(encoding="utf-8", errors="replace")
                            ).get(name, "").strip().strip('"')
    except OSError:
        return ""


def chain_docs(directory: Path | None = None) -> list[Path]:
    """Every handoff doc, newest first.

    Ordered by the `date:` frontmatter field, NOT by task id and NOT by
    modification time. Not by id because the ids are not chronological here:
    task-0081's doc was written before task-0016's. Not by mtime because mtime
    records when a file was last TOUCHED - a `--for --force` rescaffold, a path
    sweep (this project ran one in task-0009), a restore or a file sync all
    rewrite it, and any of them would silently promote a stale or grandfathered
    11985-byte document into the full-size slot. The date is written by the
    author and survives all four. mtime then name are tie-breaks, so the order
    is total and the same directory renders the same block twice; a doc with no
    parseable date sorts last (treated as oldest) rather than winning the slot."""
    d = directory or handoff_dir()
    try:
        docs = [p for p in d.glob("task-*.md") if p.is_file()]
    except OSError:
        return []

    def key(p: Path):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (_doc_field(p, "date"), mtime, p.name)

    return sorted(docs, key=key, reverse=True)


def _doc_title(path: Path) -> str:
    return _doc_field(path, "title") or path.stem


INDEX_HEAD = "Older handoff documents - read one deliberately when the task needs it:"
# Below this many bytes a truncated document teaches nothing, so the budget is
# spent on the index instead and the note keeps the path readable.
MIN_BODY_BYTES = 512


def render_injection(directory: Path | None = None) -> str:
    """The FR-33 block: the newest doc in full, every older one as one line.

    The whole chain used to travel on every dispatch and it grows by one
    document per completed task, so the budget FR-32 buys back would be spent
    again within a phase. An index line still names the path, so an agent that
    needs an older document reads it deliberately.

    Capped by handoff.inject_budget_bytes, the same way memory.inject_budget_bytes
    caps the memory block, and failing open the same way (no usable value = no
    cap). FR-32 alone does not bound this block: grandfathered documents are
    exempt from the per-document cap but not from being injected, and the index
    grows by about 100 bytes per completed task forever.

    Over budget, the OLDEST index lines go first and the newest document is
    truncated only after the index is down to nothing. The reverse order was
    built first and measured: at 7168 it cut 1785 bytes out of task-0015 while
    preserving 2526 bytes of pointers to documents nobody had opened. Both
    losses are recoverable, but they are not equally likely to be needed - this
    block's contract is the newest document in full, its own header says so, and
    an agent that wants an old one can list the directory with no index line to
    help it."""
    if not enabled():
        return ""
    docs = chain_docs(directory)
    if not docs:
        return ""
    newest, older = docs[0], docs[1:]
    try:
        body = newest.read_text(encoding="utf-8", errors="replace").rstrip()
    except OSError:
        return ""
    head = f"## Handoff chain: {len(docs)} document(s), newest in full"
    index = [f"- {p.stem} - {_doc_title(p)} - {rel(p)}" for p in older]

    def block(text: str, lines: list[str]) -> str:
        parts = [head, f"Contents of {rel(newest)}:\n\n{text}"]
        if lines:
            parts.append(INDEX_HEAD + "\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def size(text: str) -> int:
        return len(text.encode("utf-8"))

    budget = cap("inject_budget_bytes")
    out = block(body, index)
    if budget is None or size(out) <= budget:
        return out

    # 1. The oldest index lines go first; the newest document stays whole.
    while index and size(out) > budget:
        index.pop()  # the last line is the oldest document
        out = block(body, index)
    if size(out) <= budget:
        return out

    # 2. Only now, with no index left to give, cut the document itself. The
    # note names the path, so the rest is one Read away. This is the branch a
    # grandfathered document reaches: 11985 bytes in an 7168-byte budget cannot
    # be delivered whole however the index is trimmed.
    note = (f"\n\n[truncated to fit handoff.inject_budget_bytes ({budget} bytes) - "
            f"read {rel(newest)} in full]")
    allowance = budget - size(block("", [])) - size(note)
    if allowance >= MIN_BODY_BYTES:
        # Slice the ENCODED body and decode with errors='ignore', so the cut
        # cannot land inside a multi-byte character and produce mojibake.
        body = body.encode("utf-8")[:allowance].decode("utf-8", "ignore")
    else:
        body = ""
    return block(body + note, [])


def epics_dir() -> Path:
    return state.ROOT / Path(EPICS_DIR)


def thread_problems(directory: Path | None = None) -> list[dict]:
    """[{'epic','path','reason'}] for every epic thread doc that EXISTS and is
    malformed or over handoff.thread_max_bytes.

    A missing thread doc is not reported: an adopter with an epic and no thread
    document must not see permanent debt from a check they never opted into.
    Reported by the CLI only - registration reads uncovered_done_tasks(), so an
    oversized thread doc cannot block a task from starting. Fail-open: [] on any
    internal error."""
    try:
        d = directory or epics_dir()
        if not d.is_dir():
            return []
        max_bytes = cap("thread_max_bytes")
        out: list[dict] = []
        for f in sorted(d.glob("epic-*-thread.md")):
            m = EPIC_ID_RE.match(f.name)
            epic = m.group(1) if m else f.stem
            reasons: list[str] = []
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                out.append({"epic": epic, "path": rel(f), "reason": "file unreadable"})
                continue
            for name in THREAD_SECTIONS:
                if _section_body(text, name) is None:
                    reasons.append(f"missing section '## {name}'")
            if max_bytes is not None:
                try:
                    size = os.path.getsize(f)
                except OSError:
                    size = None
                if size is not None and size > max_bytes:
                    reasons.append(f"document is too large ({size} bytes, cap {max_bytes})")
            if reasons:
                out.append({"epic": epic, "path": rel(f), "reason": "; ".join(reasons)})
        return out
    except Exception:
        return []


def _task_title(task: str) -> str:
    for sub in ("done", "active"):
        p = state.ROOT / ".agentry" / "tasks" / sub / f"{task}.md"
        try:
            if p.is_file():
                t = _frontmatter(p.read_text(encoding="utf-8", errors="replace")).get("title", "")
                if t:
                    return t.strip('"')
        except OSError:
            continue
    return task


def _main_branch() -> str:
    b = str(state.load_pipeline().get("main_branch", "main"))
    return "main" if b.startswith("{{") else b


def _merge_facts(task: str) -> tuple[str, str]:
    """(merge_commit, files_section). Finds the '[task-XXXX]' commit on main
    (same convention stop_gate's reconcile uses) and its file list. When the PR
    is not merged yet, returns 'pending' plus a how-to hint."""
    try:
        log = subprocess.run(
            ["git", "-C", str(state.ROOT), "log", _main_branch(),
             "--format=%H%x09%s", "-n", "500"],
            capture_output=True, text=True, timeout=8)
        if log.returncode == 0:
            for line in log.stdout.splitlines():
                sha, _, subject = line.partition("\t")
                if f"[{task}]" in subject:
                    show = subprocess.run(
                        ["git", "-C", str(state.ROOT), "show", "--name-only",
                         "--format=", sha],
                        capture_output=True, text=True, timeout=8)
                    files = [f for f in show.stdout.splitlines() if f.strip()]
                    if show.returncode == 0 and files:
                        return sha, "\n".join(f"- {f}" for f in files)
                    return sha, (f"{FILL_MARKER}: run git show --name-only {sha[:12]} "
                                 f"and list the changed files.")
    except Exception:
        pass
    return "pending", (f"{FILL_MARKER}: PR not merged to {_main_branch()} yet - run "
                       f"git diff {_main_branch()}...<branch> --name-only and list the files.")


def scaffold(task: str, force: bool = False) -> tuple[bool, str]:
    """Create a pre-filled handoff doc. Returns (created, message)."""
    d = handoff_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = handoff_path(task)
    if path.is_file() and not force:
        return False, f"refused: {rel(path)} already exists (use --force to overwrite)"
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        template = FALLBACK_TEMPLATE
    commit, files = _merge_facts(task)
    text = template
    today = state.today()  # local date - the handoff doc is read by a human
    for token, value in (("{{TASK}}", task), ("{{TITLE}}", _task_title(task)),
                         ("{{DATE}}", today),
                         ("{{MERGE_COMMIT}}", commit), ("{{FILES}}", files)):
        text = text.replace(token, value)
    path.write_text(text, encoding="utf-8")
    return True, (f"scaffolded {rel(path)} (merge_commit: {commit}). Fill every FILL-ME "
                  f"section in your own words, then verify: python "
                  f".claude/tools/pipeline/handoff.py --check")


def waive(task: str, reason: str) -> str:
    """CEO-approved escape hatch, orchestrator-only (agent_gate denies it inside
    subagents). Recorded in the doc itself so the waiver is auditable in git."""
    d = handoff_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = handoff_path(task)
    today = state.today()  # local date - the handoff doc is read by a human
    path.write_text(
        f"---\ntask: {task}\nwaived: true\nreason: {reason}\n"
        f"date: {today}\n---\n\n"
        f"# Handoff: {task} - WAIVED\n\n"
        f"Handoff doc waived by CEO decision: {reason}\n",
        encoding="utf-8")
    return rel(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Handoff doc chain: check debt, scaffold, waive")
    parser.add_argument("--check", action="store_true", help="report handoff debt (exit 1 on debt)")
    parser.add_argument("--for", dest="scaffold_for", metavar="TASK", help="scaffold a doc for a done task")
    parser.add_argument("--force", action="store_true", help="overwrite an existing doc when scaffolding")
    parser.add_argument("--inject", action="store_true",
                        help="SubagentStart hook: newest handoff doc in full, older ones as index lines")
    parser.add_argument("--waive", metavar="TASK",
                         help="waive the handoff for a task (orchestrator-only, CEO approval required)")
    parser.add_argument("--reason", default="", help="reason for --waive")
    args = parser.parse_args()

    if args.inject:
        # Its own try/except and its own exit 0: a dispatch must start even if
        # the chain is unreadable. The envelope is mandatory - plain stdout is
        # DISCARDED on SubagentStart, which is how the memory hook delivered
        # nothing at all for weeks at exit 0 (task-0081).
        try:
            block = render_injection()
            if block:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": block,
                }}))
        except Exception:
            pass
        return 0

    try:
        if args.scaffold_for:
            created, msg = scaffold(args.scaffold_for, force=args.force)
            print(msg)
            return 0 if created else 1
        if args.waive:
            if not args.reason.strip():
                print("refused: --waive requires --reason")
                return 1
            print(f"waived: {waive(args.waive, args.reason.strip())}")
            return 0
        # default / --check
        debt = uncovered_done_tasks()
        threads = thread_problems()
        print(json.dumps({
            "enabled": enabled(),
            "baseline": str(cfg().get("baseline", "")),
            "handoff_debt": debt,
            "thread_problems": threads,
        }, indent=2))
        return 1 if (debt or threads) else 0
    except Exception as exc:  # fail-open: a checker bug must not block work
        print(json.dumps({"enabled": False, "error": str(exc), "handoff_debt": []}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
