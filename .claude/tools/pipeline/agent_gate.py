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
                      under .claude/, .agentry/ (not .agentry/state/), docs/,
                      README files, or the root CLAUDE.md;
                      mutating Bash and approve.py denied.

Every profile also denies SETTING the approvals level or workflow mode
(approvals.py / mode.py with an argument other than --show); reading is allowed.
The dev profile is also denied Write/Edit on anything under .agentry/state/, and
Bash that writes there (a redirect target, or an operand of tee, sed -i, cp, mv,
rm, truncate).

Deny = exit 2 with a one-line reason on stderr (same convention as
pretool_gate.py). Fail-open: any internal error allows the tool (exit 0) - a
bug here must never brick an agent.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

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

# Commands that mutate the working tree, matched as ARGV0 of a simple command
# (see mutating_command). The MUTATING_BASH regex list below is the same set
# spelled as text, kept for the one case tokenisation cannot serve.
MUTATING_COMMANDS = frozenset({
    "rm", "mv", "cp", "tee", "truncate", "ln", "chmod", "mkdir",
})
# Installers: the command mutates only with one of these subcommands, so
# `npm run build` and `pip --version` stay allowed.
MUTATING_SUBCOMMANDS = {
    "npm": frozenset({"install", "i", "uninstall", "update", "ci"}),
    "composer": frozenset({"install", "update", "require", "remove"}),
    "pip": frozenset({"install", "uninstall"}),
    "pip3": frozenset({"install", "uninstall"}),
    "apt": frozenset({"install", "remove"}),
    "apt-get": frozenset({"install", "remove"}),
}

# Non-git Bash fragments that mutate the working tree. FALLBACK ONLY: this runs
# on text that cannot be tokenised (see bash_mutates). Run over the whole
# command string it matched its literal inside a quoted argument, so a memory
# row whose prose said "never run rm -rf on the tree" was denied as an rm -
# task-0078's second denial, which the main-thread gate fixed and this one did
# not.
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
    # so descriptor dups (2>&1) and the discard sink (/dev/null) do not false-positive.
    r"\bnpm\s+(install|i|uninstall|update|ci)\b",
    r"\bcomposer\s+(install|update|require|remove)\b",
    r"\bpip3?\s+(install|uninstall)\b",
    r"\bapt(-get)?\s+(install|remove)\b",
]

# task-0078: these three checks used to match their literal anywhere in the raw
# command text, so naming the tool was denied as running it - a dev agent's
# read-only `git show <sha> -- <path>/approve.py` was refused as self-approval.
# They now ask pretool_gate whether the script is actually INVOKED (argv0, or
# the script argument of an interpreter), which is the same tokenisation every
# other gate here uses and is fail-closed on text it cannot tokenise.
# handoff.py --waive records a CEO waiver - orchestrator-only, same trust
# boundary as approve.py. --for / --check stay allowed for every profile.
HANDOFF_SCRIPT_RE = re.compile(r"(?:^|[\\/])handoff\.py[. )]*$", re.IGNORECASE)

runs_approve_script = pretool_gate.runs_approve_script


# task-0097: approvals.py and mode.py write .agentry/state/approvals and
# .agentry/state/mode, the files task-0075 already keeps the docs profile from
# writing. The approvals level decides which checkpoints clear without the CEO,
# so setting it from Bash is the same boundary crossed by another door. Only the
# SETTING form is denied: both scripts read when given no argument or `--show` /
# `show` (their own first-argument test), and every profile may. `--help` / `-h`
# are not `--show`, so they are denied too - a harmless read the gate does not
# special-case.
#
# Matching is by FILE NAME only, as it is for approve.py: an unrelated
# `app/mode.py` invoked with an argument is denied the same way.
STATE_SETTER_RE = re.compile(r"(?:^|[\\/])(?:approvals|mode)\.py[. )]*$", re.IGNORECASE)
STATE_SETTER_MSG = ("{script} is orchestrator-only when it SETS the approvals level or the "
                    "workflow mode - that decides what clears without the CEO. Ask the "
                    "orchestrator to change it; --show (or no argument) still reads it.")

