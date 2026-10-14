#!/usr/bin/env python3
"""Getting the diff in front of the User at the commit checkpoint (FR-53 to FR-55,
contract C-9).

An AskUserQuestion card does not show a diff, and approving a commit without one
is approving a summary of it. Two delivery paths, one entry point (deliver):

  * Windows, CONIN$ opens: queue "/diff" plus Enter into the console input
    buffer with WriteConsoleInputW. The command runs when the model's turn ends,
    so the real coloured diff renders beside the card.
  * Everything else (not Windows, CONIN$ fails, the write is short): hand the
    model a capped diff to put in the AskUserQuestion preview, with a pointer to
    /diff for the full view.

A task that lives in its own worktree always takes the second path: /diff reads the
session's checkout, not that tree, so nothing is typed and the preview carries the
tree's diff.

Neither path reaches the checkpoint without a diff, and neither can block it:
every failure on the injection path falls through to the preview, and every
failure on the preview path degrades to the bare /diff pointer (NFR-4).

FR-55, why the check is HERE and not in the caller: typing into the user's
terminal at any moment but a parking point is a defect. inject_diff() reads the
run's own record and refuses unless it is parked on an unapproved commit, so a
future caller cannot bypass the rule by forgetting to check. An auto-approved
commit never parks (commit_approved is set before this is reached), so it is
not a parking point either.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import git_state
import state
import worktree

DIFF_COMMAND = "/diff"

# Set to any non-empty value to forbid the console write outright. The test suite
# sets it (tests/tmproot.py): a test that drives advance.py to the commit park
# would otherwise type "/diff" into the real prompt of whoever ran the suite.
NO_INJECT_ENV = "AGENTRY_NO_CONSOLE_INJECT"
PREVIEW_MAX_CHARS = 4000
# Seconds per git call: the preview path must not cost the checkpoint more latency
# than the injection path (NFR-6), so a hung git degrades to the /diff pointer.
GIT_TIMEOUT = 5

# Win32 constants, spelled out because there is no stdlib module that names them.
KEY_EVENT = 0x0001
VK_RETURN = 0x0D
SCAN_RETURN = 0x1C
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value

TRUNCATED = f"... (truncated, run {DIFF_COMMAND} for the full diff)"


class _KeyEvent(ctypes.Structure):
    # `char` is a fixed 16-bit unit (the WCHAR of the Win32 struct), not c_wchar,
    # which is 4 bytes outside Windows: the layout, and so the size assert in the
    # tests, must not depend on the platform.
    _fields_ = [("key_down", ctypes.c_int), ("repeat", ctypes.c_ushort),
                ("virtual_key", ctypes.c_ushort), ("scan_code", ctypes.c_ushort),
                ("char", ctypes.c_uint16), ("control_keys", ctypes.c_uint32)]


class _EventUnion(ctypes.Union):
    # The padding member sizes the union to the largest event (MOUSE_EVENT, 16
    # bytes), so INPUT_RECORD is the 20 bytes WriteConsoleInputW expects.
    _fields_ = [("key", _KeyEvent), ("size", ctypes.c_ubyte * 16)]  # noqa: RUF012


class _InputRecord(ctypes.Structure):
    _fields_ = [("event_type", ctypes.c_ushort), ("event", _EventUnion)]


def _is_windows() -> bool:
    return sys.platform == "win32"


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _records(text: str) -> ctypes.Array:
    """Key-down and key-up for each character, then Enter."""
    keys = [(ch, 0, 0) for ch in text] + [("\r", VK_RETURN, SCAN_RETURN)]
    records = (_InputRecord * (len(keys) * 2))()
    for i, (ch, vk, scan) in enumerate(keys):
        for j, down in enumerate((1, 0)):
            rec = records[i * 2 + j]
            rec.event_type = KEY_EVENT
            rec.event.key.key_down = down
            rec.event.key.repeat = 1
            rec.event.key.virtual_key = vk
            rec.event.key.scan_code = scan
            rec.event.key.char = ord(ch)
    return records


def _queue_keystrokes(text: str) -> bool:
    """True only when every record of `text` plus Enter reached the console input
    buffer. Any failure is False, never an exception: the caller falls back to
    the preview."""
    if os.environ.get(NO_INJECT_ENV) or not _is_windows():
        return False
    try:
        k32 = _kernel32()
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.c_void_p]
        handle = k32.CreateFileW("CONIN$", GENERIC_READ | GENERIC_WRITE,
                                 FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                 OPEN_EXISTING, 0, None)
        if handle is None or handle == INVALID_HANDLE:
            return False
        try:
            records = _records(text)
            written = ctypes.c_uint32(0)
            ok = k32.WriteConsoleInputW(ctypes.c_void_p(handle), records, len(records),
                                        ctypes.pointer(written))
            return bool(ok) and written.value == len(records)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        return False


def at_commit_parking_point(task: str) -> bool:
    """The run is parked on a commit nobody has approved yet. Read from the run
    store itself, so the answer cannot be supplied by a caller."""
    try:
        conn = state.connect()
        try:
            run = state.get_run(conn, task)
        finally:
            conn.close()
    except Exception:
        return False
    return bool(run) and run.get("awaiting_human") == "commit" and not run.get("commit_approved")


def inject_diff(task: str) -> bool:
    """Queue /diff for the User. The only function in the pipeline that writes to
    the console input buffer, and it does so only at a commit parking point."""
    if not at_commit_parking_point(task):
        return False
    return _queue_keystrokes(DIFF_COMMAND)


def _cap(text: str, marker: str = TRUNCATED) -> str:
    if len(text) <= PREVIEW_MAX_CHARS:
        return text
    cut = text[:PREVIEW_MAX_CHARS - len(marker)]
    return cut[:cut.rfind("\n")] + "\n" + marker if "\n" in cut else cut + marker


def _changes(task: str, with_diff: bool) -> str:
    """Working-tree changes of every repo the task lives in, capped. The short
    status comes first because a new file does not appear in a diff. With more
    than one repo each block is headed `# <repo name>`.

    A task that lives in a worktree is read from that tree, not from `src/` (F8).
    When the tree is MISSING or INVALID there is nothing honest to show, and the
    clean `src/` would show an empty diff for work it cannot see, so the answer is
    one line saying so."""
    broken = worktree.status(task)
    if broken in (worktree.STATUS_MISSING, worktree.STATUS_INVALID):
        return (f"task worktree {broken}: no changes can be shown. Repair it: python "
                f"\"$AGENTRY_ROOT/tools/pipeline/worktree.py\" --repair --task {task}")
    repos = git_state.work_trees_for_task(task)
    in_tree = _in_tree(task)
    blocks = []
    full = ""
    for repo in repos:
        # Colour forced off: a user config of color.status=always puts escape
        # codes into text meant for a card preview.
        _, status = git_state._git(repo, "-c", "color.ui=false", "-c", "color.status=false",
                                   "status", "--short", timeout=GIT_TIMEOUT)
        diff = ""
        if with_diff:
            _, diff = git_state._git(repo, "diff", "HEAD", "--no-color", "--no-ext-diff",
                                     timeout=GIT_TIMEOUT)
            full = f'git -C "{Path(repo).as_posix()}" diff HEAD'
            if in_tree and not status and not diff:
                # A clean tree whose work is committed (a re-park after a sync merge):
                # `diff HEAD` is empty, so the change set is what the branch holds
                # beyond the trunk.
                since = f"{git_state.trunk(repo)}...HEAD"
                _, diff = git_state._git(repo, "diff", since, "--no-color", "--no-ext-diff",
                                         timeout=GIT_TIMEOUT)
                full = f'git -C "{Path(repo).as_posix()}" diff {since}'
        body = "\n".join(p for p in (status, diff) if p)
        if body:
            blocks.append(f"# {repo.name}\n{body}" if len(repos) > 1 else body)
    # The built-in /diff reads the session's checkout, which is not a task's tree.
    marker = (f"... (truncated, run {full} for the full diff)"
              if in_tree and full and len(repos) == 1 else TRUNCATED)
    return _cap("\n".join(blocks), marker)


def capped_diff(task: str) -> str:
    return _changes(task, with_diff=True)


def changed_files(task: str) -> str:
    return _changes(task, with_diff=False)


def _in_tree(task: str) -> bool:
    """The task's changes live in its own worktree. The built-in /diff reads the
    session's checkout, so for such a task it would not show them (F3): nothing is
    typed into the console and the card carries the tree's diff instead."""
    try:
        return worktree.status(task) == worktree.STATUS_OK
    except Exception:
        return False


