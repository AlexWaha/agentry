#!/usr/bin/env python3
"""Run a stage's exit gate for the repository the task actually lives in.

A build pipeline's gates get written for whichever stack came first, so `test`
runs that stack's suite and `review` runs its linter for everything. A task in
an infrastructure repo has no application container, no dependency manifest and
no screens, so those gates are wasted minutes followed by a failure that says
nothing about the change.

This reads the task's own `repo:` frontmatter - the same field `git_state.py`
scopes merge detection by - and looks it up in the `repos{}` object of
pipeline.json (contract C-3, FR-42): each entry carries a path, a stack
identifier and a command list per stage. Nothing about any one project lives in
this file, so a second repo entry with another stack changes which commands run
without a code edit (FR-44).

An empty or absent command list is not a failure. It is the honest answer for a
repository of YAML and markdown, and it prints an explicit pass naming the repo
and the stage (FR-43). With repos{} present, a task whose `repo:` is empty or names no
entry FAILS the gate; with repos{} absent the gate is a diagnostic pass ("NOT
CONFIGURED"), worded so a green line is never mistaken for a gate that ran.

Ported from the `fin.local` tree (FR-6); its `STACKS` / `STACK_CWD` tables named
one project's containers and package scripts and are gone.

CLI:
    python stack_gate.py --task task-0004 --stage test [--repo KEY]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import git_state
import state

STAGES = ("implement", "test", "review")


# The one rule for turning a task's `repo:` into a repos{} key. It lives in
# git_state, which scopes merge detection by the same field, so the gate and the
# merge check cannot resolve one value to two different repos.
resolve_key = git_state.resolve_key


def commands(entry: dict, stage: str) -> list[str]:
    """The gate commands for this repo entry at this stage. Empty means no gate."""
    return [str(c) for c in (entry["stages"].get(stage) or [])]


def run(cmds: list[str], cwd: Path) -> int:
    # shell=True on config strings, same trust as gate.py: the config is the
    # CEO's, and no agent profile may write pipeline.json.
    for cmd in cmds:
        print(f"-> {cmd}", flush=True)
        code = subprocess.run(cmd, shell=True, cwd=str(cwd)).returncode
        if code != 0:
            return code
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-repo exit gate")
    ap.add_argument("--task", required=True)
    ap.add_argument("--stage", required=True, choices=STAGES)
    ap.add_argument("--repo", help="repos{} key; defaults to the task's `repo:`")
    args = ap.parse_args()

    entries = git_state.repo_entries()
    repo = args.repo or state.task_repo(args.task) or ""
    key = resolve_key(entries, repo)

    if not key:
        # Two different facts, and the message must not blur them. With no
        # repos{} this gate checks NOTHING for any repository - saying "no
        # entry for repo X" there reads like a gate that ran and found X
        # exempt, which is how a green line gets trusted. With repos{} present an
        # empty or unknown `repo:` is a FAILURE: a task that names no configured
        # repo would otherwise skip every gate by leaving the field blank or
        # misspelling it. Only a configured empty stage (FR-43) and an absent
        # repos{} pass.
        if not entries:
            print(
                f"{args.stage} gate: NOT CONFIGURED. pipeline.json has no usable repos{{}} "
                f"object, so no repository has a gate here and nothing was checked "
                f"for {args.task}."
            )
            return 0
        print(
            f"{args.stage} gate: FAILED. repos{{}} has no entry for repo "
            f"'{repo or 'unknown'}' ({args.task}); nothing was checked. Set the "
            f"task's `repo:` to one of: {', '.join(sorted(entries))}."
        )
        return 1

    cmds = commands(entries[key], args.stage)
    if not cmds:
        print(f"{key}: no {args.stage} gate - explicit pass")
        return 0

    cwd = git_state.repo_path(key)
    if cwd is None or not cwd.is_dir():
        print(f"{key}: {args.stage} gate has commands but its path "
              f"'{entries[key]['path']}' is not a directory; nothing was run.")
        return 1
    return run(cmds, cwd)


if __name__ == "__main__":
    sys.exit(main())
