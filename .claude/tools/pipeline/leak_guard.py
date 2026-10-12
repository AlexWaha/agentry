"""Leak guard (task-0041): refuse to publish project-internal `.agentry/` files.

The rule is one boundary test. `origin/main` is the already-published state, so
anything under `.agentry/` that a commit being published adds on top of it is new
and must not go out; the template files that are already tracked there are not
"added". Every commit of the range is read, not just the tip tree, so a file
added in one commit and removed in the next still counts: both commits go out.

    git log --name-only --no-renames --diff-filter=A --format= origin/main..REF -- ':(top).agentry'

must be empty. A ref that starts with `-` or holds whitespace or a control
character is refused (git could read it as an option). Exit codes: 0 clean,
1 leaked paths (listed on stderr), 2 the check could not run (git error,
unknown ref, no origin/main). The gate fails closed: 2 blocks the push exactly
like 1.

Known gap, accepted by the CEO: a modification of an already-tracked
`.agentry/project/*.md` is not an addition and is not caught.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

GIT_TIMEOUT = 30
BASE = "origin/main"
# Anchored at the repo root: a bare `.agentry` pathspec is cwd-relative, so a push
# run from `.claude/tools` would match nothing and ship a leak with exit 0.
TOP_AGENTRY = ":(top).agentry"


def _git(repo, *args: str) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-c", "color.ui=false", *args], cwd=str(repo),
                              capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, f"git could not run: {exc}"
    return done.returncode, done.stdout if done.returncode == 0 else (done.stderr or done.stdout)


def _bad_ref(ref: str) -> bool:
    return (not ref or ref.startswith("-")
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in ref))


def check(ref: str, repo) -> tuple[int, list[str]]:
    """(0, []) clean, (1, leaked paths), (2, [error text]) when it cannot tell."""
    if not isinstance(ref, str) or _bad_ref(ref):
        return 2, [f"refusing ref {ref!r}: empty, starts with '-', or has whitespace or control characters"]
    code, out = _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{BASE}^{{commit}}")
    if code:
        return 2, [f"no {BASE} to compare against"]
    code, out = _git(repo, "log", "-z", "--name-only", "--no-renames", "--diff-filter=A",
                     "--format=", "--end-of-options", f"{BASE}..{ref}", "--", TOP_AGENTRY)
    if code:
        return 2, [f"git log failed: {out.strip()}"]
    leaked = list(dict.fromkeys(path for path in out.split("\0") if path))
    return (1, leaked) if leaked else (0, [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="HEAD", help="commit to check (default HEAD)")
    code, lines = check(parser.parse_args(argv).ref, Path.cwd())
    if code:
        print("leak_guard: " + ("project-internal files would be published:" if code == 1
                                else "cannot verify, treating as a leak:"), file=sys.stderr)
        for line in lines:
            print(f"  {line}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
