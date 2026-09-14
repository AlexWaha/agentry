"""Where the test suite is allowed to write: inside the project, nowhere else.

The measured problem this exists for (task-0069): every `tempfile` call in this
directory ran with no `dir=`, so the 45 throwaway git repositories one run
builds, plus the supervisor's lock sandboxes, were created under the HOST temp
directory - `C:\\Users\\<user>\\AppData\\Local\\Temp` on the machine where it
was found. Twelve `supervisor*.lock` files and nineteen leftover directories
were still sitting there across sessions, and six `sup-test-*` directories
survived long enough to be counted while this task was being written.

Why that is worse than untidy. Every check this project runs looks INSIDE the
project: the dash scan, the `nul`/`NUL` sweep, `git status`, the gitignore
audit, every "the tree is clean" assertion. An artifact written outside the
project cannot be reported by any of them, so the leak was invisible rather
than known and tolerated - agents reported clean trees for a day while the
locks piled up.

Four layers, so the next test cannot get this wrong by copying an older one:

1. `TMP_ROOT` below is the ONE place the location is decided. Tests ask for a
   sandbox with `sandbox(self, prefix)` and never name a path themselves.
2. A process-wide redirect: `tempfile.tempdir` and the `TMPDIR`/`TEMP`/`TMP`
   environment variables all point at `RUN_ROOT`, so a call that forgets `dir=`
   still lands inside the project, and so does every child process the suite
   spawns.
3. An audit hook records every directory and every file this process creates
   outside the project, into `ESCAPES`. This is the only layer that observes
   rather than prescribes, and the only one that would have caught the original
   leak on the day it started.
4. `test_zz_tmp_policy.py` asserts the invariants: `ESCAPES` is empty, the run
   directory is empty when the suite ends, nothing new appeared in the host temp
   directory, and no test file touches `tempfile` at all.

Importing this module is what arms layer 2, so every test module that needs a
temporary path imports it - and after layer 3 that is every test module which
uses one.
"""

from __future__ import annotations

import atexit
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

# .claude/tools/tests/tmproot.py -> the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The one decision. `tmp/` at the project root, per the CEO's position on
# task-0069: temp work belongs in a tmp directory inside the project and should
# never leave it. Gitignored as `/tmp/`.
TMP_ROOT = PROJECT_ROOT / "tmp"

# Captured BEFORE the redirect below, so the policy test has the real host temp
# directory to check and can tell a leak from a redirect. The snapshot is what
# was there when the suite started; anything new that matches a prefix the suite
# hands out (or the stdlib's bare `tmp` default) is an escape.
HOST_TMP = Path(tempfile.gettempdir()).resolve()
HOST_TMP_AT_START = frozenset(os.listdir(HOST_TMP)) if HOST_TMP.is_dir() else frozenset()

# Every prefix sandbox() has been asked for this run, registered rather than
# hardcoded so the list cannot rot as tests are added. Seeded with the stdlib's
# own default prefix (`tmp`, what a forgotten `tempfile.mkdtemp()` produces) and
# with `supervisor`, the name of the lock file that leaked twelve times.
PREFIXES: set[str] = {"tmp", "supervisor"}

# One directory per RUNNING PROCESS under TMP_ROOT, and it is not decoration.
# MEASURED while building this: a second suite running in the same workspace
# (the orchestrator verifying while an agent works) put its own sandboxes in
# TMP_ROOT, and the empty-at-the-end assertion below read them as this run's
# leak - a `gate_repo_*` from test_pretool_gate_git.py appeared during a run of
# test_supervisor.py alone. Under the host temp that collision was invisible
# because nothing asserted anything. Scoping the assertion to this pid keeps it
# strict about THIS run and blind to other runs, which is the only way it can be
# both loud and non-flaky.
RUN_ROOT = TMP_ROOT / f"run-{os.getpid()}"
RUN_ROOT.mkdir(parents=True, exist_ok=True)

# A run killed before its atexit hook leaves an empty `run-<pid>` behind. It is
# NOT swept at import, and that is a deliberate reversal: the sweep that used to
# live here was the sole reason the empty-at-the-end assertion needed a "the
# directory is missing, call it empty" escape hatch, and an escape hatch in the
# strongest guard this task has is worth more than a tidy directory. A leftover
# `run-*` is evidence that a run died, which is exactly what this task wants
# visible. _drop_run_root below removes this run's own.

# Layer 2. `tempfile.tempdir` covers this process; the environment variables
# cover the git and python children the suite spawns.
tempfile.tempdir = str(RUN_ROOT)
for _var in ("TMPDIR", "TEMP", "TMP"):
    os.environ[_var] = str(RUN_ROOT)