# A redirect operator opens with an optional descriptor: `>`, `>>`, `2>`, `<`, and
# the `&` forms `&>`, `>&`, `2>&1`. Alone (`>`, `2>>`, `>&`) its target is the NEXT
# token; glued (`>file`, `2>1`, `2>&1`) the token is whole. One definition, shared
# with the interpreter walk in pretool_gate, so the two cannot disagree on `>&`.
REDIRECT_TOKEN_RE = pretool_gate.REDIR_TOKEN_RE
BARE_REDIRECT_RE = pretool_gate.REDIR_BARE_RE


def real_arguments(tokens: list) -> list:
    """`tokens` without what is not an argument of the script: redirect operators
    and their targets, and the `)` / backtick that closes a `$( ... )` or
    backtick substitution the call sits in (`level=$(python x.py --show)` glues
    it onto the last word)."""
    out, skip_target = [], False
    for tok in tokens:
        if skip_target:
            skip_target = False
            continue
        if REDIRECT_TOKEN_RE.match(tok):
            skip_target = bool(BARE_REDIRECT_RE.match(tok))
            continue
        tok = tok.rstrip(")`")
        if tok.strip():
            out.append(tok)
    return out


def state_setter_call(segment: list) -> tuple | None:
    """(script name, its arguments) for the approvals.py / mode.py that ONE simple
    command runs, or None when it runs neither. The script is found the way the
    approval-script check finds it (exec_tokens: argv0 or an interpreter's
    script, `-m` included); its arguments are whatever follows the token that
    named it."""
    hits = [(i, n) for i, n in pretool_gate.exec_tokens(segment) if STATE_SETTER_RE.search(n)]
    if not hits:
        return None
    # `python - auto < x/approvals.py` runs the script from stdin (index -1):
    # whatever follows the interpreter is not the script's argument list to judge.
    for i, name in hits:
        if i < 0:
            return script_label(name), ["<stdin>"]
    i, name = hits[0]
    args = real_arguments(segment[i + 1:])
    # `xargs python x.py` appends its stdin as arguments, so a bare
    # invocation behind it is not a read.
    if not args and any(pretool_gate.basename_no_ext(t) == "xargs" for t in segment[:i]):
        args = ["<stdin>"]
    return script_label(name), args


def script_label(name: str) -> str:
    """`dir/Mode.py.` as the `mode.py` the deny message names: Windows runs the
    script under its dotted name, and the label must not carry the dot."""
    return pretool_gate.basename_no_ext(name).rstrip(". )")


def piped_setter(command: str, segments: list) -> str:
    """The setter script a PIPE hands to an interpreter (`cat x/approvals.py |
    python - auto`), else ''. executed_names() already counts what the previous
    command emits as executed when the next one is an interpreter, exactly as it
    does for approve.py; what it lists beyond the words each simple command runs
    itself is that piped-in set.

    The producing command's OWN script is not piped-in source: `python
    x/approvals.py --show | python -m json.tool` reads a value and runs nothing
    from x. So what the previous segment executes is counted as own too. `git
    diff -- x/approvals.py | python y.py` stays denied, as it is for approve.py:
    what a producer merely names cannot be told from what it emits."""
    executed = pretool_gate.executed_names(command) or []
    own = Counter(n for seg in segments for n in pretool_gate.exec_names_of(seg))
    pairs = pretool_gate.segments_with_separators(command) or []
    for i, (_, seg) in enumerate(pairs):
        if pretool_gate.runs_interpreter(pretool_gate.exec_names_of(seg)):
            for k in pretool_gate.pipe_sources(pairs, i):
                own.update(pretool_gate.exec_names_of(pairs[k][1]))
    return next((script_label(n) for n in (Counter(executed) - own)
                 if STATE_SETTER_RE.search(n)), "")


