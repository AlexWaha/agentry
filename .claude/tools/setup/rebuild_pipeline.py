#!/usr/bin/env python3
"""Deterministically bring each target's pipeline.json to the full v2 schema.

apply_optimization.py copies pipeline.json only if absent (copy_if_absent), so a
project that carried an older minimal pipeline.json kept it - missing the
diff-review stage, the gates/memory/handoff/orchestrator_gate blocks, and still
holding {{PLACEHOLDER}} command tokens (which gate.py treats as a HARD BLOCK).
This rebuilds those from the source schema, deterministically, no LLM:

  - skip any target whose pipeline.json already has a "gates" block AND a
    "diff-review" stage AND no {{ }} tokens (already onboarded - leave it)
  - else: start from the SOURCE pipeline.json, substitute the command tokens
    ({{BUILD_CMD}}/{{TEST_CMD}}/{{LINT_CMD}}) and {{MAIN_BRANCH}} from the
    project's stack.md; a command whose value is empty/none/n-a becomes "" (an
    empty gate cmd auto-passes in gate.py); set forbid_dev_null per OS; pick
    stack-family destructive/repl/dev-forbidden gate defaults; set the memory +
    handoff baseline to the highest existing tasks/done id.

Generic gates (branch base, AI-attribution, dash, ~/.claude writes) always run
regardless, so minimal per-stack gate data is safe and non-overengineered.

Usage: python rebuild_pipeline.py --target <root> [--target ...]
Prints one REPORT json line per target.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import substitute_tokens as st

SRC_PIPELINE = Path(__file__).resolve().parents[2] / "pipeline.json"

# stack-family gate presets: (destructive_patterns, allow_if, repl_kw,
# repl_patterns, dev_forbidden)
LARAVEL = {
    "destructive_command_patterns": ["migrate:fresh", "migrate:refresh",
                                     "migrate:reset", "db:wipe",
                                     "drop database", "truncate "],
    "destructive_allow_if": ["--env=testing", "DB_DATABASE=testing"],
    "repl_write_keyword": "tinker",
    "repl_write_patterns": ["->create(", "->save(", "->delete(", "->update(",
                            "::create(", "factory("],
    "dev_forbidden_commands": ["php artisan test", "composer test", "pest",
                               "phpunit", "./vendor/bin/pest",
                               "./vendor/bin/phpunit"],
}
OPENCART = {
    "destructive_command_patterns": ["drop database", "drop table", "truncate "],
    "destructive_allow_if": [],
    "repl_write_keyword": "",
    "repl_write_patterns": [],
    "dev_forbidden_commands": ["phpunit", "./vendor/bin/phpunit"],
}
PYTHON = {
    "destructive_command_patterns": ["drop database", "drop table", "truncate ",
                                     "flush", "sqlflush"],
    "destructive_allow_if": ["test", "TESTING"],
    "repl_write_keyword": "",
    "repl_write_patterns": [],
    "dev_forbidden_commands": ["pytest", "python -m pytest", "tox"],
}
NODE = {
    "destructive_command_patterns": ["drop database", "drop table", "truncate ",
                                     "migrate reset", "db push --force-reset"],
    "destructive_allow_if": ["test"],
    "repl_write_keyword": "",
    "repl_write_patterns": [],
    "dev_forbidden_commands": ["npm test", "npm run test", "vitest run",
                               "jest", "yarn test", "pnpm test"],
}
MINIMAL = {
    "destructive_command_patterns": ["drop database", "truncate "],
    "destructive_allow_if": [],
    "repl_write_keyword": "",
    "repl_write_patterns": [],
    "dev_forbidden_commands": [],
}

EMPTY_CMD = re.compile(r"^(none|n/a|n\.a\.|-|)$", re.I)


def stack_family(stack_md: Path) -> tuple[str, dict]:
    text = stack_md.read_text(encoding="utf-8", errors="ignore").lower() \
        if stack_md.exists() else ""
    if "laravel" in text or "artisan" in text:
        return "laravel", LARAVEL
    if "opencart" in text or "ocmod" in text:
        return "opencart", OPENCART
    if "python" in text or "django" in text or "fastapi" in text \
            or "aio-pika" in text or "pydantic" in text:
        return "python", PYTHON
    if ("node" in text or "react" in text or "vue" in text or "next" in text
            or "typescript" in text or "vite" in text):
        return "node", NODE
    return "generic", MINIMAL


def highest_done(claude: Path) -> str:
    done = claude / "tasks" / "done"
    ids = []
    if done.is_dir():
        for f in done.glob("task-*.md"):
            m = re.match(r"task-(\d+)", f.name)
            if m:
                ids.append(m.group(1))
    return f"task-{max(ids)}" if ids else ""


def already_full(pj: Path) -> bool:
    if not pj.exists():
        return False
    try:
        text = pj.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if "{{" in text:
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    has_gates = isinstance(data.get("gates"), dict)
    has_diff = any(s.get("name") == "diff-review"
                   for s in data.get("stages", []) if isinstance(s, dict))
    return has_gates and has_diff


def _stack_md(claude: Path) -> Path:
    """The overlay is usually project/, but some projects use a custom _project/."""
    std = claude / "project" / "stack.md"
    if std.exists():
        return std
    alt = claude / "_project" / "stack.md"
    return alt if alt.exists() else std


def rebuild(root: Path, rep: dict) -> None:
    claude = root / ".claude"
    pj = claude / "pipeline.json"
    if already_full(pj):
        rep["pipeline"] = "already-full"
        return
    data = json.loads(SRC_PIPELINE.read_text(encoding="utf-8"))
    mapping = st.parse_map(_stack_md(claude))
    rep["mapped"] = len(mapping)

    def fill_cmd(token_val: str) -> str:
        v = mapping.get(token_val, "")
        return "" if EMPTY_CMD.match(v.strip()) else v

    # stage exit gates
    cmd_for = {"implement": "BUILD_CMD", "test": "TEST_CMD", "review": "LINT_CMD"}
    for stage in data["stages"]:
        if stage.get("name") in cmd_for and "exit_gate" in stage:
            stage["exit_gate"]["cmd"] = fill_cmd(cmd_for[stage["name"]])
    # main branch
    data["main_branch"] = mapping.get("MAIN_BRANCH", "main") or "main"
    # neutralize the {{PLACEHOLDERS}} mention in the top comment so no token lingers
    if "_comment" in data:
        data["_comment"] = data["_comment"].replace("{{PLACEHOLDERS}}",
                                                     "placeholders")
    # OS: forbid_dev_null on Windows/Git-Bash (drive-letter root), false on WSL
    is_wsl = st_is_wsl(root)
    fam, preset = stack_family(_stack_md(claude))
    data["gates"] = {
        "_comment": data.get("gates", {}).get("_comment", ""),
        "forbid_dev_null": not is_wsl,
        **preset,
    }
    base = highest_done(claude)
    data.setdefault("memory", {})["baseline"] = base
    data.setdefault("handoff", {})["baseline"] = base
    pj.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    rep["pipeline"] = f"rebuilt(family={fam},baseline={base or 'none'})"


def st_is_wsl(root: Path) -> bool:
    s = str(root).lower().replace("\\", "/")
    return s.startswith("//wsl") or "wsl$" in s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", action="append", required=True)
    args = ap.parse_args()
    for t in args.target:
        root = Path(t)
        rep = {"root": str(root)}
        try:
            rebuild(root, rep)
        except (OSError, ValueError, KeyError) as exc:
            rep["pipeline"] = f"error({str(exc)[:80]})"
        print("REPORT " + json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
