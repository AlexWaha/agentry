#!/usr/bin/env python3
"""PreToolUse gate for dispatched subagents - obedience enforced, not prompted.

Wired into agent frontmatter (`hooks.PreToolUse`, matcher Bash|Edit|Write|MultiEdit|NotebookEdit, plus
WebSearch|WebFetch on the agents that carry web tools) so the SAME deterministic
rules that govern the orchestrator also fire INSIDE every subagent. Three
profiles:

  --profile dev       code-writing agents, qa-engineer included. Reuses
                      pretool_gate.py (commit/push approval flags,
                      protected-branch push deny, review-stage edit freeze) and
                      adds: deny `git push --force`, deny any invocation of
                      approve.py (recording User approval is the orchestrator's
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

Every profile also denies SETTING the approvals level, workflow mode or swarm flag
(approvals.py / mode.py / swarm.py with an argument other than --show); reading is
allowed.
Every profile is denied Write/Edit on anything under .agentry/state/, on
.agentry/pipeline.json and on the live harness (see pretool_gate.is_live_harness),
and Bash that writes there (a redirect target, or an operand of tee, sed -i, cp, mv,
rm, truncate). Paths are judged by where they RESOLVE (realpath, NTFS stream suffix
removed, the payload cwd and any `cd` in the command applied), not by spelling.

The readonly and docs profiles are default-deny on Bash: every simple command's
argv0 must be on ALLOWED_PROGRAMS (read-only utilities, git, codegraph), or be an
interpreter running one of ALLOWED_SCRIPTS from <ROOT>/.claude/tools/, or be named
by a `--allow-script NAME` flag on the agent's hook line. `python -c`, `python -`,
`python -m` and a script the agent wrote itself are therefore refused whatever the
spelling. The dev profile cannot be default-deny (running code is its job): an
interpreter one-liner there is NOT COVERED - see rules/orchestration.md, "Subagent
enforcement".

Deny = exit 2 with a one-line reason on stderr (same convention as
pretool_gate.py). The dev profile is fail-open: any internal error allows the tool
(exit 0) - a bug here must never brick the agent that builds the harness. readonly and
docs fail CLOSED for Bash and the file tools: the allowlist (check_program) runs before
every lexical check, an error inside it denies, and an error anywhere else in the handler
denies too (SEC-A H2 of task-0120), because a gate that fails open is bypassed by whatever
input crashes it.
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import re
import shlex
import sys
import threading
from collections import Counter
from pathlib import Path

import pretool_gate
import state
import zones

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
# handoff.py --waive records a User waiver - orchestrator-only, same trust
# boundary as approve.py. --for / --check stay allowed for every profile.
HANDOFF_SCRIPT_RE = re.compile(r"(?:^|[\\/])handoff\.py[. )]*$", re.IGNORECASE)

runs_approve_script = pretool_gate.runs_approve_script
# One git option-abbreviation rule for both gates (task-0125).
is_long_option_prefix = pretool_gate.is_long_option_prefix


# task-0097: approvals.py and mode.py write .agentry/state/approvals and
# .agentry/state/mode, the files task-0075 already keeps the docs profile from
# writing. The approvals level decides which checkpoints clear without the User,
# so setting it from Bash is the same boundary crossed by another door. Only the
# SETTING form is denied: both scripts read when given no argument or `--show` /
# `show` (their own first-argument test), and every profile may. `--help` / `-h`
# are not `--show`, so they are denied too - a harmless read the gate does not
# special-case.
#
# Matching is by FILE NAME only, as it is for approve.py: an unrelated
# `app/mode.py` invoked with an argument is denied the same way.
#
# zones.py joins for `--prove` / `--release`: the frozen zone map decides which paths a
# dev agent may write, so a subagent that could freeze or release it could lift its own
# fence (SEC-A of task-0120). `--show` reads and stays allowed.
#
# repo_scan.py joins the list for its `--write`, which edits the protected
# .agentry/pipeline.json: it writes repos{} (and lanes{}), and the `stages` command
# lists inside repos{} are what stack_gate.py later runs with shell=True. Its
# other forms only print a scan, so they stay allowed; see is_setting_call().
STATE_SETTER_RE = re.compile(
    r"(?:^|[\\/])(?:approvals|mode|swarm|zones|repo_scan)\.py[. )]*$", re.IGNORECASE)
STATE_SETTER_MSG = ("{script} is orchestrator-only when it SETS the approvals level, the "
                    "workflow mode or the swarm flag, freezes or releases the swarm zone map "
                    "(zones.py --prove / --release), or WRITES the pipeline config "
                    "(repo_scan.py --write) - that decides what clears without "
                    "the User, which procedure is in force, which paths a dev agent may write "
                    "and which commands the gates run. Ask the orchestrator to change it; "
                    "--show (or no argument) still reads the first three, zones.py --show "
                    "reads the map, and repo_scan.py without --write only prints a scan.")
REPO_SCAN_SCRIPT = "repo_scan.py"
# argparse accepts any unambiguous prefix of a long option, and every prefix of
# `--write` from `--w` up is unambiguous in repo_scan.py (--root, --json, --table
# do not start with w): `--w`, `--wr`, `--write`, `--write=F` all write.
REPO_SCAN_WRITE_PREFIX_RE = re.compile(r"^--w", re.IGNORECASE)
# Inline code handed to an interpreter (`python -c "import repo_scan;
# repo_scan.write(...)"`): argv walking sees one token, so it is read as text. A
# net, not a proof - repo_scan.write() itself refuses a live config as well.
INLINE_REPO_SCAN_WRITE_RE = re.compile(r"\brepo_scan\b[\s\S]*\bwrite\s*\(", re.IGNORECASE)

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
        # pad_separators puts GLUED_CLOSE in front of a substitution closer that stays glued
        # to its word (`--show)`): it is not part of the argument either.
        tok = tok.replace(pretool_gate.GLUED_CLOSE, "").rstrip(")`")
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


def is_setting_call(script: str, args: list) -> bool:
    """True when one call of a state-setter script changes state. approvals.py /
    mode.py / swarm.py set with anything but no argument or `--show` / `show` as
    the first argument; repo_scan.py writes only with a `--w...` option (any
    argparse abbreviation of `--write`), and a script run from stdin cannot be
    judged, so it counts as writing."""
    if script == REPO_SCAN_SCRIPT:
        return "<stdin>" in args or any(REPO_SCAN_WRITE_PREFIX_RE.match(a) for a in args)
    return bool(args) and args[0] not in ("--show", "show")


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
        if call and is_setting_call(*call):
            return call[0]
        if (pretool_gate.runs_interpreter(pretool_gate.exec_names_of(seg))
                and any(INLINE_REPO_SCAN_WRITE_RE.search(tok) for tok in seg)):
            return REPO_SCAN_SCRIPT
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


WAIVE_OPTION = "--waive"


def has_waive_option(seg: list) -> bool:
    """True when a word of `seg` is `--waive`, `--waive=T` or an abbreviation of it: handoff.py
    reads its options with argparse, which accepts any unambiguous prefix (`--wai`, `--waiv=T`)."""
    return any(tok.startswith("--") and is_long_option_prefix(tok.partition("=")[0], WAIVE_OPTION)
               for tok in seg)


def runs_waive(command: str, _depth: int = 0) -> bool:
    """True when handoff.py is invoked WITH --waive (or an abbreviation) in the same simple
    command. Fail-closed on text that cannot be tokenised."""
    segments = pretool_gate.command_segments(command)
    if segments is None:
        return True
    for seg in segments:
        if has_waive_option(seg) and any(HANDOFF_SCRIPT_RE.search(n)
                                         for n in pretool_gate.exec_names_of(seg)):
            return True
    if pretool_gate.unscannable_depth(command, _depth):
        return True
    return any(runs_waive(body, _depth + 1)
               for body in pretool_gate.nested_command_bodies(command))


# --- Worktree layout (epic-0004) ------------------------------------------------------
# With worktrees.enabled, advance.py creates a task's tree and the merge cleanup removes it.
# A dev agent that could add, move, remove or re-link one by hand could point its task at
# another tree or pull the tree out from under a running gate. worktree.py --list and
# --sweep only read, so they stay open; --remove and --repair (and any argparse
# abbreviation of them) write.
GIT_WORKTREE_WRITERS = frozenset({"add", "remove", "move", "prune", "repair", "lock", "unlock"})
WORKTREE_SCRIPT_RE = re.compile(r"(?:^|[\\/])worktree\.py[. )]*$", re.IGNORECASE)
WORKTREE_WRITE_OPTIONS = ("--remove", "--repair")
WORKTREE_LAYOUT_MSG = ("{who}: '{hit}' changes the worktree layout. A task's worktree is created by "
                       "advance.py and removed by the merge cleanup (worktree.py --remove and "
                       "--repair are the orchestrator's). Report the layout change you need to "
                       "the orchestrator instead.")


def worktree_layout_change(command: str, _depth: int = 0) -> str:
    """What `command` runs that changes the worktree layout: `git worktree add|remove|move|
    prune|repair|lock|unlock`, or worktree.py with --remove / --repair, else ''. Nested
    `sh -c` bodies are unwrapped like runs_waive does; text that cannot be tokenised is
    left to the unparseable-command denies that run beside this one."""
    for sub, args in pretool_gate.git_invocations(command):
        if sub == "worktree" and args and args[0].lower() in GIT_WORKTREE_WRITERS:
            return f"git worktree {args[0].lower()}"
    for seg in pretool_gate.command_segments(command) or []:
        if not any(WORKTREE_SCRIPT_RE.search(n) for n in pretool_gate.exec_names_of(seg)):
            continue
        for tok in seg:
            name = tok.partition("=")[0]
            if tok.startswith("--") and any(is_long_option_prefix(name, o) for o in WORKTREE_WRITE_OPTIONS):
                return f"worktree.py {name}"
    if pretool_gate.unscannable_depth(command, _depth):
        return "a command nested too deep to read"
    for body in pretool_gate.nested_command_bodies(command):
        hit = worktree_layout_change(body, _depth + 1)
        if hit:
            return hit
    return ""


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


# Refs a dev agent may not write by hand. refs/remotes/ holds what the gates compare HEAD
# against (`git merge-base --is-ancestor origin/main HEAD`, the leak guard's range), and
# `git update-ref refs/remotes/origin/main HEAD` or `git fetch . +HEAD:refs/remotes/origin/main`
# makes any branch look up to date with main. A plain `git fetch origin` is how the real
# remote updates them and stays allowed.
# git expands a destination `remotes/x` to `refs/remotes/x`, so both spellings land there.
REMOTE_REFS_PREFIXES = ("refs/remotes", "remotes")
# Subcommands whose refspec destination writes a ref: fetch and pull into the local
# repository (`git fetch . X:refs/remotes/..`), push to a path (`git push . X:refs/remotes/..`).
REFSPEC_WRITERS = ("fetch", "pull", "push")
DEV_REF_MSG = ("Dev agent: {hit} writes a ref by hand. refs/remotes/ is what the branch-base and "
               "leak checks trust, so it is updated only by a plain `git fetch <remote>`. Report "
               "the ref change you need to the orchestrator.")


def is_remote_ref(name: str) -> bool:
    """True for a ref name under refs/remotes/ in either spelling, any case."""
    low = strip_quotes(name).casefold()
    return any(low == prefix or low.startswith(prefix + "/") for prefix in REMOTE_REFS_PREFIXES)


def refspec_writes_remote_refs(args: list) -> bool:
    """True when a fetch/pull/push refspec (or a --refmap value) has a destination under
    refs/remotes/: `+src:refs/remotes/origin/main`, `HEAD:remotes/*`."""
    words, n = [], 0
    while n < len(args):
        tok, n = args[n], n + 1
        if tok == "--refmap" and n < len(args):
            words.append(args[n])
            n += 1
        elif tok.startswith("--refmap="):
            words.append(tok.partition("=")[2])
        elif not tok.startswith("-"):
            words.append(tok)
    return any(":" in word and is_remote_ref(strip_quotes(word).rpartition(":")[2])
               for word in words)


def symbolic_ref_writes_remote_ref(args: list) -> bool:
    """True for `git symbolic-ref` that sets or deletes a ref under refs/remotes/, or points
    another symbolic ref at one (a single operand without -d is a read)."""
    operands = [tok for tok in args if not tok.startswith("-")]
    deleting = any(tok == "-d" or (tok.startswith("--") and is_long_option_prefix(
        tok.partition("=")[0], "--delete")) for tok in args)
    return any(is_remote_ref(op) for op in operands) and (deleting or len(operands) >= 2)


def dev_ref_write(command: str, _depth: int = 0) -> str:
    """The git call in `command` that writes a ref by hand ('git update-ref', a refspec
    fetch/pull/push whose destination is under refs/remotes/, or 'git symbolic-ref' on such
    a ref), else ''. Nested shell bodies count."""
    for sub, args in pretool_gate.git_invocations(command):
        if sub == "update-ref":
            return "git update-ref"
        if sub in REFSPEC_WRITERS and refspec_writes_remote_refs(args):
            return f"git {sub} into refs/remotes/"
        if sub == "symbolic-ref" and symbolic_ref_writes_remote_ref(args):
            return "git symbolic-ref on refs/remotes/"
    if _depth < pretool_gate.MAX_SHELL_DEPTH:
        for body in pretool_gate.nested_command_bodies(command):
            hit = dev_ref_write(body, _depth + 1)
            if hit:
                return hit
    return ""


# Redirect detection is shared with the main-thread gate - single source of truth.
redirect_write_target = pretool_gate.redirect_write_target

# One definition of the path questions both gates ask (task-0096): pretool_gate owns
# them, this module re-exports them so a docs agent and the review freeze can never
# answer the same path differently.
README_RE = pretool_gate.README_RE
normalize = pretool_gate.normalize
is_root_claude_md = pretool_gate.is_root_claude_md

# The whole .agentry/state/ tree is the orchestrator's: the approvals level and mode,
# the run.db commit/push/plan approval flags, the solo trunk_push approval and the
# memory stamps all decide what clears without the User. Nothing a subagent writes
# lives there. .agentry/pipeline.json is protected for the same reason: it holds the
# gate configuration, so a subagent that could edit it could switch the gates off.
# Windows resolves `state.` and `state ` (and `state./x`, `pipeline.json.`) to the
# same entry (measured), so the segment tolerates trailing dots and spaces.
PROTECTED_STATE_RE = re.compile(r"(^|/)\.agentry/(state[. ]*(/|$)|pipeline\.json[. ]*$)")
PROTECTED_STATE_MSG = ("{who}: '{path}' is under .agentry/state/, is .agentry/pipeline.json, is "
                       "the live harness (.claude/tools, rules, skills, commands, settings, agent "
                       "definitions of the project root), or is inside a .git directory. They hold "
                       "the approvals level, workflow mode, run flags, gate settings and gate code "
                       "that decide what clears without the User, and the git hooks and config that "
                       "run as the orchestrator at its next commit. Report the change you need to "
                       "the orchestrator.")
# Any repository's .git directory (or the gitfile named .git): a hook written there runs
# as the orchestrator at its next commit or push, and `config` can name a program for
# every later git call (SEC-A M3 of task-0120). Windows ignores trailing dots and spaces
# on a name, so `.git.` and `.git ` are the same entry. `.gitignore`, `.github` and
# `.githooks` are different names and stay ordinary files.
GIT_DIR_RE = re.compile(r"(^|/)\.git[. ]*(/|$)")
# The live workspace's instruction trees. A docs agent writes documents, and rules, skills
# and commands steer every later session, so its edits there go through the orchestrator.
# Only the project ROOT's copy counts; a checkout under src/ is ordinary code.
LIVE_INSTRUCTIONS_RE = re.compile(r"\.claude/(?:rules|skills|commands)(?:/.*)?")
LIVE_INSTRUCTIONS_MSG = ("Docs agent: '{path}' is in the live workspace's .claude/rules, .claude/skills "
                         "or .claude/commands, which steer every later session. Write the change under "
                         "src/.claude/ (the checkout) or report it to the orchestrator.")
UNPARSEABLE_MSG = ("{who}: this command cannot be tokenised (an unbalanced quote - often an "
                   "apostrophe in a message or a heredoc body), so the gate cannot tell what "
                   "it runs and refuses it. Balance the quotes, or write the text with "
                   "Write/Edit instead of a heredoc.")
TOO_COMPLEX_MSG = ("{who}: this command is too complex to check - it nests shells or "
                   "substitutions past the gate's scan budget, so the gate cannot tell what "
                   "it runs and refuses it. Split it into simpler commands.")


def unparseable_msg(who: str, command: str) -> str:
    """The deny text for a command the gate cannot read: one over the scan budget
    says so instead of blaming the quotes."""
    template = TOO_COMPLEX_MSG if pretool_gate.too_complex(command) else UNPARSEABLE_MSG
    return template.format(who=who)


# A word that starts with TWO OR MORE separators (either slash) and then a host character is a
# network path: Git Bash collapses `///host/share` to `//host/share`, and `\\host\share` is the
# native spelling, so neither a third separator nor a backslash is an escape. unc_argument uses
# this instead of pretool_gate.is_unc because the host character must not be whitespace, which
# keeps a search pattern like '// TODO' out of it.
UNC_WORD_RE = re.compile(r"^[\\/]{2,}[^\\/\s]")
GLUED_SHORT_OPTION_RE = re.compile(r"^-[A-Za-z]+")


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


def resolves_into_state(form: str) -> bool:
    """True when the absolute `form` is the real state directory (or below it), or
    the same file as the live pipeline.json. The regex only knows the NAME
    `.agentry/state/`; this follows a junction or symlink at either end, and a
    hard link to pipeline.json (samefile compares the file identity)."""
    if not os.path.isabs(form) or pretool_gate.is_unc(form):
        return False
    try:
        real = normalize(os.path.realpath(form))
        state_dir = normalize(os.path.realpath(state.STATE_DIR))
        if real == state_dir or real.startswith(state_dir + "/"):
            return True
        return os.path.samefile(form, state.PIPELINE_PATH)
    except (OSError, ValueError):
        return False


def is_protected_state(path: str, cwd: str = "") -> bool:
    """`path` is under .agentry/state/ or is .agentry/pipeline.json, by name or by
    where it resolves (see pretool_gate.path_forms). `cwd` is the payload cwd, which
    a relative path resolves against."""
    forms = pretool_gate.path_forms(path, cwd)
    return (any(PROTECTED_STATE_RE.search(normalize(f)) for f in forms)
            or any(resolves_into_state(f) for f in forms))


def is_git_internals(path: str, cwd: str = "") -> bool:
    """`path` is, or lies inside, a .git directory, by name or by where it resolves."""
    return any(GIT_DIR_RE.search(normalize(f)) for f in pretool_gate.path_forms(path, cwd))


def is_live_instructions(path: str, cwd: str = "") -> bool:
    """`path` resolves to <ROOT>/.claude/rules, skills or commands (or below)."""
    root = normalize(os.path.realpath(state.ROOT))
    for form in pretool_gate.path_forms(path, cwd):
        p = normalize(form)
        if p.startswith(root + "/") and LIVE_INSTRUCTIONS_RE.fullmatch(p[len(root) + 1:]):
            return True
    return False


def is_protected(path: str, cwd: str = "") -> bool:
    """What no subagent profile may write: the state tree, pipeline.json, the live
    harness, and any .git directory."""
    return (is_protected_state(path, cwd) or pretool_gate.is_live_harness(path, cwd)
            or is_git_internals(path, cwd))


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


def git_mutates(command: str, _depth: int = 0, subs: frozenset | None = None) -> str:
    """Return the mutating git invocation, or '' if every git call is read-only.
    Unparseable git text is treated as mutating (fail-closed) - see
    pretool_gate.git_invocations().

    `subs` swaps the question for "does any git call use one of these subcommands"
    (ZONED_GIT_DENIED): every spelling of a listed subcommand counts, `git stash` and
    `git checkout <branch>` included, and the text-only nets below stay out of it.

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
        if subs is not None:
            if sub in subs and not (sub == "stash" and args and args[0].lower() in ("list", "show")):
                return f"git {sub}"
            continue
        if sub in MUTATING_GIT_SUBS:
            return f"git {sub}"
        if sub == "stash" and args and args[0].lower() in MUTATING_GIT_STASH_ARGS:
            return f"git stash {args[0].lower()}"
        if sub == "checkout" and "--" in args:
            return "git checkout --"
    if subs is None and pretool_gate.gate_tokens(command) is None:
        # Heredoc-stripped: the body is data on stdin, and this net would
        # otherwise read a document that MENTIONS `git add` as a `git add`.
        m = MUTATING_GIT_FALLBACK_RE.search(pretool_gate.strip_heredocs(command))
        if m:
            return "git " + " ".join(m.group("sub").split())
    if pretool_gate.unscannable_depth(command, _depth):
        return pretool_gate.DEPTH_EXCEEDED
    for body in pretool_gate.nested_command_bodies(command):
        hit = git_mutates(body, _depth + 1, subs)
        if hit:
            return hit
    return ""