def sets_pipeline_state(command: str, _depth: int = 0) -> str:
    """The script name (approvals.py / mode.py) when `command` invokes it with
    anything but no argument or `--show` / `show` as its first argument, or feeds
    it to an interpreter through a pipe or `<`, else ''.
    Fail-closed on text that cannot be tokenised; nested `sh -c` bodies are
    unwrapped like runs_waive does."""
    segments = pretool_gate.command_segments(command)
    if segments is None:
        return "approvals.py and mode.py"
    for seg in segments:
        call = state_setter_call(seg)
        if call and call[1] and call[1][0] not in ("--show", "show"):
            return call[0]
    piped = piped_setter(command, segments)
    if piped:
        return piped
    if pretool_gate.unscannable_depth(command, _depth):
        return "approvals.py and mode.py"
    for body in pretool_gate.nested_command_bodies(command):
        hit = sets_pipeline_state(body, _depth + 1)
        if hit:
            return hit
    return ""


def runs_waive(command: str, _depth: int = 0) -> bool:
    """True when handoff.py is invoked WITH --waive in the same simple command.
    Fail-closed on text that cannot be tokenised."""
    segments = pretool_gate.command_segments(command)
    if segments is None:
        return True
    for seg in segments:
        if "--waive" in seg and any(HANDOFF_SCRIPT_RE.search(n)
                                    for n in pretool_gate.exec_names_of(seg)):
            return True
    if pretool_gate.unscannable_depth(command, _depth):
        return True
    return any(runs_waive(body, _depth + 1)
               for body in pretool_gate.nested_command_bodies(command))


def is_force_push(command: str, _depth: int = 0) -> bool:
    """Force push in any spelling: `--force`, `--force-with-lease`, `-f`, and any
    short-flag cluster containing `f`. Resolved through git_invocations() so
    `git -C dir push --force` is caught, and through the nested `sh -c` body for
    the form argv walking cannot see (`bash -c "git push -f"`)."""
    for sub, args in pretool_gate.git_invocations(command):
        if sub == pretool_gate.GIT_UNKNOWN:
            return True  # unparseable git text - refuse rather than guess
        if sub != "push":
            continue
        for tok in args:
            if tok == "--":
                break
            if tok.startswith("--force"):
                return True
            if tok.startswith("-") and not tok.startswith("--") and "f" in tok[1:]:
                return True
    if pretool_gate.unscannable_depth(command, _depth):
        return True
    return any(is_force_push(body, _depth + 1)
               for body in pretool_gate.nested_command_bodies(command))


# Redirect detection is shared with the main-thread gate - single source of truth.
redirect_write_target = pretool_gate.redirect_write_target

README_RE = re.compile(r"(^|[\\/])readme[^\\/]*\.(md|rst)$", re.IGNORECASE)

# The whole .agentry/state/ tree is the orchestrator's: the approvals level and mode,
# the run.db commit/push/plan approval flags, the solo trunk_push approval and the
# memory stamps all decide what clears without the CEO. Nothing a docs agent writes
# lives there. Windows resolves `state.` and `state ` (and `state./x`) to the same
# directory (measured), so the segment tolerates trailing dots and spaces.
PROTECTED_STATE_RE = re.compile(r"(^|/)\.agentry/state[. ]*(/|$)")
PROTECTED_STATE_MSG = ("{who}: '{path}' is under .agentry/state/, which holds the approvals, "
                       "workflow mode and run flags that decide what clears without the CEO. "
                       "Report the level or mode change you need to the orchestrator.")


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


def normalize(path: str) -> str:
    """Lowercase posix spelling with `..` resolved, so `docs/../src/a.py` is judged
    as the `src/a.py` it names and not as the `docs/` prefix it starts with."""
    return posixpath.normpath(path.replace("\\", "/").lower())


def is_protected_state(path: str) -> bool:
    return bool(PROTECTED_STATE_RE.search(normalize(path)))


def is_root_claude_md(path: str) -> bool:
    """The repository-root CLAUDE.md, resolved against the project root the way
    pretool_gate.orch_allowed_path() resolves it. Root only: a CLAUDE.md inside an
    application tree is not the document the technical-writer owns."""
    p = Path(path)
    root = pretool_gate.state.ROOT
    if not p.is_absolute():
        p = root / p
    try:
        return p.resolve().relative_to(root.resolve()).as_posix().lower() == "claude.md"
    except (OSError, ValueError):
        return False


