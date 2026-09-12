#!/usr/bin/env python3
"""Handoff documentation chain - forced context absorption between tasks.

Before any NEW task registers in the pipeline, every completed task above the
configured baseline must have a valid handoff doc in .claude/tasks/handoffs/.
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
  hard_edit_gate (bool), min_section_chars (int), min_total_chars (int).
Missing block = feature OFF. Every public function is fail-open: an internal
error reports "no debt" rather than deadlocking the pipeline.

CLI:
    python handoff.py --check                    JSON debt report, exit 1 on debt
    python handoff.py --for task-0016 [--force]  scaffold a pre-filled doc
    python handoff.py --waive task-0016 --reason "..."   orchestrator-only
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import state

REQUIRED_SECTIONS = ("What was done", "Key decisions", "Files touched",
                     "Gotchas and lessons", "Impact on next tasks", "Context loaded")
FILL_MARKER = "FILL-ME"
DEFAULT_DIR = ".claude/tasks/handoffs"
TEMPLATE_PATH = state.ROOT / ".claude" / "tasks" / "templates" / "handoff-template.md"

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
- .claude/project/project-context.md - <takeaway>
- spec for my upcoming task (<spec-id>, or none with reason) - <takeaway>
- .claude/tasks/done/{{TASK}}.md and its diff on main - <takeaway>
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

    ctx = _section_body(body, "Context loaded") or ""
    if ctx and FILL_MARKER not in ctx:
        low = ctx.lower()
        if "project-context.md" not in low:
            problems.append("Context loaded must confirm reading .claude/project/project-context.md")
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
        done_dir = state.ROOT / ".claude" / "tasks" / "done"
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


def _task_title(task: str) -> str:
    for sub in ("done", "active"):
        p = state.ROOT / ".claude" / "tasks" / sub / f"{task}.md"
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
    for token, value in (("{{TASK}}", task), ("{{TITLE}}", _task_title(task)),
                         ("{{DATE}}", date.today().isoformat()),
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
    path.write_text(
        f"---\ntask: {task}\nwaived: true\nreason: {reason}\n"
        f"date: {date.today().isoformat()}\n---\n\n"
        f"# Handoff: {task} - WAIVED\n\n"
        f"Handoff doc waived by CEO decision: {reason}\n",
        encoding="utf-8")
    return rel(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Handoff doc chain: check debt, scaffold, waive")
    parser.add_argument("--check", action="store_true", help="report handoff debt (exit 1 on debt)")
    parser.add_argument("--for", dest="scaffold_for", metavar="TASK", help="scaffold a doc for a done task")
    parser.add_argument("--force", action="store_true", help="overwrite an existing doc when scaffolding")
    parser.add_argument("--waive", metavar="TASK", help="waive the handoff for a task (orchestrator-only, CEO approval required)")
    parser.add_argument("--reason", default="", help="reason for --waive")
    args = parser.parse_args()

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
        print(json.dumps({
            "enabled": enabled(),
            "baseline": str(cfg().get("baseline", "")),
            "handoff_debt": debt,
        }, indent=2))
        return 1 if debt else 0
    except Exception as exc:  # fail-open: a checker bug must not block work
        print(json.dumps({"enabled": False, "error": str(exc), "handoff_debt": []}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
