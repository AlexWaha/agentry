#!/usr/bin/env python3
"""SessionStart hook - one process instead of three.

Does, in order, all fail-open:
  1. Remove stray Windows `nul`/`NUL` files (created by accidental unix redirects).
  2. Print the pipeline resume summary (in-flight runs from state.py).
  3. Sync the codegraph index (one status line; silent skip if CLI absent).
  3.5 Module-map drift check (tools/memory/codebase_sync.py --check): silent
     when fresh, prints changed paths + the update instruction on drift.

There is no memory-size health check any more: memory is a queried SQLite store
(.claude/memory/memory.db), not a file head under a cap, so it does not overflow
an injection window and needs no curation nudge.

Anything printed to stdout is injected into the session context, so every
branch stays quiet unless it has something actionable to say (token economy).
A bug here must never brick the session: every step swallows its own errors.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
CLAUDE_DIR = HERE.parents[2]          # .../.claude
ROOT = HERE.parents[3]                # project root


def cleanup_nul() -> None:
    for name in ("nul", "NUL"):
        try:
            p = ROOT / name
            if p.is_file():
                p.unlink()
        except OSError:
            pass


def pipeline_resume() -> None:
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))
        import state
        conn = state.connect()
        text = state._resume_text(conn)
        conn.close()
        if text:
            print(text)
    except Exception:
        pass


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
    """Announce a non-default mode so a forgotten switch never stays silent."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))
        import mode

        current = mode.read()
        # Compared against the project's own default flow, not a hardcoded
        # `build`: a workspace whose first declared pipeline is something else
        # would otherwise be told it is in a non-default mode on every start.
        if current != mode.default():
            print(f"pipeline mode: {current} - {mode.describe(current)}. "
                  f"Switch back with: /pipeline {mode.default()}")
    except Exception:
        pass


def main() -> int:
    cleanup_nul()
    pipeline_mode()
    pipeline_resume()
    codegraph_sync()
    codebase_memory_check()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
