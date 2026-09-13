#!/usr/bin/env python3
"""PreToolUse gate for dispatched subagents - obedience enforced, not prompted.

Wired into agent frontmatter (`hooks.PreToolUse`, matcher Bash|Edit|Write, plus
WebSearch|WebFetch on the agents that carry web tools) so the SAME deterministic
rules that govern the orchestrator also fire INSIDE every subagent. Three
profiles:

  --profile dev       code-writing agents, qa-engineer included. Reuses
                      pretool_gate.py (commit/push approval flags,
                      protected-branch push deny, review-stage edit freeze) and
                      adds: deny `git push --force`, deny any invocation of
                      approve.py (recording CEO approval is the orchestrator's
                      exclusive right - a dev agent running it would be
                      self-approval), deny an UNNARROWED run of the stack's test
                      suite while permitting a narrowed one.
  --profile readonly  analysis agents whose tools list already blocks Edit/Write
                      but whose Bash access is a mutation hole. Denies mutating
                      Bash: git write commands, file mutation utilities, shell
                      redirects, in-place editors, package installers, approve.py.
  --profile docs      documentation/planning agents. Edit/Write allowed only
                      under .claude/, .agentry/, docs/, or README files;
                      mutating Bash and approve.py denied.

Deny = exit 2 with a one-line reason on stderr (same convention as
pretool_gate.py). Fail-open: any internal error allows the tool (exit 0) - a
bug here must never brick an agent.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys

import pretool_gate

# Git subcommands that mutate the working tree, history, or environment. Matched
# through pretool_gate.git_invocations() rather than `\bgit\s+commit\b` regexes:
# a global option between `git` and the subcommand (`git -C dir push`,
# `git -c k=v commit`) defeated the regex form and handed subagents the same
# bypass the main-thread gate had. `merge-base` is a distinct subcommand token,
# so listing `merge` no longer needs a negative lookahead.
MUTATING_GIT_SUBS = frozenset({
    "commit", "push", "add", "reset", "clean", "restore", "rebase", "merge",
})
MUTATING_GIT_STASH_ARGS = ("pop", "apply", "drop")

# Second layer, the same shape git_invokes() already carries: a plain regex net
# for git text argv walking cannot see from the outside (`bash -c "git add -A"`,
# or whatever glued form the next reviewer finds). The subcommand must be
# preceded by whitespace and not continue into another word, so `git merge-base`,
# `git log --merges` and `--author=add` do not false-positive.
#
# It fires ONLY when git_invocations() resolved nothing - see git_mutates(). Run
# unconditionally it also matched read-only git whose ARGUMENT happens to be a
# listed word (`git diff HEAD -- add.py`, `git log --grep add`), denying the
# readonly/docs agents whose whole job is reading diffs.
MUTATING_GIT_FALLBACK_RE = re.compile(
    r"(?<![\w.-])git(?:\.exe)?\b[^;|&]*?\s"
    r"(?P<sub>commit|push|add|reset|clean|restore|rebase|merge"
    r"|stash\s+(?:pop|apply|drop)|checkout\s+--)"
    r"(?![-\w])",
    re.IGNORECASE)

# Non-git Bash fragments that mutate the working tree.
MUTATING_BASH = [
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


def is_force_push(command: str) -> bool:
    """Force push in any spelling: `--force`, `--force-with-lease`, `-f`, and any
    short-flag cluster containing `f`. Resolved through git_invocations() so
    `git -C dir push --force` is caught; the legacy regex stays as a net for
    forms argv walking cannot see (e.g. `bash -c "git push -f"`)."""
    for sub, args in pretool_gate.git_invocations(command):
        if sub != "push":
            continue
        for tok in args:
            if tok == "--":
                break
            if tok.startswith("--force"):
                return True
            if tok.startswith("-") and not tok.startswith("--") and "f" in tok[1:]:
                return True
    return bool(FORCE_PUSH_RE.search(command))


# Redirect detection is shared with the main-thread gate - single source of truth.
redirect_write_target = pretool_gate.redirect_write_target

README_RE = re.compile(r"(^|[\\/])readme[^\\/]*\.md$", re.IGNORECASE)


def deny(reason: str) -> int:
    sys.stderr.write(reason + "\n")
    return 2


def allow() -> int:
    return 0


# --- Web tools (WebSearch / WebFetch) ---------------------------------------
# Four agents carry web tools (business-analyst, financial-analyst,
# marketing-strategist, content-writer). With permissionMode: bypassPermissions
# the permission prompt is gone, and the gate's old matcher (Bash|Edit|Write)
# did not cover these tools at all - so their web access had no layer left.
# Reading the web is legitimate research for those roles, so the gate allows it
# for the readonly and docs profiles and writes an audit line instead of a deny;
# a dev agent has no research mandate, so web access there is refused.
WEB_TOOLS = ("WebSearch", "WebFetch")
WEB_LOG = pretool_gate.state.STATE_DIR / "web-access.log"
WEB_LOG_MAX_BYTES = 256 * 1024


def log_web_access(profile: str, tool: str, ti: dict) -> None:
    """Append one audit line per web call: what was requested, by which profile.
    Two-file rotation keeps it bounded. Fail-open - logging must never block a
    tool call."""
    try:
        target = str(ti.get("url") or ti.get("query") or ti.get("prompt") or "")
        target = " ".join(target.split())[:300]
        WEB_LOG.parent.mkdir(parents=True, exist_ok=True)
        if WEB_LOG.exists() and WEB_LOG.stat().st_size > WEB_LOG_MAX_BYTES:
            WEB_LOG.replace(WEB_LOG.with_suffix(".log.1"))
        with WEB_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{pretool_gate.state.now()}\t{profile}\t{tool}\t{target}\n")
    except (OSError, ValueError, AttributeError):
        pass


def handle_web(profile: str, tool: str, ti: dict) -> int:
    if profile == "dev":
        return deny(f"{tool} is not available to dev agents - implement from the task, the "
                    f"spec and the codebase. Research requests go to the orchestrator.")
    log_web_access(profile, tool, ti)
    return allow()


def is_docs_path(path: str) -> bool:
    """.agentry/ was ADDED alongside .claude/, not substituted for it: FR-13
    moved the work product (tasks, handoffs, specs, plans, epics) to .agentry/,
    which is exactly what the docs profile writes - but rules/, skills/ and
    agents/ stayed under .claude/, so a docs agent still needs both."""
    p = path.replace("\\", "/").lower()
    if "/.claude/" in p or p.startswith(".claude/"):
        return True
    if "/.agentry/" in p or p.startswith(".agentry/"):
        return True
    if "/docs/" in p or p.startswith("docs/"):
        return True
    return bool(README_RE.search(p))


def git_mutates(command: str) -> str:
    """Return the mutating git invocation, or '' if every git call is read-only.
    Unparseable git text is treated as mutating (fail-closed) - see
    pretool_gate.git_invocations().

    Two layers on purpose. argv resolution is the real check; the regex net
    behind it covers the case argv walking is structurally blind to - the whole
    git call sits inside ONE shlex token (`bash -c "git add -A"`), so no `git`
    token is ever seen and git_invocations() comes back empty.

    The net therefore runs only on that empty result. When the walk DID resolve
    invocations and none of them mutate, that answer is authoritative and the
    regex can only add false positives. Residual gap, narrower than those false
    positives were: `git status && bash -c "git add -A"` resolves one read-only
    invocation, so the nested call skips the net. Closing it needs per-segment
    matching, not a whole-string regex."""
    invocations = pretool_gate.git_invocations(command)
    for sub, args in invocations:
        if sub == pretool_gate.GIT_UNKNOWN:
            return "git (command could not be parsed)"
        if sub in MUTATING_GIT_SUBS:
            return f"git {sub}"
        if sub == "stash" and args and args[0].lower() in MUTATING_GIT_STASH_ARGS:
            return f"git stash {args[0].lower()}"
        if sub == "checkout" and "--" in args:
            return "git checkout --"
    if not invocations:
        m = MUTATING_GIT_FALLBACK_RE.search(command)
        if m:
            return "git " + " ".join(m.group("sub").split())
    return ""


def bash_mutates(command: str) -> str:
    """Return the matched mutating fragment, or '' if the command is read-only."""
    low = " ".join(command.split())
    hit = git_mutates(low)
    if hit:
        return hit
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
        if is_force_push(command):
            return deny("Force push is forbidden for all agents (rules/git-workflow.md).")
        hit = unnarrowed_test_cmd(command)
        if hit:
            return deny(f"Dev agents do not run the FULL test suite ('{hit}'). Run only the "
                        f"cases you touched, narrowed with one of "
                        f"{', '.join(test_filter_flags())}, then report - the orchestrator "
                        f"runs the whole suite once after you finish. "
                        f"(see .claude testing rules).")
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


# Flags that narrow a suite command to selected cases. Every runner spells it
# differently, so the list is config-driven from pipeline.json
# `gates.dev_test_filter_flags`; the default covers the common spellings. A
# runner that narrows by argument rather than by flag (an explicit module path,
# a file path) never matches dev_forbidden_commands in the first place.
DEFAULT_FILTER_FLAGS = ("--filter", "-k", "--testsuite", "--group", "-run", "--grep")


def test_filter_flags() -> tuple[str, ...]:
    configured = pretool_gate.gates_cfg().get("dev_test_filter_flags")
    if isinstance(configured, list) and configured:
        return tuple(str(c).lower() for c in configured)
    return DEFAULT_FILTER_FLAGS


def is_narrowed(command: str) -> bool:
    """Whether ONE command was narrowed to selected cases.

    A narrowed run is permitted where the full run is not, because proving a new
    test actually bites means running it with the production line removed. A
    test written and never executed is a claim, not a result: two shipped red
    under the strict split, which is the incident this allowance closes. The
    UNFILTERED run stays denied, so the one-full-run-per-cycle budget still
    belongs to the orchestrator.

    Takes ONE command, not a chain: a filter flag anywhere in
    `pytest -k x; pytest` used to mark the whole string narrowed, which let the
    bare second run through. Callers split first - see unnarrowed_test_cmd().

    The flag must be a TOKEN, not a substring: `pytest  # -k nothing` is a full
    suite run with the flag sitting in a comment, and a substring test read it
    as narrowed. Unparseable text (an unbalanced quote) is not narrowed, so the
    full-run deny still applies."""
    try:
        # comments=True: a `#` at a word boundary starts a shell comment, so the
        # flag in `pytest  # -k nothing` is not an argument of anything.
        tokens = shlex.split(command.lower(), comments=True)
    except ValueError:
        return False
    flags = test_filter_flags()
    return any(tok in flags or any(tok.startswith(f"{flag}=") for flag in flags)
               for tok in tokens)


# Shell separators that start a new command. Splitting on them is crude - a
# separator inside a quoted filter argument splits too - but the failure mode is
# a narrowed segment being read as two narrowed segments, never a full run being
# read as narrowed.
SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|&\n]")


def unnarrowed_test_cmd(command: str) -> str:
    """The forbidden test command of the first segment that runs it UNNARROWED.

    Per segment, because narrowing is a property of one invocation: the flag in
    `npm test -- -k cart; npm test` narrows the first run and says nothing about
    the second."""
    for segment in SEGMENT_SPLIT_RE.split(command):
        hit = forbidden_test_cmd(segment)
        if hit and not is_narrowed(segment):
            return hit
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
            return deny(f"Docs agent: writes allowed only under .claude/, .agentry/, docs/, "
                        f"or README files - '{path}' is outside that scope. Code changes "
                        f"belong to dev agents via the pipeline.")
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
                        f"Use Write/Edit for documents under .claude/, .agentry/ or docs/.")
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
        # Shared with the main-thread gate: raw bytes decoded as UTF-8, because
        # sys.stdin's locale encoding (cp1252 on Windows) turns byte 0x97 into
        # U+2014 and 0x96 into U+2013 - so Cyrillic content arrived carrying em
        # dashes it never contained and the dash gate refused the edit.
        payload = pretool_gate.read_payload()
    except (ValueError, OSError, UnicodeDecodeError):
        return allow()
    try:
        tool = payload.get("tool_name", "")
        ti = payload.get("tool_input", {}) or {}
        if tool in WEB_TOOLS:
            return handle_web(args.profile, tool, ti)
        return HANDLERS[args.profile](tool, ti, str(payload.get("cwd", "")))
    except Exception:
        return allow()


if __name__ == "__main__":
    raise SystemExit(main())