def is_docs_path(path: str) -> bool:
    """.agentry/ was ADDED alongside .claude/, not substituted for it: FR-13
    moved the work product (tasks, handoffs, specs, plans, epics) to .agentry/,
    which is exactly what the docs profile writes - but rules/, skills/ and
    agents/ stayed under .claude/, so a docs agent still needs both. The root
    CLAUDE.md was added the same way (task-0075), and .agentry/ is allowed except
    for .agentry/state/, which holds the approvals, mode and run flags."""
    if is_protected_state(path):
        return False
    if is_root_claude_md(path):
        return True
    p = normalize(path)
    if "/.claude/" in p or p.startswith(".claude/"):
        return True
    if "/.agentry/" in p or p.startswith(".agentry/"):
        return True
    if "/docs/" in p or p.startswith("docs/"):
        return True
    return bool(README_RE.search(p))


def git_mutates(command: str, _depth: int = 0) -> str:
    """Return the mutating git invocation, or '' if every git call is read-only.
    Unparseable git text is treated as mutating (fail-closed) - see
    pretool_gate.git_invocations().

    Two layers, in the order task-0078 requires. argv resolution is the real
    check, and it now recurses into every nested body (a shell's `-c` argument,
    an `eval` operand, a `$( ... )` substitution) exactly as pretool_gate does,
    so the git call inside one shlex token is RESOLVED rather than guessed at.

    The regex net behind it fires only when the text cannot be tokenised at
    all. Run any wider it matched a listed word inside a quoted argument - a
    prose field naming `git add`, or a read-only `git diff HEAD -- add.py` -
    and denied the readonly/docs agents whose whole job is reading diffs."""
    for sub, args in pretool_gate.git_invocations(command):
        if sub == pretool_gate.GIT_UNKNOWN:
            return "git (command could not be parsed)"
        if sub in MUTATING_GIT_SUBS:
            return f"git {sub}"
        if sub == "stash" and args and args[0].lower() in MUTATING_GIT_STASH_ARGS:
            return f"git stash {args[0].lower()}"
        if sub == "checkout" and "--" in args:
            return "git checkout --"
    if pretool_gate.gate_tokens(command) is None:
        # Heredoc-stripped: the body is data on stdin, and this net would
        # otherwise read a document that MENTIONS `git add` as a `git add`.
        m = MUTATING_GIT_FALLBACK_RE.search(pretool_gate.strip_heredocs(command))
        if m:
            return "git " + " ".join(m.group("sub").split())
    if pretool_gate.unscannable_depth(command, _depth):
        return pretool_gate.DEPTH_EXCEEDED
    for body in pretool_gate.nested_command_bodies(command):
        hit = git_mutates(body, _depth + 1)
        if hit:
            return hit
    return ""


def base_name(token: str) -> str:
    """argv0 reduced to a bare command name: no directory, no .exe."""
    name = re.split(r"[\\/]", token)[-1].lower()
    return name.removesuffix(".exe")


def in_place_sed(args: list) -> bool:
    """`sed -i`, `sed -i.bak`, a cluster ending in it (`sed -ni`), `--in-place`."""
    for tok in args:
        if tok.startswith("--in-place"):
            return True
        if tok.startswith("-") and not tok.startswith("--") and "i" in tok[1:]:
            return True
    return False


def mutating_command(segment: list) -> str:
    """The mutating command ONE simple command runs, or ''. argv0 is resolved
    through pretool_gate.argv0_index(), so `echo x | xargs rm` is an rm and
    `--fix "never run rm -rf"` is one token of prose."""
    i = pretool_gate.argv0_index(segment)
    if i is None:
        return ""
    name, args = base_name(segment[i]), segment[i + 1:]
    if name in MUTATING_COMMANDS:
        return name
    if name == "sed" and in_place_sed(args):
        return "sed -i"
    subs = MUTATING_SUBCOMMANDS.get(name)
    if subs:
        for tok in args:
            if tok.startswith("-"):
                continue
            return f"{name} {tok.lower()}" if tok.lower() in subs else ""
    return ""


