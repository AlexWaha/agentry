#!/usr/bin/env python3
"""SubagentStart injector - prints the requested memory layers to stdout.

Wired in .claude/settings.json (SubagentStart hooks, matcher per agent type).
Whatever this prints lands in the dispatched subagent's context:

  --layers l1,l3      planning agents (codebase map + patterns index)
  --layers l1,l2      spec-writing agents (map + lessons)
  --layers l2         implementing/reviewing agents (lessons only)

Empty or stub layer files print nothing (token economy). Each layer is
truncated to its cap so a bloated file cannot flood a dispatch. Fail-open:
any error prints nothing and exits 0.
"""

from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve()
MEMORY_DIR = HERE.parents[2] / "memory"

LAYERS = {
    "l1": ("codebase.md", 200, "L1 codebase map"),
    "l2": ("lessons.md", 150, "L2 lessons (do not repeat these mistakes)"),
    "l3": ("patterns.md", 60, ("L3 reusable patterns index (reuse before reinventing; "
                               "details in .claude/memory/patterns/)")),
}
STUB_MARKERS = ("FILL-ME", "(no lessons recorded yet)", "(no patterns recorded yet)")


def render(layer: str) -> str:
    name, cap, title = LAYERS[layer]
    path = MEMORY_DIR / name
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text or any(m in text for m in STUB_MARKERS):
        return ""
    lines = text.splitlines()
    if len(lines) > cap:
        lines = [*lines[:cap], f"... (truncated at {cap} lines - curate {name})"]
    return f"## Project memory: {title}\n" + "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print memory layers for subagent injection")
    parser.add_argument("--layers", default="", help="comma-separated: l1,l2,l3")
    args = parser.parse_args()
    try:
        blocks = []
        for layer in [s.strip().lower() for s in args.layers.split(",") if s.strip()]:
            if layer in LAYERS:
                block = render(layer)
                if block:
                    blocks.append(block)
        if blocks:
            print("\n\n".join(blocks))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