# One consequence of moving in, and it is load-bearing. Two tests in
# test_pretool_gate_git.py need a directory that is NOT inside any git
# repository (repo_bootstrap must fall through a non-repo candidate rather than
# read it as an unborn HEAD). Under the host temp that was free; inside the
# project every directory is inside THIS repository, so `git -C <sandbox>
# rev-parse` walked up and found it - measured, the test failed on the move.
# A ceiling at TMP_ROOT restores the old answer from git itself instead of
# faking it: discovery stops here, so a sandbox with no `.git` of its own is
# genuinely not a repository, while a sandbox that ran `git init` still is.
# Commands run against the project root are unaffected - TMP_ROOT is not above
# it.
os.environ["GIT_CEILING_DIRECTORIES"] = str(TMP_ROOT)

# How long to keep trying to delete a sandbox. Windows releases a file handle a
# moment after the process holding it dies, and the suite kills real children.
_DELETE_ATTEMPTS = 12
_DELETE_PAUSE = 0.25


def _clear_readonly(func, path, exc) -> None:
    """rmtree's error hook: git writes its object files read-only, which makes a
    plain delete fail on Windows. Clear the bit and retry the one operation."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise exc from None


def rmtree(path) -> None:
    """Delete a sandbox for real, and say so loudly when it cannot be deleted.

    The old cleanups passed `ignore_errors=True`, which is exactly how the six
    leftover `sup-test-*` directories survived: a detached grandchild still held
    its inherited log handle open, the delete failed, and nothing said a word.
    Here a failure raises, so a cleanup that does not clean up fails the test.
    """
    target = Path(path)
    last: OSError | None = None
    for _ in range(_DELETE_ATTEMPTS):
        if not target.exists():
            return
        try:
            shutil.rmtree(target, onexc=_clear_readonly)
            return
        except OSError as exc:
            last = exc
            time.sleep(_DELETE_PAUSE)
    if target.exists():
        raise AssertionError(f"sandbox {target} could not be deleted: {last}")


def mkdtemp(prefix: str) -> Path:
    """A project-local temporary directory. Prefer sandbox() - this one has no
    cleanup attached and is for the rare caller that owns its own lifetime."""
    PREFIXES.add(prefix)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=RUN_ROOT))


def sandbox(case, prefix: str) -> Path:
    """A project-local temporary directory, deleted when `case` finishes.

    `addCleanup` rather than a try/finally the next author forgets, and rather
    than tearDown, so the directory goes even when setUp itself raises halfway
    through.
    """
    path = mkdtemp(prefix)
    case.addCleanup(rmtree, path)
    return path


def inside_project(path) -> bool:
    """True when `path` is the project tree or under it. The question the policy
    test asks about every location the suite writes to.

    Defined ABOVE the audit hook on purpose: a hook that raises makes the
    audited operation itself fail, so a NameError in here would break whatever
    the suite was opening rather than report anything.
    """
    try:
        Path(path).resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return False
    return True


# Every path this process created outside the project tree, as observed rather
# than as reasoned about. See _watch() below.
ESCAPES: list[str] = []

# The audit events that create something on disk, all three verified on this
# interpreter (3.12.4) rather than read off the docs:
#   os.mkdir          - every directory. Fires for Path.mkdir, os.makedirs,
#                       shutil, tempfile.mkdtemp and TemporaryDirectory.
#   open              - every file. Fires for builtin open(), Path.write_text
#                       and os.open; there is NO `os.open` event, which is why
#                       an earlier version watching that name saw nothing while
#                       `open` fired with ('tmp/x.txt', 'w', 33665).
#   tempfile.mkstemp  - mkstemp and NamedTemporaryFile. Does NOT fire for
#                       mkdtemp, whatever an earlier comment here claimed.
# `open` also fires on every read in the suite, so the O_CREAT filter in _watch
# drops those before any path work: measured cost of the whole hook is inside
# the run-to-run noise (see the task file).
_WATCHED = frozenset({"os.mkdir", "open", "tempfile.mkstemp"})


def _watch(event: str, args) -> None:
    """Record any creation outside the project tree.

    This is the observational half of the policy. The constants and the static
    grep say where artifacts SHOULD go; this says where they actually went, and
    it is the only check that would have caught the original leak on the day it
    started rather than a day later. An audit hook cannot be uninstalled, which
    is fine - it is only ever armed by importing a test module.
    """
    if event not in _WATCHED or not args:
        return
    if event == "open":
        flags = args[2] if len(args) > 2 else 0
        if not isinstance(flags, int) or not flags & os.O_CREAT:
            return
    target = args[0]
    if not isinstance(target, (str, bytes, os.PathLike)):
        return
    try:
        if not inside_project(target):
            ESCAPES.append(f"{event}: {os.fsdecode(target)}")
    except (OSError, ValueError):
        pass


sys.addaudithook(_watch)


@atexit.register
def _drop_run_root() -> None:
    """Take the per-run directory itself away when the run ends, so a green run
    leaves TMP_ROOT as it found it. Only when it is empty: a run that leaked has
    already failed the assertion in test_zz_tmp_policy.py, and the evidence is
    worth more than the tidiness."""
    try:
        RUN_ROOT.rmdir()
    except OSError:
        pass