def bash_mutates(command: str, _depth: int = 0) -> str:
    """Return the matched mutating fragment, or '' if the command is read-only."""
    hit = git_mutates(command)
    if hit:
        return hit
    segments = pretool_gate.command_segments(command)
    if segments is None:
        # Fail-closed fallback: text shlex cannot tokenise gets the old net.
        low = " ".join(pretool_gate.strip_heredocs(command).split())
        for pattern in MUTATING_BASH:
            m = re.search(pattern, low, re.IGNORECASE)
            if m:
                return m.group(0).strip()
    else:
        for segment in segments:
            hit = mutating_command(segment)
            if hit:
                return hit
    hit = redirect_write_target(command)
    if hit:
        return hit
    if pretool_gate.unscannable_depth(command, _depth):
        return pretool_gate.DEPTH_EXCEEDED
    for body in pretool_gate.nested_command_bodies(command):
        hit = bash_mutates(body, _depth + 1)
        if hit:
            return hit
    return ""


# What `cp` takes as the NEXT word: `-t`/`-S` in a cluster, and the long options with a
# required value. getopt matches any unambiguous prefix of a long option (`--target=`,
# `--suf x`), and an ambiguous one is an error, so a prefix of any of these counts.
CP_TARGET_OPTION = "--target-directory"
CP_VALUE_OPTIONS = (CP_TARGET_OPTION, "--suffix", "--sparse", "--no-preserve")


def is_long_option_prefix(name: str, option: str) -> bool:
    """True when `name` (`--target`, `--t`) abbreviates `option`; a bare `--` ends the
    options and abbreviates nothing."""
    return len(name) > 2 and option.startswith(name)


def target_directory(args: list) -> list:
    """The directory `cp -t DIR`, `-tDIR`, `-rt DIR`, `--target-directory DIR` or
    `--target-directory=DIR` (or any abbreviation of the long name, `--target=DIR`,
    `--t DIR`) names, as a one-item list, else []. With it every other operand is a
    SOURCE, so the last operand is no longer the destination. `-S` takes a value that
    may itself hold a `t` (`-Sbackup`), so a cluster with an `S` ahead of the `t` is
    not read as the option."""
    for n, tok in enumerate(args):
        if tok == "--":
            break
        if tok.startswith("--"):
            name, eq, value = tok.partition("=")
            if is_long_option_prefix(name, CP_TARGET_OPTION):
                return [value] if eq else args[n + 1:n + 2]
        elif tok.startswith("-"):
            head, found, glued = tok[1:].partition("t")
            if found and "S" not in head:
                return [glued] if glued else args[n + 1:n + 2]
    return []


def destination_operand(args: list) -> list:
    """The last operand of a `cp` that is neither an option nor an option's value, as
    a one-item list, else []. GNU permutes options, so `cp a b -v` and `cp a b -S .bak`
    copy onto `b`: taking the last word whole named `-v` or `.bak` as the destination."""
    operands, skip, n = [], False, 0
    while n < len(args):
        tok, n = args[n], n + 1
        if skip:
            skip = False
        elif tok == "--":
            operands.extend(args[n:])
            break
        elif tok.startswith("--"):
            name, eq, _ = tok.partition("=")
            skip = not eq and any(is_long_option_prefix(name, o) for o in CP_VALUE_OPTIONS)
        elif tok.startswith("-") and len(tok) > 1:
            # a cluster ends in the option that takes the value: `-vS .bak`, but not `-S.bak`
            skip = tok[-1] in "St" and not any(c in "St" for c in tok[1:-1])
        else:
            operands.append(tok)
    return operands[-1:]


