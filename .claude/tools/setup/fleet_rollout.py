#!/usr/bin/env python3
"""Fleet rollout driver for the AI-team-universal build.

Wraps apply_optimization.py across many projects and adds the two things it does
not do: per-project safety (git branch or .claude.bak backup) and scaffolding of
the universal pieces that apply_optimization leaves alone (root CLAUDE.md,
.claude/CLAUDE.md, project/ overlay, memory/ layer seeds, agent-memory skeleton,
onboarding docs, hooks, missing rules/skills). Content is copied only if absent
so onboarded/customised files are never clobbered. Tooling refresh + settings
merge + frontmatter patching stay the responsibility of apply_optimization.

Discovery: find `.claude` dirs at depth <= 2 under the configured roots, plus the
explicit WSL platform-backend path.

Classification per target:
  - wsl      : path under \\wsl$ / //wsl$   (git never touched here)
  - git      : has a .git at the project root and is not wsl
  - full     : has .claude/CLAUDE.md and .claude/memory  (already onboarded)
  - partial  : otherwise (needs scaffold + onboarding from scratch)

Safety:
  - git (non-wsl) -> git checkout -b chore/ai-team-upgrade (reuse if exists)
  - non-git or wsl -> copy .claude -> .claude.bak (only if .bak absent)

Usage:
  python fleet_rollout.py --discover                 # list targets, no changes
  python fleet_rollout.py                            # rollout to all targets
  python fleet_rollout.py --only reifen.local,api_service
  python fleet_rollout.py --no-git                   # skip branch creation

Prints one REPORT json line per target. Exit 0 if all ok, 2 if any partial.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SETUP_DIR = Path(__file__).resolve().parent
SRC_CLAUDE = SETUP_DIR.parents[1]              # <universal>/.claude
UNIVERSAL_ROOT = SETUP_DIR.parents[2]          # <universal>
APPLY = SETUP_DIR / "apply_optimization.py"

DEFAULT_ROOTS = ["e:/projects", "e:/Personal", "e:/TRG", "d:/OS/home"]
WSL_TARGETS = ["//wsl$/Debian/home/user/platform-backend"]

BRANCH = "chore/ai-team-upgrade"

# Projects with a special, non-coding setup and custom agents that MUST be left
# untouched by the rollout (no scaffold, no frontmatter patch, no onboarding).
EXCLUDE_SUBSTRINGS = [
    "personal/eb1a",
    "personal/research",           # covers research and research-projects
]


def excluded(root: Path) -> bool:
    s = str(root).lower().replace("\\", "/")
    return any(sub in s for sub in EXCLUDE_SUBSTRINGS)

# versioned runtime tooling that apply_optimization.py does NOT copy (it handles
# only tools/pipeline + tools/hooks). These carry no placeholders, so always
# refresh to the source version. tools/memory is referenced by session_start.py
# and the memory update gate; tools/review by the diff-review pipeline stage.
TOOLS_ALWAYS = ["tools/memory", "tools/review"]

# scaffold relative to the target .claude dir (copy_if_absent, files or trees)
SCAFFOLD_CLAUDE = [
    "CLAUDE.md",
    "_onboarding.md",
    "_init-prompt.md",
    "settings.local.example.json",
    "hooks",
    "project",
    "memory",
    "agent-memory",
    "rules",
    "skills",
    "specs",
    "tasks/templates",
]


def is_wsl(root: Path) -> bool:
    s = str(root).lower().replace("\\", "/")
    return s.startswith("//wsl") or "wsl$" in s


def discover(roots: list[str]) -> list[Path]:
    found: list[Path] = []
    for r in roots:
        base = Path(r)
        if not base.exists():
            continue
        # depth 1: base/.claude
        if (base / ".claude").is_dir():
            found.append(base)
        # depth 2: base/*/.claude
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / ".claude").is_dir():
                found.append(child)
    for w in WSL_TARGETS:
        wp = Path(w)
        if (wp / ".claude").is_dir():
            found.append(wp)
    # de-dup preserving order
    seen, out = set(), []
    for p in found:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def classify(root: Path) -> dict:
    claude = root / ".claude"
    wsl = is_wsl(root)
    git = (not wsl) and (root / ".git").exists()
    full = (claude / "CLAUDE.md").exists() and (claude / "memory").is_dir()
    return {"root": str(root), "wsl": wsl, "git": git,
            "state": "full" if full else "partial"}


def git_safety(root: Path, no_git: bool, rep: dict) -> None:
    if no_git:
        rep["safety"] = "skipped(--no-git)"
        return
    # is there a branch already / current branch?
    cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                         cwd=root, capture_output=True, text=True)
    if cur.returncode != 0:
        rep["safety"] = f"git-error({cur.stderr.strip()[:60]})"
        return
    if cur.stdout.strip() == BRANCH:
        rep["safety"] = f"on-branch({BRANCH})"
        return
    made = subprocess.run(["git", "checkout", "-b", BRANCH],
                          cwd=root, capture_output=True, text=True)
    if made.returncode == 0:
        rep["safety"] = f"branch-created({BRANCH})"
        return
    # branch may already exist -> switch to it
    sw = subprocess.run(["git", "checkout", BRANCH],
                        cwd=root, capture_output=True, text=True)
    rep["safety"] = (f"branch-reused({BRANCH})" if sw.returncode == 0
                     else f"branch-failed({made.stderr.strip()[:60]})")


def backup_safety(root: Path, rep: dict) -> None:
    src = root / ".claude"
    bak = root / ".claude.bak"
    if bak.exists():
        rep["safety"] = "backup-exists(.claude.bak)"
        return
    try:
        shutil.copytree(src, bak)
        rep["safety"] = "backup-created(.claude.bak)"
    except OSError as exc:
        rep["safety"] = f"backup-failed({str(exc)[:60]})"


def copy_if_absent_file(src: Path, dst: Path) -> bool:
    if dst.exists() or not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def copy_tree_if_absent(src: Path, dst: Path, added: list, rel_base: Path) -> None:
    if not src.exists():
        return
    if src.is_file():
        if copy_if_absent_file(src, dst):
            added.append(str(dst.relative_to(rel_base)).replace("\\", "/"))
        return
    for item in sorted(src.rglob("*")):
        if item.is_file():
            target = dst / item.relative_to(src)
            if copy_if_absent_file(item, target):
                added.append(str(target.relative_to(rel_base)).replace("\\", "/"))


def install_tools_always(root: Path, rep: dict) -> None:
    """Refresh versioned runtime tooling apply_optimization does not handle."""
    claude = root / ".claude"
    refreshed: list[str] = []
    for rel in TOOLS_ALWAYS:
        src = SRC_CLAUDE / rel
        if not src.exists():
            continue
        for item in sorted(src.rglob("*")):
            if item.is_file():
                dst = claude / rel / item.relative_to(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst)
                refreshed.append(f"{rel}/{item.relative_to(src)}".replace("\\", "/"))
    rep["tools_refreshed"] = len(refreshed)


def scaffold(root: Path, rep: dict) -> None:
    claude = root / ".claude"
    added: list[str] = []
    # root-level CLAUDE.md
    copy_tree_if_absent(UNIVERSAL_ROOT / "CLAUDE.md", root / "CLAUDE.md",
                        added, root)
    for rel in SCAFFOLD_CLAUDE:
        copy_tree_if_absent(SRC_CLAUDE / rel, claude / rel, added, root)
    rep["scaffold"] = added or "nothing-missing"


def apply_optimization(root: Path, rep: dict) -> None:
    proc = subprocess.run(
        [sys.executable, str(APPLY), "--target", str(root),
         "--source", str(SRC_CLAUDE)],
        capture_output=True, text=True)
    line = ""
    for ln in proc.stdout.splitlines():
        if ln.startswith("REPORT "):
            line = ln[len("REPORT "):]
    try:
        rep["apply"] = json.loads(line) if line else {"error": "no-report"}
    except ValueError:
        rep["apply"] = {"error": "bad-report", "raw": proc.stdout[-200:]}
    rep["apply_rc"] = proc.returncode
    if proc.returncode not in (0, 2):
        rep["apply_stderr"] = proc.stderr[-200:]


def rollout_one(root: Path, no_git: bool) -> dict:
    rep = classify(root)
    if rep["git"]:
        git_safety(root, no_git, rep)
    else:
        backup_safety(root, rep)
    apply_optimization(root, rep)
    install_tools_always(root, rep)
    scaffold(root, rep)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true", help="list targets only")
    ap.add_argument("--only", default="", help="comma-separated path substrings")
    ap.add_argument("--roots", default="", help="override roots (comma-separated)")
    ap.add_argument("--no-git", action="store_true", help="skip git branch safety")
    args = ap.parse_args()

    roots = [r.strip() for r in args.roots.split(",") if r.strip()] or DEFAULT_ROOTS
    targets = discover(roots)
    excluded_targets = [t for t in targets if excluded(t)]
    targets = [t for t in targets if not excluded(t)]
    if args.only:
        subs = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        targets = [t for t in targets
                   if any(s in str(t).lower() for s in subs)]

    if args.discover:
        for t in targets:
            print("TARGET " + json.dumps(classify(t), ensure_ascii=False))
        for t in excluded_targets:
            print("EXCLUDED " + json.dumps({"root": str(t)}, ensure_ascii=False))
        print(f"# {len(targets)} targets, {len(excluded_targets)} excluded")
        return 0

    worst = 0
    for t in targets:
        rep = rollout_one(t, args.no_git)
        print("REPORT " + json.dumps(rep, ensure_ascii=False))
        if rep.get("apply_rc") == 2:
            worst = 2
    print(f"# processed {len(targets)} targets")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
