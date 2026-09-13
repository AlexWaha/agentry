#!/usr/bin/env python3
"""Run a stage's exit gate for the stack the task actually lives in.

A build pipeline's gates get written for whichever stack came first, so `test`
runs that stack's suite and `review` runs its linter for everything. A task in
an infrastructure repo has no application container, no dependency manifest and
no screens, so those gates are wasted minutes followed by a failure that says
nothing about the change.

This reads the task's own `repo:` frontmatter - the same field `git_state.py`
scopes merge detection by - and dispatches to the matching stack. A repo with no
entry in STACKS is not "unsupported": it is a stack whose gate is a no-op, which
is the honest answer for a repository holding YAML and markdown.

Ported from the `fin.local` tree (FR-6). Its `STACKS` / `STACK_CWD` dicts named
one project's containers and package scripts, so they are NOT ported as values:
the template ships the mechanism with the tables empty, which makes every repo a
no-op until a project fills them in. task-0029 (FR-44) replaces both dicts with
the `repos{}` block in pipeline.json and deletes them.

CLI:
    python stack_gate.py --task task-0004 --stage test
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import state

ROOT = state.ROOT

# `repo:` frontmatter value -> per-stage gate commands, run in STACK_CWD's
# directory. Shape, for a project filling this in by hand before FR-44 lands:
#
#   STACKS = {"backend": {"test": ["<test cmd>"], "review": ["<lint cmd>"]}}
#   STACK_CWD = {"backend": ROOT / "backend"}
#
# A stack may declare only some stages; the stages it omits are no-ops, same as
# an unknown repo. A missing STACK_CWD entry falls back to the workspace root
# rather than raising, so a half-filled table degrades to a working gate.
STACKS: dict[str, dict[str, list[str]]] = {}
STACK_CWD: dict[str, Path] = {}

STAGES = ("test", "review")


def resolve_stack(repo: str) -> str:
    """Loose match, because task files carry both `backend` and `backend/src`
    for the same repo."""
    head = repo.replace("\\", "/").strip("/").split("/")[0]
    return head if head in STACKS else ""


def commands(repo: str, stage: str) -> list[str]:
    """The gate commands for this repo at this stage. Empty means no-op."""
    stack = resolve_stack(repo)
    if not stack:
        return []
    return [str(c) for c in (STACKS[stack].get(stage) or [])]


def run(cmds: list[str], cwd: Path) -> int:
    for cmd in cmds:
        print(f"-> {cmd}", flush=True)
        code = subprocess.run(cmd, shell=True, cwd=str(cwd)).returncode
        if code != 0:
            return code
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-stack exit gate")
    ap.add_argument("--task", required=True)
    ap.add_argument("--stage", required=True, choices=sorted(STAGES))
    args = ap.parse_args()

    repo = state.task_repo(args.task) or ""
    cmds = commands(repo, args.stage)

    if not cmds:
        # Two different facts, and the message must not blur them. An empty
        # STACKS means this gate checks NOTHING for any repository - saying
        # "no application stack configured for repo X" there reads like a gate
        # that ran and found X exempt, which is how a green line gets trusted.
        if not STACKS:
            print(
                f"{args.stage} gate: NOT CONFIGURED. stack_gate.STACKS is empty, so no "
                f"repository has a gate here and nothing was checked for {args.task}. "
                f"Fill STACKS / STACK_CWD (or wait for the repos{{}} config in FR-44) "
                f"before relying on this gate."
            )
        else:
            print(
                f"{args.stage} gate: no application stack configured for repo "
                f"'{repo or 'unknown'}' ({args.task}). Nothing to build, lint or test "
                f"here - the change is verified by its own stage work, not by an app "
                f"suite."
            )
        return 0

    return run(cmds, STACK_CWD.get(resolve_stack(repo), ROOT))


if __name__ == "__main__":
    sys.exit(main())
