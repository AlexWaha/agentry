#!/usr/bin/env python3
"""SessionStart hook - one process instead of three.

Does, in order, all fail-open:
  1. Remove stray Windows `nul`/`NUL` entries. The `2>NUL` spelling leaves them
     (bash has no device named NUL, so it opens a file); `2>/dev/null` does
     not, MSYS2 mounts a real device there. See rules/quality-standard.md.
     Win32 resolves the name as the reserved device, so `Path.is_file()` is
     False, `Path.exists()` is True in EVERY directory (entry or not) and
     `unlink()` raises WinError 5. So the entry is found by listing the
     directory (os.scandir) and removed by bash (`rm -f`), the same tool
     hooks/cleanup-nul.sh uses. An exists()-based guard would fire at the
     reserved device every session. Failures are printed, not swallowed.
  1.5 Print every pipeline dial that is not at its default (mode, approvals,
     swarm) and, with the swarm on, the resume note of each in-flight umbrella
     task (.agentry/tasks/swarm/<task>/resume.md).
  2. Print the pipeline resume summary (in-flight runs from state.py).
  3. Sync the codegraph index (one status line; silent skip if CLI absent).
  3.5 Module-map drift check (tools/memory/codebase_sync.py --check): silent
     when fresh, prints changed paths + the update instruction on drift.

There is no memory-size health check any more: memory is a queried SQLite store
(.agentry/memory/memory.db), not a file head under a cap, so it does not overflow
an injection window and needs no curation nudge.

Anything printed to stdout is injected into the session context, so every
branch stays quiet unless it has something actionable to say (token economy).
A bug here must never brick the session: every step swallows its own errors.

Output channel (measured in the 2.1.295 binary): stdout that does not start with
`{` is plain text and becomes context; stdout that does is parsed as ONE JSON
document, so plain lines and JSON cannot share it. Every step prints as before,
into a buffer. Normally the buffer is printed unchanged. When a run is parked
BLOCKED the person starting the session has to be told, and `systemMessage` is
the field for him, so then the buffer goes out as the `additionalContext` of one
JSON envelope with the `systemMessage` beside it. Nothing the steps print is lost
in either shape.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
CLAUDE_DIR = HERE.parents[2]          # .../.claude
ROOT = HERE.parents[3]                # project root


def stray_nul_entries(root: Path) -> list[str]:
    """Names in `root` that are a NUL entry, read from the directory listing.
    Never from the path: Path("NUL").exists() is True with no entry at all."""
    with os.scandir(root) as entries:
        return [e.name for e in entries
                if e.name.lower() == "nul" and e.is_file(follow_symlinks=False)]


def find_bash() -> str | None:
    """Git bash beside git on Windows: a bare `bash` there can be WSL, a
    different operating system. Elsewhere, the bash on PATH."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        for parent in Path(git).resolve().parents[:3]:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def cleanup_nul() -> None:
    try:
        names = stray_nul_entries(ROOT)
    except OSError as exc:
        print(f"nul cleanup: cannot list {ROOT}: {exc}")
        return
    if not names:
        return
    listed = ", ".join(names)
    try:
        bash = find_bash()
        if bash is None:
            print(f"nul cleanup: {listed} in {ROOT} needs bash to remove and none was "
                  f"found - run `rm -f {listed}` from Git bash")
            return
        proc = subprocess.run([bash, "-c", 'rm -f -- "$@"', "_", *names], cwd=ROOT,
                              capture_output=True, timeout=5,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(f"nul cleanup: removing {listed} failed: {proc.stderr.strip()}")
    except Exception as exc:
        print(f"nul cleanup: could not remove {listed}: {exc}")


def pipeline_resume() -> str:
    """Prints the resume summary for the model and returns the sentence for the
    human about a blocked run ("" when there is none)."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))
        import state
        conn = state.connect()
        try:
            text = state._resume_text(conn)
            # Its own try: a failure computing the human sentence must not
            # swallow the resume text the model needs.
            try:
                notice = state.blocked_notice(conn)
            except Exception:
                notice = ""
        finally:
            conn.close()
        if text:
            print(text)
        return notice
    except Exception:
        return ""


def codegraph_sync() -> None:
    try:
        import shutil
        exe = shutil.which("codegraph")  # resolves .cmd/.exe shims on Windows
        if not exe:
            raise FileNotFoundError("codegraph")
        proc = subprocess.run(
            [exe, "sync", str(ROOT)],
            capture_output=True, text=True, timeout=45,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0:
            line = (proc.stdout or "").strip().splitlines()
            print(f"codegraph: {line[-1] if line else 'synced'}")
        else:
            print("codegraph: sync failed - run 'codegraph status' to inspect; "
                  "falling back to Grep/Glob per rules/code-retrieval.md")
    except FileNotFoundError:
        # CLI not installed on this machine - one line, not an error.
        print("codegraph: CLI not installed - code retrieval falls back to "
              "Grep/Glob (see rules/code-retrieval.md, onboarding prerequisites)")
    except Exception:
        pass


def codebase_memory_check() -> None:
    """Step 3.5: module-map drift detection (tools/memory/codebase_sync).
    Quiet when fresh; prints changed paths + update instruction on drift."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "tools" / "memory"))
        import codebase_sync
        codebase_sync.check()
    except Exception:
        pass


def pipeline_mode() -> None:
    """Announce every dial that is not at its default (mode, approvals, swarm) so
    a forgotten switch never stays silent, and, with the swarm on, point at the
    resume note of each in-flight umbrella task."""
    sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))
    # One try per dial: an ImportError in one module must not silence the others.
    try:
        import mode

        current = mode.read()
        # Compared against the project's own default flow, not a hardcoded
        # `build`: a workspace whose first declared pipeline is something else
        # would otherwise be told it is in a non-default mode on every start.
        if current != mode.default():
            print(f"pipeline mode: {current} - {mode.describe(current)}. "
                  f"Switch back with: /pipeline-{mode.default()}")
    except Exception:
        pass
    try:
        import approvals

        level = approvals.read()
        if level != approvals.MANUAL:
            print(f"pipeline approvals: {level} - {approvals.DESCRIPTIONS[level]}. "
                  f"Switch back with: /pipeline-approvals-{approvals.MANUAL}")
    except Exception:
        pass
    try:
        import swarm

        flag = swarm.read()
        if flag != swarm.OFF:
            print(f"pipeline swarm: {flag} - {swarm.DESCRIPTIONS[flag]}. "
                  f"Switch back with: /pipeline-swarm-{swarm.OFF}")
            swarm_resume_notes()
    except Exception:
        pass


