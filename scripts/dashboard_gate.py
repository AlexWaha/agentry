#!/usr/bin/env python3
"""Gate for the dashboard SPA sources in dashboard/ (FR-118).

    python scripts/dashboard_gate.py typecheck|test|lint

Runs `npm run <stage>` inside dashboard/, but only when dashboard/ differs from
the trunk. "Differs" counts committed work on the branch, staged and unstaged
edits, and untracked files, because nothing is committed until the end of a task.
When dashboard/ is unchanged the script exits 0 without looking for Node, so every
task that never touches the SPA, and every machine without Node, is unaffected.

Exit status:
  0  nothing changed (nothing ran), or the npm command passed
  N  the npm command's own status
  1  dashboard/ changed and Node or npm is missing, or its dependencies are not installed
  2  bad arguments (argparse)

The repository root is the parent of this script's folder, so a task worktree
gates its own copy of dashboard/. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

STAGES = ("typecheck", "test", "lint")
DASHBOARD_DIR = "dashboard"
DEFAULT_TRUNK = "main"
# Never part of a change: installed packages and test output, whether or not .gitignore is present.
EXCLUDE = (f":(exclude){DASHBOARD_DIR}/node_modules", f":(exclude){DASHBOARD_DIR}/coverage")

EXIT_MISSING_TOOL = 1


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def trunk_name(root: Path) -> str:
    """`main_branch` from the repo's pipeline.json, else main."""
    try:
        config = json.loads((root / ".agentry" / "pipeline.json").read_text(encoding="utf-8"))
        name = config.get("main_branch")
        return name if isinstance(name, str) and name else DEFAULT_TRUNK
    except (OSError, ValueError):
        return DEFAULT_TRUNK


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def trunk_ref(root: Path, name: str) -> str | None:
    """The first of `name` and `origin/name` that resolves to a commit."""
    for ref in (name, f"origin/{name}"):
        if git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0:
            return ref
    return None


def dashboard_changed(root: Path) -> bool:
    """True when dashboard/ differs from the trunk or holds untracked files.

    No trunk ref, or a git failure, counts as changed: running the check on a
    doubt is safe, skipping it is not."""
    if not (root / DASHBOARD_DIR).is_dir():
        return False
    ref = trunk_ref(root, trunk_name(root))
    if ref is None:
        return True
    diff = git(root, "diff", "--name-only", ref, "--", DASHBOARD_DIR, *EXCLUDE)
    if diff.returncode != 0 or diff.stdout.strip():
        return True
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", DASHBOARD_DIR, *EXCLUDE)
    return untracked.returncode != 0 or bool(untracked.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a dashboard/ check when dashboard/ changed.")
    parser.add_argument("stage", choices=STAGES)
    stage = parser.parse_args(argv).stage

    root = repo_root()
    if not dashboard_changed(root):
        print(f"dashboard_gate: dashboard/ is unchanged against the trunk, {stage} skipped")
        return 0

    npm = shutil.which("npm")
    if npm is None:
        print(
            f"dashboard_gate: dashboard/ has changes but Node.js and npm were not found on PATH, "
            f"so `npm run {stage}` cannot run. Install Node (a dev machine needs it; end users do not).",
            file=sys.stderr,
        )
        return EXIT_MISSING_TOOL
    if not (root / DASHBOARD_DIR / "node_modules").is_dir():
        print(
            f"dashboard_gate: dashboard/node_modules is missing. Run `npm ci` in dashboard/ first "
            f"(needs Node), then `npm run {stage}`.",
            file=sys.stderr,
        )
        return EXIT_MISSING_TOOL

    print(f"dashboard_gate: dashboard/ changed, running `npm run {stage}`", flush=True)
    return subprocess.run([npm, "run", stage], cwd=root / DASHBOARD_DIR, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
