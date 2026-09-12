#!/usr/bin/env python3
"""PostToolUse(Edit|Write) hook for dev agents - dangerous/debug pattern warning.

Replaces the legacy hooks/check-dangerous-patterns.sh (which read the retired
CLAUDE_TOOL_INPUT env var and was never wired). Reads the hook JSON payload from
stdin, scans the edited file by extension, and PRINTS warnings - it never
blocks (exit 0 always): the reviewer and the quality gates decide, this hook
just makes the pattern visible immediately after it is written.

Fail-open by construction.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PATTERNS: dict[tuple[str, ...], list[str]] = {
    (".php",): [
        r"\bdd\s*\(", r"\bdump\s*\(", r"\bray\s*\(", r"\bvar_dump\s*\(",
        r"\bprint_r\s*\(", r"\beval\s*\(", r"\bexec\s*\(", r"\bshell_exec\s*\(",
        r"\bsystem\s*\(", r"\bpassthru\s*\(",
    ],
    (".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"): [
        r"\bconsole\.(log|debug|dir|trace)\s*\(", r"\bdebugger\b",
        r"\beval\s*\(", r"\bnew\s+Function\s*\(", r"\balert\s*\(",
    ],
    (".py",): [
        r"^\s*print\s*\(", r"\bbreakpoint\s*\(", r"\bpdb\.set_trace\s*\(",
        r"\bipdb\.set_trace\s*\(", r"\beval\s*\(", r"\bexec\s*\(",
        r"\bos\.system\s*\(", r"shell\s*=\s*True",
    ],
    (".rb",): [
        r"\bbinding\.pry\b", r"\bbyebug\b", r"^\s*puts\s", r"^\s*pp?\s",
        r"\beval\s*\(",
    ],
    (".go",): [
        r"\bfmt\.Print(ln|f)?\s*\(",
    ],
    (".sh", ".bash"): [
        r"\beval\s", r">\s*/dev/null",
    ],
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    try:
        ti = payload.get("tool_input", {}) or {}
        file_path = str(ti.get("file_path", ""))
        if not file_path:
            return 0
        p = Path(file_path)
        if not p.is_file():
            return 0
        suffix = p.suffix.lower()
        rules = None
        for exts, pats in PATTERNS.items():
            if suffix in exts:
                rules = pats
                break
        if not rules:
            return 0
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        hits: list[str] = []
        for i, line in enumerate(text.splitlines(), 1):
            for pat in rules:
                if re.search(pat, line):
                    hits.append(f"  {p.name}:{i}: {line.strip()[:120]}")
                    break
        if hits:
            print("dangerous-patterns: debug/dangerous calls detected - remove "
                  "before commit (rules/security.md):")
            print("\n".join(hits[:20]))
            if len(hits) > 20:
                print(f"  ... and {len(hits) - 20} more")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