def swarm_resume_notes() -> None:
    """With the swarm on, the path of `.agentry/tasks/swarm/<task>/resume.md` for
    every run still in flight whose note exists: that note is how a swarm
    survives a restart, and the session must be told it is there."""
    import state

    conn = state.connect()
    try:
        live = [r["task"] for r in state.all_runs(conn) if r["stage"] != "done"]
    finally:
        conn.close()
    for task in live:
        note = ROOT / ".agentry" / "tasks" / "swarm" / task / "resume.md"
        if note.is_file():
            print(f"swarm resume note for {task}: .agentry/tasks/swarm/{task}/resume.md "
                  f"- read it before relaunching or briefing any agent")


def main() -> int:
    buf = io.StringIO()
    notice = ""
    try:
        with contextlib.redirect_stdout(buf):
            cleanup_nul()
            pipeline_mode()
            notice = pipeline_resume()
            codegraph_sync()
            codebase_memory_check()
    finally:
        # In a finally so a step that escapes its own handler still lets the
        # lines printed before it reach the session, as they did unbuffered.
        text = buf.getvalue()
        if notice:
            print(json.dumps({
                "hookSpecificOutput": {"hookEventName": "SessionStart",
                                       "additionalContext": text.rstrip("\n")},
                "systemMessage": notice,
            }))
        elif text:
            print(text, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