def writes_protected_state(command: str, _depth: int = 0) -> str:
    """The `.agentry/state/` path a Bash command writes through a redirect or a
    mutating command (`tee`, `sed -i`, `cp` onto it, `rm`, ...), else ''.

    task-0097 denied the dev profile a Write or Edit there, but `echo auto >
    .agentry/state/approvals` set the same level from Bash. Only paths named in
    the command are seen: a relative target after a `cd` into the tree, and code
    an interpreter runs (`python -c "open(...)"`), are not (task-0100: readonly
    is not hermetic against inline interpreter code either, so no heuristic is
    claimed for it)."""
    for frag in pretool_gate.redirect_write_fragments(command):
        target = pretool_gate.redirect_target(frag).strip("'\"")
        if is_protected_state(target):
            return target
    for segment in pretool_gate.command_segments(command) or []:
        if not mutating_command(segment):
            continue
        i = pretool_gate.argv0_index(segment)
        operands = real_arguments(segment[i + 1:])
        if base_name(segment[i]) == "cp":
            operands = target_directory(operands) or destination_operand(operands)
        for tok in operands:
            if is_protected_state(tok):
                return tok
    if _depth < pretool_gate.MAX_SHELL_DEPTH:
        for body in pretool_gate.nested_command_bodies(command):
            hit = writes_protected_state(body, _depth + 1)
            if hit:
                return hit
    return ""


def handle_dev(tool: str, ti: dict, cwd: str = "") -> int:
    if tool == "Bash":
        command = str(ti.get("command", ""))
        if runs_approve_script(command):
            return deny("approve.py is orchestrator-only. Recording CEO approval from a "
                        "dev agent is self-approval - report readiness to the orchestrator instead.")
        if runs_waive(command):
            return deny("handoff.py --waive is orchestrator-only - it records a CEO waiver. "
                        "Report the handoff problem to the orchestrator instead.")
        setter = sets_pipeline_state(command)
        if setter:
            return deny(STATE_SETTER_MSG.format(script=setter))
        written = writes_protected_state(command)
        if written:
            return deny(PROTECTED_STATE_MSG.format(who="Dev agent", path=written))
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
        path = str(ti.get("file_path", ""))
        # handle_edit allows anything under .agentry/ as bookkeeping, which would
        # let a dev agent write the approvals level or the mode directly.
        if is_protected_state(path):
            return deny(PROTECTED_STATE_MSG.format(who="Dev agent", path=path))
        content = str(ti.get("new_string", "") or ti.get("content", ""))
        return pretool_gate.handle_edit(path, content)
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
        if runs_approve_script(command):
            return deny("approve.py is orchestrator-only.")
        if runs_waive(command):
            return deny("handoff.py --waive is orchestrator-only - it records a CEO waiver.")
        setter = sets_pipeline_state(command)
        if setter:
            return deny(STATE_SETTER_MSG.format(script=setter))
        piped = pretool_gate.check_piped_test_suite(command)
        if piped != allow():
            return piped
        frag = bash_mutates(command)
        if frag:
            return deny(f"Read-only agent: mutating Bash denied (matched: '{frag}'). "
                        f"Use Read/Grep/Glob or read-only git commands; report changes "
                        f"you would make to the orchestrator."
                        f"{pretool_gate.bare_nul_note(frag)}")
        return pretool_gate.check_nul_redirect(command)
    return allow()


def handle_docs(tool: str, ti: dict, cwd: str = "") -> int:
    if tool in ("Edit", "Write"):
        path = str(ti.get("file_path", ""))
        if not is_docs_path(path):
            if is_protected_state(path):
                return deny(PROTECTED_STATE_MSG.format(who="Docs agent", path=path))
            return deny(f"Docs agent: writes allowed only under .claude/, .agentry/, docs/, "
                        f"README files or the root CLAUDE.md - '{path}' is outside that "
                        f"scope. Code changes belong to dev agents via the pipeline.")
        return allow()
    if tool == "Bash":
        command = str(ti.get("command", ""))
        if runs_approve_script(command):
            return deny("approve.py is orchestrator-only.")
        if runs_waive(command):
            return deny("handoff.py --waive is orchestrator-only - it records a CEO waiver.")
        setter = sets_pipeline_state(command)
        if setter:
            return deny(STATE_SETTER_MSG.format(script=setter))
        piped = pretool_gate.check_piped_test_suite(command)
        if piped != allow():
            return piped
        frag = bash_mutates(command)
        if frag:
            return deny(f"Docs agent: mutating Bash denied (matched: '{frag}'). "
                        f"Use Write/Edit for documents under .claude/, .agentry/ or docs/."
                        f"{pretool_gate.bare_nul_note(frag)}")
        return pretool_gate.check_nul_redirect(command)
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