def base_name(token: str) -> str:
    """argv0 reduced to a bare command name: no directory, no .exe."""
    name = re.split(r"[\\/]", token)[-1].lower()
    return name.removesuffix(".exe")


def in_place_sed(args: list) -> bool:
    """`sed -i`, `sed -i.bak`, a cluster ending in it (`sed -ni`), `--in-place`, `--in-place=.bak`
    and every abbreviation of it (getopt_long: `--i`, `--in`, `--in-pl=.bak`)."""
    for tok in args:
        if tok.startswith("--") and is_long_option_prefix(tok.partition("=")[0], "--in-place"):
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


GLOB_RE = re.compile(r"[*?\[]")
MAX_GLOB_HITS = 64
# A glob walk is bounded by time as well as by hits: a pattern with many wildcard levels
# over a big tree (`C:/Windows/*/*/*/*/*/zz?`) yields nothing for minutes, and the hook's
# own timeout is an allow. Past this deadline glob_hits() raises and the caller denies.
GLOB_DEADLINE_SECONDS = 2.0


class GlobDeadline(Exception):
    """A glob expansion ran past GLOB_DEADLINE_SECONDS. Deliberately NOT an OSError or
    ValueError: the callers that skip an unreadable glob must not swallow this one."""


def glob_hits(pattern: str) -> list:
    """At most MAX_GLOB_HITS matches of `pattern`, taken lazily: glob.glob would
    expand every match of a broad pattern before the slice cut it down, which is
    time an attacker chooses, inside a hook whose timeout is an allow. A UNC
    pattern is never expanded (it would walk a network share). The walk runs in a
    daemon thread and is abandoned after GLOB_DEADLINE_SECONDS (GlobDeadline): a
    generator that finds nothing never yields, so the clock cannot be checked from
    the consuming side."""
    if pretool_gate.is_unc(pattern):
        return []
    hits, failure = [], []

    def walk() -> None:
        try:
            hits.extend(itertools.islice(glob.iglob(pattern), MAX_GLOB_HITS))
        except BaseException as exc:  # handed to the caller below, never lost in the thread
            failure.append(exc)

    worker = threading.Thread(target=walk, daemon=True)
    worker.start()
    worker.join(GLOB_DEADLINE_SECONDS)
    if worker.is_alive():
        raise GlobDeadline(pattern)
    if failure:
        raise failure[0]
    return list(hits)


def base_dir(cwd: str) -> str:
    """The directory a relative path resolves against: the payload cwd when it is
    absolute, else the project root."""
    return cwd if cwd and os.path.isabs(cwd) else str(state.ROOT)


# What a substitution is replaced by: a shell variable, because what it expands to is
# unknown to the gate and an argument that holds it counts as an expansion (dynamic_arg).
SUBST_WORD = "$X"
# A process substitution always expands to a /dev/fd/N path, never to an option.
PROCESS_SUBST_WORD = "/dev/fd/9"


def flatten_substitutions(command: str) -> str:
    """`command` with every `$( ... )`, `<( ... )`, `>( ... )` and backtick span
    replaced by SUBST_WORD, quoting respected as in pretool_gate.command_substitutions.
    shlex does not know a substitution: `level=$(python x.py --show)` tokenises into an
    assignment followed by a bogus argv0 `x.py`. The bodies are judged on their own,
    through nested_command_bodies."""
    out, i, n, dq = [], 0, len(command), False
    while i < n:
        ch = command[i]
        if ch == "\\":
            out.append(command[i:i + 2])
            i += 2
        elif ch == '"':
            dq = not dq
            out.append(ch)
            i += 1
        elif ch == "'" and not dq:
            j = command.find("'", i + 1)
            j = n if j < 0 else j + 1
            out.append(command[i:j])
            i = j
        elif ch == "`":
            j = command.find("`", i + 1)
            out.append(SUBST_WORD)
            i = n if j < 0 else j + 1
        elif (ch in "$<>" and command.startswith(ch + "(", i) and (ch == "$" or not dq)
              and not command.startswith("$((", i)):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += (command[j] == "(") - (command[j] == ")")
                j += 1
            out.append(SUBST_WORD if ch == "$" else PROCESS_SUBST_WORD)
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def walk_segments(command: str, cwd: str = "", expand_project_dir: bool = False) -> list | None:
    """[(simple command tokens, directory it runs in)], in order, None when the
    text cannot be tokenised. A `cd DIR` / `pushd DIR` moves the directory for every
    later command, so `cd .agentry && sed -i s/a/b/ pipeline.json` is judged on the
    file it writes. A subshell cd is applied too: more denies, never fewer.
    `expand_project_dir` reads a leading $CLAUDE_PROJECT_DIR in the target as the
    project root (the caller refuses a command that assigns the variable)."""
    segments = pretool_gate.command_segments(command)
    if segments is None:
        return None
    cur, out = base_dir(cwd), []
    for seg in segments:
        out.append((seg, cur))
        i = pretool_gate.argv0_index(seg)
        if i is not None and base_name(seg[i]) in ("cd", "pushd"):
            target = next((t for t in seg[i + 1:] if not t.startswith("-")), "")
            if expand_project_dir:
                target = PROJECT_DIR_VAR_RE.sub(lambda _: str(state.ROOT), target)
            target = pretool_gate.msys_to_win(target)
            if target and not target.startswith("~"):
                cur = os.path.normpath(os.path.join(cur, target))
    return out


def strip_quotes(tok: str) -> str:
    return tok.strip().strip("'\"")


def protected_operand(tok: str, dirs: list) -> bool:
    """`tok` names (or, as a glob, expands to) a protected path from any of `dirs`."""
    for d in dirs:
        if is_protected(tok, d):
            return True
        if GLOB_RE.search(tok):
            try:
                hits = glob_hits(os.path.join(d, strip_quotes(tok)))
            except (OSError, ValueError):
                continue
            if any(is_protected(h, d) for h in hits):
                return True
    return False


