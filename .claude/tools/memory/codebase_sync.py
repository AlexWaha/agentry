#!/usr/bin/env python3
"""Module-map drift detection - cheap, deterministic, quiet when clean.

The map itself is model-authored, and it now lives in the store as `module`
rows (.agentry/memory/memory.db, one row per module/dir) instead of a markdown
table. This script only DETECTS drift and stamps freshness:

  --check   compare the git heads recorded in the store's meta table with the
            actual workspace repos. Clean -> silent exit 0. Drift -> print the
            changed paths grouped by top-level dir plus the update instruction,
            exit 1. No module rows at all -> print the generation instruction,
            exit 1.
  --stamp   record the current heads as fresh (run AFTER recording module rows).

Fail-open: any internal error exits 0 silently - memory hygiene must never
brick a session.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
CLAUDE_DIR = HERE.parents[2]
ROOT = HERE.parents[3]
sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))
sys.path.insert(0, str(HERE.parent))

# Sibling module; the dir is not on sys.path by default, hence the insert above.
import memory

MEMORY_DIR = ROOT / ".agentry" / "memory"
META_KEY = "codebase_heads"
DIFF_LINES_CAP = 30
RECORD_HINT = ("python .claude/tools/memory/memory.py --record --kind module "
               "--path <dir> --responsibility <one line> [--symbols <entry points>] "
               "[--notes <dependency notes>]")

_SKIP_DIRS = {".git", "node_modules", "vendor", ".claude", "storage",
              "dist", "build", "data", "logs"}


def _git_repos() -> list:
    """Workspace git repos (root + up to 2 levels down) - same walk as
    stop_gate._git_repos so both tools agree on what 'the workspace' is."""
    repos = []
    try:
        roots = [ROOT]
        for d1 in ROOT.iterdir():
            if d1.is_dir() and d1.name not in _SKIP_DIRS and not d1.name.startswith("."):
                roots.append(d1)
                for d2 in d1.iterdir():
                    if d2.is_dir() and d2.name not in _SKIP_DIRS and not d2.name.startswith("."):
                        roots.append(d2)
        for r in roots:
            if (r / ".git").exists():
                repos.append(r)
    except Exception:
        pass
    return repos


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix() or "."
    except ValueError:
        return str(p)


def _head(repo: Path) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=8)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def current_heads() -> dict:
    heads = {}
    for repo in _git_repos():
        sha = _head(repo)
        if sha:
            heads[_rel(repo)] = sha
    return heads


def stored_heads() -> dict:
    """Heads recorded at the last --stamp, read from the store's meta table."""
    conn = memory.connect_readonly()
    if conn is None:
        return {}
    try:
        heads = memory.meta_get(conn, META_KEY, {})
        return heads if isinstance(heads, dict) else {}
    finally:
        conn.close()


def map_is_empty() -> bool:
    """No module rows in the store - the map was never recorded."""
    try:
        return memory.counts().get("module", 0) == 0
    except Exception:
        return False


def _changed_paths(repo: Path, old_sha: str, new_sha: str) -> list:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", f"{old_sha}..{new_sha}"],
            capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def check() -> int:
    heads = current_heads()
    if not heads:
        return 0  # non-git workspace (e.g. the template itself) - nothing to sync
    if map_is_empty():
        print("memory: the store holds no module rows. Generate the module map "
              "(codegraph explore per module + project/architecture.md) and record one row "
              f"per module: {RECORD_HINT}. Then run: python "
              ".claude/tools/memory/codebase_sync.py --stamp")
        return 1
    stored = stored_heads()
    drifted = []
    for repo_rel, sha in heads.items():
        old = stored.get(repo_rel, "")
        if old != sha:
            drifted.append((repo_rel, old, sha))
    if not drifted:
        return 0

    print("memory: the module map is STALE - repos moved since the last sync:")
    shown = 0
    for repo_rel, old, sha in drifted:
        if not old:
            print(f"  {repo_rel}: no stamp recorded yet")
            continue
        repo = ROOT / repo_rel if repo_rel != "." else ROOT
        by_dir: dict[str, int] = {}
        for path in _changed_paths(repo, old, sha):
            top = path.split("/", 1)[0]
            by_dir[top] = by_dir.get(top, 0) + 1
        for top, count in sorted(by_dir.items(), key=lambda kv: -kv[1]):
            if shown >= DIFF_LINES_CAP:
                print("  ... (more)")
                break
            print(f"  {repo_rel}/{top}: {count} file(s) changed")
            shown += 1
    print(f"Re-record the affected module rows ({RECORD_HINT}) - recording a path that "
          "already exists updates it - then run: python "
          ".claude/tools/memory/codebase_sync.py --stamp")
    return 1


def stamp() -> int:
    heads = current_heads()
    conn = memory.connect()
    try:
        memory.meta_set(conn, META_KEY, heads)
        memory.meta_set(conn, "codebase_stamped_at",
                        datetime.now(timezone.utc).isoformat(timespec="seconds"))
    finally:
        conn.close()
    print(f"memory: stamped {len(heads)} repo head(s) as fresh")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Module-map drift check/stamp")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--stamp", action="store_true")
    args = parser.parse_args()
    try:
        if args.stamp:
            return stamp()
        return check()
    except Exception:
        return 0  # fail-open


if __name__ == "__main__":
    raise SystemExit(main())
