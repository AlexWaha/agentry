#!/usr/bin/env python3
"""PreToolUse gate for dispatched subagents - obedience enforced, not prompted.

Wired into agent frontmatter (`hooks.PreToolUse`, matcher Bash|Edit|Write) so the
SAME deterministic rules that govern the orchestrator also fire INSIDE every
subagent. Three profiles:

  --profile dev       code-writing agents. Reuses pretool_gate.py (commit/push
                      approval flags, protected-branch push deny, review-stage
                      edit freeze) and adds: deny `git push --force`, deny any
                      invocation of approve.py (recording CEO approval is the
                      orchestrator's exclusive right - a dev agent running it
                      would be self-approval).
  --profile readonly  analysis agents whose tools list already blocks Edit/Write
                      but whose Bash access is a mutation hole. Denies mutating
                      Bash: git write commands, file mutation utilities, shell
                      redirects, in-place editors, package installers, approve.py.
  --profile docs      documentation/planning agents. Edit/Write allowed only
                      under .claude/, docs/, or README files; mutating Bash and
                      approve.py denied.

Deny = exit 2 with a one-line reason on stderr (same convention as
pretool_gate.py). Fail-open: any internal error allows the tool (exit 0) - a
bug here must never brick an agent.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import pretool_gate
import state

# Bash fragments that mutate the working tree, history, or environment.
MUTATING_BASH = [
    r"\bgit\s+commit\b",
    r"\bgit\s+push\b",
    r"\bgit\s+add\b",
    r"\bgit\s+reset\b",
    r"\bgit\s+clean\b",
    r"\bgit\s+restore\b",
    r"\bgit\s+checkout\s+--\s",
    r"\bgit\s+rebase\b",
    r"\bgit\s+merge(?!-)\b",         # actual merge, not read-only `git merge-base`
    r"\bgit\s+stash\s+(pop|apply|drop)\b",
    r"\brm\s",
    r"\bmv\s",
    r"\bcp\s",
    r"\bsed\s+(-\w*\s+)*-i",
    r"\btee\s",
    r"\btruncate\s",
    r"\bln\s",
    r"\bchmod\s",
    r"\bmkdir\s",
    # NOTE: file-writing redirects are detected separately in redirect_write_target()
    # so descriptor dups (2>&1) and discard sinks (NUL, /dev/null) do not false-positive.
    r"\bnpm\s+(install|i|uninstall|update|ci)\b",
    r"\bcomposer\s+(install|update|require|remove)\b",
    r"\bpip3?\s+(install|uninstall)\b",
    r"\bapt(-get)?\s+(install|remove)\b",
]

APPROVE_RE = re.compile(r"approve\.py", re.IGNORECASE)
# handoff.py --waive records a CEO waiver - orchestrator-only, same trust
# boundary as approve.py. --for / --check stay allowed for every profile.
WAIVE_RE = re.compile(r"handoff\.py[^|;&]*--waive", re.IGNORECASE)
FORCE_PUSH_RE = re.compile(r"\bgit\s+push\b[^|;&]*(--force|-f\b)", re.IGNORECASE)

# Redirect detection is shared with the main-thread gate - single source of truth.
redirect_write_target = pretool_gate.redirect_write_target

README_RE = re.compile(r"(^|[\\/])readme[^\\/]*\.md$", re.IGNORECASE)


def deny(reason: str) -> int:
    sys.stderr.write(reason + "\n")
    return 2


def allow() -> int:
    return 0


def is_docs_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    if "/.claude/" in p or p.startswith(".claude/"):
        return True
    if "/docs/" in p or p.startswith("docs/"):
        return True
    return bool(README_RE.search(p))


def bash_mutates(command: str) -> str:
    """Return the matched mutating fragment, or '' if the command is read-only."""
    low = " ".join(command.split())
    for pattern in MUTATING_BASH:
        m = re.search(pattern, low, re.IGNORECASE)
        if m:
            return m.group(0)
    return redirect_write_target(low)


def handle_dev(tool: str, ti: dict, cwd: str = "") -> int:
    if tool == "Bash":
        command = str(ti.get("command", ""))
        if APPROVE_RE.search(command):
            return deny("approve.py is orchestrator-only. Recording CEO approval from a "
                        "dev agent is self-approval - report readiness to the orchestrator instead.")
        if WAIVE_RE.search(command):
            return deny("handoff.py --waive is orchestrator-only - it records a CEO waiver. "
                        "Report the handoff problem to the orchestrator instead.")
        if FORCE_PUSH_RE.search(command):
            return deny("Force push is forbidden for all agents (rules/git-workflow.md).")
        hit = forbidden_test_cmd(command)
        if hit:
            return deny(f"Dev agents do not run the test suite ('{hit}'). Write the code and "
                        f"tests, then report - the orchestrator runs the suite once after you "
                        f"finish. (see .claude testing rules).")
        return pretool_gate.handle_bash(command, cwd)
    if tool in ("Edit", "Write"):
        content = str(ti.get("new_string", "") or ti.get("content", ""))
        return pretool_gate.handle_edit(str(ti.get("file_path", "")), content)
    return allow()


def forbidden_test_cmd(command: str) -> str:
    """E: the stack's test-suite commands dev agents must not run (they stall on
    long runs; the orchestrator/QA runs the suite once). Data-driven from
    pipeline.json 'gates.dev_forbidden_commands' so the code stays stack-agnostic."""
    low = " ".join(command.lower().split())
    for c in pretool_gate.gates_cfg().get("dev_forbidden_commands", []):
        if str(c).lower() in low:
            return str(c)
    return ""


def handle_readonly(tool: str, ti: dict, cwd: str = "") -> int:
    if tool in ("Edit", "Write"):
        return deny("This agent is read-only - it audits and reports, it does not modify files. "
                    "Return findings to the orchestrator.")
    if tool == "Bash":
        command = str(ti.get("command", ""))
        if APPROVE_RE.search(command):
            return deny("approve.py is orchestrator-only.")
        if WAIVE_RE.search(command):
            return deny("handoff.py --waive is orchestrator-only - it records a CEO waiver.")
        frag = bash_mutates(command)
        if frag:
            return deny(f"Read-only agent: mutating Bash denied (matched: '{frag}'). "
                        f"Use Read/Grep/Glob or read-only git commands; report changes "
                        f"you would make to the orchestrator.")
    return allow()


def handle_docs(tool: str, ti: dict, cwd: str = "") -> int:
    if tool in ("Edit", "Write"):
        path = str(ti.get("file_path", ""))
        if not is_docs_path(path):
            return deny(f"Docs agent: writes allowed only under .claude/, docs/, or README "
                        f"files - '{path}' is outside that scope. Code changes belong to "
                        f"dev agents via the pipeline.")
        return allow()
    if tool == "Bash":
        command = str(ti.get("command", ""))
        if APPROVE_RE.search(command):
            return deny("approve.py is orchestrator-only.")
        if WAIVE_RE.search(command):
            return deny("handoff.py --waive is orchestrator-only - it records a CEO waiver.")
        frag = bash_mutates(command)
        if frag:
            return deny(f"Docs agent: mutating Bash denied (matched: '{frag}'). "
                        f"Use Write/Edit for documents under .claude/ or docs/.")
    return allow()


HANDLERS = {
    "dev": handle_dev,
    "readonly": handle_readonly,
    "docs": handle_docs,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-agent PreToolUse gate")
    parser.add_argument("--profile", choices=sorted(HANDLERS), required=True)
    try:
        args = parser.parse_args()
    except SystemExit:
        return allow()  # bad wiring must not brick the agent

    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return allow()
    try:
        tool = payload.get("tool_name", "")
        ti = payload.get("tool_input", {}) or {}
        return HANDLERS[args.profile](tool, ti, str(payload.get("cwd", "")))
    except Exception:
        return allow()


if __name__ == "__main__":
    raise SystemExit(main())