def _preview_note(task: str) -> str:
    try:
        diff = capped_diff(task)
    except Exception:
        diff = ""
    if _in_tree(task):
        pointer = (f" {DIFF_COMMAND} does not read this task's worktree, so do not point the User "
                   f"to it.")
    else:
        pointer = f" Tell the User to run {DIFF_COMMAND} for the full diff."
    if not diff:
        return pointer
    return (f"{pointer} Put the diff below, verbatim, in the preview field of the "
            f"AskUserQuestion commit card.\n--- diff ---\n{diff}")


def _injected_note(task: str) -> str:
    """/diff runs only when the model's turn ends and the card keeps the turn
    open, so nothing here promises the User already sees it."""
    try:
        files = changed_files(task)
    except Exception:
        files = ""
    note = f" {DIFF_COMMAND} queued; if it has not rendered, run {DIFF_COMMAND}."
    return f"{note}\n--- status ---\n{files}" if files else note


def deliver(task: str, *, inject: bool) -> str:
    """The text for the end of advance.py's commit-park message. Empty when the
    run is not at a commit parking point (C-9 row 4). `inject=False` skips the
    console write for a park that was already announced, so re-running advance.py
    on a parked task does not type into the terminal a second time."""
    try:
        if not at_commit_parking_point(task):
            return ""
        if inject and not _in_tree(task) and inject_diff(task):
            return _injected_note(task)
        return _preview_note(task)
    except Exception:
        return f" Tell the User to run {DIFF_COMMAND} for the full diff."
