#!/usr/bin/env python3
"""Deterministic {{TOKEN}} substitution from a project's filled stack.md table.

The onboarding runbook has the LLM fill the canonical "Placeholder values" table
in project/stack.md early, then replace tokens file-by-file - the expensive,
interruption-prone part. This script does that replacement deterministically:
parse the token->value map from stack.md, substitute across all .claude/ files
(minus the intentional runtime-token files) and the root CLAUDE.md. Rows whose
value is still a placeholder/marker/example are skipped, so a partially filled
table just does partial (safe) substitution. No-op when nothing is mapped.

Usage: python substitute_tokens.py --target <project-root> [--target ...]
Prints one REPORT json line per target.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# stack.md is the MAP SOURCE - substituting inside it would replace the {{TOKEN}}
# keys in its own value table and destroy the map for later runs. Never touch it.
SKIP_FILES = ("_onboarding.md", "_init-prompt.md", "handoff-template.md",
              "stack.md")
ROW_RE = re.compile(r"^\|\s*`\{\{([A-Z_]+)\}\}`\s*\|\s*(.*?)\s*\|")
SKIP_VALUE = re.compile(r"PROJECT-SPECIFIC|REPLACE ME|\{\{[A-Z_]+\}\}")


def parse_map(stack_md: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not stack_md.exists():
        return mapping
    for line in stack_md.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        token, value = m.group(1), m.group(2).strip()
        # strip surrounding backticks the table sometimes wraps values in
        value = value.strip("`").strip()
        if not value or value.startswith("[") or SKIP_VALUE.search(value):
            continue
        mapping[token] = value
    return mapping


def substitute(root: Path, rep: dict) -> None:
    claude = root / ".claude"
    mapping = parse_map(claude / "project" / "stack.md")
    rep["mapped_tokens"] = len(mapping)
    if not mapping:
        rep["substituted_files"] = 0
        rep["remaining_tokens"] = "unknown(no-map)"
        return
    pattern = re.compile(r"\{\{(" + "|".join(map(re.escape, mapping)) + r")\}\}")
    files = list(claude.rglob("*"))
    root_claude = root / "CLAUDE.md"
    if root_claude.exists():
        files.append(root_claude)
    changed = 0
    for f in files:
        if not f.is_file() or f.name.endswith((".db", ".png", ".jpg", ".jpeg",
                                               ".pyc", ".ico", ".gif")):
            continue
        if any(s in f.name for s in SKIP_FILES):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "{{" not in text:
            continue
        new = pattern.sub(lambda m: mapping[m.group(1)], text)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed += 1
    rep["substituted_files"] = changed
    # any tokens still present anywhere (excluding skip files)?
    leftover = set()
    for f in files:
        if not f.is_file() or any(s in f.name for s in SKIP_FILES):
            continue
        try:
            for m in re.finditer(r"\{\{([A-Z_]+)\}\}",
                                 f.read_text(encoding="utf-8", errors="ignore")):
                leftover.add(m.group(1))
        except OSError:
            pass
    rep["remaining_tokens"] = sorted(leftover)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", action="append", required=True)
    args = ap.parse_args()
    for t in args.target:
        root = Path(t)
        rep = {"root": str(root)}
        substitute(root, rep)
        print("REPORT " + json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
