#!/usr/bin/env python3
"""Exit gate for stages whose output is a document, not a test run.

Building has commands that pass or fail on their own. Planning has none: there
is nothing to execute after writing a plan. The temptation is to leave those
stages ungated, but an ungated stage is one the model closes by asserting it is
finished - exactly what the pipeline exists to prevent.

So a planning stage is gated on its artifact. The brief, the plan, the spec must
exist, be about this task, and carry actual content rather than a heading and a
promise. Cheap to check, impossible to fake by claiming.

Kinds:
  brief   .claude/plans/*<task>*-brief.md   the problem statement
  plan    .claude/plans/*<task>*.md         the plan itself, brief excluded
  spec    .claude/specs/*<task>*.md         the spec
  tasks   .claude/tasks/backlog/*.md        task files the breakdown produced

`--glob` replaces the four kinds with any path pattern, for a project whose
planning artifact is neither a plan nor a spec. The task-naming and content
checks are identical either way.

Exit 0 when satisfied, 1 with a reason on stdout otherwise.

CLI:
    python artifact_gate.py --task task-0007 --kind plan
    python artifact_gate.py --task task-0007 --glob "docs/design/*.md" --label design
"""

from __future__ import annotations

import argparse
from pathlib import Path

import state

MIN_CHARS = 400  # a heading plus a sentence is not a plan


def _matches(folder: Path, task: str, exclude: str = "") -> list[Path]:
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.md")):
        if task not in p.stem:
            continue
        if exclude and p.stem.endswith(exclude):
            continue
        out.append(p)
    return out


def _substantial(paths: list[Path]) -> list[Path]:
    kept = []
    for p in paths:
        try:
            if len(p.read_text(encoding="utf-8", errors="replace").strip()) >= MIN_CHARS:
                kept.append(p)
        except OSError:
            continue
    return kept


def check(task: str, kind: str) -> tuple[bool, str]:
    plans = state.ROOT / ".claude" / "plans"
    specs = state.ROOT / ".claude" / "specs"

    if kind == "brief":
        found = _substantial([p for p in _matches(plans, task) if p.stem.endswith("-brief")])
        if found:
            return True, f"brief: {found[0].name}"
        return False, (f"no brief for {task}: write the problem statement to "
                       f".claude/plans/<date>-<slug>-{task}-brief.md - what hurts, "
                       f"scope, non-goals, open questions. At least {MIN_CHARS} characters.")

    if kind == "plan":
        found = _substantial(_matches(plans, task, exclude="-brief"))
        if found:
            return True, f"plan: {found[0].name}"
        return False, (f"no plan for {task}: write it to "
                       f".claude/plans/<date>-<slug>-{task}.md. The brief alone does "
                       f"not close this stage.")

    if kind == "spec":
        found = _substantial(_matches(specs, task))
        if found:
            return True, f"spec: {found[0].name}"
        return False, (f"no spec for {task}: write it to .claude/specs/ with testable "
                       f"acceptance criteria.")

    if kind == "tasks":
        produced = [p for p in state.BACKLOG_DIR.glob("task-*.md")] \
            if state.BACKLOG_DIR.is_dir() else []
        if produced:
            return True, f"{len(produced)} task file(s) queued in tasks/backlog/"
        return False, ("breakdown produced no task files: a spec that is not sliced "
                       "into tasks in .claude/tasks/backlog/ is half done.")

    return False, f"unknown artifact kind '{kind}'"


def check_glob(task: str, pattern: str, label: str) -> tuple[bool, str]:
    """Generic form for a project whose artifacts are not plans and specs: any
    glob, still required to name the task and carry real content.

    The four kinds above hardcode this template's own document layout. A project
    that gates a planning stage on a design file, an ADR or a data contract needs
    the same two checks against a different path, and that is the whole
    difference - so it is a pattern argument, not a fifth kind."""
    found = _substantial([p for p in state.ROOT.glob(pattern) if task in p.stem])
    if found:
        return True, f"{label}: {found[0].name}"
    return False, (f"no {label} for {task}: expected a file matching {pattern} "
                   f"naming the task, with at least {MIN_CHARS} characters.")


def _main() -> int:
    parser = argparse.ArgumentParser(description="Document-artifact exit gate")
    parser.add_argument("--task", required=True)
    parser.add_argument("--kind", choices=["brief", "plan", "spec", "tasks"])
    parser.add_argument("--glob", help="artifact path pattern, relative to the project root")
    parser.add_argument("--label", default="artifact")
    args = parser.parse_args()

    if args.glob:
        ok, message = check_glob(args.task, args.glob, args.label)
    elif args.kind:
        ok, message = check(args.task, args.kind)
    else:
        print("pass --kind or --glob")
        return 1

    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
