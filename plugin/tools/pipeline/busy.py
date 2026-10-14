#!/usr/bin/env python3
"""The busy marker: one task's record that work is in flight, with an owner.

The Stop hook stays quiet about a task while its marker is live, so the marker
is a promise that someone is working. This module is the ONLY code that knows
the marker's file name, format and lifecycle: advance.py (a long gate, or a
dispatched subagent), handoff.py (a scaffolded handoff doc) and stop_gate.py
(the reader) all go through it. A marker written by hand has no owner and no
release, which is how a leftover one silenced the conveyor twice (task-0057).

LIFECYCLE
    acquire()  writes it; release() removes it; hold() is acquire + release in
               a finally, for work that runs inside one process. Dispatches and
               scaffolds outlive the process that starts them, so they call
               acquire() and a later release() (advance.py --idle, or
               handoff.py --check dropping the scaffold's marker).
    read()     says which of five things a task's marker is. Never raises.

ONE MARKER, ONE TASK. The file is per task, and acquire() refuses anything that
is not a single task id. Writing one marker for two tasks is what let a dispatch
for task-0009 keep task-0010's run reading as busy.

LIVENESS IS A FACT, THE TIMEOUT IS A BACKSTOP. The marker records its owner: the
Claude Code SESSION's pid. A marker whose owner is gone reads OWNER_GONE and is
treated as released, however young. The timeout still ends a marker whose owner
is alive or unknown, and reads EXPIRED - distinct from ABSENT, so a stale marker
is diagnosable from the hook's own output instead of looking like no marker.

WHY THE SESSION AND NOT THE WRITER. The writer is advance.py, which exits the
moment it has written; a marker naming it is dead on arrival. Measured on this
host (Windows 11, Claude Code 2.1.284), walking parents from a script run
through the Bash tool with a toolhelp snapshot:

    python.exe -> bash.exe -> bash.exe -> bash.exe -> claude.exe (26848)
                                                   -> cmd.exe -> WindowsTerminal.exe

so os.getpid() and os.getppid() both name short-lived shells, and the session is
three levels up. Claude Code exports the answer: CLAUDE_PID is in the
environment of every command it runs and equalled that claude.exe pid, from the
orchestrator's shell and from inside a dispatched subagent alike. The binary
confirms it is set on purpose (`CLAUDE_PID:String(process.pid)`, beside
CLAUDE_CODE_SESSION_ID). That is why session_pid() reads the variable rather
than walking processes: one measured fact instead of a heuristic over image names.

WHAT THIS CANNOT SEE. A script run outside Claude Code (a human terminal) has no
CLAUDE_PID, so its marker records no owner and only the timeout ends it - the
pre-existing behaviour, never a guess. supervisor.pid_alive reads every "cannot
tell" (an OpenProcess failure other than ERROR_INVALID_PARAMETER (87), a probe
exception, and on POSIX a reused pid) as ALIVE, so such a marker stays FRESH until
its timeout and only the timeout ends it. On Windows a reused pid is NOT such a
case: the probe is given the marker's start time, and a process created after it
cannot be the owner, so it reads OWNER_GONE (task-0073).

FAIL-OPEN. Any error reading a marker returns INVALID, which is not fresh: the
hook nags, it never goes silent. Any error writing one is the caller's to
swallow (hold() does) for the same reason.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import state

# Exactly one task id. fullmatch, so "task-0009,task-0010" and "task-0009 x" fail.
TASK_RE = re.compile(r"task-\d+")

# Set by Claude Code in the environment of every command it runs. See the
# module docstring for the measurement.
SESSION_PID_ENV = "CLAUDE_PID"

_PREFIX = "gate-"
_SUFFIX = ".json"

ABSENT = "absent"          # no marker file
FRESH = "fresh"            # work is in flight
EXPIRED = "expired"        # the timeout backstop ran out
OWNER_GONE = "owner_gone"  # the recorded session is no longer running
INVALID = "invalid"        # unreadable, malformed, or naming another task


@dataclass(frozen=True)
class Marker:
    """What read() found. `owner_pid` is the session pid (None when unknown), so
    a supervisor can ask the same question of it without re-parsing the file, and
    `started` is when the dispatch was recorded (epoch seconds, 0.0 when there is
    no usable record), which is how the supervisor ages a dispatch."""
    task: str
    status: str
    stage: str = ""
    owner_pid: int | None = None
    timeout: float = 0.0
    started: float = 0.0

    @property
    def fresh(self) -> bool:
        return self.status == FRESH


def path(task: str) -> Path:
    return state.STATE_DIR / f"{_PREFIX}{task}{_SUFFIX}"


def tasks() -> list[str]:
    """Every task that has a marker file, whatever state that marker is in."""
    return [p.name[len(_PREFIX):-len(_SUFFIX)]
            for p in state.STATE_DIR.glob(f"{_PREFIX}*{_SUFFIX}")]


def session_pid() -> int | None:
    """The Claude Code session this process runs under, or None when unknown.
    Never the calling process's own pid - see the module docstring."""
    try:
        pid = int(os.environ.get(SESSION_PID_ENV, ""))
    except ValueError:
        return None
    return pid if pid > 0 else None


def acquire(task: str, stage: str, timeout: float = state.BUSY_TIMEOUT) -> None:
    """Write the marker. Raises ValueError for anything but one task id, OSError
    when it cannot be written."""
    if not TASK_RE.fullmatch(task):
        raise ValueError(f"a busy marker covers exactly one task (task-NNNN), got {task!r}")
    state.STATE_DIR.mkdir(parents=True, exist_ok=True)
    path(task).write_text(json.dumps({
        "task": task, "stage": stage, "owner_pid": session_pid(),
        "started": time.time(), "timeout": timeout,
    }), encoding="utf-8")


def release(task: str) -> None:
    try:
        path(task).unlink()
    except OSError:
        pass


@contextmanager
def hold(task: str, stage: str, timeout: float = state.BUSY_TIMEOUT):
    """Mark the task busy for the block and release on every way out of it. A
    marker that cannot be written is not worth failing the covered work over: the
    hook nags, which is the safe direction."""
    try:
        acquire(task, stage, timeout)
    except (ValueError, OSError):
        pass
    try:
        yield
    finally:
        release(task)


def read(task: str) -> Marker:
    """The task's marker, classified. A dead owner outranks the clock: the fact
    decides first, the timeout only backs it up."""
    try:
        try:
            data = json.loads(path(task).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Marker(task, ABSENT)
        if data.get("task") != task:
            return Marker(task, INVALID)
        stage = str(data.get("stage", ""))
        timeout = float(data["timeout"])
        started = float(data["started"])
        age = time.time() - started
        owner = data.get("owner_pid")
        owner = None if owner is None else int(owner)
        if owner is not None:
            # Imported here, not at the top: supervisor pulls in mode and the
            # process-spawning code, and this module is loaded by every hook.
            from supervisor import pid_alive
            if not pid_alive(owner, started):
                return Marker(task, OWNER_GONE, stage, owner, timeout, started)
        return Marker(task, FRESH if age < timeout else EXPIRED, stage, owner, timeout,
                      started)
    except Exception:
        return Marker(task, INVALID)