def sed_files(args: list) -> list:
    """The file operands of `sed -i`: the non-option words, minus the script, which is
    the first of them unless -e/-f (or their long forms) supplied it."""
    plain, script_given, skip = [], False, False
    for tok in args:
        if skip:
            skip = False
        elif tok == "--":
            continue
        elif tok.startswith("-") and len(tok) > 1:
            name = tok.partition("=")[0]
            long_form = tok.startswith("--") and any(
                is_long_option_prefix(name, o) for o in ("--expression", "--file"))
            if long_form or (not tok.startswith("--") and tok.startswith(("-e", "-f"))):
                script_given = True
                # a bare option takes the next word as its value; `-e1d` and `--exp=1d` do not
                skip = name == tok and (long_form or tok in ("-e", "-f"))
        else:
            plain.append(tok)
    return plain if script_given else plain[1:]


def path_operands(name: str, args: list) -> list:
    """The words of one mutating command that name a path: no options, no `chmod` mode,
    no `sed` script. Only for the commands that write the files they name."""
    if name == "sed -i":
        return sed_files(args)
    plain = [t for t in args if t != "--" and not (t.startswith("-") and len(t) > 1)]
    return plain[1:] if name == "chmod" else plain


def write_operands(command: str, cwd: str = "", _depth: int = 0, paths_only: bool = False):
    """Yield (operand, directories it may resolve against, mutating command) for every
    path `command` writes through a redirect or a mutating command (`tee`, `sed -i`,
    `cp` onto it, `rm`, ...). A redirect yields '' as the command and every directory
    the command visits; an operand yields the directory its own segment runs in. Nested
    shell bodies are walked too. `paths_only` keeps the commands that write the files
    they name (not `npm install`) and reduces their words to those files."""
    walked = walk_segments(command, cwd) or []
    dirs = list(dict.fromkeys([base_dir(cwd)] + [d for _, d in walked]))
    for frag in pretool_gate.redirect_write_fragments(command):
        yield pretool_gate.redirect_target(frag).strip("'\""), dirs, ""
    for segment, cur in walked:
        name = mutating_command(segment)
        if not name or (paths_only and name not in PATH_WRITERS):
            continue
        i = pretool_gate.argv0_index(segment)
        operands = real_arguments(segment[i + 1:])
        if base_name(segment[i]) == "cp":
            operands = target_directory(operands) or destination_operand(operands)
        elif paths_only:
            operands = path_operands(name, operands)
        for tok in operands:
            yield tok, [cur], name
    if _depth < pretool_gate.MAX_SHELL_DEPTH:
        for body in pretool_gate.nested_command_bodies(command):
            yield from write_operands(body, cwd, _depth + 1, paths_only)


# --- The writers only the trunk freeze judges (epic-0004) -----------------------------
# write_operands() knows the writers the plan gate and the swarm zone map share. The trunk
# freeze adds the other common ones, and the git calls that rewrite the working tree. They
# stay out of MUTATING_COMMANDS on purpose: the plan gate and the readonly profile judge
# exactly what they judged before.
TRUNK_ONLY_WRITERS = frozenset({"touch", "rmdir", "unlink", "install", "dd", "patch"})
TRUNK_WRITER_NAMES = MUTATING_COMMANDS | TRUNK_ONLY_WRITERS
TRUNK_GIT_WRITERS = frozenset({"restore", "apply", "am", "clean", "rm", "mv"})
# The read-only forms of a git writer: `git apply --check`, `git clean -n`.
TRUNK_GIT_READ_FORMS = {"apply": ("--check", "--stat", "--numstat", "--summary"),
                        "clean": ("-n", "--dry-run")}
