"""Leak guard (task-0041): refuse to publish project-internal `.agentry/` files.

The rule is one boundary test. `<remote>/<trunk>` is the already-published state
(`origin/main` by default: the trunk is `main_branch` in pipeline.json, read by
`trunk_name()`, which approve.py shares, and the remote is the optional `remote` key). Anything under
`.agentry/` that a commit being published adds on top of it is new and must not go
out; the template files that are already tracked there are not "added". Every
commit of the range is read, not just the tip tree, so a file
added in one commit and removed in the next still counts: both commits go out.

    git log --name-only --no-renames --diff-filter=A --format= <remote>/<trunk>..REF -- ':(top).agentry'

must be empty. A ref that starts with `-` or holds whitespace or a control
character is refused (git could read it as an option). Exit codes: 0 clean,
1 leaked paths (listed on stderr), 2 the check could not run (git error,
unknown ref, no <remote>/<trunk>). The gate fails closed: 2 blocks the push exactly
like 1.

Known gap, accepted by the User: a modification of an already-tracked
`.agentry/project/*.md` is not an addition and is not caught.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

import state

GIT_TIMEOUT = 30
DEFAULT_REMOTE = "origin"
DEFAULT_TRUNK = "main"
# Anchored at the repo root: a bare `.agentry` pathspec is cwd-relative, so a push
# run from `.claude/tools` would match nothing and ship a leak with exit 0.
TOP_AGENTRY = ":(top).agentry"
# A remote is one name; a trunk may carry slashes. Anything else is refused: the
# base is spliced into a ref, and `heads/main` style names would resolve to
# something other than the published trunk.
REMOTE_RE = re.compile(r"[A-Za-z0-9._-]+")
TRUNK_RE = re.compile(r"[A-Za-z0-9._/-]+")


def _config_name(key: str, default: str) -> str:
    """A string value from pipeline.json; an absent, non-string or unresolved
    `{{PLACEHOLDER}}` value reads as the default."""
    value = state.load_pipeline().get(key)
    if isinstance(value, str) and value.strip() and not value.startswith("{{"):
        return value.strip()
    return default


def trunk_name() -> str:
    """The configured trunk (`main_branch`), the template placeholder resolved: the
    one reader of that key for the leak guard and approve.py, so the branch whose
    publication is approved and the base the leak check compares against cannot
    name different branches."""
    return _config_name("main_branch", DEFAULT_TRUNK)


def base_ref() -> str:
    """The already-published state the range starts from: <remote>/<trunk>.
    Raises ValueError for a remote or trunk with characters outside a ref name.
    The `remote` key is optional and defaults to origin."""
    remote = _config_name("remote", DEFAULT_REMOTE)
    trunk = trunk_name()
    if not REMOTE_RE.fullmatch(remote) or not TRUNK_RE.fullmatch(trunk):
        raise ValueError(f"remote {remote!r} or trunk {trunk!r} in pipeline.json is not a plain ref name")
    return f"{remote}/{trunk}"


def _git(repo, *args: str, deadline: float | None = None) -> tuple[int, str]:
    """`deadline` is a time.monotonic() instant shared by a caller's whole path:
    each git call gets what is left of it, and none left is an error (exit 2)."""
    timeout = GIT_TIMEOUT
    if deadline is not None:
        timeout = min(GIT_TIMEOUT, deadline - time.monotonic())
        if timeout <= 0:
            return 2, "the time budget for this check ran out"
    try:
        done = subprocess.run(["git", "-c", "color.ui=false", *args], cwd=str(repo),
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, f"git could not run: {exc}"
    return done.returncode, done.stdout if done.returncode == 0 else (done.stderr or done.stdout)


def _bad_ref(ref: str) -> bool:
    return (not ref or ref.startswith("-")
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in ref))


def check(ref: str, repo, deadline: float | None = None) -> tuple[int, list[str]]:
    """(0, []) clean, (1, leaked paths), (2, [error text]) when it cannot tell."""
    if not isinstance(ref, str) or _bad_ref(ref):
        return 2, [f"refusing ref {ref!r}: empty, starts with '-', or has whitespace or control characters"]
    try:
        base = base_ref()
    except ValueError as exc:
        return 2, [str(exc)]
    # The full refs/remotes/ name: a bare <remote>/<trunk> is a DWIM revision, so a
    # remote named `heads` would read refs/heads/<trunk> and the range would be empty.
    full = f"refs/remotes/{base}"
    code, out = _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{full}^{{commit}}",
                     deadline=deadline)
    if code:
        return 2, [f"no {base} to compare against" + (f" ({out.strip()})" if out.strip() else "")]
    code, out = _git(repo, "log", "-z", "--name-only", "--no-renames", "--diff-filter=A",
                     "--format=", "--end-of-options", f"{full}..{ref}", "--", TOP_AGENTRY,
                     deadline=deadline)
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
