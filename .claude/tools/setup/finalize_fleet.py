#!/usr/bin/env python3
"""Fleet finalization: verify each target, then optionally commit on its branch.

Reuses discovery + classification from fleet_rollout. Two modes:

  --verify   (default) per-target checks, no writes:
             - leftover {{PLACEHOLDER}} under .claude/ (excluding the intentional
               runtime-token files) -> must be 0
             - "PROJECT-SPECIFIC - REPLACE ME" under .agentry/project/ -> must be 0
             - pipeline engine loads (state.py --show returns 0)
  --commit   for git repos only (never WSL, never excluded): stage the rollout
             paths and commit on the chore/ai-team-upgrade branch. No push.

Prints one REPORT json line per target.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import fleet_rollout as fr

SKIP_TOKEN_FILES = ("_onboarding.md", "_init-prompt.md", "handoff-template.md")
# {{VERSION}} appears only as a Docker image-tag EXAMPLE in rules/quality-standard.md
# (identical in every project); it is template documentation, not a fill slot.
BENIGN_TOKENS = {"VERSION"}
TOKEN_RE = re.compile(r"\{\{(?!(?:" + "|".join(BENIGN_TOKENS) + r")\}\})[A-Z_]+\}\}")
COMMIT_MSG = ("chore: roll out AI-team-universal build + onboarding + "
              "memory migration")
COMMIT_PATHS = [".claude", "CLAUDE.md", ".mcp.json", ".gitignore"]


def verify(root: Path) -> dict:
    claude = root / ".claude"
    rep = fr.classify(root)
    # leftover placeholders
    leftover = []
    for f in claude.rglob("*"):
        if not f.is_file() or f.name.endswith((".db", ".png", ".jpg", ".pyc",
                                               ".py")):
            continue  # .py are template tools (code) - token literals are intended
        if any(s in f.name for s in SKIP_TOKEN_FILES):
            continue
        rel = str(f.relative_to(claude)).replace("\\", "/")
        # stack.md is the canonical token MAP - its {{TOKEN}} keys stay by design.
        # create-aw-module templates are scaffold generators - {{MODULE_NAME}} etc.
        # are runtime tokens filled when a new module is generated (like handoff).
        if rel == "project/stack.md" or "create-aw-module" in rel:
            continue
        # plans/ are working notes that may quote {{TOKEN}} as content, not config
        if rel.startswith("plans/"):
            continue
        try:
            if TOKEN_RE.search(f.read_text(encoding="utf-8", errors="ignore")):
                leftover.append(str(f.relative_to(claude)).replace("\\", "/"))
        except OSError:
            pass
    rep["placeholder_files"] = leftover
    # PROJECT-SPECIFIC markers under project/
    markers = []
    proj = claude / "project"
    if proj.is_dir():
        for f in proj.glob("*.md"):
            try:
                if "PROJECT-SPECIFIC - REPLACE ME" in f.read_text(
                        encoding="utf-8", errors="ignore"):
                    markers.append(f.name)
            except OSError:
                pass
    rep["marker_files"] = markers
    # engine
    proc = subprocess.run(
        [sys.executable, str(claude / "tools" / "pipeline" / "state.py"),
         "--show"], capture_output=True, text=True)
    rep["engine_ok"] = proc.returncode == 0
    rep["clean"] = (not leftover and not markers
                    and (proc.returncode == 0 or rep["wsl"]))
    return rep


def commit(root: Path, rep: dict) -> None:
    if rep["wsl"] or not rep["git"]:
        rep["commit"] = "skipped(wsl-or-nongit)"
        return
    # confirm on the rollout branch
    cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                         cwd=root, capture_output=True, text=True)
    if cur.returncode != 0 or cur.stdout.strip() != fr.BRANCH:
        rep["commit"] = f"skipped(not-on-branch:{cur.stdout.strip()})"
        return
    paths = [p for p in COMMIT_PATHS if (root / p).exists()]
    add = subprocess.run(["git", "add", "-A", *paths],
                         cwd=root, capture_output=True, text=True)
    if add.returncode != 0:
        rep["commit"] = f"add-failed({add.stderr.strip()[:60]})"
        return
    # anything staged?
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"],
                          cwd=root, capture_output=True, text=True)
    if diff.returncode == 0:
        rep["commit"] = "nothing-to-commit"
        return
    cm = subprocess.run(["git", "commit", "-m", COMMIT_MSG],
                        cwd=root, capture_output=True, text=True)
    rep["commit"] = ("committed" if cm.returncode == 0
                     else f"commit-failed({cm.stderr.strip()[:80]})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="commit after verify")
    ap.add_argument("--only", default="")
    ap.add_argument("--roots", default="")
    args = ap.parse_args()

    roots = [r.strip() for r in args.roots.split(",") if r.strip()] or fr.DEFAULT_ROOTS
    targets = [t for t in fr.discover(roots) if not fr.excluded(t)]
    if args.only:
        subs = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        targets = [t for t in targets if any(s in str(t).lower() for s in subs)]

    n_clean = n_commit = 0
    for t in targets:
        rep = verify(t)
        if args.commit and rep["clean"]:
            commit(t, rep)
            if rep.get("commit") == "committed":
                n_commit += 1
        if rep["clean"]:
            n_clean += 1
        print("REPORT " + json.dumps(rep, ensure_ascii=False))
    print(f"# {len(targets)} targets, {n_clean} clean, {n_commit} committed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