FIND_WRITE_ACTIONS = frozenset({"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
FIND_EXEC_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
# Options whose value is the next word, so the word is no path of the command's.
TOUCH_VALUE_OPTIONS = frozenset({"-d", "-t", "-r", "--date", "--time", "--reference"})
INSTALL_VALUE_OPTIONS = frozenset({"-m", "-o", "-g", "-S", "--mode", "--owner", "--group", "--suffix"})
PATCH_VALUE_OPTIONS = frozenset({"-d", "-i", "-o", "-p", "-r", "-B", "-V", "-Y", "-z", "-D", "-F",
                                 "--directory", "--input", "--strip", "--reject-file"})


def option_operands(args: list, value_options: frozenset) -> list:
    """The words of `args` that are operands: no option, no separate option value."""
    out, skip = [], False
    for n, tok in enumerate(args):
        if skip:
            skip = False
        elif tok == "--":
            out.extend(args[n + 1:])
            break
        elif tok.startswith("-") and len(tok) > 1:
            skip = "=" not in tok and tok in value_options
        else:
            out.append(tok)
    return out


def find_write_targets(args: list) -> list:
    """The start points of a `find` that deletes or writes (an action of its own, or an
    -exec of a writer), else []. A read-only find, `-exec grep` included, yields nothing."""
    writes = any(tok in FIND_WRITE_ACTIONS for tok in args)
    for n, tok in enumerate(args):
        if tok in FIND_EXEC_ACTIONS and n + 1 < len(args) and base_name(args[n + 1]) in TRUNK_WRITER_NAMES:
            writes = True
    if not writes:
        return []
    starts = []
    for tok in args:
        if tok.startswith("-") or tok in ("(", ")", "!", ","):
            break
        starts.append(tok)
    return starts or ["."]


def trunk_command_targets(name: str, args: list) -> list:
    """The words one command names as what it writes, for the commands only the trunk
    freeze judges (TRUNK_ONLY_WRITERS and `find`)."""
    if name == "dd":
        return [tok[3:] for tok in args if tok.startswith("of=")]
    if name == "find":
        return find_write_targets(args)
    if name == "touch":
        return option_operands(args, TOUCH_VALUE_OPTIONS)
    if name in ("rmdir", "unlink"):
        return option_operands(args, frozenset())
    if name == "install":
        into = target_directory(args)
        if into:
            return into
        operands = option_operands(args, INSTALL_VALUE_OPTIONS)
        return operands if "-d" in args else operands[-1:]
    if name == "patch":
        where = next((tok.partition("=")[2] for tok in args if tok.startswith("--directory=")), "")
        for n, tok in enumerate(args):
            if tok == "-d" and n + 1 < len(args):
                where = args[n + 1]
            elif tok.startswith("-d") and len(tok) > 2 and not tok.startswith("--"):
                where = tok[2:]
        return [where or ".", *option_operands(args, PATCH_VALUE_OPTIONS)]
    return []


def git_writes_tree(sub: str, args: list) -> bool:
    """Whether `git SUB ARGS` rewrites working-tree files: restore, apply, am, clean, rm,
    mv, `checkout -- paths` and `stash pop|apply`, except the read-only forms."""
    if sub in TRUNK_GIT_WRITERS:
        return not any(tok in TRUNK_GIT_READ_FORMS.get(sub, ()) for tok in args)
    if sub == "checkout":
        return "--" in args
    return sub == "stash" and bool(args) and args[0].lower() in ("pop", "apply")


def trunk_write_operands(command: str, cwd: str = "", _depth: int = 0):
    """Yield (operand, directories it may resolve against) for what `command` writes
    through the writers only the trunk freeze judges: touch, rmdir, unlink, install, dd
    of=, patch, `find -delete`, and the git calls that rewrite the working tree (their
    operand is the directory the call runs in, placed by its own -C and the cd in front
    of it). Nested shell bodies are walked too."""
    for segment, cur in walk_segments(command, cwd) or []:
        i = pretool_gate.argv0_index(segment)
        if i is not None:
            name, args = base_name(segment[i]), real_arguments(segment[i + 1:])
            for tok in trunk_command_targets(name, args):
                yield tok, [cur]
        for sub, gargs, opts in pretool_gate.git_calls_in(segment):
            if git_writes_tree(sub, gargs):
                yield ".", [pretool_gate.place_of(opts, cur)]
    if _depth < pretool_gate.MAX_SHELL_DEPTH:
        for body in pretool_gate.nested_command_bodies(command):
            yield from trunk_write_operands(body, cwd, _depth + 1)


def writes_protected_state(command: str, _depth: int = 0, cwd: str = "") -> str:
    """The protected path (see is_protected) a Bash command writes through a
    redirect or a mutating command (`tee`, `sed -i`, `cp` onto it, `rm`, ...), else ''.

    task-0097 denied the dev profile a Write or Edit there, but `echo auto >
    .agentry/state/approvals` set the same level from Bash. Operands are judged where
    they resolve (junction, 8.3 name, case, `..`, a stream suffix, a glob, the payload
    cwd, a `cd` earlier in the command). Code an interpreter runs (`python -c
    "open(...)"`) is still not seen (task-0100): the readonly and docs profiles refuse
    the interpreter itself (program_denied), the dev profile does not."""
    for tok, dirs, _ in write_operands(command, cwd, _depth):
        if protected_operand(tok, dirs):
            return tok
    return ""


# --- Default-deny Bash for the readonly and docs profiles (task-0116) --------
# The gates above recognise writes lexically, so a Python one-liner, a script the
# agent wrote itself or any program the denylist forgot (awk, dd of=, perl -i, curl
# -o, node) reaches the same files. These two profiles have no reason to run code, so
# they are inverted: a simple command runs only when its argv0 is on this list.
# Every program here has an option allowlist of its own (FLAG_SPECS, below: an option
# that is not named is refused, SEC-A H1 of task-0120); sed and awk are absent on purpose
# (`sed -n 'w f'`, `awk system()`), the Read/Grep/Glob tools replace them.
READ_ONLY_PROGRAMS = frozenset({
    "cat", "head", "tail", "ls", "dir", "pwd", "echo", "printf", "wc", "cut", "tr", "nl", "tac",
    "rev", "column", "fold", "comm", "paste", "diff", "cmp", "stat", "file", "du", "which",
    "where", "basename", "dirname", "realpath", "readlink", "date", "uname", "whoami",
    "hostname", "id", "sleep", "seq", "expr", "type", "true", "false", "test", "[", "[[", "cd", "read",
    "for", "exit", "grep", "egrep", "fgrep", "rg", "jq", "find", "sort", "uniq", "git",
    "codegraph",
})
ALLOWED_PROGRAMS = {"readonly": READ_ONLY_PROGRAMS, "docs": READ_ONLY_PROGRAMS}
# Harness scripts an interpreter may run, and only from <ROOT>/.claude/tools/ - which
# no dev or docs agent can write (is_live_harness), so a copy planted under another
# directory under the same name does not qualify. The setters among them are refused
# separately (sets_pipeline_state) unless they only read.
ALLOWED_SCRIPTS = frozenset({
    "memory.py", "state.py", "handoff.py", "events.py", "approvals.py", "mode.py", "swarm.py",
    "repo_scan.py",
})
PYTHON_ENV_RE = re.compile(r"^PYTHON\w*=", re.IGNORECASE)
PROJECT_DIR_ASSIGN_RE = re.compile(r"CLAUDE_PROJECT_DIR\s*=")
PROJECT_DIR_VAR_RE = re.compile(r"^\$\{?CLAUDE_PROJECT_DIR\}?")
ASSIGNMENT_RE = pretool_gate.ASSIGNMENT_RE
ALLOWLIST_PHRASE = "not on the Bash allowlist"
PROGRAM_MSG = ("{who}: '{what}' is " + ALLOWLIST_PHRASE + " of this profile (read-only "
               "utilities, git, codegraph, and the harness scripts memory.py, state.py, "
               "handoff.py, events.py, approvals.py, mode.py, swarm.py and repo_scan.py). "
               "Use Read/Grep/Glob, or ask the orchestrator to run it. The options of the allowed "
               "programs are allowlisted too (agent_gate.FLAG_SPECS), as are NAME=value prefixes "
               "(none) and shell expansions in the arguments of programs with a write or exec "
               "option. A tool this agent legitimately needs is added with --allow-script on "
               "its hook line.")


# --- git for the readonly and docs profiles (FX-3 of task-0120) --------------
# `git log --pretty=format:auto --output=.agentry/state/approvals` exits 0 and writes
# `auto` into the approvals file: an allowed program's own flag is a file write. The
# same family reaches the exec side (`git -c core.fsmonitor=CMD status`, an alias set
# with `git config`, GIT_EXTERNAL_DIFF) and the subcommands that write by design
# (`archive -o`, `format-patch -o`, `bundle create`, `apply`, `hash-object -w`). A
# denylist of writers cannot be complete, so the subcommand is default-denied like the
# program is: only the read-only ones below run, and the options that write are refused
# inside them. A `git branch` or `git stash` is allowed in its listing form only.
GIT_READ_SUBS = frozenset({
    "log", "show", "diff", "status", "blame", "annotate", "shortlog", "describe", "rev-parse",
    "rev-list", "merge-base", "name-rev", "show-ref", "for-each-ref", "ls-files", "ls-tree",
    "cat-file", "grep", "diff-tree", "diff-index", "diff-files", "whatchanged", "show-branch",
    "cherry", "range-diff", "count-objects", "check-ignore", "check-attr", "verify-commit",
    "verify-tag", "patch-id", "var", "version",
})
# Global options that run something or force a pager: `-c`/`--config-env` inject
# config (core.fsmonitor, core.pager, alias.*), `-p`/`--paginate` start the pager.
GIT_EXEC_GLOBALS = frozenset({"-c", "--config-env", "-p", "--paginate"})
# Global options whose value is the NEXT word (`--opt=value` is one token). Not
# `--exec-path`, which takes its value with `=` only.
GIT_VALUE_GLOBALS = frozenset({"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
                               "--attr-source"})
# Long options that write a file or run a program: `--output=FILE` (diff, log, show),
# `--ext-diff`, grep's `--open-files-in-pager`. git abbreviates any unambiguous prefix and
# an ambiguous one is an error, so a prefix of any of these counts (see
# is_long_option_prefix). `--output-indicator-new` is longer than `--output`, so it is
# not a prefix of it and stays allowed.
GIT_WRITE_LONG_OPTIONS = ("--output", "--ext-diff", "--open-files-in-pager")
# A variable a git child honours as a program to run: GIT_EXTERNAL_DIFF, GIT_PAGER,
# GIT_SSH_COMMAND, GIT_CONFIG_*, and the pager/editor fallbacks.
GIT_ENV_RE = re.compile(r"^(?:GIT_\w*|PAGER|EDITOR|VISUAL)=", re.IGNORECASE)
BRANCH_READ_LONG = frozenset({
    "--list", "--show-current", "--all", "--remotes", "--verbose", "--contains", "--no-contains",
    "--merged", "--no-merged", "--points-at", "--sort", "--format", "--abbrev", "--no-abbrev",
    "--column", "--no-column", "--color", "--no-color", "--ignore-case",
})
BRANCH_LISTING = frozenset({"--list", "--show-current", "--contains", "--no-contains", "--merged",
                            "--no-merged", "--points-at", "--sort", "--format"})
BRANCH_READ_SHORT = frozenset("avrliq")


def branch_lists(rest: list) -> bool:
    """`git branch` that only lists: read-only flags, and a positional word (a pattern,
    or the value of `--contains`) only when a listing flag is present. `git branch NAME`
    creates a branch, `-d`/`-m`/`-c`/`-f`/`-u` delete, rename, copy or re-point one."""
    listing = positional = False
    for tok in rest:
        if tok.startswith("--"):
            name = tok.partition("=")[0]
            if name not in BRANCH_READ_LONG:
                return False
            listing = listing or name in BRANCH_LISTING
        elif tok.startswith("-") and len(tok) > 1:
            if not set(tok[1:]) <= BRANCH_READ_SHORT:
                return False
            listing = listing or "l" in tok
        else:
            positional = True
    return listing or not positional


def git_option_writes(sub: str, tok: str) -> bool:
    """True when one argument of an allowed git subcommand is an option that writes a
    file or runs a program. `grep -O[CMD]` runs CMD (any short cluster holding the
    O, `-nOless`); `diff -O<file>` only READS an order file and is not refused."""
    if tok.startswith("--"):
        name = tok.partition("=")[0]
        return any(is_long_option_prefix(name, o) for o in GIT_WRITE_LONG_OPTIONS)
    return sub == "grep" and tok.startswith("-") and "O" in tok


def git_denied(args: list) -> str:
    """What makes `git ARGS` more than a read ('' when nothing does): a global option
    that injects config or starts a pager, a subcommand outside GIT_READ_SUBS, a
    `branch`/`stash` that is not a listing, or an option that writes a file or runs a
    program. A git call with no subcommand (`git --version`) is a read."""
    args = real_arguments(args)
    n = 0
    while n < len(args) and args[n].startswith("-"):
        flag, eq, _ = args[n].partition("=")
        if flag in GIT_EXEC_GLOBALS:
            return flag
        n += 2 if flag in GIT_VALUE_GLOBALS and not eq else 1
    if n >= len(args):
        return ""
    sub, rest = args[n].lower(), args[n + 1:]
    if sub == "stash" and rest[:1] not in (["list"], ["show"]):
        return "stash"
    if sub == "branch" and not branch_lists(rest):
        return "branch (not a listing)"
    if sub not in GIT_READ_SUBS | {"stash", "branch"}:
        return sub
    return next((f"{sub} {tok}" for tok in rest if git_option_writes(sub, tok)), "")


# --- memory.py --out / --db (FX-3 of task-0120) -------------------------------
# `memory.py --export --out FILE` overwrites FILE with a markdown export and `--db FILE`
# creates a store there, so a path anywhere else replaces pipeline.json, a state file
# or gate code - and a broken gate script exits non-2, which is an allow. memory.py is
# on ALLOWED_SCRIPTS for readonly and docs and a dev agent runs it too, so the target is
# checked for every profile. memory.py refuses the same places itself (second layer).
MEMORY_SCRIPT = "memory.py"
MEMORY_TOOL_TAIL = "/.claude/tools/memory/memory.py"
MEMORY_TARGET_MSG = ("{who}: memory.py {option} '{value}' is not under .agentry/memory/ or tmp/. "
                     "--export --out overwrites the file it names and --db creates one, so a "
                     "target elsewhere can replace pipeline.json, a state file or gate code. "
                     "Write the export under tmp/ (or leave --out off for the default) and "
                     "report what you need changed to the orchestrator.")


def memory_options(args: list) -> list:
    """[(option, value)] for every `--out` and `--db` among a memory.py call's arguments,
    in argparse's spellings: `--out F`, `--out=F` and any prefix of `--out` (`--o`,
    `--ou`; no other memory.py option starts `--o`). `--db` has no unambiguous
    abbreviation (`--d` is also `--dry-run`)."""
    found, n = [], 0
    while n < len(args):
        tok, n = args[n], n + 1
        if tok == "--":
            break
        name, eq, value = tok.partition("=")
        if is_long_option_prefix(name, "--out"):
            option = "--out"
        elif name == "--db":
            option = "--db"
        else:
            continue
        if not eq:
            value, n = (args[n] if n < len(args) else ""), n + 1
        found.append((option, value))
    return found


def memory_target_ok(value: str, cur: str, roots: list) -> bool:
    """True when `value` is a plain path that resolves under one of `roots`. A value the
    shell would still expand (`$VAR`, a substitution, `~`) names no path the gate can
    see, so it is not ok."""
    if not value or "$" in value or "`" in value or value.startswith("~"):
        return False
    real = [normalize(f) for f in pretool_gate.path_forms(value, cur)[1:]]  # [0] is the spelling as written
    return bool(real) and all(any(f == r or f.startswith(r + "/") for r in roots) for f in real)


def memory_target_denied(command: str, cwd: str = "", _depth: int = 0) -> tuple:
    """(option, value) of the first `--out`/`--db` of a harness memory.py call in
    `command` that points outside .agentry/memory/ and tmp/, else ()."""
    roots = [normalize(os.path.realpath(state.ROOT / ".agentry" / "memory")),
             normalize(os.path.realpath(state.ROOT / "tmp"))]
    for seg, cur in walk_segments(command, cwd) or []:
        for i, word in pretool_gate.exec_tokens(seg):
            if i < 0 or script_label(word) != MEMORY_SCRIPT:
                continue
            tool = normalize(pretool_gate.safe_realpath(
                os.path.join(cur, pretool_gate.msys_to_win(word))))
            if not tool.endswith(MEMORY_TOOL_TAIL):
                continue  # not the harness script: a profile that may not run it is refused elsewhere
            for option, value in memory_options(real_arguments(seg[i + 1:])):
                if not memory_target_ok(value, cur, roots):
                    return option, value
    if _depth < pretool_gate.MAX_SHELL_DEPTH:
        for body in pretool_gate.nested_command_bodies(command):
            hit = memory_target_denied(body, cwd, _depth + 1)
            if hit:
                return hit
    return ()


def memory_target_deny(who: str, command: str, cwd: str) -> int:
    """deny() for a memory.py target outside its two directories, allow() otherwise.
    Fails CLOSED: this check guards a write, so an error in it is a refusal."""
    try:
        hit = memory_target_denied(command, cwd)
    except Exception:
        hit = ("--out/--db", "(the target check failed on this command)")
    return deny(MEMORY_TARGET_MSG.format(who=who, option=hit[0], value=hit[1])) if hit else allow()


# --- git writing options in the dev profile (FX-3b of task-0120) --------------
# readonly and docs refuse every git call that is not a read. The dev profile commits
# and branches, so git stays open there, but `git log --output=FILE` writes FILE exactly
# as a redirect does: onto the approvals file, the trunk-push marker, pipeline.json or
# the live harness, which every profile is refused. So the file or directory a git
# option names is judged like a redirect target, and a target the gate cannot resolve is
# refused. Other git writes to working files stay allowed. The options come from
# `git <sub> -h` (2.51): git abbreviates any unambiguous long option, so a prefix counts.
GIT_TARGET_LONG = ("--output", "--output-directory", "--export-marks")
# The short option that names a file or directory, per subcommand (`archive -o FILE`,
# `format-patch -o DIR`, `index-pack -o FILE`, `config -f FILE`). Elsewhere `-o` is a
# flag (`ls-files -o`), not a target.
GIT_TARGET_SHORT = {"archive": "o", "format-patch": "o", "index-pack": "o", "config": "f"}
# Global options that can move git's working tree, so a relative target no longer
# resolves against the directory the gate sees. A GIT_* assignment anywhere in the
# command can do the same (GIT_WORK_TREE, GIT_DIR, GIT_CONFIG_*).
GIT_MOVING_GLOBALS = frozenset({"-c", "--config-env", "--work-tree", "--git-dir"})
GIT_ENV_ASSIGN_RE = re.compile(r"(?<!\w)GIT_\w*\s*=", re.IGNORECASE)
GIT_TARGET_MSG = ("{who}: git {option} '{value}' writes into .agentry/state/, .agentry/pipeline.json "
                  "or the live harness (.claude/tools, settings, agent definitions of the project "
                  "root), or its target cannot be resolved, so the gate refuses it. `git log "
                  "--output=FILE` writes FILE like a redirect does. Write under tmp/ and report "
                  "what you need changed to the orchestrator.")


def git_target_options(sub: str, rest: list) -> list:
    """[(option, value)] for every option of `git SUB REST` that names a file or directory
    git writes: `--output`, `--output-directory`, `--export-marks` and `config --file`
    (`--opt=V`, `--opt V` and every unambiguous prefix), the short option of
    GIT_TARGET_SHORT (`-o V`, `-oV`, inside a cluster `-no V`) and the words after
    `bundle create`. A missing value is ''. Arguments after a bare `--` are pathspecs."""
    found = []
    words = [w for w in rest if not w.startswith("-")]
    if sub == "bundle" and words[:1] == ["create"]:
        found += [("bundle create", w) for w in words[1:]]
    long_names = GIT_TARGET_LONG + (("--file",) if sub == "config" else ())
    letter, n = GIT_TARGET_SHORT.get(sub, ""), 0
    while n < len(rest):
        tok, n = rest[n], n + 1
        if tok == "--":
            break
        name, eq, value = tok.partition("=")
        if tok.startswith("--"):
            if any(is_long_option_prefix(name, o) for o in long_names):
                found.append((name, value if eq else (rest[n] if n < len(rest) else "")))
        elif letter and tok.startswith("-") and letter in tok[1:]:
            glued = tok[1:].partition(letter)[2]
            found.append((f"-{letter}", glued or (rest[n] if n < len(rest) else "")))
    return found


def git_call_target_hit(seg: list, cur: str, moved: bool) -> tuple:
    """(option, value) of the first git target in one simple command that is protected or
    unresolvable, else (). `-C DIR` moves the directory the target resolves against;
    `moved` says an environment assignment may have moved the working tree."""
    for i, tok in enumerate(seg):
        if not pretool_gate.GIT_TOKEN_RE.match(tok):
            continue
        args, n, base, lost = real_arguments(seg[i + 1:]), 0, cur, False
        while n < len(args) and args[n].startswith("-"):
            flag, eq, _ = args[n].partition("=")
            lost = lost or flag in GIT_MOVING_GLOBALS
            if flag == "-C" and n + 1 < len(args):
                where = args[n + 1]
                if "$" in where or "`" in where or where.startswith("~"):
                    lost = True
                else:
                    base = os.path.normpath(os.path.join(base, pretool_gate.msys_to_win(where)))
            n += 2 if (flag in GIT_VALUE_GLOBALS or flag in ("-c", "--config-env")) and not eq else 1
        if n >= len(args):
            continue
        for option, value in git_target_options(args[n].lower(), args[n + 1:]):
            if (lost or moved or not value or "$" in value or "`" in value or value.startswith("~")
                    or protected_operand(value, [base])):
                return option, value
    return ()


def git_target_denied(command: str, cwd: str = "", _moved: bool | None = None, _depth: int = 0) -> tuple:
    """(option, value) of the first git option in `command` whose target is protected state,
    pipeline.json or the live harness, or cannot be resolved, else ()."""
    moved = bool(GIT_ENV_ASSIGN_RE.search(command)) if _moved is None else _moved
    for seg, cur in walk_segments(command, cwd) or []:
        hit = git_call_target_hit(seg, cur, moved)
        if hit:
            return hit
    if _depth < pretool_gate.MAX_SHELL_DEPTH:
        for body in pretool_gate.nested_command_bodies(command):
            hit = git_target_denied(body, cwd, moved, _depth + 1)
            if hit:
                return hit
    return ()


def git_target_deny(who: str, command: str, cwd: str) -> int:
    """deny() for a git write target the dev profile may not touch, allow() otherwise.
    Fails CLOSED, like memory_target_deny: an error in a check that guards a write is a
    refusal."""
    try:
        hit = git_target_denied(command, cwd)
    except Exception:
        hit = ("option", "(the target check failed on this command)")
    return deny(GIT_TARGET_MSG.format(who=who, option=hit[0], value=hit[1])) if hit else allow()


# --- Per-program flag allowlists (SEC-A H1 of task-0120) ------------------------------
# A program on READ_ONLY_PROGRAMS runs with the options listed for it here and no others.
# The denylist this replaces (find actions, sort -o, uniq's second operand, rg --pre) was
# closed under nothing: `sort --compress-program=PROG` runs PROG, `rg --search-zip` runs a
# decompressor, `RIPGREP_CONFIG_PATH=f rg` loads a config that carries `--pre`, `date -s`
# sets the clock, `file -C` writes a compiled magic file. An option not named below is
# refused, so a flag a future version adds is refused until someone reads what it does.
#
# What is NOT a spelling this module can judge, and is refused instead:
#   * a `NAME=value` in front of the program (see text_program_denied): the environment
#     reaches the program's own configuration (RIPGREP_CONFIG_PATH, GIT_*, PAGER, PATH);
#   * a word the shell will still expand (`$x`, `$(..)`, a backtick, a leading `{`) in an
#     argument of a program that has a write or exec option, because the expansion can be
#     `-o` or `--output=f` and the gate sees `$x`. After a bare `--` the words are operands
#     and may expand (`rg -- "$term" docs`);
#   * options and operands the allowlist does not name.
# Not covered: a glob that expands to a file NAMED like an option (`sort *` beside a file
# called `--output=x`). The gate sees `*`; a docs agent could create such a file.
#
# Programs left out on purpose (a Bash tool is the wrong tool for them): sed and awk
# (`w file`, `system()`), tar (`--to-command`, `-I`), less and more (pager shell escape),
# xargs as a program (it is a wrapper: the command behind it is judged), curl, dd, tee.
class FlagSpec:
    """The options one allowed program takes. `short` and `short_value` are letters
    (`-n`, a cluster `-rn`; a value letter ends a cluster and takes the rest of the word or
    the next word), `long` are boolean names, `long_value` the names that take values (an
    `:N` suffix says how many words, default 1). `max_operands` caps the non-option words;
    `operand_prefix` is what every operand must start with."""

    def __init__(self, short: str = "", short_value: str = "", long: str = "",
                 long_value: str = "", max_operands: int | None = None, operand_prefix: str = ""):
        self.short = frozenset(short)
        self.short_value = frozenset(short_value)
        self.long = frozenset(long.split())
        self.long_value = {n: int(c or 1) for n, _, c in (item.partition(":") for item in long_value.split())}
        self.max_operands = max_operands
        self.operand_prefix = operand_prefix


_HELP = "--help --version"
_HEAD_TAIL = FlagSpec("qvz", "nc", "--quiet --silent --verbose --zero-terminated " + _HELP,
                      "--lines --bytes")  # no -f/-F/--follow/--pid/--retry: they never return
_LS = FlagSpec(
    "aAbBcCdDfFgGhHiklLmnNopqQrRsStuUvxXZ1", "ITw",
    "--all --almost-all --escape --ignore-backups --classify --file-type --dereference "
    "--dereference-command-line --directory --human-readable --si --inode --kibibytes "
    "--numeric-uid-gid --literal --hide-control-chars --show-control-chars --quote-name "
    "--reverse --recursive --size --group-directories-first --no-group --author --full-time "
    "--color --context " + _HELP,
    "--block-size --format --hide --ignore --indicator-style --quoting-style --sort --time "
    "--time-style --width --tabsize")
_GREP = FlagSpec(
    "abcEFGHhIiLlnoPqRrsTUuVvwxyZz", "ABCDdefmX",
    "--basic-regexp --byte-offset --count --dereference-recursive --extended-regexp "
    "--files-with-matches --files-without-match --fixed-strings --perl-regexp --ignore-case "
    "--no-ignore-case --initial-tab --invert-match --line-buffered --line-number --line-regexp "
    "--no-filename --no-messages --null --null-data --only-matching --quiet --recursive "
    "--silent --text --binary --unix-byte-offsets --with-filename --word-regexp "
    "--no-group-separator --color --colour " + _HELP,
    "--after-context --before-context --binary-files --context --devices --directories "
    "--exclude --exclude-dir --exclude-from --file --group-separator --include --label "
    "--max-count --regexp")

FLAG_SPECS = {
    "cat": FlagSpec("AbeEnstTuv", "", "--show-all --number-nonblank --show-ends --number "
                    "--squeeze-blank --show-tabs --show-nonprinting " + _HELP),
    "head": _HEAD_TAIL, "tail": _HEAD_TAIL, "ls": _LS, "dir": _LS,
    "pwd": FlagSpec("LP", "", "--logical --physical " + _HELP),
    "wc": FlagSpec("cmlLw", "", "--bytes --chars --lines --max-line-length --words " + _HELP),
    "cut": FlagSpec("nsz", "bcdf", "--complement --only-delimited --zero-terminated " + _HELP,
                    "--bytes --characters --delimiter --fields --output-delimiter"),
    "tr": FlagSpec("cCdst", "", "--complement --delete --squeeze-repeats --truncate-set1 " + _HELP),
    "nl": FlagSpec("p", "bdfhilnsvw", "--no-renumber " + _HELP,
                   "--body-numbering --section-delimiter --footer-numbering --header-numbering "
                   "--line-increment --join-blank-lines --number-format --number-separator "
                   "--starting-line-number --number-width"),
    "tac": FlagSpec("br", "s", "--before --regex " + _HELP, "--separator"),
    "rev": FlagSpec("hV"),
    "column": FlagSpec("txJ", "scoNRTHEWl", "--table --json " + _HELP),
    "fold": FlagSpec("bs", "w", "--bytes --spaces " + _HELP, "--width"),
    "comm": FlagSpec("123z", "", "--check-order --nocheck-order --total --zero-terminated " + _HELP,
                     "--output-delimiter"),
    "paste": FlagSpec("sz", "d", "--serial --zero-terminated " + _HELP, "--delimiters"),
    # no -l / --paginate: it pipes the output through `pr`
    "diff": FlagSpec(
        "abBcdEfiNnpqrsTtuwyZ", "CUDFIWxXLS",
        "--text --ignore-space-change --ignore-blank-lines --ignore-case --ignore-tab-expansion "
        "--ignore-trailing-space --ignore-all-space --expand-tabs --initial-tab --minimal "
        "--brief --report-identical-files --recursive --new-file --unidirectional-new-file "
        "--no-dereference --strip-trailing-cr --side-by-side --left-column "
        "--suppress-common-lines --show-c-function --speed-large-files --ed --rcs --unified "
        "--context --normal --color --ignore-file-name-case --no-ignore-file-name-case "
        "--suppress-blank-empty " + _HELP,
        "--ignore-matching-lines --label --exclude --exclude-from --width --starting-file "
        "--show-function-line"),
    "cmp": FlagSpec("bls", "inI", "--print-bytes --verbose --silent --quiet " + _HELP,
                    "--ignore-initial --bytes --skip-bytes --ignore-bytes"),
    "stat": FlagSpec("LftZ", "c", "--dereference --file-system --terse " + _HELP,
                     "--format --printf"),
    # no -C / --compile (writes a compiled magic file), no -z / -Z / --uncompress (runs a
    # decompressor)
    "file": FlagSpec("bhiIkLnNprsv", "fmeP",
                     "--brief --mime --mime-type --mime-encoding --keep-going --dereference "
                     "--no-dereference --no-buffer --no-pad --preserve-date --raw --special-files "
                     "--print0 " + _HELP, "--files-from --magic-file --exclude --parameter"),
    "du": FlagSpec("abchHkLmPsSxX0", "dtB",
                   "--all --apparent-size --bytes --total --dereference-args --human-readable --si "
                   "--inodes --dereference --separate-dirs --summarize --one-file-system "
                   "--count-links --no-dereference --null --time " + _HELP,
                   "--block-size --exclude --exclude-from --max-depth --threshold --time-style"),
    "which": FlagSpec("asV", "", "--all --version --help"),
    "where": FlagSpec(),
    "basename": FlagSpec("az", "s", "--multiple --zero " + _HELP, "--suffix"),
    "dirname": FlagSpec("z", "", "--zero " + _HELP),
    "realpath": FlagSpec("eEmsqzLP", "", "--canonicalize-existing --canonicalize-missing "
                         "--no-symlinks --quiet --strip --zero --logical --physical " + _HELP,
                         "--relative-to --relative-base"),
    "readlink": FlagSpec("femnqsvz", "", "--canonicalize --canonicalize-existing "
                         "--canonicalize-missing --no-newline --quiet --silent --verbose --zero " + _HELP),
    # no -s / --set (sets the clock); an operand that does not start with + would
    "date": FlagSpec("uRI", "drf", "--utc --universal --rfc-email --debug --resolution "
                     "--iso-8601 " + _HELP, "--date --reference --file --rfc-3339",
                     operand_prefix="+"),
    "uname": FlagSpec("asnrvmpio", "", "--all --kernel-name --nodename --kernel-release "
                      "--kernel-version --machine --processor --hardware-platform "
                      "--operating-system " + _HELP),
    "whoami": FlagSpec("", "", _HELP),
    "hostname": FlagSpec("sfiIdaAyv", "", _HELP, max_operands=0),  # an operand SETS the name
    "id": FlagSpec("agGnrRuzZ", "", "--context --group --groups --name --real --user --zero " + _HELP),
    "seq": FlagSpec("w", "fs", "--equal-width " + _HELP, "--format --separator"),
    "type": FlagSpec("afpPt"),
    "read": FlagSpec("res", "dinNptu"),  # operands are variable names, see safe_var
    "grep": _GREP, "egrep": _GREP, "fgrep": _GREP,
    # no --pre / --pre-glob (run a program per file), no -z / --search-zip (run a
    # decompressor), no --hostname-bin, no --generate
    "rg": FlagSpec(
        "FHILNPSUVabchilnopqsuvwx0", "ABCEMTdefgjmrt",
        "--auto-hybrid-regex --binary --block-buffered --byte-offset --case-sensitive --column "
        "--count --count-matches --crlf --debug --files --files-with-matches --files-without-match "
        "--fixed-strings --follow --glob-case-insensitive --heading --hidden --ignore-case "
        "--ignore-file-case-insensitive --include-zero --invert-match --json --line-buffered "
        "--line-number --line-regexp --max-columns-preview --mmap --multiline --multiline-dotall "
        "--no-binary --no-block-buffered --no-byte-offset --no-column --no-config --no-crlf "
        "--no-filename --no-glob-case-insensitive --no-heading --no-hidden --no-ignore "
        "--no-ignore-dot --no-ignore-exclude --no-ignore-files --no-ignore-global "
        "--no-ignore-messages --no-ignore-parent --no-ignore-vcs --no-include-zero "
        "--no-invert-match --no-line-buffered --no-line-number --no-max-columns-preview "
        "--no-messages --no-mmap --no-multiline --no-multiline-dotall --no-one-file-system "
        "--no-pcre2 --no-require-git --no-sort-files --no-stats --no-trim --no-unicode --null "
        "--null-data --one-file-system --only-matching --passthru --pcre2 --pretty --quiet "
        "--smart-case --sort-files --stats --text --trace --trim --type-list --unicode "
        "--unrestricted --vimgrep --with-filename --word-regexp " + _HELP,
        "--after-context --before-context --color --colors --context --context-separator "
        "--dfa-size-limit --encoding --engine --field-context-separator --field-match-separator "
        "--file --glob --iglob --ignore-file --max-columns --max-count --max-depth --maxdepth "
        "--max-filesize --path-separator --regex-size-limit --regexp --replace --sort --sortr "
        "--threads --type --type-add --type-clear --type-not"),
    "jq": FlagSpec(
        "CMSabcehjnrsRV", "fL",
        "--null-input --raw-input --slurp --compact-output --raw-output --raw-output0 "
        "--join-output --ascii-output --color-output --monochrome-output --sort-keys --tab --seq "
        "--stream --stream-errors --unbuffered --exit-status --binary --args --jsonargs " + _HELP,
        "--indent --arg:2 --argjson:2 --slurpfile:2 --rawfile:2 --from-file --library-path"),
    # no -o / --output, no --compress-program (runs a program), no -T / --temporary-directory,
    # no --files0-from, no --parallel
    "sort": FlagSpec(
        "bdfghiMnRrVcCsumz", "ktS",
        "--ignore-leading-blanks --dictionary-order --ignore-case --general-numeric-sort "
        "--human-numeric-sort --ignore-nonprinting --month-sort --numeric-sort --random-sort "
        "--reverse --version-sort --check --stable --unique --zero-terminated --merge --debug "
        + _HELP, "--key --field-separator --buffer-size --sort"),
    # a second operand is the OUTPUT file
    "uniq": FlagSpec("cdDiuz", "fsw", "--count --repeated --all-repeated --ignore-case --unique "
                     "--zero-terminated --group " + _HELP,
                     "--skip-fields --skip-chars --check-chars", max_operands=1),
}
# Programs whose words are not options: shell builtins and tests, `expr 5 - 3`, `sleep 1`.
# None of them takes an option that writes or runs anything. `for` and `printf` get a
# check of their own (loop variable, `printf -v`), `find` and `codegraph` are judged below.
ANY_FLAGS = frozenset({"echo", "test", "[", "[[", "true", "false", "expr", "sleep", "exit", "cd"})
# Programs with a write or exec option of their own: a word the shell will still expand
# is refused in their arguments (see the block comment). The second set refuses it after
# `--` too, because there a second word is a second operand (uniq's output file, date's
# new time, hostname's new name) or a predicate (find -delete).
DYNAMIC_REFUSED = frozenset({"find", "sort", "uniq", "rg", "git", "codegraph", "date", "file",
                             "hostname", "diff"})
DYNAMIC_ANYWHERE = frozenset({"find", "uniq", "date", "hostname"})
DYNAMIC_RE = re.compile(r"\$[\w@*#?!$'\"{(-]|`")
# find takes single-dash predicates, so the allowlist is a set of names. `-newerXY` is
# matched by FIND_NEWER_RE. Left out: -exec -execdir -ok -okdir -delete -fprint -fprint0
# -fprintf -fls (run a program or write a file) and -files0-from.
FIND_ALLOWED = frozenset({
    "-H", "-L", "-P", "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex",
    "-iregex", "-regextype", "-type", "-xtype", "-size", "-mtime", "-atime", "-ctime", "-mmin",
    "-amin", "-cmin", "-newer", "-anewer", "-cnewer", "-used", "-perm", "-user", "-group", "-uid",
    "-gid", "-nouser", "-nogroup", "-links", "-inum", "-samefile", "-lname", "-ilname", "-fstype",
    "-empty", "-executable", "-readable", "-writable", "-true", "-false", "-print", "-print0",
    "-ls", "-printf", "-prune", "-quit", "-not", "-and", "-or", "-a", "-o", "-depth", "-d",
    "-maxdepth", "-mindepth", "-mount", "-xdev", "-follow", "-noleaf", "-daystart",
    "-ignore_readdir_race", "-noignore_readdir_race", "-warn", "-nowarn", "-help", "-version",
})
FIND_NEWER_RE = re.compile(r"^-newer[acBmt][acBmt]$")
# A negative number is a predicate's VALUE (`-mtime -7`, `-size -10k`, `-perm -644`), never an option.
FIND_NUMBER_RE = re.compile(r"^-\d+[A-Za-z]*$")
# codegraph's read commands. init, uninit, index, sync, install, uninstall, upgrade, daemon,
# unlock, serve and telemetry write the index, the agent configuration or the binary.
CODEGRAPH_READ = frozenset({"explore", "callers", "callees", "impact", "affected", "query", "node",
                            "files", "status", "version", "help"})
CODEGRAPH_GLOBALS = frozenset({"-V", "-v", "--version", "-h", "--help"})
# A shell variable an agent may assign: lower case and not already in the environment. An
# exported name (PATH, HOME, TEMP, a GIT_* or *_PROXY left by the host) reaches every child
# process; a new lower-case name stays a shell variable and is never exported.
SAFE_VAR_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def safe_var(name: str) -> bool:
    return bool(SAFE_VAR_RE.match(name)) and name.casefold() not in {k.casefold() for k in os.environ}


def dynamic_arg(args: list, anywhere: bool) -> str:
    """The first word that still holds a shell expansion (`$x`, `${x}`, `$(`, a backtick,
    a leading `{` for brace expansion), before a bare `--` unless `anywhere`."""
    for tok in args:
        if tok == "--" and not anywhere:
            break
        if DYNAMIC_RE.search(tok) or tok.startswith("{"):
            return tok
    return ""


def scan_flags(spec: FlagSpec, args: list) -> tuple:
    """(first option the spec does not name or '', the operands). A word after a bare `--`
    is an operand; a lone `-` is stdin. Long options are matched by their whole name:
    getopt also accepts an unambiguous prefix (`--out` for `--output`), and a prefix is not
    on the list, so it is refused."""
    operands, n = [], 0
    while n < len(args):
        tok, n = args[n], n + 1
        if tok == "--":
            operands.extend(args[n:])
            break
        if tok.startswith("--"):
            name, eq, _ = tok.partition("=")
            if name in spec.long:
                continue
            if name in spec.long_value:
                n += 0 if eq else spec.long_value[name]
                continue
            return tok, operands
        if tok.startswith("-") and len(tok) > 1:
            for k, ch in enumerate(tok[1:], 1):
                if ch in spec.short_value:
                    n += 0 if k < len(tok) - 1 else 1
                    break
                if ch not in spec.short and not ch.isdigit():
                    return tok, operands
            continue
        operands.append(tok)
    return "", operands


def find_denied(args: list) -> str:
    """The first word of a `find` that is a single-dash predicate or option it does not
    allow (`-exec`, `-delete`, `-fprint`, ...)."""
    return next((a for a in args if a.startswith("-") and len(a) > 1 and a not in FIND_ALLOWED
                 and not FIND_NEWER_RE.match(a) and not FIND_NUMBER_RE.match(a)), "")


def codegraph_denied(args: list) -> str:
    """'' for a read command (or a version/help flag), else the word that is not one."""
    if not args:
        return ""
    return "" if args[0] in CODEGRAPH_READ | CODEGRAPH_GLOBALS else args[0]


# Programs that print or compare their words and open no path. Every other allowed program
# reads or stats the files it is given, and a UNC operand makes the host contact a server of
# the agent's choosing (credentials go with the connection), so one is refused for them all.
NO_PATH_PROGRAMS = frozenset({"echo", "printf", "expr", "true", "false", "sleep", "exit", "seq",
                              "basename", "dirname"})


PATTERN_PROGRAMS = frozenset({"grep", "egrep", "fgrep", "rg"})


def unc_argument(name: str, args: list) -> str:
    """The first argument that is a network path, bare or as the value of `--option=VALUE`.
    For grep and rg the word after a bare `-e` / `--regexp` is a search pattern, not a path
    (`grep -e '//TODO'`); for `test -e` it is the file."""
    pattern_next = False
    for tok in args:
        if pattern_next:
            pattern_next = False
            continue
        pattern_next = name in PATTERN_PROGRAMS and tok in ("-e", "--regexp")
        word = strip_quotes(tok)
        # The word itself, the value of `--option=VALUE`, and the operand glued to a short
        # option (`grep -f//host/share/p`).
        for form in (word, word.partition("=")[2], GLUED_SHORT_OPTION_RE.sub("", word)):
            if UNC_WORD_RE.match(form.strip()):
                return tok
    return ""


def unsafe_args(name: str, args: list) -> str:
    """The argument that gives an allowed program a write or exec mode, or that the
    program's allowlist does not name, else ''. Fails closed for a program on
    READ_ONLY_PROGRAMS that has no entry above."""
    args = real_arguments(args)
    if name not in NO_PATH_PROGRAMS:
        hit = unc_argument(name, args)
        if hit:
            return f"{hit} (a network path: the gate never touches one)"
    if name in DYNAMIC_REFUSED:
        hit = dynamic_arg(args, name in DYNAMIC_ANYWHERE)
        if hit:
            return f"{hit} (a shell expansion the gate cannot read)"
    if name == "git":
        return git_denied(args)
    if name == "find":
        return find_denied(args)
    if name == "codegraph":
        return codegraph_denied(args)
    if name == "printf":  # bash's `printf -v VAR` assigns a shell variable, PATH included
        return args[0] if args[:1] and args[0].startswith("-v") else ""
    if name == "for":
        return args[0] if args and not safe_var(args[0]) else ""
    if name in ANY_FLAGS:
        return ""
    spec = FLAG_SPECS.get(name)
    if spec is None:
        return "(no flag allowlist for this program)"
    flag, operands = scan_flags(spec, args)
    if flag:
        return flag
    if name == "read":
        return next((o for o in operands if not safe_var(o)), "")
    if spec.max_operands is not None and len(operands) > spec.max_operands:
        return f"{operands[spec.max_operands]} (operand)"
    return next((o for o in operands if not o.startswith(spec.operand_prefix)), "")


def planted_binary(token: str, cur: str) -> bool:
    """A path-qualified argv0 that resolves INSIDE the project: a file the agent could
    have written under the name of an allowed program. System binaries live outside."""
    if not re.search(r"[\\/]", token):
        return False
    if pretool_gate.is_unc(token) or pretool_gate.is_unc(os.path.join(cur, token)):
        return True
    real = normalize(os.path.realpath(os.path.join(cur, token)))
    root = normalize(os.path.realpath(state.ROOT))
    return real == root or real.startswith(root + "/")


def interpreter_script_denied(seg: list, i: int, cur: str, extra: set, project_dir: bool) -> str:
    """'' when the python at seg[i] runs a harness script it may run, else what it
    runs. `-c`, stdin (`-`, `< file`, a pipe) and a bare REPL name no script. `-m MOD`
    is the file MOD.py resolved from the current directory, which is where python looks
    first, so a planted copy elsewhere does not qualify."""
    if any(PYTHON_ENV_RE.match(t) for t in seg[:i]):
        return "python (PYTHON* variable)"
    found = pretool_gate.script_operand(seg, i)
    if found is None:
        return "python (no script file)"
    n, word = found
    if any(t.startswith("-") and not t.startswith("--") and "c" in t[1:] for t in seg[i + 1:n + 1]):
        return "python -c"
    name = script_label(word)
    if name in extra:
        return ""
    if project_dir:
        word = PROJECT_DIR_VAR_RE.sub(lambda _: str(state.ROOT), word)
    tools = normalize(os.path.realpath(state.ROOT / ".claude" / "tools"))
    if pretool_gate.is_unc(os.path.join(cur, word)):
        return f"python {word}"
    real = normalize(os.path.realpath(os.path.join(cur, word)))
    if name in ALLOWED_SCRIPTS and real.startswith(tools + "/"):
        return ""
    return f"python {word}"


def text_program_denied(text: str, profile: str, extra: set, cwd: str, project_dir: bool) -> str:
    """program_denied() for ONE command text, its substitutions flattened out."""
    walked = walk_segments(flatten_substitutions(text), cwd, project_dir)
    if walked is None:
        return "(command could not be parsed)"
    allowed = ALLOWED_PROGRAMS[profile]
    for seg, cur in walked:
        i = pretool_gate.argv0_index(seg)
        if i is None:
            # `PATH=docs/bin; ls` assigns without running anything, and the assignment
            # outlives the segment. Only a new lower-case name is a harmless variable.
            bad = next((t for t in seg if ASSIGNMENT_RE.match(t)
                        and not safe_var(t.partition("=")[0])), "")
            if bad:
                return f"{bad.partition('=')[0]}= (an assignment to a name the environment may use)"
            continue
        name = base_name(seg[i])
        # `NAME=value prog` hands the value to the program: RIPGREP_CONFIG_PATH loads a
        # config that carries `--pre`, GIT_* names a pager, PYTHONPATH a module. Refused
        # in front of every program, an interpreter running a harness script included.
        assignment = next((t for t in seg[:i] if ASSIGNMENT_RE.match(t)), "")
        if pretool_gate.PYTHON_RE.match(seg[i]) and name not in extra:
            if assignment:
                return f"python ({assignment.partition('=')[0]}= prefix)"
            hit = interpreter_script_denied(seg, i, cur, extra, project_dir)
            if hit:
                return hit
        elif name in extra:
            continue
        elif name not in allowed or planted_binary(seg[i], cur):
            return seg[i]
        elif assignment:
            if name == "git" and GIT_ENV_RE.match(assignment):
                return "git (GIT_*, PAGER, EDITOR or VISUAL variable)"
            return f"{name} ({assignment.partition('=')[0]}= prefix)"
        else:
            hit = unsafe_args(name, seg[i + 1:])
            if hit:
                return f"{name} {hit}"
    return ""


def program_denied(command: str, profile: str, allow_scripts: tuple = (), cwd: str = "") -> str:
    """The first program `command` runs that `profile` may not ('' when all are
    allowed). Default-deny: a simple command whose argv0 is not on ALLOWED_PROGRAMS,
    not an allowed harness script and not named in `allow_scripts` is the hit.
    Substitution bodies (`$( )`, backticks, `<( )`) are judged the same way. A nested
    shell or `eval` is no allowed program, so its body never needs unwrapping. Text that
    cannot be tokenised is a hit."""
    extra = {base_name(a) for a in allow_scripts}
    project_dir = not PROJECT_DIR_ASSIGN_RE.search(command)
    for text in [command, *pretool_gate.substitution_bodies(command)]:
        hit = text_program_denied(text, profile, extra, cwd, project_dir)
        if hit:
            return hit
    return ""


# --- Swarm zones (task-0060 S3) -----------------------------------------------
# While a frozen zone map is in force (zones.load(): the umbrella task sits at implement
# or test) a dev agent may write only inside the zone its own dispatch prompt names, or
# under a shared directory. Everything else is refused, with the owning zone named. The
# zone comes from the agent's OWN transcript (docs/technical/agent-identity-2.1.295.md);
# the parent transcript names every agent's zone and is never read. With no zones.json,
# or with its task outside implement/test, none of this runs and dev behaves as before.
# A map that is unusable denies: an agent must not slip out of its zone because the file
# was damaged.
PATH_WRITERS = MUTATING_COMMANDS | {"sed -i"}
# Git commands that rewrite files without naming a path, or move work between zones.
ZONED_GIT_DENIED = frozenset({
    "checkout", "switch", "stash", "reset", "restore", "clean",
    "apply", "am", "merge", "rebase", "cherry-pick", "revert", "pull", "rm", "mv",
})
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
ZONE_MSG = ("Dev agent: '{path}' is outside your swarm zone {zone}: {owner}. Write only "
            "inside your zone, under tmp/ or in the swarm state dir; report the change you "
            "need to the orchestrator.")
ZONE_NONE_MSG = ("Dev agent: a swarm zone map is in force and your dispatch prompt names no "
                 "'Swarm zone: <id>' line the gate can read, so you may not write ('{path}'). "
                 "Report it to the orchestrator.")
ZONE_GIT_MSG = ("Dev agent: {hit} is not available while a swarm zone map is in force - it "
                "rewrites files outside your zone and can undo a sibling's work. Ask the "
                "orchestrator.")
ZONE_BROKEN_MSG = ("Dev agent: a swarm zone map exists ({why}) but cannot be used, so writes "
                   "are refused until the orchestrator re-freezes it (zones.py --prove).")


def valid_zone_map(data: object) -> bool:
    """The frozen map has the shape zones.py writes: a task id and zone id -> paths, every
    path an exact path or dir/**."""
    if not (isinstance(data, dict) and isinstance(data.get("task"), str)
            and isinstance(data.get("zones"), dict)):
        return False
    try:
        return all(isinstance(paths, list) and paths
                   and all(isinstance(p, str) and zones.parse_path(p) for p in paths)
                   for paths in data["zones"].values())
    except ValueError:
        return False


def map_is_dormant() -> bool:
    """zones.load() said None. True when that is only 'its task is not at implement/test'
    (a well-formed map of a finished swarm); False when the file is unreadable or not a map,
    or when the task's stage could not be READ (a locked or corrupt run.db): an unknown
    stage is not a finished swarm (SEC-A M5 of task-0120). The stage is read again here, and
    only a stage that is read and is not live counts, so a database that was unreadable a
    moment ago and says implement now is refused as well."""
    try:
        data = json.loads(zones.zones_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return False
    if not valid_zone_map(data):
        return False
    stage = zones.task_stage(data["task"])
    return stage != zones.STAGE_UNREADABLE and stage not in zones.LIVE_STAGES


def agent_transcript_dir(payload: dict) -> Path | None:
    """The directory holding the calling agent's OWN transcript, or None. Needs the
    payload's agent_id (absent on the main thread), session_id and transcript_path."""
    agent_id, sid, transcript = (payload.get(k) for k in ("agent_id", "session_id",
                                                           "transcript_path"))
    if not (isinstance(agent_id, str) and isinstance(sid, str) and isinstance(transcript, str)
            and transcript and SESSION_ID_RE.match(sid)):
        return None
    return Path(transcript).parent / sid / "subagents"


def agent_zone(payload: dict) -> str | None:
    """The zone of the calling agent, read from its own transcript, or None."""
    where = agent_transcript_dir(payload)
    return None if where is None else zones.zone_for_agent(payload["agent_id"], where)


def agent_worktree_task(payload: dict) -> str | None:
    """The `Task worktree: <task>` of the calling agent's dispatch prompt, read from its
    own transcript like the zone, or None."""
    where = agent_transcript_dir(payload)
    return None if where is None else zones.worktree_task_for_agent(payload["agent_id"], where)


def zone_path_denial(path: str, dirs: list, zone: str, data: dict) -> str:
    """'' when every place `path` can name (resolved against each of `dirs`, a glob
    expanded) lies in `zone` or under a shared directory, else the deny text."""
    root = pretool_gate.normalize(os.path.realpath(state.ROOT))
    state_dir = pretool_gate.normalize(os.path.realpath(state.STATE_DIR))
    shared = [root + "/tmp"] + ([state_dir] if state_dir.startswith(root + "/") else [])
    swarm = zones.swarm_dir(data.get("task"))
    if swarm is not None:  # the board and QA reports of THIS swarm, no other task folder
        shared.append(pretool_gate.normalize(os.path.realpath(swarm)))
    if pretool_gate.is_unc(path):
        return ZONE_MSG.format(path=path, zone=zone, owner="it is a network path, outside the project")
    places = []
    for d in dirs:
        # [0] is the path as written; only where it resolves counts here
        places += pretool_gate.path_forms(path, d)[1:]
        if GLOB_RE.search(path):
            places += glob_hits(os.path.join(d, strip_quotes(path)))
    if not places:
        return ZONE_MSG.format(path=path, zone=zone, owner="the path could not be resolved")
    failures = []
    for place in dict.fromkeys(places):
        p = pretool_gate.normalize(os.path.realpath(place))
        if any(p.startswith(s + "/") for s in shared):
            continue
        if not p.startswith(root + "/"):
            failures.append((None, "it is outside the project"))
            continue
        rel = p[len(root) + 1:]
        owner = zones.owner_of(rel, data)
        if owner is None and os.path.isdir(place):
            owner = zones.owner_of(rel + "/_", data)  # a zone's own directory is the zone's
        if owner != zone:
            failures.append((owner, f"it belongs to zone {owner}" if owner
                             else "no zone owns it (undeclared or shared)"))
    if not failures:
        return ""
    # a place another zone owns is the useful thing to name, not the first place tried
    why = next((text for owner, text in failures if owner), failures[0][1])
    return ZONE_MSG.format(path=path, zone=zone, owner=why)


def zone_targets(tool: str, ti: dict, cwd: str):
    """(path, directories) for every file one tool call writes by an explicit path."""
    if tool == "Bash":
        for tok, dirs, _ in write_operands(str(ti.get("command", "")), cwd, paths_only=True):
            if tok.strip():
                yield tok, dirs
        return
    path, _ = pretool_gate.edit_target(tool, ti)
    if path.strip():
        yield path, [base_dir(cwd)]


def zone_denial(tool: str, ti: dict, cwd: str, payload: dict | None) -> str:
    """The reason a swarm zone refuses this dev tool call, '' when it does not. `payload`
    is the hook payload; None (a direct call without one) means no identity, no check."""
    if payload is None or not (tool == "Bash" or tool in pretool_gate.EDIT_TOOLS):
        return ""
    try:
        if not zones.zones_path().exists():
            return ""
    except OSError:
        return ""
    try:
        data = zones.load()
        if data is None and map_is_dormant():
            return ""
        hit = git_mutates(str(ti.get("command", "")), subs=ZONED_GIT_DENIED) if tool == "Bash" else ""
        targets = list(zone_targets(tool, ti, cwd))
        if not hit and not targets:
            return ""  # a read: nothing here for a zone to judge
        if data is None:
            return ZONE_BROKEN_MSG.format(why="unreadable, or the run database it depends on cannot be read")
        if not valid_zone_map(data):
            return ZONE_BROKEN_MSG.format(why="malformed")
        if hit:
            return ZONE_GIT_MSG.format(hit=hit)
        zone = agent_zone(payload)
        for path, dirs in targets:
            if zone is None:
                return ZONE_NONE_MSG.format(path=path)
            reason = zone_path_denial(path, dirs, zone, data)
            if reason:
                return reason
        return ""
    except Exception:
        return ZONE_BROKEN_MSG.format(why="the check itself failed")


# --- Task worktree confinement (task-0134) -----------------------------------------------
# While worktrees are in play (pretool_gate.worktrees_active(): the flag is on, or a run
# that is not done records a tree) a subagent that writes must write inside its OWN task's
# tree. Its task comes from the `Task worktree: <task>` line of its dispatch prompt, read
# from its own transcript the way the swarm zone is (agent_worktree_task), and is believed
# only for a task that holds a usable tree at an editing stage (worktree.holds_tree). The
# dev profile (Edit, Write, Bash) and the docs profile (Edit, Write) share the check.
#   identity T:  own tree allowed; another recorded tree denied; a repository checkout that
#                is no tree (the trunk, a run with no tree) denied; anywhere else allowed.
#   no identity: writes into any recorded tree denied; elsewhere unchanged, so a legacy or
#                umbrella dispatch keeps working while the flag is on.
#   any doubt:   the store unreadable, an operand the shell expands, a path that cannot be
#                placed, a module that fails: denied.
# Not covered, as everywhere in this file: code an interpreter runs (`python -c "open(...)"`).
WORKTREE_GIT_WRITERS = ZONED_GIT_DENIED | {"add", "commit"}
WT_OTHER_MSG = ("{who}: '{path}' is inside the worktree of {owner}, not yours ({mine}). Write "
                "only inside your own task worktree (or under tmp/ and .agentry/); report the "
                "change you need in {owner} to the orchestrator.")
WT_OUTSIDE_MSG = ("{who}: '{path}' is outside your task worktree ({mine}): it lies in a "
                  "repository checkout (the trunk, or a run that has no tree of its own). "
                  "Write only inside your worktree; report the change you need to the "
                  "orchestrator.")
WT_NONE_MSG = ("{who}: '{path}' is inside the worktree of {owner}, and your dispatch prompt "
               "names no task worktree the gate can believe. It needs a 'Task worktree: "
               "task-NNNN' line alone on its own line, for a task that holds a tree at an "
               "editing stage. Report it to the orchestrator, who re-dispatches you with it.")
WT_BROKEN_MSG = ("{who}: worktrees are in play and the gate cannot place this write ({why}), so "
                 "it refuses rather than guess. Name the file with a literal path (no $ or "
                 "backtick expansion); if it keeps happening, report the command to the "
                 "orchestrator.")


def worktree_flag(name: str) -> bool:
    """pretool_gate.worktrees_on or worktrees_active, by `name`. An exception reads as 'in
    play': a pretool_gate that lacks the function (a partial deploy) or fails must not turn
    a subagent's write check into an allow."""
    try:
        return bool(getattr(pretool_gate, name)())
    except Exception:
        return True


def worktree_places(tok: str, dirs: list) -> list:
    """[(place, directory)] for every place the written path `tok` can name from each of
    `dirs`, ready for worktree.owner_of(place, directory). Raises pretool_gate.Unplaceable
    for an operand the shell expands."""
    if "$" in tok or "`" in tok or tok.startswith("~"):
        raise pretool_gate.Unplaceable(f"'{tok}'")
    places = []
    for d in dirs:
        if pretool_gate.is_unc(tok):
            # An extended-length path is a local spelling and a share is not. path_forms
            # resolves neither (and mangles the drive colon), worktree.owner_of tells them apart.
            places.append((strip_quotes(tok), d))
            continue
        places += [(form, d) for form in pretool_gate.path_forms(tok, d)[1:]]
        if GLOB_RE.search(tok):
            places += [(hit, d) for hit in glob_hits(os.path.join(d, strip_quotes(tok)))]
    return places


def worktree_targets(tool: str, ti: dict, cwd: str) -> list:
    """[(shown, places)] for everything one tool call writes: the explicit write operands
    the swarm zones read, the trunk freeze's extra writers, and every state-changing git
    call, placed where it runs (its own -C and the cd in front of it)."""
    out = []
    for tok, dirs in zone_targets(tool, ti, cwd):
        out.append((tok, worktree_places(tok, dirs)))
    if tool != "Bash":
        return out
    command = str(ti.get("command", ""))
    for tok, dirs in trunk_write_operands(command, cwd):
        if tok.strip() and tok.strip().strip("'\"").lower() != "nul":
            out.append((tok, worktree_places(tok, dirs)))
    calls = pretool_gate.placed_git_calls(command, cwd, WORKTREE_GIT_WRITERS)
    if calls:
        hit = pretool_gate.repo_redirect(command) or next(
            (pretool_gate.redirecting_option(opts) for _, _, opts, _ in calls
             if pretool_gate.redirecting_option(opts)), "")
        if hit:
            raise pretool_gate.Unplaceable(f"{hit}, which moves the repository a git call runs in")
    for sub, _, _, place in calls or []:
        out.append((f"git {sub}", [(place, "")]))
    return out


def worktree_verdict(who: str, task: str | None, shown: str, places: list) -> str:
    """'' when every (place, directory) lies where the agent (task `task`, or None) may
    write."""
    import worktree

    for place, base in dict.fromkeys(places):
        owner = worktree.owner_of(place, base)
        if owner is not None and owner == task:
            continue
        if owner is not None:
            template = WT_OTHER_MSG if task else WT_NONE_MSG
            return template.format(who=who, path=shown, owner=owner, mine=task)
        if task and worktree.in_checkout(place, base):
            return WT_OUTSIDE_MSG.format(who=who, path=shown, mine=task)
    return ""


def worktree_denial(who: str, tool: str, ti: dict, cwd: str, payload: dict | None) -> str:
    """The reason task-worktree confinement refuses this tool call, '' when it does not.
    `payload` is the hook payload; None (a direct call without one) means no identity, no
    check. Inactive unless worktrees are in play; then any failure of the check denies."""
    if payload is None or not (tool == "Bash" or tool in pretool_gate.EDIT_TOOLS):
        return ""
    if not worktree_flag("worktrees_active"):
        return ""
    try:
        import worktree

        targets = worktree_targets(tool, ti, cwd)
        if not targets:
            return ""  # a read: nothing here for confinement to judge
        task = agent_worktree_task(payload)
        if task and not worktree.holds_tree(task):
            task = None
        for shown, places in targets:
            reason = worktree_verdict(who, task, shown, places)
            if reason:
                return reason
        return ""
    except GlobDeadline:
        raise
    except pretool_gate.Unplaceable as exc:
        return WT_BROKEN_MSG.format(who=who, why=f"it cannot place {exc}")
    except Exception:
        return WT_BROKEN_MSG.format(who=who, why="the check itself failed")


def worktree_confinement(who: str, tool: str, ti: dict, cwd: str, payload: dict | None) -> int:
    """worktree_denial as a verdict: deny(reason), else allow(). The one deny site of the
    confinement, called by the dev and docs handlers."""
    reason = worktree_denial(who, tool, ti, cwd, payload)
    return deny(reason) if reason else allow()


# --- HEAD-moving git in the trunk checkout (epic-0004) ---------------------------------
# The trunk freeze reads "HEAD is on main". A subagent that moves HEAD off main (checkout -b,
# switch -c, --detach, reset, rebase onto another branch, branch -M, symbolic-ref) would lift
# the freeze for the next write, so with worktrees.enabled those calls are refused in a
# primary checkout that no feature branch holds. A task's own linked worktree is not one.
# The orchestrator is not bound by this prelude: it cuts branches in the trunk checkout.
HEAD_MOVING_GIT = frozenset({"checkout", "switch", "reset", "rebase", "symbolic-ref", "update-ref"})
HEAD_MOVE_MSG = ("{who}: {hit} moves HEAD of the trunk checkout, which would lift the freeze that "
                 "keeps the trunk free of edits while worktrees.enabled. Work in your task's "
                 "worktree (the Task worktree line of your dispatch names it); git checkout, "
                 "switch, reset and branch moves there are yours. Report what you need from the "
                 "trunk checkout to the orchestrator.")


def branch_moves(args: list) -> bool:
    """`git branch` that renames or force-moves a branch: -f, -m, -M, --force, --move (or a
    cluster holding one of them)."""
    for tok in args:
        if tok == "--":
            break
        if tok.startswith("--"):
            if any(is_long_option_prefix(tok.partition("=")[0], o) for o in ("--force", "--move")):
                return True
        elif tok.startswith("-") and any(ch in tok[1:] for ch in "fmM"):
            return True
    return False


def trunk_head_move(command: str, cwd: str) -> str:
    """The first HEAD-moving git call of `command` that runs in a trunk checkout, as
    `git <sub>`, else ''. A call whose place the text does not give counts as a hit."""
    try:
        calls = pretool_gate.placed_git_calls(command, cwd, HEAD_MOVING_GIT | {"branch"})
        for sub, args, _, place in calls or []:
            if sub == "branch" and not branch_moves(args):
                continue
            if pretool_gate.primary_trunk_checkout(place):
                return f"git {sub}"
    except pretool_gate.Unplaceable as exc:
        return f"a git call placed by {exc}"
    except Exception:
        return "a git call the check failed on"
    return ""


DEV_WHO = "Dev agent"
# What a dev agent is told on top of the shared orchestrator-only sentence; the readonly
# and docs profiles get the sentence alone.
PRELUDE_TAILS = {DEV_WHO: {
    "approve": " Recording User approval from a dev agent is self-approval - report readiness "
               "to the orchestrator instead.",
    "waive": " Report the handoff problem to the orchestrator instead.",
}}


def bash_prelude(who: str, command: str, cwd: str) -> int:
    """The Bash refusals every profile shares, in order: approve.py, handoff --waive, a state
    setter, a protected-state write, a memory target. The one place a new boundary check of
    this kind is added for dev, readonly and docs alike. `who` is the label the agent is
    addressed by."""
    tails = PRELUDE_TAILS.get(who, {})
    if runs_approve_script(command):
        return deny("approve.py is orchestrator-only." + tails.get("approve", ""))
    if runs_waive(command):
        return deny("handoff.py --waive is orchestrator-only - it records a User waiver."
                    + tails.get("waive", ""))
    setter = sets_pipeline_state(command)
    if setter:
        return deny(STATE_SETTER_MSG.format(script=setter))
    written = writes_protected_state(command, cwd=cwd)
    if written:
        return deny(PROTECTED_STATE_MSG.format(who=who, path=written))
    layout = worktree_layout_change(command)
    if layout and worktree_flag("worktrees_active"):
        return deny(WORKTREE_LAYOUT_MSG.format(who=who, hit=layout))
    moved = trunk_head_move(command, cwd) if worktree_flag("worktrees_on") else ""
    if moved:
        return deny(HEAD_MOVE_MSG.format(who=who, hit=moved))
    return memory_target_deny(who, command, cwd)


def handle_dev(tool: str, ti: dict, cwd: str = "", allow_scripts: tuple = (),
               payload: dict | None = None) -> int:
    if tool == "Bash":
        command = str(ti.get("command", ""))
        if unparseable(command):
            return deny(unparseable_msg(DEV_WHO, command))
        prelude = bash_prelude(DEV_WHO, command, cwd)
        if prelude != allow():
            return prelude
        target = git_target_deny(DEV_WHO, command, cwd)
        if target != allow():
            return target
        zoned = zone_denial(tool, ti, cwd, payload)
        if zoned:
            return deny(zoned)
        confined = worktree_confinement(DEV_WHO, tool, ti, cwd, payload)
        if confined != allow():
            return confined
        if is_force_push(command):
            return deny("Force push is forbidden for all agents (rules/git-workflow.md).")
        ref_write = dev_ref_write(command)
        if ref_write:
            return deny(DEV_REF_MSG.format(hit=ref_write))
        hit = unnarrowed_test_cmd(command)
        if hit:
            return deny(f"Dev agents do not run the FULL test suite ('{hit}'). Run only the "
                        f"cases you touched, narrowed with one of "
                        f"{', '.join(test_filter_flags())}, then report - the orchestrator "
                        f"runs the whole suite once after you finish. "
                        f"(see .claude testing rules).")
        return pretool_gate.handle_bash(command, cwd)
    if tool in pretool_gate.EDIT_TOOLS:
        path, content = pretool_gate.edit_target(tool, ti)
        # handle_edit allows anything under .agentry/ as bookkeeping, which would
        # let a dev agent write the approvals level or the mode directly.
        if is_protected(path, cwd):
            return deny(PROTECTED_STATE_MSG.format(who=DEV_WHO, path=path))
        zoned = zone_denial(tool, ti, cwd, payload)
        if zoned:
            return deny(zoned)
        confined = worktree_confinement(DEV_WHO, tool, ti, cwd, payload)
        if confined != allow():
            return confined
        return pretool_gate.handle_edit(path, content, cwd=cwd)
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


def unparseable(command: str) -> bool:
    return pretool_gate.gate_tokens(command) is None


def program_hit(profile: str, command: str, cwd: str, allow_scripts: tuple) -> str:
    """What program_denied() refuses in `command` ('' when nothing). An error inside it is a
    hit: this is the one check whose failure must never read as an allow."""
    try:
        return program_denied(command, profile, allow_scripts, cwd)
    except Exception:
        return "(the allowlist check failed on this command)"


def check_program(who: str, profile: str, command: str, cwd: str, allow_scripts: tuple) -> int:
    hit = program_hit(profile, command, cwd, allow_scripts)
    return deny(PROGRAM_MSG.format(who=who, what=hit)) if hit else allow()


# readonly and docs share one Bash chain; only the label and the advice differ.
READONLY_ADVICE = ("Use Read/Grep/Glob or read-only git commands; report changes you would make "
                   "to the orchestrator.")
DOCS_ADVICE = "Use Write/Edit for documents under .claude/, .agentry/ or docs/."
RESTRICTED_BASH = {"readonly": ("Read-only agent", READONLY_ADVICE), "docs": ("Docs agent", DOCS_ADVICE)}


def lexical_bash_checks(profile: str, command: str, cwd: str) -> int:
    """The spelling-based refusals for a readonly or docs Bash call, in order. Each is a
    second wall behind the allowlist; see restricted_bash()."""
    who, advice = RESTRICTED_BASH[profile]
    prelude = bash_prelude(who, command, cwd)
    if prelude != allow():
        return prelude
    piped = pretool_gate.check_piped_test_suite(command)
    if piped != allow():
        return piped
    frag = bash_mutates(command)
    if frag:
        return deny(f"{who}: mutating Bash denied (matched: '{frag}'). {advice}"
                    f"{pretool_gate.bare_nul_note(frag)}")
    return pretool_gate.check_nul_redirect(command)


def restricted_bash(profile: str, command: str, cwd: str, allow_scripts: tuple) -> int:
    """A readonly or docs Bash call. The allowlist decides FIRST (SEC-A H2 of task-0120): it is
    the one check that does not depend on recognising a spelling, so a lexical check that
    raises (evaluate() turns that into a refusal for these profiles) is never the only thing
    between an unlisted program and the shell. When it refuses, the lexical checks still run
    for one reason only: to name the boundary the command also crosses (approve.py, a state
    setter, a protected file), which tells the agent more than 'not on the allowlist'. The
    refusal does not depend on them, and an error in them changes nothing."""
    who = RESTRICTED_BASH[profile][0]
    if unparseable(command):
        return deny(unparseable_msg(who, command))
    hit = program_hit(profile, command, cwd, allow_scripts)
    if not hit:
        return lexical_bash_checks(profile, command, cwd)
    try:
        code = lexical_bash_checks(profile, command, cwd)
    except Exception:
        code = allow()
    return code if code != allow() else deny(PROGRAM_MSG.format(who=who, what=hit))


def handle_readonly(tool: str, ti: dict, cwd: str = "", allow_scripts: tuple = ()) -> int:
    if tool in pretool_gate.EDIT_TOOLS:
        return deny("This agent is read-only - it audits and reports, it does not modify files. "
                    "Return findings to the orchestrator.")
    if tool == "Bash":
        return restricted_bash("readonly", str(ti.get("command", "")), cwd, allow_scripts)
    return allow()


def handle_docs(tool: str, ti: dict, cwd: str = "", allow_scripts: tuple = (),
                payload: dict | None = None) -> int:
    if tool in pretool_gate.EDIT_TOOLS:
        path, _ = pretool_gate.edit_target(tool, ti)
        # First, so the agent hears which tree this is (pretool_gate.is_live_harness covers
        # the same trees now, and its answer would be the generic protected-file text), and
        # so the rule holds on its own if that predicate narrows again.
        if is_live_instructions(path, cwd):
            return deny(LIVE_INSTRUCTIONS_MSG.format(path=path))
        if is_protected(path, cwd):
            return deny(PROTECTED_STATE_MSG.format(who="Docs agent", path=path))
        if not is_docs_path(path):
            return deny(f"Docs agent: writes allowed only under .claude/, .agentry/, docs/, "
                        f"README files or the root CLAUDE.md - '{path}' is outside that "
                        f"scope. Code changes belong to dev agents via the pipeline.")
        # handle_edit never runs for this profile, so the confinement is its only tree check
        return worktree_confinement("Docs agent", tool, ti, cwd, payload)
    if tool == "Bash":
        return restricted_bash("docs", str(ti.get("command", "")), cwd, allow_scripts)
    return allow()


FAIL_CLOSED_PROFILES = ("readonly", "docs")
PROFILE_LABELS = {"readonly": "Read-only agent", "docs": "Docs agent", "dev": DEV_WHO}
FAIL_CLOSED_MSG = ("{who}: the gate failed while checking this {tool} call, so it refuses it rather "
                   "than guess. Retry with a simpler command; if it keeps failing, report the "
                   "command to the orchestrator.")

GLOB_DEADLINE_MSG = ("The gate could not expand a wildcard in this command within its time limit, "
                     "so it refuses the call rather than guess what the wildcard names. Use a "
                     "narrower pattern or an explicit path.")

HANDLERS = {
    "dev": handle_dev,
    "readonly": handle_readonly,
    "docs": handle_docs,
}


# The hook line carries `timeout: 20`, and a hook that times out is an ALLOW. So the gate
# answers for itself first: at WATCHDOG_SECONDS a timer thread writes the deny and ends the
# process, which works while the main thread sits in a syscall (a glob walk, a stat on a
# dead share, a git call). Every profile, because the timeout reads as an allow for all.
WATCHDOG_SECONDS = 14.0
WATCHDOG_MSG = ("The gate did not finish checking this call in time, so it refuses it rather than "
                "let the hook timeout read as an allow. Retry with a simpler command; if it keeps "
                "happening, report the command to the orchestrator.")


def watchdog_fire() -> None:
    pretool_gate.watchdog_deny_and_exit(WATCHDOG_MSG)


def main() -> int:
    del pretool_gate.PENDING_HUMAN[:]
    watchdog = pretool_gate.start_watchdog_timer(watchdog_fire, WATCHDOG_MSG, WATCHDOG_SECONDS)
    try:
        code = evaluate()
    finally:
        watchdog.cancel()
    if code == 2:
        pretool_gate.emit_human()  # a dev agent's commit/push deny is pretool_gate's
    return code


def evaluate() -> int:
    parser = argparse.ArgumentParser(description="Per-agent PreToolUse gate")
    parser.add_argument("--profile", choices=sorted(HANDLERS), required=True)
    parser.add_argument("--allow-script", action="append", default=[], metavar="NAME",
                        help="extra program or interpreter script this agent may run "
                             "(readonly and docs profiles; repeatable)")
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
    tool = ""
    try:
        tool = payload.get("tool_name", "")
        ti = payload.get("tool_input", {}) or {}
        if tool in WEB_TOOLS:
            return handle_web(args.profile, tool, ti)
        extra = {"payload": payload} if args.profile in ("dev", "docs") else {}
        return HANDLERS[args.profile](tool, ti, str(payload.get("cwd", "")),
                                      tuple(args.allow_script), **extra)
    except GlobDeadline:
        # A glob that ran out of time is not a crash: the walk is the attacker's, and its
        # result is unknown, so every profile refuses (dev included - this is no bug of ours).
        return deny(GLOB_DEADLINE_MSG)
    except Exception:
        # Fail-open is right for the dev profile: its job is running code, a bug here must
        # not brick the agent that builds the harness, and its writes have the approval
        # gates behind them. readonly and docs are the profiles whose whole value is
        # that they cannot write, and an exception is exactly what a crafted command
        # provokes, so for them a crash in any check refuses the tools that write (SEC-A
        # H2 of task-0120). Reads and web tools stay allowed.
        if args.profile in FAIL_CLOSED_PROFILES and (tool == "Bash" or tool in pretool_gate.EDIT_TOOLS):
            return deny(FAIL_CLOSED_MSG.format(who=PROFILE_LABELS[args.profile], tool=tool))
        return allow()


if __name__ == "__main__":
    raise SystemExit(main())
