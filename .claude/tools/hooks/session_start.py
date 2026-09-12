#!/usr/bin/env python3
"""SessionStart hook - one process instead of three.

Does, in order, all fail-open:
  1. Remove stray Windows `nul`/`NUL` files (created by accidental unix redirects).
  2. Print the pipeline resume summary (in-flight runs from state.py).
  3. Sync the codegraph index (one status line; silent skip if CLI absent).
  3.5 L1 codebase-memory drift check (tools/memory/codebase_sync.py --check):
     silent when fresh, prints changed paths + the update instruction on drift.
  4. Memory health check: warn only when an agent MEMORY.md nears the
     native injection window (200 lines / 25KB).

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

MEMORY_DIR = CLAUDE_DIR / "agent-memory"
MEMORY_LINE_LIMIT = 180               # nudge before the 200-line injection cap
MEMORY_BYTE_LIMIT = 22 * 1024         # nudge before the 25KB injection cap


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
        import state  # noqa: PLC0415
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
    """Step 3.5: L1 codebase-memory drift detection (tools/memory/codebase_sync).
    Quiet when fresh; prints changed paths + update instruction on drift."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "tools" / "memory"))
        import codebase_sync  # noqa: PLC0415
        codebase_sync.check()
    except Exception:
        pass


def memory_health() -> None:
    try:
        if not MEMORY_DIR.is_dir():
            return
        for mem in sorted(MEMORY_DIR.glob("*/MEMORY.md")):
            try:
                data = mem.read_bytes()
            except OSError:
                continue
            lines = data.count(b"\n") + 1
            if len(data) > MEMORY_BYTE_LIMIT or lines > MEMORY_LINE_LIMIT:
                agent = mem.parent.name
                print(f"memory: {agent}/MEMORY.md at {lines} lines / "
                      f"{len(data) // 1024}KB - nearing the 200-line/25KB "
                      f"injection window. Curate it (skills/self-learning, "
                      f"mode: curate).")
    except Exception:
        pass


def pipeline_mode() -> None:
    """Announce a non-default mode so a forgotten switch never stays silent."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))
        import mode

        current = mode.read()
        if current != mode.BUILD:
            print(f"pipeline mode: {current} - {mode.DESCRIPTIONS[current]}. "
                  f"Switch back with: /pipeline build")
    except Exception:
        pass


def main() -> int:
    cleanup_nul()
    pipeline_mode()
    pipeline_resume()
    codegraph_sync()
    codebase_memory_check()
    memory_health()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
