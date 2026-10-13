#!/usr/bin/env python3
"""PreToolUse brain - deterministic backstop for the human checkpoints.

Wired to Claude Code's `PreToolUse` event (matchers: Bash, Edit|Write|MultiEdit|NotebookEdit). It denies
(exit code 2, reason on stderr; a deny that waits on the CEO also prints a
{"systemMessage": ...} document on stdout for him - see HUMAN_NOTICES) when:
  - `git commit` runs while the current task's commit is not yet approved;
  - `git commit` / `git push` runs on a task branch whose run row cannot be
    read, or is absent from the lane's run store - the approval cannot be
    verified, so these two checks fail CLOSED (see handle_bash);
  - `git push` runs while push is not approved, or targets ANY protected branch
    (main and master always, plus the configured trunk and pipeline.json
    "protected_branches" - default staging / production). Two things lift that
    for the trunk alone, never for an extra protected branch and never for a
    force or delete push: the one-shot `approve.py --trunk-push` marker (solo
    mode) and pipeline.json `allow_push_to_main` = true (default false);
  - `git merge` or `git pull` runs while HEAD sits on a protected branch, unless
    the project is in `solo` workflow mode AND the source is an approved task
    branch - see check_trunk_merge();
  - `git merge`, `git pull`, `git push` or an approve.py invocation runs in a session
    the supervisor spawned unattended (env AGENTRY_UNATTENDED) - see
    check_unattended(), which holds at every approvals level and in both
    workflow modes;
  - a code file is edited while the active task sits in the read-only `review`
    stage (bookkeeping under .claude/, .agentry/ and docs/ is always allowed);
  - a code file is edited while handoff debt exists and no task is mid-stage
    (config flag handoff.hard_edit_gate - see tools/pipeline/handoff.py);
  - the ORCHESTRATOR (main thread) writes outside its bookkeeping allowlist
    (.claude/, .agentry/, docs/, README*, root CLAUDE.md) - config block
    pipeline.json "orchestrator_gate". The orchestrator formalizes, delegates,
    verifies and synthesizes; it never authors code. Subagents are exempt
    (their own frontmatter hooks run agent_gate.py profiles instead): when the
    hook payload carries an agent identity, the orchestrator checks are skipped.

Everything else is allowed (exit 0). Fail-open: any error allows the tool, so a
bug here can never brick the agent.

STATED LIMITATIONS of the command analysis below (task-0078). All of them are
textual: the gate reads the command it is given and nothing else.
  - A target reached through a VARIABLE is not resolved: `X=/dev/null; ls > $X`
    redirects to the forbidden target and reads here as a redirect to `$X`.
    Shell variables are not expanded, and expanding them would mean executing
    the command to find out. `eval` with a redirect operator in its operand is
    therefore refused outright rather than guessed at (see eval_redirect).
  - A body built at runtime (`bash -c "$CMD"`), decoded (`base64 -d | sh`), or
    held in a script file invoked by path is invisible. So is nesting deeper
    than MAX_SHELL_DEPTH.
  - A gate that RAISES is an allow: every main() here catches Exception and
    returns allow(), by design (fail-open, so a bug cannot brick the agent), so
    any parser added to this file must be TOTAL over arbitrary text - one
    IndexError on a crafted string silently disables every check. The fuzz test
    over quote / newline / heredoc combinations is the guard on that.
  - A command that takes more than SCAN_BUDGET characters of scanning work is
    refused ("too complex to check", see too_complex): a hook that outlasts its
    timeout is an allow, and a 300-character run of `"$(` used to take minutes.
    Every gate treats it like text it cannot tokenise.
  - These are the profile gates' floor, not a proof of absence. A pass means
    "no forbidden thing is written in this text", never "this command cannot do
    the forbidden thing".
"""

from __future__ import annotations

import functools
import json
import os
import posixpath
import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import state

TASK_PREFIX = "task-"

# Branch-naming gate: a newly created work branch must follow
# <work-type>/...task-<id>... per .claude/rules/git-workflow.md, so agents stop
# cutting branches without a task number.
BRANCH_TYPES = ("feature", "bugfix", "hotfix", "enhancement", "techdebt",
                "fix", "refactor", "chore")
PROTECTED_BRANCHES = ("main", "staging", "production", "master", "develop")

# Push protection is a SET, not the single `main_branch` name. Two names are
# protected unconditionally: `main` and `master` are never a work branch in any
# project, so there is no legitimate push to weigh against - and a template that
# only reads `main_branch` ships with ZERO protection for a `master` project
# until someone remembers to set the key, failing silently. The configured trunk
# and the optional pipeline.json "protected_branches" list join them, so extra
# environments need config, not code.
PUSH_PROTECTED_ALWAYS = ("main", "master")
PUSH_PROTECTED_DEFAULT = ("staging", "production")

# `git push` options that consume the NEXT argv entry (the `--opt=value`
# spelling is one token and falls through the generic flag skip).
PUSH_OPTS_WITH_ARG = frozenset({"--repo", "-o", "--push-option",
                                "--receive-pack", "--exec"})

# `git push` options that rewrite or remove what is already on the remote. The
# trunk-push approval publishes the trunk; it never authorizes overwriting or
# deleting it, so these gate even when the marker is present.
PUSH_DESTRUCTIVE_LONG = frozenset({"--force", "--force-with-lease", "--mirror",
                                   "--delete", "--prune"})
PUSH_DESTRUCTIVE_SHORT = frozenset("fd")  # -f, -d, and bundles like -fu

# `git push` options that push every local branch, trunk and task branches alike.
PUSH_FANOUT_LONG = frozenset({"--all", "--branches", "--mirror"})

# git accepts any unambiguous prefix of a long option (measured on 2.51: `--al`,
# `--mir`, `--bra`, `--del`, `--force-with`, `--pru` all act as the full option), so
# a token that merely abbreviates one of the sets above is that option.
def push_option_in(tok: str, names) -> str:
    """The member of `names` that the long option `tok` spells or abbreviates, or ''.
    An ambiguous prefix (`--a`, `--fo`) is refused by git itself; matching the first
    member is fail-closed for it."""
    name = tok.split("=", 1)[0]
    if len(name) < 3 or not name.startswith("--"):
        return ""
    return next((full for full in sorted(names) if full.startswith(name)), "")


# A refspec side naming a branch: `main`, `heads/main` and `refs/heads/main` are one.
HEADS_PREFIX_RE = re.compile(r"^(?:refs/)?heads/")

# --- Generic gates (no project/stack scope - safe in the universal template) ---
# D: AI-authorship trailers forbidden in commit messages.
AI_ATTRIB_RE = re.compile(r"co-authored-by|generated by|generated with|ai-assisted|\U0001F916", re.IGNORECASE)
# F: em dash / en dash are forbidden anywhere. Referenced by escape so this
# source file itself stays free of the literal characters it bans.
EM_EN_DASH = (chr(0x2014), chr(0x2013))  # em dash, en dash

# --- Stack/project-specific gates are DATA, read from pipeline.json "gates" ---
# so the code stays language-agnostic and the template ships with empty defaults.
# Keys (all optional):
#   forbid_dev_null: bool          - block Unix /dev/null redirects (Windows)   [B]
#   destructive_command_patterns: [regex,...] - deny these commands              [A]
#   destructive_allow_if: [substr,...]        - ... unless one of these appears
#   repl_write_keyword: str        - REPL command name (e.g. a live shell)       [G]
#   repl_write_patterns: [regex,...] - write ops denied inside that REPL
#   forbid_piped_test_suite: bool  - block unittest discover / pytest piped      [H]
#                                     into head/tail/grep/wc/more/less
def gates_cfg() -> dict:
    cfg = state.load_pipeline().get("gates", {})
    return cfg if isinstance(cfg, dict) else {}


# --- Shared redirect detection (used here and by agent_gate.py) ---
# A file-writing redirect (mutation) vs a read-only-safe descriptor dup / discard.
# `> file` and `2>> log` write files; `2>&1`, `>&2` only dup descriptors;
# `/dev/null` discards. Only the first kind is a mutation. The lookbehind
# `(?<![<>])` skips only the second `>` of `>>` / `<>`. It does NOT skip a word
# character: `echo hi>src/app.py`, `cat a.txt>f` and `ls 2>&1>f` are real
# redirects, and refusing a glued word (task-0095's lookbehind did) let readonly
# and docs agents overwrite any file (task-0102). Nor does it skip a `-`
# (task-0108): an unquoted `echo x -> f`, `echo x-->f` and `git log
# --format=%h->%s` are redirects to `f` / `f` / `%s` in the shell, and the old
# arrow guard let readonly and docs agents write through them. Arrows in text
# are quoted, in a comment or in a heredoc body, which the callers mask or strip.
# `(?<!\d)` keeps the descriptor digits from starting inside a digit run: `\d*`
# there retried from every digit and went quadratic (task-0108).
#
# Operators (task-0095): `>`, `>>`, `>|` (clobber) and `<>` (read-write open)
# always name a file. `>&word` is a file too (both streams, bash manual) unless
# the word is a descriptor - `2`, `2-`, `-` - which DUP_TARGET_RE recognises.
#
# A bare `NUL` is NOT a discard target and used to be listed as one (task-0074).
# Measured under git-bash: bash has no device of that name, so the redirect
# creates a regular file called NUL, which Windows then cannot unlink by name.
#
# The two-character operators come BEFORE `>>?` in the alternation: REDIR_OP_RE
# has nothing after the operator to force a backtrack, so `>` would win on `>&x`
# and leave `&x` as the "target".
_REDIR_OP = r"(?:(?<!\d)\d+)?(?:>&|>\||<>|>>?)"
REDIR_RE = re.compile(rf"(?<![<>])(?P<op>{_REDIR_OP})\s*(?P<t>[^\s;|&<>]+)")
DUP_TARGET_RE = re.compile(r"\d+-?|-")
REDIR_OP_RE = re.compile(rf"^{_REDIR_OP}\s*")
DISCARD_TARGETS = ("/dev/null",)
BARE_NUL_NOTE = (
    "`NUL` is a real file on this host: bash has no device of that name, so the "
    "redirect creates an entry named NUL that Windows cannot unlink by name. Drop "
    "the redirect and let the output through, or capture it with `out=$(cmd 2>&1)`.")


def redirect_target(frag: str) -> str:
    """The target of a fragment redirect_write_target() reported: the text after
    its operator (`2>x`, `>&x`, `>|x` and `<>x` all give `x`)."""
    return REDIR_OP_RE.sub("", frag, count=1).strip()


def bare_nul_note(frag: str) -> str:
    """The explanation to append to a deny whose fragment writes a bare NUL,
    else ''. Exact name, any case, quotes and a closing `)` or backtick from a
    command substitution ignored. `nullable.py` gets no note, and neither does a
    path-qualified `./NUL`: readonly and docs deny it like any other path and
    test_nul_entry pins that no note is added. check_bare_nul() DOES deny a
    path-qualified NUL, with the note, because the dev profile has no other
    reason to."""
    target = redirect_target(frag).strip("'\"`)")
    return " " + BARE_NUL_NOTE if target.lower() == "nul" else ""

# A nested sh-family shell: the `-c` argument is a whole command in its own
# right, and quoting it is what hides it from every whole-string scan here.
SHELL_TOKEN_RE = re.compile(r"^(?:.*[\\/])?(?:ba|z|k|da)?sh(?:\.exe)?$", re.IGNORECASE)
MAX_SHELL_DEPTH = 3

# READ THIS BEFORE TOUCHING THE REDIRECT SCAN.
#
# Four consecutive "precision fixes" to this gate each opened a new hole while
# closing the old one: shlex tokenisation went blind to glued separators
# (`git status&&git add -A`), the narrow regex that replaced it refused ordinary
# reads, and quote masking then hid the body of a nested shell
# (`bash -c "echo x > src/app.py"` scanned as the empty string).
#
# So: every change here must be diffed against adversarial input on BOTH sides -
# what it starts refusing, AND what it stops refusing. A passing suite proves
# neither, because the suite only holds the cases somebody already thought of.
# Run the new and the old version over the same list and compare the answers.


def _mask_span(text: str) -> str:
    """Blank ONE quoted span character for character, keeping its newlines.

    Equal length is not enough. split_heredocs() indexes the mask BY LINE, and
    a multi-line quoted argument whose newlines were masked away collapsed into
    one masked line, so `masked[i]` ran off the end and raised IndexError - and
    every main() here catches Exception and allows, so the raise did not fail
    the command, it disabled the whole gate. Measured on the first version of
    this file: `git commit -m 'msg<newline><<' && git push origin main` exited
    0 against a protected branch. Keeping the newlines keeps the line counts
    equal while the offsets stay exact."""
    return "".join("\n" if c == "\n" else "Q" for c in text)


# How many `"$( ... "$( ... )" )"` levels _dq_end / _subst_end follow. Real
# commands nest one or two; the cap keeps adversarial input from recursing.
MAX_QUOTE_NEST = 16

# --- The scan budget (task-0112) ---------------------------------------------
# Measured: `redirect_write_fragments('"$(' * 100)` took 27 s, and 82 s on a clean
# checkout. Each primitive is cheap on its own; the cost is the FAN-OUT. The
# recursing gates (redirect_write_fragments, runs_approve_script, git_invokes, ...)
# visit every nested body, a body's own substitutions are flattened into the list
# again, and the list holds up to 64 entries, so a 300-character command ran ~64^3
# scans. A hook that outlasts its timeout is an allow, so this is a bypass, not a
# slowdown.
#
# too_complex() is the guard. It walks the same tree the gates walk, with every
# primitive charging the characters it scans, and answers True when the walk spends
# SCAN_BUDGET. Callers treat True like an unparseable command: refuse. Outside that
# walk _SCAN is None and nothing is charged, so no other behaviour changes and no
# state leaks between commands. gate_tokens() asks first (every agent profile calls
# it before anything else), handle_bash() asks with its own message, and
# redirect_write_fragments() and unscannable_depth() ask for a direct caller.
SCAN_BUDGET = 250_000
SCAN_PER_CHAR = 8  # work a longer command is owed on top: ordinary scans are linear
_SCAN: list | None = None


class ScanBudgetExceeded(Exception):
    """Raised inside too_complex() only, and caught there. It never leaves it: a gate
    that raises is an allow (see the module docstring)."""


def _charge(chars: int) -> None:
    """Spend `chars` of the scan budget; a no-op outside too_complex()."""
    if _SCAN is not None:
        _SCAN[0] -= chars
        if _SCAN[0] < 0:
            raise ScanBudgetExceeded


def _dq_end(s: str, i: int, nest: bool = True, _depth: int = 0) -> int:
    """Index just past the double-quoted span that opens at s[i], or -1 when it
    never closes. A backslash makes the next character literal, so an escaped
    quote does not end the span. With `nest`, a `$( ... )` inside it is a command
    of its own whose quotes pair up among themselves (`"$(echo "a>b")"` is ONE
    span, not three); without it the span ends at the next unescaped quote."""
    j, n = i + 1, len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
        elif c == '"':
            return j + 1
        elif nest and c == "$" and s.startswith("$(", j):
            j = _subst_end(s, j + 2, _depth + 1)
            if j < 0:
                return -1
        else:
            j += 1
    return -1


def _subst_end(s: str, j: int, _depth: int = 0) -> int:
    """Index just past the `)` that closes a `$(` whose body starts at s[j], or
    -1. Quotes inside the body are skipped as spans, so a `)` in one does not
    end it."""
    if _depth > MAX_QUOTE_NEST:
        return -1
    depth, n = 1, len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            j = s.find("'", j + 1) + 1
            if not j:
                return -1
            continue
        if c == '"':
            j = _dq_end(s, j, True, _depth)
            if j < 0:
                return -1
            continue
        depth += (c == "(") - (c == ")")
        j += 1
        if not depth:
            return j
    return -1


# A `#` starts a comment only as the first character of a word: after a blank, a
# command separator or an opening parenthesis. `a#b`, `$#` and `${x#p}` are not.
COMMENT_LEADERS = " \t\n;&|("


def mask_quoted(command: str) -> str:
    """Blank quoted spans and comments so a `>` inside a quoted SQL comparison, a
    commit message or a `# note` is not read as a redirect.

    Observed, not theoretical: a tool call carrying `>` inside a Python format
    string was denied as shell file-authoring. Length is preserved, so match
    offsets still index the ORIGINAL command - which is what lets a quoted
    TARGET (`> "out.txt"`) stay detectable while a quoted OPERATOR (`n >= 2`)
    stops matching.

    The scan follows the shell, not a regex (task-0108): a backslash, outside
    quotes and inside double quotes, makes the next character literal, so an
    escaped quote opens and closes nothing; a `$( ... )` inside double quotes
    carries its own quotes; a comment runs to the end of its line. A `#` inside
    `${ ... }` is never a comment (`${x/ #/y}`), because masking from there
    would hide the rest of the line. A span that never closes is left alone -
    unmasked text is the fail-closed direction. A double-quoted span whose
    `$( ... )` pairing fails ends at its next unescaped quote instead, which is
    where the old regex ended every one.

    NOT masked, on purpose: `(( n>0 ))`, `[[ a>b ]]`, `$((a>b))` and
    `${x:-a>b}`. The shell reads no redirect in them, but `[[` and `((` are
    keywords only in command position (`echo [[ > f` is a real write) and `$((`
    is ambiguous with `$( (`, so masking them needs a command parser. Each is
    still read as a redirect; test_mask_quoted_escapes pins that as the known
    false positive.

    Masking alone is NOT enough: a quoted span can be a whole nested command
    (`bash -c "echo x > f"`), and blanking it makes the redirect invisible. That
    is what shell_c_bodies() + the recursion in redirect_write_target() cover.

    NEWLINES inside the span survive the mask (see _mask_span). Do not
    "simplify" that back to plain filler."""
    out, last, i, n, braces = [], 0, 0, len(command), 0
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2
            continue
        if c == "$" and command.startswith("${", i):
            braces += 1
            i += 2
            continue
        if c == "}" and braces:
            braces -= 1
        end = -1
        if c == "'":
            end = command.find("'", i + 1) + 1 or -1
        elif c == '"':
            end = _dq_end(command, i)
            if end < 0:
                # A pairing attempt that fails has read to the end of the text,
                # level after level: the cost the budget has to see.
                _charge(4 * (n - i))
                end = _dq_end(command, i, False)
            else:
                _charge(end - i)
        elif c == "#" and not braces and (i == 0 or command[i - 1] in COMMENT_LEADERS):
            end = command.find("\n", i)
            end = n if end < 0 else end
        if end < 0:
            i += 1
            continue
        out.append(command[last:i])
        out.append(_mask_span(command[i:end]))
        last = i = end
    out.append(command[last:])
    return "".join(out)


# --- One tokenisation for every gate here (task-0078) -----------------------
# The defect these close: every gate matched its forbidden literal as a
# SUBSTRING of the raw command text, so text that merely DESCRIBES a forbidden
# thing was indistinguishable from doing it. Four measured denials, all on
# truthful text: a commit message quoting the redirect literal, a memory row
# whose prose field named a git verb, a `git show -- <path to approve.py>`, and
# a task file written through a heredoc whose markdown described a push.
#
# Quoting is already handled by shlex: `--fix "never run git commit"` collapses
# to ONE token, which matches no command name. What shlex has no notion of is a
# heredoc, so its body was tokenised as argv - that is what strip_heredocs()
# removes before any tokenisation happens.
#
# Fail-closed is preserved throughout: every helper below returns None (not an
# empty result) when the text cannot be tokenised, and every caller must treat
# None as "refuse", exactly as git_invocations() already treats GIT_UNKNOWN.

# `<<WORD`, `<<'WORD'`, `<<-WORD`. The lookbehind keeps a herestring (`<<<x`)
# out: its trailing `<<x` would otherwise read as a heredoc opener.
HEREDOC_RE = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# Same set as SHELL_SEPARATORS below, spelled out because that constant is
# defined further down the file; a test pins the two together.
SEPARATOR_TOKENS = frozenset({"&&", "||", "|&", ";", "|", "&"})
# Both feed the next command's stdin; `|&` sends stderr as well.
PIPE_SEPS = frozenset({"|", "|&"})
# Words that open or continue a compound command and run nothing themselves: the
# command that runs is the word behind them (`then python x`, `{ python x; }`).
SHELL_RESERVED = frozenset({"!", "{", "(", "((", "if", "then", "elif", "else",
                            "while", "until", "do"})
# Words that only CLOSE a group. Alone in a segment (`{ cat x; } | python -` leaves
# a `}` between the `;` and the `|`) they are no command, and must not stand as a
# segment of their own: that segment would break the pipe chain.
SHELL_CLOSERS = frozenset({"}", "fi", "done", "esac"})
# Words that OPEN a group a closer above ends; `for`, `select` and `case` open one
# too, but only as the first word of a command (their `in` list is not a command).
GROUP_OPENERS = frozenset({"{", "(", "((", "if", "while", "until"})
GROUP_LOOPS = frozenset({"for", "select", "case"})
# What pad_separators emits for the `)` that ends a group. A control character in
# front keeps it distinct from a quoted `")"` after shlex has stripped the quotes.
GROUP_CLOSE_MARK = "\x1f"
GROUP_CLOSE = GROUP_CLOSE_MARK + ")"
# The two other `)` it pads, kept apart from a group's closer because only that one
# ends a group (group_marks): the `)` that ends a `$( ... )` or backtick substitution
# holding a separator, and the one that ends a case pattern (`a) cmd ;;`). Counting
# either as a group closer popped an enclosing group early (task-0112).
SUBST_CLOSE = GROUP_CLOSE_MARK + "s"
PATTERN_CLOSE = GROUP_CLOSE_MARK + "p"
CLOSE_MARKERS = frozenset({GROUP_CLOSE, SUBST_CLOSE, PATTERN_CLOSE})
# Not a token of its own: pad_separators puts it IN FRONT of a substitution closer
# that stays glued to a path (`$(echo x; git push origin main)/foo`), so a push
# can tell that closer from a `)` the user quoted into a ref name (`'x)y:main'`),
# which shlex turns into the very same character. Only an unquoted closer is ever
# marked; the control character itself is rewritten to `?` before padding, so no
# command text can forge it.
GLUED_CLOSE = GROUP_CLOSE_MARK + "g"
# argv0 forms that run their first non-flag argument as a script, so the script
# name is an invocation rather than a path argument. Shells are absent on
# purpose: their `-c` body is handled by shell_c_bodies() instead.
INTERPRETER_RE = re.compile(
    r"^(?:.*[\\/])?(?:python[\d.]*|pythonw|py|node|ruby|perl|php)(?:\.exe)?$", re.IGNORECASE)
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# argv0 forms that run ANOTHER command given as their argument, so the real
# argv0 is the word behind them. Without this, `env python .../approve.py` had
# argv0 `env`, which is not an interpreter, and the script argument was never
# read as executed - the gate saw a path.
WRAPPER_RE = re.compile(
    r"^(?:.*[\\/])?(?:env|command|nohup|time|sudo|xargs|exec|timeout|uv|cmd"
    r"|nice|stdbuf|setsid|uvx|pipenv|ionice|taskset|flock|chrt|numactl|doas|unbuffer)(?:\.exe)?$",
    re.IGNORECASE)
# What a wrapper takes ahead of the command it runs that is not a flag: timeout's
# duration (`30`, `1.5`, `30s`; also the `10` of `nice -n 10`), the `run` of uv and
# pipenv, cmd's `/c` (`//c` under git-bash, which rewrites a lone `/c` into a drive
# path).
WRAPPER_OPERAND_RE = re.compile(r"^(?:\d+(?:\.\d+)?[smhd]?|run|/{1,2}[ck])$", re.IGNORECASE)
# flock takes a lock file in front of the command it runs (`flock f python x`), so the
# word behind its options is not argv0.
LOCK_WRAPPER_RE = re.compile(r"^(?:.*[\\/])?flock(?:\.exe)?$", re.IGNORECASE)
# Programs that run a command given as the argument of `-c` / `--command`, the way a
# shell does, with positionals of their own in front (`su root -c cmd`, `flock f -c cmd`).
COMMAND_OPTION_RE = re.compile(r"^(?:.*[\\/])?(?:flock|su|runuser)(?:\.exe)?$", re.IGNORECASE)
CMD_RE =re.compile(r"^(?:.*[\\/])?cmd(?:\.exe)?$", re.IGNORECASE)
PYTHON_RE = re.compile(r"^(?:.*[\\/])?(?:python[\d.]*|pythonw|py)(?:\.exe)?$", re.IGNORECASE)
# A redirect operator opening a token: `>`, `>>`, `2>`, `&>`, `>&`, `<`, `<<<`, `2>&1`.
# A token that is ONLY the operator takes the NEXT token as its target.
REDIR_TOKEN_RE = re.compile(r"^(?:\d*|&)(?:>>?|<{1,3})&?")
REDIR_BARE_RE = re.compile(r"^(?:\d*|&)(?:>>?|<{1,3})&?$")
# `< file`, `<file`, `0< file` - stdin redirection. An interpreter reading a
# script this way RUNS it, exactly as it would as an argument. The negative
# lookahead keeps a heredoc / herestring operator out.
STDIN_REDIR_RE = re.compile(r"^\d*<(?!<)(.*)$")
EVAL_RE = re.compile(r"^(?:.*[\\/])?eval$", re.IGNORECASE)
# Any redirect operator, target unread: what eval_redirect() looks for in an
# eval operand it cannot resolve.
ANY_REDIR_RE = re.compile(r"(?<![-<>=])\d*[<>&]{0,2}[<>](?!<)")


# Text-level twin of SHELL_TOKEN_RE for a command shlex cannot read. Quote
# characters are deleted first, so `'s'h` and `ba""sh` read as the shell they spawn.
SHELL_WORD_RE = re.compile(r"(?<![\w.-])(?:ba|z|k|da)?sh(?:\.exe)?(?![\w.-])", re.IGNORECASE)


def runs_a_shell(command: str) -> bool:
    """True when any token is an sh-family shell - then a heredoc body may BE a
    script (`bash <<EOF ... EOF`) rather than data.

    An unbalanced quote makes shlex fail, and that used to answer True for the
    whole command: `python - <<'PY'` with an apostrophe in a comment of the body
    kept the body unstripped, which then could not be tokenised either, so the
    approval-script check refused it as if approve.py ran (task-0118 QA). The
    fallback now looks for a shell NAME anywhere in the text, body included, so
    the strip is lost only when a shell could really be the consumer."""
    try:
        return any(SHELL_TOKEN_RE.match(t)
                   for t in shlex.split(pad_separators(command), posix=True))
    except ValueError:
        return bool(SHELL_WORD_RE.search(re.sub(r"['\"]", "", command).replace("\\", "/")))


def strip_heredocs(command: str) -> str:
    """`command` with every heredoc BODY - and its `<<WORD` operator - removed.

    A heredoc body is data on stdin, not words the shell runs. shlex tokenises
    it exactly like the command line, so a task file written with
    `cat > file <<EOF` handed the gates its own markdown as argv, and a
    paragraph describing a push was denied as a push.

    Two rules keep the strip from DELETING text that must still be scanned -
    both were bypasses, both measured on the first attempt (task-0078 review):

      1. The opener must sit outside quotes. `echo "see <<EOF in docs"` is a
         sentence, and reading it as an opener made every following line a
         heredoc body: a real `git push origin main` on the next line, an
         `rm -rf`, a redirect - all deleted before any gate saw them. An opener
         counts only when its `<<` OPERATOR sits outside quotes; the delimiter
         itself may be quoted, which is the ordinary `<<'EOF'` spelling, so the
         test is on the operator offset alone (mask_quoted preserves length, so
         the mask can be indexed by an offset into the original).
      2. A MISSING terminator keeps the remaining lines. The shell would indeed
         swallow them, but a gate that deletes text on the strength of an
         unterminated opener can be made to delete anything. Keeping them is
         the fail-closed direction: at worst a document is scanned as argv and
         gets denied, which is visible, rather than a command being hidden,
         which is not.

    Nothing is stripped at all when a shell could be executing the body
    (runs_a_shell)."""
    return split_heredocs(command)[0]


def expanding_heredoc_bodies(command: str) -> list:
    """The bodies of heredocs whose delimiter is UNQUOTED.

    `<<EOF` expands `$(...)` and backticks inside the body; `<<'EOF'` does not.
    The body is data either way - it is never argv - but a substitution in an
    unquoted body really does run, so those bodies are handed to
    command_substitutions() and to nothing else."""
    return [body for quoted, body in split_heredocs(command)[1] if not quoted]


def split_heredocs(command: str) -> tuple:
    """(text with the heredocs removed, [(delimiter-was-quoted, body), ...])."""
    if "<<" not in command or runs_a_shell(command):
        return command, []
    lines = command.split("\n")
    masked = mask_quoted(command).split("\n")
    out, bodies, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        # The mask is line-aligned by construction (_mask_span keeps newlines),
        # so this bound never trims. It stays because when it DID trim, the
        # IndexError was caught by main() as an allow and every gate went off.
        # An unreadable mask line yields no openers, so the line is KEPT for the
        # gates to scan - the fail-closed direction, never a silent drop.
        mline = masked[i] if i < len(masked) else ""
        i += 1
        openers = [m for m in HEREDOC_RE.finditer(line)
                   if m.start() < len(mline) and mline[m.start()] == line[m.start()]]
        if not openers:
            out.append(line)
            continue
        # Find each body's terminator BEFORE dropping anything: one missing
        # terminator disqualifies the whole line (rule 2 above).
        end, terminated, found = i, True, []
        for m in openers:
            word, start = m.group(2), end
            while end < len(lines) and lines[end].strip() != word:
                end += 1
            _charge(end - start + 1)  # an opener with no terminator reads to the end
            if end >= len(lines):
                terminated = False
                break
            found.append((bool(m.group(1)), "\n".join(lines[start:end])))
            end += 1  # drop the terminator line too
        if not terminated:
            out.append(line)
            continue
        kept, pos = [], 0
        for m in openers:
            kept.append(line[pos:m.start()])
            pos = m.end()
        kept.append(line[pos:])
        out.append("".join(kept))
        bodies.extend(found)
        i = end
    return "\n".join(out), bodies


def gate_tokens(command: str) -> list | None:
    """shlex tokens of `command`, heredoc bodies removed and glued shell
    separators padded. None when the text cannot be tokenised - or is too complex
    to scan (too_complex), which every caller must refuse the same way."""
    if too_complex(command):
        return None
    try:
        return shlex.split(pad_separators(strip_heredocs(command)), posix=True)
    except ValueError:
        return None


def ungroup(segment: list) -> list:
    """`segment` without the shell grouping syntax that opens it: `(python x)`
    and `( python x )` open with `(`, `{ python x; }` with `{`, and a compound
    command's keyword (`then`, `do`, `else`, ...) sits in front of the command it
    introduces. None of those runs anything, so the word behind them is argv0."""
    out = list(segment)
    while out:
        head = out[0]
        if head in SHELL_RESERVED or not head.strip("("):
            out.pop(0)
        elif head.startswith("("):
            out[0] = head.lstrip("(")
        else:
            break
    # pad_separators leaves a group's `)` as a marker token of its own; it ends the
    # group and is no argument of the command in front of it (`(git push origin main)`).
    # Only the marker goes: a quoted `")"` is an operand (`git -C ")" push`).
    # The substitution and case-pattern markers end a word and nothing more. Only the
    # markers go: the tokens in front of a pattern's `)` stay, since a stray `)` can
    # be typed anywhere (`git -C x) push origin main`) and must not hide the git.
    out = [tok for tok in out if tok not in CLOSE_MARKERS]
    # A closer is no command, with or without the redirects that follow it
    # (`{ cat x; } 2>&1 | python -`): kept as a segment it would break the pipe chain.
    if out and out[0] in SHELL_CLOSERS and (len(out) == 1 or REDIR_TOKEN_RE.match(out[1])):
        return []
    return out


class SegmentPairs(list):
    """The result of segments_with_separators(): a plain list of (separator,
    tokens), plus `groups` - the (first, last) indices of every group (`{ ...; }`,
    `( ... )`, `if ... fi`, `for ... done`) that holds more than one command. A
    group's closing word is no segment, so without the spans a pipe after it
    could only see the command right behind the closer."""

    def __init__(self):
        super().__init__()
        self.groups = []


def group_marks(raw: list) -> tuple:
    """(openers, closers) the RAW tokens of one segment carry: the group openers
    leading it (`{`, `(`, `if`, `while`, `until`, `for`, `case`) and the closers - a
    group's `)` marker, or a lone `}` / `fi` / `done` / `esac` (see ungroup). The
    substitution and case-pattern markers close nothing."""
    opens, i = 0, 0
    while i < len(raw) and (raw[i] in SHELL_RESERVED or not raw[i].strip("(")):
        opens += raw[i] in GROUP_OPENERS
        i += 1
    opens += i < len(raw) and raw[i] in GROUP_LOOPS
    closes = raw.count(GROUP_CLOSE)
    if raw and raw[0] in SHELL_CLOSERS and (len(raw) == 1 or REDIR_TOKEN_RE.match(raw[1])):
        closes += 1
    return opens, closes


def closer_redirects(raw: list) -> list:
    """The tokens behind a group's closer in `raw`, where its redirects sit:
    `} < f`, `done 2>&1 < f`, or the `< f` after a group's `)` marker."""
    if raw and raw[0] in SHELL_CLOSERS:
        return raw[1:]
    if GROUP_CLOSE in raw:
        return raw[len(raw) - raw[::-1].index(GROUP_CLOSE):]
    return []


def feed_group(pairs: list, first: int, operands: list) -> None:
    """Hand `< operand` to every interpreter of the group that starts at pairs[first].
    A redirect on a group's closer is the stdin of each command inside it, so
    `{ python - auto; } < approve.py` runs the script like `python - auto < approve.py`
    does. Only an interpreter gets it: nothing else turns stdin into code."""
    for k in range(first, len(pairs)):
        sep, seg = pairs[k]
        if runs_interpreter(exec_names_of(seg)):
            pairs[k] = (sep, seg + [tok for op in operands for tok in ("<", op)])


def segments_with_separators(command: str) -> list | None:
    """[(separator-before, tokens), ...] - one entry per simple command. The
    separator of the first entry is ''. None when the text cannot be tokenised.

    The separator is kept because a PIPE changes what the next command runs:
    `cat script.py | python` executes the script on the left. The list also
    carries `.groups` (SegmentPairs) for `{ cat script.py; echo; } | python`."""
    tokens = gate_tokens(command)
    if tokens is None:
        return None
    pairs, starts = SegmentPairs(), []

    def take(raw: list, sep: str) -> None:
        opens, closes = group_marks(raw)
        starts.extend([len(pairs)] * opens)
        cmd = ungroup(raw)
        if cmd:
            pairs.append((sep, cmd))
        stdin = stdin_operands(closer_redirects(raw)) if closes else []
        for _ in range(closes):
            if starts:
                first = starts.pop()
                if first < len(pairs) - 1:
                    pairs.groups.append((first, len(pairs) - 1))
                if stdin:
                    feed_group(pairs, first, stdin)

    current, sep = [], ""
    for tok in tokens:
        if tok in SEPARATOR_TOKENS:
            take(current, sep)
            current, sep = [], tok
        else:
            current.append(tok)
    take(current, sep)
    return pairs


def command_segments(command: str) -> list | None:
    """Tokens of `command` grouped into simple commands, split on the shell
    separators. None when the text cannot be tokenised."""
    pairs = segments_with_separators(command)
    return None if pairs is None else [seg for _, seg in pairs]


def _argv0(segment: list) -> tuple:
    """(index of the word a simple command RUNS, whether a wrapper stood in
    front of it). The index is None when the segment runs nothing."""
    i, wrapped, lock = 0, False, False
    while i < len(segment):
        tok = segment[i]
        after_option = i > 0 and segment[i - 1].startswith("-")
        if REDIR_TOKEN_RE.match(tok):
            i += 2 if REDIR_BARE_RE.match(tok) else 1
        elif ASSIGNMENT_RE.match(tok) or tok.startswith("-"):
            i += 1
        elif WRAPPER_RE.match(tok):
            wrapped = True
            lock = lock or bool(LOCK_WRAPPER_RE.match(tok))
            i += 1
        elif wrapped and WRAPPER_OPERAND_RE.match(tok) and (after_option or not lock):
            i += 1
        elif lock:
            # flock's lock file, or its descriptor number, sits in front of the
            # command it runs: `flock f x/approve.py` runs the script, not `f`
            lock = False
            i += 1
        else:
            return i, wrapped
    return None, wrapped


def argv0_index(segment: list) -> int | None:
    """Index of the word a simple command actually RUNS: past leading
    `VAR=value` assignments, redirects (`2>&1 python x`), flags, and wrapper
    commands that run their own argument (`env`, `sudo`, `nohup`, `xargs`,
    `timeout 30`, `uv run`, `cmd /c`, ...). None when the segment runs
    nothing."""
    return _argv0(segment)[0]


def stdin_operands(segment: list) -> list:
    """Every `< file` operand of one simple command, glued or spaced."""
    out = []
    for n, tok in enumerate(segment):
        m = STDIN_REDIR_RE.match(tok)
        if not m:
            continue
        if m.group(1):
            out.append(m.group(1))
        elif n + 1 < len(segment):
            out.append(segment[n + 1])
    return out


def script_operand(segment: list, i: int) -> tuple | None:
    """(index, word) of the script the interpreter at segment[i] is handed as
    its first operand, or the `-m` module resolved to its file path
    (`pipeline.approve` -> `pipeline/approve.py`), else None.

    Redirects between the interpreter and the script are not operands. For
    python the flags are read the way python reads them, as short-option
    clusters: `-X utf8` and `-W ignore` take the NEXT word as their value, and
    `-m` ends a cluster with the module glued on (`-mapprovals`, `-Bmapprovals`)
    or as the next word (`-m approvals`, `-Bm approvals`). Taking `utf8` for the
    script is what let `python -X utf8 approve.py` through."""
    python = bool(PYTHON_RE.match(segment[i]))
    n = i + 1
    while n < len(segment):
        tok = segment[n]
        if REDIR_TOKEN_RE.match(tok):
            n += 2 if REDIR_BARE_RE.match(tok) else 1
            continue
        if not tok.startswith("-"):
            return n, tok
        n += 1
        if python and not tok.startswith("--"):
            for k, c in enumerate(tok[1:], 1):
                if not c.isalpha():
                    break
                glued = tok[k + 1:]
                if c == "m":
                    module = glued or (segment[n] if n < len(segment) else "")
                    return (n - 1 if glued else n), module.replace(".", "/") + ".py"
                if c in "XW":
                    n += 0 if glued else 1
                    break
                if c == "c":
                    break
        elif python and tok == "--check-hash-based-pycs":
            n += 1  # the one long python option that takes its value as the next word
        elif tok == "-m" and n < len(segment):
            return n, segment[n].replace(".", "/") + ".py"
    return None


def exec_tokens(segment: list) -> list:
    """[(index, word), ...] for the words ONE simple command actually runs: argv0
    (see argv0_index), plus - when argv0 is an interpreter - the script it is
    handed, whether as its first non-flag argument, on stdin (index -1), or as a
    `-m` module. `python .../approve.py`, `python < .../approve.py` and `python -m
    approve` all run that script; `git show -- x.py` does not run x.py.

    The `-m` operand went unread, and that was the whole of a bypass: the flag
    loop skipped `-m` as a flag and `approve` as its value, so
    `cd .claude/tools/pipeline && python3 -m approve --gate commit` named no
    script and passed every profile. A module is resolved to its file path so the
    script regexes, which anchor on a path separator, match it the same way they
    match the argument spelling.

    A wrapper has options of its own that this file cannot enumerate per wrapper
    (`sudo -u root`, `uv run --with pkg`, `timeout --signal KILL`), and the word
    behind such an option is then taken for argv0. So behind a wrapper every
    interpreter further along the segment counts as run too: more names than the
    shell runs is a deny at worst, fewer is a bypass."""
    i, wrapped = _argv0(segment)
    if i is None:
        return []
    found = [(i, segment[i])]
    if INTERPRETER_RE.match(segment[i]):
        found.extend((-1, name) for name in stdin_operands(segment))
        script = script_operand(segment, i)
        if script:
            found.append(script)
    if wrapped:
        for j in range(i + 1, len(segment)):
            if INTERPRETER_RE.match(segment[j]):
                found.extend((j + k if k >= 0 else k, name)
                             for k, name in exec_tokens(segment[j:]))
    return found


def exec_names_of(segment: list) -> list:
    """The words ONE simple command actually runs; see exec_tokens()."""
    return [name for _, name in exec_tokens(segment)]


def runs_interpreter(names: list) -> bool:
    """True when `names` (exec_names_of output) include an interpreter - not only
    as argv0, because a wrapper's option value can push the real one behind it."""
    return any(INTERPRETER_RE.match(name) for name in names)


def pipe_sources(pairs: list, n: int) -> list:
    """Indices of every earlier command in the unbroken pipe chain that feeds
    pairs[n]: `cat x | cat | python -` feeds the interpreter from BOTH cats, and
    `{ cat x; echo; } | python -` from every command of the group. The same chain
    feeds every command INSIDE a group that sits behind the pipe:
    `cat x | { echo; python -; }`."""
    out, groups = [], getattr(pairs, "groups", ())
    # A command inside a group that sits behind a pipe reads that pipe too:
    # `cat x/approve.py | { echo; python - auto; }` feeds the python.
    for first, last in groups:
        if first < n <= last and pairs[first][0] in PIPE_SEPS:
            out.extend(pipe_sources(pairs, first))
    while n and pairs[n][0] in PIPE_SEPS:
        last = n - 1
        first = min((f for f, g in groups if g == last), default=last)
        out.extend(range(first, last + 1))
        n = first
    return out


def executed_names(command: str) -> list | None:
    """Every word `command` actually runs, across all its simple commands.
    None when the text cannot be tokenised (callers gate that).

    A pipe INTO an interpreter runs what the commands before it emitted, so
    their operands count as executed: `cat x/approve.py | python`,
    `echo x/approve.py | xargs python` and `cat x/approve.py | cat | python -`
    all run the script the gate would otherwise have read as a path argument."""
    pairs = segments_with_separators(command)
    if pairs is None:
        return None
    names = []
    for n, (_, seg) in enumerate(pairs):
        seg_names = exec_names_of(seg)
        names.extend(seg_names)
        if runs_interpreter(seg_names):
            for k in pipe_sources(pairs, n):
                names.extend(tok for tok in pairs[k][1][1:] if not tok.startswith("-"))
    return names


# H: piping a test-suite run into a pager/filter deadlocks in Git Bash on
# Windows - a test child can inherit the pipe's write end, so the sink never
# sees EOF and blocks past any timeout, holding a live process behind it
# (task-0082, measured: two orphaned `unittest discover` runs held ~4h each,
# a QA agent stalled the full 600s tool timeout). The two suite entry points
# this project runs end to end, never a single narrowed test file.
PIPED_SUITE_SINKS = frozenset({"head", "tail", "grep", "wc", "more", "less"})


def basename_no_ext(token: str) -> str:
    """`token` reduced to its bare, extension-stripped, lowercased filename -
    `C:/tools/tail.exe` and `tail` compare equal."""
    name = token.lower().replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"\.exe$", "", name)


def is_test_suite_segment(segment: list) -> bool:
    """True when `segment` (one simple command's tokens) invokes the unittest
    discover runner or pytest. Deliberately narrow: `python -m unittest
    <single test>` (no `discover` token) is a narrowed run, not the suite, and
    stays out of scope."""
    low = [t.lower() for t in segment]
    if "unittest" in low and "discover" in low:
        return True
    i = argv0_index(segment)
    if i is None:
        return False
    if basename_no_ext(segment[i]) == "pytest":
        return True
    if INTERPRETER_RE.match(segment[i]) and "-m" in low:
        m = low.index("-m")
        if m + 1 < len(low) and low[m + 1] == "pytest":
            return True
    return False


def piped_test_suite_sink(command: str) -> str:
    """The forbidden sink name when `command` pipes a test-suite invocation
    (unittest discover / pytest) into head, tail, grep, wc, more or less - the
    shape that deadlocks (see PIPED_SUITE_SINKS above). '' when nothing
    matches, including when the text cannot be tokenised: this gate is
    additive on top of the suite commands, which already run elsewhere in
    the pipeline, so failing open here never hides a mutation another gate
    would have caught."""
    pairs = segments_with_separators(command)
    if not pairs:
        return ""
    for n in range(1, len(pairs)):
        sep, seg = pairs[n]
        if sep not in PIPE_SEPS or not is_test_suite_segment(pairs[n - 1][1]):
            continue
        i = argv0_index(seg)
        if i is None:
            continue
        name = basename_no_ext(seg[i])
        if name in PIPED_SUITE_SINKS:
            return name
    return ""


def command_substitutions(command: str) -> list:
    """The inner text of every `$( ... )` and backtick substitution, and of every
    process substitution `<( ... )` / `>( ... )` - bash runs that body as a command
    too (`cat <(git commit -m x)`), and an unquoted one is no word shlex can read.

    The shell runs these as commands; shlex does not know them, so
    `echo $(git push origin main)` tokenises to `$(git`, `push`, `main)` and no
    gate ever saw a `git` token, while the quoted `"$(git push)"` was a single
    token. Both are returned here as ordinary command strings for the caller to
    analyse recursively.

    Single quotes suppress substitution, so their content is skipped; double
    quotes do not. `$(( ... ))` is arithmetic, not a command - skipped, or its
    `>` would be read as a redirect. An unbalanced opener yields the rest of
    the text (fail-closed: the caller then resolves what it can and refuses
    what it cannot).

    Heredocs are removed first, so a backtick inside a document being written
    is prose - but the body of an UNQUOTED heredoc is scanned too, because
    `<<EOF` really does expand a substitution written in it."""
    text, _ = split_heredocs(command)
    out = []
    for body in expanding_heredoc_bodies(command):
        out.extend(command_substitutions(body))
    command = text
    i, n, dq = 0, len(command), False
    _charge(n)
    while i < n:
        ch = command[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            dq = not dq
            i += 1
            continue
        if ch == "'" and not dq:
            j = command.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "`":
            j = command.find("`", i + 1)
            out.append(command[i + 1:] if j < 0 else command[i + 1:j])
            if j < 0:
                break
            i = j + 1
            continue
        if ch in "$<>" and command.startswith(ch + "(", i) and (ch == "$" or not dq):
            if command.startswith("$((", i):
                j = command.find("))", i + 3)
                i = n if j < 0 else j + 2
                continue
            # Quotes pair up inside the body, so a `(` or `)` in one is text
            # (`$(grep -c "it(" f)`): the parenthesis count below read it as
            # structure and cut the body in the wrong place (task-0108). Quotes
            # that do not pair up fall back to counting parentheses alone.
            j = _subst_end(command, i + 2)
            if j > 0:
                _charge(j - i)
                out.append(command[i + 2:j - 1])
                i = j
                continue
            _charge(4 * (n - i))  # the pairing failed after reading to the end
            depth, j = 1, i + 2
            while j < n:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                    if not depth:
                        break
                j += 1
            if depth:
                out.append(command[i + 2:])
                break
            out.append(command[i + 2:j])
            i = j + 1
            continue
        i += 1
    return out


def eval_operands(command: str) -> list:
    """The string operands of every `eval` in `command`. What eval runs is a
    command, so the operand is analysed as one."""
    segments = command_segments(command)
    if segments is None:
        return []
    out = []
    for seg in segments:
        i = argv0_index(seg)
        if i is not None and EVAL_RE.match(seg[i]):
            out.extend(tok for tok in seg[i + 1:] if not tok.startswith("-"))
    return out


def eval_redirect(command: str, _depth: int = 0) -> str:
    """The `eval` operand carrying a redirect operator, or ''.

    Its TARGET cannot be resolved - that is the whole point of eval, and
    `eval 'ls > $X'` after `X=/dev/null` is the shape that motivates this. So
    the operator alone is refused, rather than guessing at the target. An eval
    with no redirect operator in it is not this function's business."""
    for operand in eval_operands(command):
        if ANY_REDIR_RE.search(operand):
            return operand
    if unscannable_depth(command, _depth):
        return DEPTH_EXCEEDED
    for body in nested_command_bodies(command):
        hit = eval_redirect(body, _depth + 1)
        if hit:
            return hit
    return ""


# Windows drops trailing dots and spaces from a file name (measured: `python
# .../mode.py. --show` runs the script), and a `)` closing a `( ... )` group is
# glued to the last word, so the name may carry either behind `.py`.
APPROVE_SCRIPT_RE = re.compile(r"(?:^|[\\/])approve\.py[. )]*$", re.IGNORECASE)


def runs_approve_script(command: str, _depth: int = 0) -> bool:
    """True when approve.py is INVOKED (not merely named as a path argument).

    Fail-closed on untokenisable text. Nested `sh -c` bodies are unwrapped, the
    same layering git resolution uses."""
    names = executed_names(command)
    if names is None:
        return True
    if any(APPROVE_SCRIPT_RE.search(n) for n in names):
        return True
    if unscannable_depth(command, _depth):
        return True
    return any(runs_approve_script(body, _depth + 1)
               for body in nested_command_bodies(command))


def shell_c_bodies(command: str) -> list:
    """The `-c` argument of every nested sh-family shell in `command`.

    shlex removes the quoting, so the body comes back as a plain command string
    the caller can scan exactly like a top-level one. Same two-layer shape the
    file already uses for git (git_invocations plus a nested-body pass): argv
    walking is the real check, and this is what stops a quoted subshell from
    being scanned as an empty string."""
    tokens = gate_tokens(command)
    if tokens is None:
        return []
    bodies = []
    for i, tok in enumerate(tokens):
        if CMD_RE.match(tok):
            # `cmd /c "python x"`: cmd's switches open with `/` (`//` under git-bash).
            for j in range(i + 1, len(tokens)):
                if not tokens[j].startswith("/"):
                    break
                if WRAPPER_OPERAND_RE.match(tokens[j]) and j + 1 < len(tokens):
                    bodies.append(tokens[j + 1])
                    break
            continue
        if COMMAND_OPTION_RE.match(tok):
            # `flock f -c 'cmd'`, `su root -c 'cmd'`: the body is the option's
            # argument, and positionals may stand in front of the option.
            for j in range(i + 1, len(tokens)):
                arg = tokens[j]
                if arg in SEPARATOR_TOKENS:
                    break
                if arg.startswith("--command="):
                    bodies.append(arg.partition("=")[2])
                    break
                if arg == "--command" or (arg.startswith("-") and not arg.startswith("--")
                                          and arg.endswith("c")):
                    if j + 1 < len(tokens):
                        bodies.append(tokens[j + 1])
                    break
            continue
        if not SHELL_TOKEN_RE.match(tok):
            continue
        for j in range(i + 1, len(tokens)):
            arg = tokens[j]  # not `tok`: that is the outer loop's shell token
            if not arg.startswith("-"):
                break  # a positional argument: this shell runs a script, not -c
            # `-c`, and the clusters that end in it (`-lc`, `-ec`, `-xc`) - a
            # shell takes the command as the argument of whichever cluster ends
            # with c, so matching only the bare `-c` missed `bash -lc "..."`.
            if arg == "-c" or (not arg.startswith("--") and arg.endswith("c")):
                if j + 1 < len(tokens):
                    bodies.append(tokens[j + 1])
                break
    return bodies


def nested_command_bodies(command: str) -> list:
    """Every command string `command` carries INSIDE itself: a nested shell's
    `-c` body, an `eval` operand, and the inner text of a `$( ... )` or
    backtick substitution.

    One list, because every gate that recurses needs all three and any gate
    that knows only some of them is a bypass with extra steps.

    Substitutions are FLATTENED (substitution_bodies), so they all arrive at
    one depth charge; only a nested shell or an eval costs a level."""
    _charge(len(command) + 64)
    return (shell_c_bodies(command) + eval_operands(command)
            + substitution_bodies(command))


def substitution_bodies(command: str, _limit: int = 64) -> list:
    """Every `$( ... )` / backtick body in `command`, transitively flattened.

    A substitution is not a nested shell, so it must not cost recursion depth.
    It did, and `echo $($($($(git push origin main))))` exhausted
    MAX_SHELL_DEPTH and was ALLOWED against a protected branch (measured, exit
    0). Each `$(` is two characters; the depth ceiling is there for `sh -c` and
    `eval`, which is where each level really is a separate shell. Flattening
    here means an arbitrarily deep stack of substitutions is resolved at the
    single charge the outermost one pays.

    `_limit` caps pathological input; the bodies a real command carries are few."""
    out, queue = [], command_substitutions(command)
    while queue and len(out) < _limit:
        body = queue.pop(0)
        out.append(body)
        queue.extend(command_substitutions(body))
    if queue:
        # Past the cap the rest used to be dropped: `echo $(:) x64 $(git push origin
        # main)` hid the push (measured, git_invokes() said False). They are kept as
        # ONE more body, a line each, so every later gate still reads them.
        out.append("\n".join(queue))
    return out


TOO_COMPLEX = ("command too complex to check: it nests more shells, substitutions and "
               "quotes than the gate scans, so what it runs cannot be told and it is refused")


def _scan_walk(command: str, depth: int) -> None:
    """The work the recursing gates do on `command`, charged to the budget: the
    heredoc strip, the quote mask, and every nested body down to MAX_SHELL_DEPTH."""
    mask_quoted(strip_heredocs(command))
    if depth < MAX_SHELL_DEPTH:
        for body in nested_command_bodies(command):
            _scan_walk(body, depth + 1)


@functools.lru_cache(maxsize=256)
def _too_complex(command: str) -> bool:
    global _SCAN
    _SCAN = [SCAN_BUDGET + SCAN_PER_CHAR * len(command)]
    try:
        _scan_walk(command, 0)
        return False
    except ScanBudgetExceeded:
        return True
    finally:
        _SCAN = None


def too_complex(command: str) -> bool:
    """True when scanning `command` takes more than SCAN_BUDGET characters of work.
    Every caller must refuse it, as it refuses an unparseable one: the gate cannot
    say what the command runs, and it cannot afford to find out.

    Asked from inside the walk itself (gate_tokens() is called by the primitives
    the walk uses) it answers False, so the budget is never reset half way."""
    return _SCAN is None and _too_complex(command)


DEPTH_EXCEEDED = (f"nested shell deeper than MAX_SHELL_DEPTH ({MAX_SHELL_DEPTH}) "
                  f"- the inner body was never scanned, so it is refused")


def unscannable_depth(command: str, _depth: int) -> bool:
    """True when the recursion budget is spent and a nested body remains.

    Every recursing gate treats this as a HIT. Exceeding the depth used to be a
    silent allow, which made the ceiling a bypass instead of a limit: whatever
    the gate could not read, it waved through. Refusing costs a legitimate
    4-shells-deep command a deny it can rewrite; allowing costs the gate.

    The scan budget is checked at the root visit (too_complex): the gates call
    this once per visit, and a command that would take minutes to walk is a hit
    before the first visit starts."""
    if _depth == 0 and too_complex(command):
        return True
    return _depth >= MAX_SHELL_DEPTH and bool(nested_command_bodies(command))


def redirect_write_fragments(command: str, _depth: int = 0) -> Iterator[str]:
    """Yield every file-writing redirect fragment of `command`, nested bodies
    included. The first one is redirect_write_target(); the whole sequence is
    what check_bare_nul() needs, because a bare NUL can follow a legitimate
    write (`ls > out.txt 2>NUL`)."""
    if _depth == 0 and too_complex(command):
        yield TOO_COMPLEX
        return
    command = strip_heredocs(command)
    for m in REDIR_RE.finditer(mask_quoted(command)):
        # Offsets index the original, so the reported fragment and the target
        # test both read the real text rather than the mask's filler.
        target = command[m.start("t"):m.end("t")].strip("'\"")
        # A `)` or backtick that closes a command substitution is glued to the
        # target class (`$(ls 2>&1)` reads `1)`), so it is dropped for the dup
        # test only. `>&x)` and `>&2-x` still name a file.
        if m.group("op").endswith("&") and DUP_TARGET_RE.fullmatch(target.rstrip(")`")):
            continue  # descriptor dup, e.g. 2>&1 - not a file write
        if target.lower() in DISCARD_TARGETS:
            continue  # discard sink - not a tree mutation
        yield command[m.start():m.end()].strip()
    if unscannable_depth(command, _depth):
        yield DEPTH_EXCEEDED
        return
    for body in nested_command_bodies(command):
        yield from redirect_write_fragments(body, _depth + 1)


def redirect_write_target(command: str, _depth: int = 0) -> str:
    """Return the file-writing redirect fragment, or '' if the command only dups
    descriptors (2>&1) or discards output (/dev/null). A bare NUL is a write.

    Nested `sh -c "..."` bodies are unwrapped and scanned recursively, because
    mask_quoted() blanks the quoted body and would otherwise report ''.

    KNOWN LIMITATION - this is textual, so it sees only a body written out in
    the command itself. A body built at runtime (`bash -c "$CMD"`), decoded
    (`base64 -d | sh`), fed through stdin (`echo ... | sh`), or held in a script
    file invoked by path stays invisible here. Nesting deeper than
    MAX_SHELL_DEPTH shells is equally invisible: the unwrapping stops there, so a
    redirect at depth 4 or below is not seen. The ceiling stays on purpose -
    building that command is harder than the runtime-body bypass above, which
    this can never catch anyway - but do not read a pass as proof of no
    redirect. Those need the profile's other layers, not a bigger regex."""
    return next(redirect_write_fragments(command, _depth), "")


# The NUL-only twin of DEV_NULL_REDIR_RE, kept as a second net behind REDIR_RE
# (which since task-0102 sees a glued `ls>NUL` too). The operator list is wider
# than DEV_NULL_REDIR_RE's `[<>&]{0,2}[<>]`, which cannot match `>&NUL` or
# `>|NUL` at all. A bare `<` only reads, so it is not listed. A path-qualified
# glued NUL (`ls>./NUL`) is NOT matched here: redirect_write_fragments() yields
# it and check_bare_nul() compares its last path component.
#
# This net is not redundant (task-0108): `ls>NUL:` is the one spelling ONLY it
# catches. REDIR_RE reports the target `NUL:`, whose last path component is not
# `nul`, so the loop lets it through. Whether the colon survives on disk is
# not measured; denying it is the cautious side. test_nul_entry pins it.
# The lookbehind skips only the second character of `>>` / `<>`; an arrow
# (`ls ->NUL`) is a real redirect in the shell and is not skipped.
NUL_REDIR_RE = re.compile(
    r"(?<![<>])(?:(?<!\d)\d+)?(?:&>>?|<>|>[>&|]?)\s*['\"]?nul['\"]?(?![\w./-])", re.IGNORECASE)


def redirects_to_bare_nul(command: str, _depth: int = 0) -> bool:
    """True when `command` redirects to a bare NUL through an operator outside
    quotes, heredoc bodies and nested bodies included. Same layering as
    redirects_to_dev_null(): a quoted sentence about `2>NUL` is not a redirect,
    a quoted TARGET (`ls>"NUL"`) still is, and a nested body too deep to scan
    counts as a hit."""
    text = strip_heredocs(command)
    masked = mask_quoted(text)
    if any(masked[m.start()] == text[m.start()] for m in NUL_REDIR_RE.finditer(text)):
        return True
    if unscannable_depth(command, _depth):
        return True
    return any(redirects_to_bare_nul(body, _depth + 1)
               for body in nested_command_bodies(command))


def check_nul_redirect(command: str) -> int:
    """Deny a redirect to NUL that NUL_REDIR_RE reads, glued or spaced, outside
    quotes and heredoc bodies. readonly and docs call this on top of
    bash_mutates(), which since task-0102 denies every file write in either
    spelling, so here it adds the NUL explanation and the one spelling the
    fragment loop of check_bare_nul() cannot see (`ls>NUL:`); check_bare_nul()
    calls it for the dev profile and the main thread."""
    if redirects_to_bare_nul(command):
        return deny(f"A redirect to NUL is denied in every profile. {BARE_NUL_NOTE}")
    return allow()


def check_bare_nul(command: str) -> int:
    """Deny a redirect to a NUL entry in EVERY profile (task-0095). The readonly,
    docs and orchestrator gates already deny any file write; the dev profile
    allows ordinary ones, so it needs this on top. It checks every fragment, not
    just the first, so `ls > out.txt 2>NUL` is caught too, and compares the last
    path component, so `> ./NUL` and `> /tmp/NUL` create the same undeletable
    entry and are denied with it. `nul.txt` stays allowed: measured under
    git-bash, it is a regular file Python can delete. A glued operator
    (`ls>NUL`, `ls>./NUL`) is a fragment like a spaced one since task-0102, so the
    loop catches it; check_nul_redirect() is the net behind it for `ls>NUL:`."""
    for frag in redirect_write_fragments(command):
        target = redirect_target(frag).strip("'\"`)")
        if re.split(r"[\\/]", target)[-1].lower() == "nul":
            return deny(f"Redirect to NUL ('{frag}') is denied in every profile. "
                        f"{BARE_NUL_NOTE}")
    return check_nul_redirect(command)


# --- Orchestrator gate (config: pipeline.json "orchestrator_gate") ---
# The main thread coordinates; it never authors code. Writes are allowed only
# under the bookkeeping allowlist. Subagent tool calls skip this (they carry an
# agent identity in the hook payload and are governed by agent_gate.py).
#
# The allowlisted directory trees, in ONE place: orch_allowed_path() iterates
# this tuple and the deny message formats it, so a tree can never be accepted
# without being named to the orchestrator it was denied to (the FR-13 bug, where
# .agentry/ was accepted for two tasks while the message still said .claude/).
# Lowercase, trailing slash, plain prefixes only - README*, the root CLAUDE.md,
# ~/.claude/plans/ and extra_allow globs are special cases handled below.
ORCH_ALLOW_PREFIXES = (".claude/", ".agentry/", "docs/")
# A README file and nothing else: the basename is `readme` or `readme.md|rst|txt`.
# The old `readme[^/]*(\.md)?$` was a prefix match, so `readme_gen.py` passed.
# agent_gate imports this same object - one pattern, two gates.
README_RE = re.compile(r"(^|[\\/])readme(\.(md|rst|txt))?$", re.IGNORECASE)
ORCH_SED_RE = re.compile(r"\bsed\s+(-\w*\s+)*-i", re.IGNORECASE)
ORCH_TEE_RE = re.compile(r"\btee\s+(?:-\w+\s+)*(?P<t>[^\s;|&]+)", re.IGNORECASE)
ORCH_PATCH_RE = re.compile(r"(^|[\s;|&])(patch\b|git\s+apply\b)", re.IGNORECASE)


def normalize(path: str) -> str:
    """Lowercase posix spelling with `..` resolved, so `docs/../src/a.py` is judged
    as the `src/a.py` it names and not as the `docs/` prefix it starts with. The
    single definition: orch_allowed_path(), is_bookkeeping() and agent_gate all
    judge the path this returns."""
    return posixpath.normpath(path.replace("\\", "/").lower())


def is_root_claude_md(path: str) -> bool:
    """The repository-root CLAUDE.md, resolved against the project root. Root only:
    a CLAUDE.md inside an application tree is not the document the technical-writer
    owns."""
    if is_unc(path):
        return False
    p = Path(path)
    if not p.is_absolute():
        p = state.ROOT / p
    try:
        return p.resolve().relative_to(state.ROOT.resolve()).as_posix().lower() == "claude.md"
    except (OSError, ValueError):
        return False


def strip_streams(path: str) -> str:
    """`path` without NTFS alternate-data-stream suffixes: `pipeline.json::$DATA`
    and `pipeline.json:x` name the same file as `pipeline.json` (measured on
    Windows), and `state::$INDEX_ALLOCATION/x` names the directory. A colon after
    the drive letter is kept."""
    p = path.replace("\\", "/")
    drive = ""
    if re.match(r"^[A-Za-z]:", p):
        drive, p = p[:2], p[2:]
    return drive + "/".join(part.split(":", 1)[0] for part in p.split("/"))


MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])(?=/|$)")


def msys_to_win(path: str) -> str:
    """`/e/dir/file` is how a POSIX-style shell on Windows spells `E:/dir/file`.
    Native Windows resolves the first as a path on the current drive, so a gate that
    only realpath()s it never sees the tree the shell will open."""
    return MSYS_DRIVE_RE.sub(lambda m: m.group(1).upper() + ":", path)


# A UNC path (`\\server\share`, `//server/share`, `\\?\UNC\server\share`) is a network
# location: realpath() on it opens a connection to a host the agent named, which can
# stall a hook past its timeout (and a timeout is an allow) and leaks credentials to
# that host. The gates never resolve one; a caller that must judge it denies.
UNC_RE = re.compile(r"^[\\/]{2,}[^\\/]")


def is_unc(path: str) -> bool:
    """True for a path that starts with two or more separators (either slash), which
    on Windows is a UNC share, an extended-length path or a device path. Three or
    more still count: `///host/share` resolves to the share on Windows."""
    return bool(UNC_RE.match(str(path).strip().strip("'\"")))


def safe_realpath(path: str) -> str:
    """os.path.realpath, except that a UNC path is only normalised lexically, so
    no network host is contacted."""
    return os.path.normpath(path) if is_unc(path) else os.path.realpath(path)


def path_forms(path: str, cwd: str = "") -> list:
    """Every spelling of `path` a gate has to judge: as written (stream suffixes
    removed) and its real location. realpath folds `..`, case, 8.3 short names,
    junctions and symlinks, so `evil/approvals` through a junction onto
    `.agentry/state` is judged as the state file it is. A relative path resolves
    against the payload `cwd` when that is absolute, else the project root."""
    raw = strip_streams(path.strip().strip("'\""))
    if not raw:
        return []
    forms = [raw]
    if is_unc(raw) or is_unc(msys_to_win(raw)):
        return forms  # never resolved; see UNC_RE
    base = cwd if cwd and os.path.isabs(cwd) else str(state.ROOT)
    for spelling in dict.fromkeys([raw, msys_to_win(raw)]):
        joined = os.path.join(base, spelling)
        if is_unc(joined):
            continue  # a UNC cwd makes even a relative path a network path
        try:
            forms.append(os.path.realpath(joined))
        except (OSError, ValueError):
            pass
    return forms


# The LIVE harness: the gate code and hook wiring that run in every session, the
# agent definitions that name the gate profile, and the rules, skills and commands
# every agent loads as instructions. A dev or docs agent that can write them can
# switch the gates off for the next call (hook edits are live) or rewrite what the
# next agent is told. Only the project ROOT's copy counts (D4): a checkout under
# src/ is ordinary code the CEO reads at the commit checkpoint, and the
# orchestrator deploys it.
LIVE_HARNESS_RE = re.compile(
    r"\.claude/(?:(?:tools|rules|skills|commands)(?:/.*)?|agents|agents/[^/]+\.md|settings[^/]*\.json)")
# Anything inside a `.git` directory, of ANY repo (hooks, config, info/attributes): a
# hook or an `include.path` runs code at the next git call, outside every gate. NTFS
# drops trailing dots and spaces from a name, so `.git.` and `.git ` are `.git`. The
# `.git` file of a worktree counts too (its `gitdir:` line redirects the repo).
GIT_DIR_RE = re.compile(r"(?:^|/)\.git[. ]*(?:/|$)")


def is_live_harness(path: str, cwd: str = "") -> bool:
    """True when `path` resolves to <ROOT>/.claude/{tools,rules,skills,commands}/**,
    <ROOT>/.claude/settings*.json, <ROOT>/.claude/agents/*.md, <ROOT>/.mcp.json, or
    to anything inside a `.git` directory of any repo."""
    if is_unc(path):
        return True  # a network path cannot be judged, so no subagent may write it
    root = normalize(os.path.realpath(state.ROOT))
    for form in path_forms(path, cwd):
        p = normalize(form)
        if GIT_DIR_RE.search(p):
            return True
        if p.startswith(root + "/"):
            rel = p[len(root) + 1:]
            if rel == ".mcp.json" or LIVE_HARNESS_RE.fullmatch(rel):
                return True
    return False


# Every tool that writes a file. Claude Code 2.1.295 lists them as
# Write, Edit, MultiEdit, NotebookEdit; each hook matcher must name all four.
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def edit_target(tool: str, ti: dict) -> tuple[str, str]:
    """(path, new text) of one file-writing tool call, for the path and dash checks."""
    if tool == "NotebookEdit":
        return str(ti.get("notebook_path", "")), str(ti.get("new_source", ""))
    path = str(ti.get("file_path", ""))
    if tool == "MultiEdit":
        edits = ti.get("edits")
        if not isinstance(edits, list):
            return path, ""
        return path, "\n".join(str(e.get("new_string", "")) for e in edits
                               if isinstance(e, dict))
    return path, str(ti.get("new_string", "") or ti.get("content", ""))


def orch_cfg() -> dict:
    cfg = state.load_pipeline().get("orchestrator_gate", {})
    return cfg if isinstance(cfg, dict) else {}


def orch_enabled() -> bool:
    return bool(orch_cfg().get("enabled"))


def orch_allowed_path(file_path: str) -> bool:
    """Bookkeeping allowlist for the orchestrator: any .claude/ or .agentry/
    tree, docs/, README*, the root CLAUDE.md, the harness plan scratch
    (~/.claude/plans/), plus configurable extra_allow globs (matched
    repo-relative).

    .agentry/ joined the list when FR-13 moved the work product out of
    .claude/: task files, plans, specs and run state are exactly what the
    orchestrator is supposed to write, so leaving it out denied the main
    thread its own job."""
    if is_unc(file_path):
        return False
    try:
        low = normalize(file_path)
        for prefix in ORCH_ALLOW_PREFIXES:
            if low.startswith(prefix) or "/" + prefix in low:
                return True
        if README_RE.search(low):
            return True
        p = Path(file_path)
        if not p.is_absolute():
            p = state.ROOT / p
        try:
            rel = p.resolve().relative_to(state.ROOT.resolve()).as_posix()
        except ValueError:
            # outside the project root: only the harness plan area is legal
            # (under_home_claude() already handled the rest of ~/.claude/)
            home_plans = (Path.home() / ".claude" / "plans").resolve()
            rp = p.resolve()
            return rp == home_plans or home_plans in rp.parents
        if rel.lower() == "claude.md":
            return True
        import fnmatch
        for pattern in orch_cfg().get("extra_allow", []):
            if fnmatch.fnmatch(rel, str(pattern)) or fnmatch.fnmatch(rel.lower(), str(pattern).lower()):
                return True
        return False
    except (OSError, ValueError):
        return True  # fail-open


def orch_check_edit(file_path: str) -> int:
    if not orch_enabled():
        return allow()
    if orch_allowed_path(file_path):
        return allow()
    trees = ", ".join(ORCH_ALLOW_PREFIXES)
    return deny(f"Orchestrator never writes code: '{file_path}' is outside the bookkeeping "
                f"allowlist ({trees}, README*, root CLAUDE.md). Dispatch "
                f"the owning agent via the Agent tool instead. (orchestrator_gate - see "
                f".claude/rules/orchestration.md)")


def orch_check_bash(command: str) -> int:
    """Deny shell-based file authoring by the orchestrator: redirects and tee
    into non-allowlisted paths, in-place editors, patch application. Pipeline
    tooling, read-only git and the approval-gated commit/push flow stay intact."""
    if not orch_enabled():
        return allow()
    for frag in redirect_write_fragments(command):
        target = redirect_target(frag)
        if target and not orch_allowed_path(target):
            return deny(f"Orchestrator never writes files via shell redirects ('{frag}'). "
                        f"Dispatch the owning agent instead. (orchestrator_gate)"
                        f"{bare_nul_note(frag)}")
    # The three scans below are plain-text regexes, so words that DESCRIBE a forbidden
    # edit read as the edit: a heredoc body (a task file that mentions `sed -i`,
    # 2026-10-09) and a quoted argument (`printf '- sed -i x' >> task.md`). They run
    # on each command text with heredoc bodies removed and quoted spans blanked, as
    # redirect_write_fragments() already does; a nested shell, eval or substitution
    # body is a text of its own, so `bash -c "sed -i ..."` is still seen.
    for text in orch_scan_texts(command):
        plain = strip_heredocs(text)
        masked = mask_quoted(plain)
        m = ORCH_TEE_RE.search(masked)
        if m and not orch_allowed_path(plain[m.start("t"):m.end("t")].strip("'\"")):
            return deny(f"Orchestrator never writes files via tee ('{m.group(0)}'). "
                        f"Dispatch the owning agent instead. (orchestrator_gate)")
        if ORCH_SED_RE.search(masked):
            return deny("Orchestrator never edits files in place (sed -i). Use Edit for "
                        "bookkeeping files or dispatch the owning agent. (orchestrator_gate)")
        if ORCH_PATCH_RE.search(masked):
            return deny("Orchestrator never applies patches (patch / git apply). Dispatch "
                        "the owning agent instead. (orchestrator_gate)")
    return allow()


def orch_scan_texts(command: str, _depth: int = 0) -> list:
    """`command` plus every command text nested inside it (a shell `-c` body, an
    eval operand, a substitution), down to MAX_SHELL_DEPTH."""
    texts = [command]
    if _depth < MAX_SHELL_DEPTH:
        for body in nested_command_bodies(command):
            texts.extend(orch_scan_texts(body, _depth + 1))
    return texts

# Multi-repo workspaces: the workspace root is often NOT a git repo (the repos
# are subdirectories like backend/ and frontend/). The branch must be read from
# the repo the command actually targets, in priority order:
#   git -C <dir>  >  last `cd <dir>` in the command  >  session cwd  >  ROOT.
GIT_C_RE = re.compile(r"git\s+-C\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")
CD_RE = re.compile(r"(?:^|&&|\|\||;)\s*cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")


# What the CEO owes the run, for the denies that wait on him (task-0114, FR-50).
# `reason` on stderr is for the MODEL ("do X next"); a deny that asks the human
# carries one more sentence, printed by emit_human() as the hook's stdout JSON
# {"systemMessage": ...}. Measured on claude.exe 2.1.295 with a real PreToolUse
# hook: with exit code 2 the tool call is blocked on stderr alone, exactly as
# before, and the stdout document reaches the person as a "PreToolUse:Bash says:
# <message>" notice in the main thread (a subagent's stream showed no notice).
# Not in this table: the policy denies, which ask nobody for anything.
# Words that belong to the model (approve.py, advance.py, --reject) stay out.
HUMAN_NOTICES = {
    "commit": "{task} is ready to commit and is waiting for you. Read the diff (/diff), "
              "then approve the commit or send the task back.",
    "push": "{task} is ready to push and is waiting for you. Approve the push, or leave "
            "the branch where it is.",
    "merge": "{task} cannot be merged into {trunk}: you have not approved its commit. "
             "Read the diff (/diff) and approve it first.",
    "plan": "{task} tried to change code before you approved its plan, so nothing outside "
            ".agentry/ can be written yet. Once the plan is written, read it and approve "
            "or reject it.",
    "unattended": "An unattended session stopped at {step}, which only you may do. Read the "
                  "finished work and take that step yourself.",
}

# The sentence of the first deny of this run, awaiting emit_human(); "" when that
# deny asks nobody. Module state rather than a print in deny(): handle_bash()
# evaluates its checks eagerly, so two denies can be computed for one call and the
# person must hear only the one whose reason is returned, which is the first.
PENDING_HUMAN: list = []


def human_notice(kind: str, **fields) -> str:
    """The sentence for the CEO, or "" when it cannot be built.

    NEVER raises. A message that fails to build is dropped and the deny it rides
    on stands: a formatting error must not turn a commit, push or plan deny into
    an allow (supervisor decision on task-0114, over the NFR-4 wording)."""
    try:
        return str(HUMAN_NOTICES[kind]).format(**fields)
    except Exception:
        return ""


def deny(reason: str, human: str = "") -> int:
    sys.stderr.write(reason + "\n")
    try:
        if not PENDING_HUMAN:
            PENDING_HUMAN.append(str(human or ""))
    except Exception:
        pass
    return 2


def emit_human() -> None:
    """Print the pending sentence as the hook's stdout JSON, once, then forget it.
    Called only on the way out with exit code 2. Every failure is swallowed: the
    deny has already been written to stderr and is not conditional on this."""
    try:
        if PENDING_HUMAN and PENDING_HUMAN[0]:
            sys.stdout.write(json.dumps({"systemMessage": PENDING_HUMAN[0]}) + "\n")
            sys.stdout.flush()
    except Exception:
        pass
    finally:
        del PENDING_HUMAN[:]


def allow() -> int:
    return 0


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


# --- Git argv resolution: the single source of truth for "which subcommand?" ---
# Substring matching (`"git commit" in command`) is defeated by ANY global option
# sitting between `git` and its subcommand - `git -C dir push origin main`,
# `git -c user.name=x commit -m y`, `git --no-pager commit`. Every gate that
# identified a subcommand that way could be bypassed with one extra flag, so
# subcommand resolution now happens here, once, by walking argv.
GIT_UNKNOWN = "?"  # git text that could not be parsed - callers MUST gate it

# Global options that consume the NEXT argv entry. The `--opt=value` spelling
# needs no entry: it is a single token and falls through the generic flag skip.
GIT_OPTS_WITH_ARG = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix", "--config-env", "--attr-source",
})
GIT_TOKEN_RE = re.compile(r"^(?:.*[\\/])?git(?:\.exe)?$", re.IGNORECASE)
MENTIONS_GIT_RE = re.compile(r"(?<![\w.-])git(?:\.exe)?(?![\w.-])", re.IGNORECASE)
# A token that ends the current command: shell separator or redirect.
SEGMENT_BREAK_RE = re.compile(r"^(?:&&|\|\||;|\||&|\d*[<>])")

# Separators a shell splits on even with no surrounding whitespace. shlex does
# NOT split on them, so `git status&&git add -A` tokenises as one glued token
# ('status&&git'), the second `git` is never seen, and every argv-based gate goes
# blind. Longest first so `&&` / `||` win over `&` / `|`.
SHELL_SEPARATORS = ("&&", "||", "|&", ";", "|", "&")


# Every control character but TAB and LF. shlex splits a word on CR; bash does not
# (CR is a word character), and the group closer marker is one of these. Mapping each
# to a non-whitespace placeholder keeps the word whole, which is what bash sees.
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# A CR that ends a word or a line is a line ending (CRLF) and belongs to no word: it
# is dropped, so a CRLF script is read as written. The second alternative is the CR
# at the end of a line whatever stands in front of it: `cat <<EOF<CR>` loses its
# operator to the heredoc strip and leaves `cat <CR>`, where the CR followed a blank
# and read as a word of its own (a stray `?` argument).
LINE_END_CR_RE = re.compile(r"(?<=\S)\r+(?=\s|\Z)|\r+(?=\n|\Z)")


def normalise_controls(command: str) -> str:
    """`command` with its control characters made harmless to the tokeniser: a CR
    that ends a word is dropped, every other control character becomes `?`.

    Blanking a character to whitespace SPLITS the word it sat in, which is how
    `git -c user.name=a<US>b commit` lost its `commit`; a placeholder keeps the
    word whole. It applies inside quotes too: a quoted marker character forges the
    group closer just as well once shlex has stripped the quotes."""
    return CONTROL_RE.sub("?", LINE_END_CR_RE.sub("", command))


def escaped(command: str, i: int) -> bool:
    """True when the character at `i` follows an ODD run of backslashes: an odd run
    escapes it, an even one cancels out."""
    backslashes = 0
    while backslashes < i and command[i - 1 - backslashes] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def ends_substitution(command: str, i: int) -> bool:
    """False when the closer at `i` is glued to a path that carries on behind it
    (`$(pwd)/x`, `$(pwd).py`): padding it would cut the word the interpreter is
    handed as its script."""
    return command[i + 1:i + 2] not in ("/", "\\", ".")


def pushes_inside(command: str, start: int, end: int) -> bool:
    """True when `command[start:end]`, the body of a substitution, mentions a push.
    Only such a closer can end a push ref, so only such a closer is marked."""
    return "push" in command[start:end].lower()


def pad_separators(command: str) -> str:
    """Insert whitespace around glued shell separators, outside quotes.

    `git status&&git add -A` -> `git status && git add -A`, so shlex yields the
    second `git` as its own token. Quoted regions are copied verbatim: padding
    inside them would rewrite a commit message or a branch name.

    An unquoted NEWLINE terminates a command too, and shlex swallows it as
    ordinary whitespace - so `echo hi\\nrm -rf src` came back as one simple
    command whose argv0 is `echo`, and the second line read as an argument.
    It is emitted as `;` for that reason.

    A parenthesis that GROUPS commands is padded as well: `(git commit -m x)`
    tokenised as `(git` ... `x)`, so no `git` token existed and the commit gate never
    fired, and `(git push origin main)` resolved the target `main)`. Only a group
    is padded. A `(` after `$`, `<`, `>` or `=` opens a substitution, a process
    substitution or an array, and a backslash-escaped paren is an argument
    (`find \\( ... \\)`): those stay one word, and so does everything inside a
    substitution, which the nested-body scan unwraps on its own.

    A group's `)` is emitted as GROUP_CLOSE, not as a plain `)`, so that a quoted
    `")"` (a git option's operand, a commit message) stays a different token: `ungroup`
    drops only the marker. Control characters are normalised first (normalise_controls),
    so a marker character in the text cannot be forged.

    The `)` that closes a substitution is padded too, as SUBST_CLOSE (a group's own
    closer ends a group, this one does not), but only when the substitution held a
    separator and only when it ends a word. Behind a separator the last command is a
    command of its own that the top-level scan reads, and unpadded its last argument
    keeps the closer (`$(echo x; git push origin main)` read the target `main)`). A
    closer followed by a path character (`$(pwd)/x/approve.py`, `$(pwd).py`) stays in
    its word, because that word is the script an interpreter is handed; when such a
    word is a git push's ref, cut_closer() takes the closer and the path off it. A
    closing backtick is padded alike. The `)` of a case pattern, with nothing open in
    front of it, is padded as PATTERN_CLOSE.

    Every other unquoted closer that ends a substitution mentioning a push stays glued
    to its word but carries GLUED_CLOSE in front of it, whether or not the substitution
    held a separator: a push whose ref is the last word of
    `$( git push origin feature/task-1)` must read `feature/task-1`, while a quoted
    `x)y` (no marker) stays a closer-in-the-ref that is refused (task-0120). A
    substitution that pushes nothing is left as it was, so `$(date)`, `$((1+2))`,
    `<(git status)` and `a=(x y)` are not rewritten.

    A newline behind a pipe, `&&` or `||` continues the command and is not padded as
    a `;`; any other unquoted newline ends it."""
    command = normalise_controls(command)
    out = []
    quote = ""
    frames = []  # one entry per open `(` outside quotes: True when it groups commands
    backtick = False
    n_seps = 0  # separators emitted so far; a frame compares it with its value at the opener
    opened_at = []  # n_seps at each open `(`, parallel to `frames`
    opened_pos = []  # index of each open `(` in `command`, parallel to `frames`
    backtick_at = 0
    backtick_pos = 0
    after_pipe = False  # the last thing emitted was `|`, `|&`, `&&` or `||`
    i = 0
    while i < len(command):
        ch = command[i]
        if not quote and not ch.isspace():
            after_pipe = False
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            elif ch == "\\" and quote == '"' and i + 1 < len(command):
                out.append(command[i + 1])
                i += 1
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "\n":
            if after_pipe:
                # The shell reads on past a newline behind a pipe or && / ||: the
                # next line is the same pipeline. `;` here broke the chain, and
                # `cat x/approve.py |<newline>python -` fed nothing to the python.
                out.append(" ")
            else:
                frames.clear()  # a newline ends whatever substitution was left open
                opened_at.clear()
                opened_pos.clear()
                backtick = False
                n_seps += 1
                out.append(" ; ")
            i += 1
            continue
        if ch == "`" and not escaped(command, i):
            backtick = not backtick
            backtick_at = n_seps if backtick else backtick_at
            backtick_pos = i if backtick else backtick_pos
            closes = not backtick
            pad = closes and n_seps > backtick_at and ends_substitution(command, i)
            glued = closes and (n_seps > backtick_at or pushes_inside(command, backtick_pos, i))
            out.append(f" {SUBST_CLOSE} " if pad else GLUED_CLOSE + ch if glued else ch)
            i += 1
            continue
        if ch in "()" and not escaped(command, i):
            if ch == "(":
                frames.append(False not in frames
                              and command[i - 1:i] not in ("$", "<", ">", "="))
                opened_at.append(n_seps)
                opened_pos.append(i)
                out.append(" ( " if frames[-1] else ch)
            else:
                matched = bool(frames)
                group = frames.pop() if frames else False
                # measured from the OUTERMOST open paren: `$(echo x; (git push main))`
                # holds its separator outside the inner group, and the inner `)` still
                # glues onto `main`
                outer = opened_at[0] if opened_at else n_seps
                if opened_at:
                    opened_at.pop()
                opener = opened_pos.pop() if opened_pos else i
                if group:
                    marker = GROUP_CLOSE
                elif not matched:  # nothing is open, so this `)` ends a case pattern
                    marker = PATTERN_CLOSE
                elif n_seps > outer and ends_substitution(command, i):
                    marker = SUBST_CLOSE
                else:
                    marker = ""
                glued = (matched and not group and not marker
                         and (n_seps > outer or pushes_inside(command, opener, i)))
                out.append(f" {marker} " if marker else GLUED_CLOSE + ch if glued else ch)
            i += 1
            continue
        # An `&` that is part of a redirect operator (`2>&1`, `>&f`, `&>f`) is not a
        # separator: splitting there moved everything behind it into a new command,
        # so `python 2>&1 approve.py` showed the interpreter no script. `&<` is no
        # operator, so there the `&` still ends the command.
        if ch == "&" and (command[i - 1:i] in (">", "<") or command.startswith("&>", i)):
            out.append(ch)
            i += 1
            continue
        sep = next((s for s in SHELL_SEPARATORS if command.startswith(s, i)), "")
        if sep:
            n_seps += 1
            out.append(" " + sep + " ")
            after_pipe = sep in ("|", "|&", "&&", "||")
            i += len(sep)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def git_invocations(command: str) -> list:
    """[(subcommand, args), ...] - one entry per `git` call in `command`.

    Global options are skipped, and the ones taking a separate argument consume
    it, so `git -C dir push origin main` resolves to ('push', ['origin', 'main']).
    `args` stops at the first shell separator, redirect, or next `git` token, so
    `&&` / `;` chains yield one entry per link.

    Glued separators are normalised first (pad_separators), so
    `git status&&git add -A` resolves to [('status', []), ('add', ['-A'])]
    instead of hiding the second call inside one token. The walk is per simple
    command (command_segments), the same split every script gate reads, so a
    group (`(git push origin main)`, `{ git commit -m x; }`) and a control word
    (`then`, `do`) are stripped there rather than patched in here.

    The failure mode is deliberately fail-CLOSED: when the text mentions git but
    shlex cannot tokenise it, the subcommand is GIT_UNKNOWN and callers must
    treat it as needing the gate. For these checks, failing open reopens the
    bypass. A git call carrying no subcommand at all (`git --version`) yields
    no entry - that is resolved, not unknown.
    """
    if not MENTIONS_GIT_RE.search(command):
        return []
    segments = command_segments(command)
    if segments is None:
        return [(GIT_UNKNOWN, [])]
    found = []
    for tokens in segments:
        i = 0
        while i < len(tokens):
            if not GIT_TOKEN_RE.match(tokens[i]):
                i += 1
                continue
            j, sub = i + 1, ""
            while j < len(tokens):
                tok = tokens[j]
                if SEGMENT_BREAK_RE.match(tok):
                    break
                if tok.startswith("-"):
                    j += 2 if tok in GIT_OPTS_WITH_ARG else 1
                    continue
                sub = tok.lower()
                j += 1
                break
            if sub:
                args = []
                while (j < len(tokens) and not SEGMENT_BREAK_RE.match(tokens[j])
                       and not GIT_TOKEN_RE.match(tokens[j])):
                    args.append(tokens[j])
                    j += 1
                found.append((sub, args))
            i = max(j, i + 1)
    return found


UNPARSEABLE_GIT_MSG = (
    "This command mentions git but cannot be tokenised (unbalanced quote - often an "
    "apostrophe in a message or heredoc body). The gate cannot tell which git "
    "subcommand would run, so it refuses rather than guessing. Rewrite the command "
    "with balanced quotes (or write the text with Write/Edit instead of a heredoc).")


def git_unparseable(command: str) -> bool:
    """True when the text mentions git but shlex could not tokenise it."""
    return any(sub == GIT_UNKNOWN for sub, _ in git_invocations(command))


def git_invokes(command: str, *subcommands: str, _depth: int = 0) -> bool:
    """True when `command` runs one of `subcommands` ('commit', 'push', ...).

    Unparseable git text counts as a match (fail-closed). The second layer is
    the nested `sh -c` body, which argv walking is structurally blind to
    (`bash -c "git push origin main"` is one shlex token).

    That second layer USED to be `f"git {sub}" in command.lower()`, and it is
    the whole of task-0078: a substring net cannot tell an invocation from a
    mention, so a memory row whose `--fix` prose named a git verb, and a task
    file whose markdown described a push, were both gated as git commands.
    Unwrapping the nested body keeps the case the net existed for and drops
    every prose match with it."""
    for sub, _ in git_invocations(command):
        if sub == GIT_UNKNOWN or sub in subcommands:
            return True
    if unscannable_depth(command, _depth):
        return True
    return any(git_invokes(body, *subcommands, _depth=_depth + 1)
               for body in nested_command_bodies(command))


def repo_dir_operands(command: str) -> list:
    """The directories a command names for its git call, as written: the `git -C`
    target and the last `cd` before the git call."""
    dirs = []
    m = GIT_C_RE.search(command)
    if m:
        dirs.append(_unquote(m.group(1)))
    git_at = command.find("git ")
    cds = CD_RE.findall(command if git_at < 0 else command[:git_at])
    if cds:
        dirs.append(_unquote(cds[-1]))
    return dirs


def repo_candidates(command: str, cwd: str) -> list:
    """Directories to try for the branch check, most specific first. A UNC
    directory is never a candidate: every caller stats its candidates, and a stat
    of a network host the agent named can stall the hook past its timeout, which
    is an allow (handle_bash refuses such a command before it gets here)."""
    base = Path(cwd) if cwd and not is_unc(cwd) else state.ROOT
    resolved = []
    for d in repo_dir_operands(command):
        if is_unc(d):
            continue
        try:
            p = Path(d)
            joined = p if p.is_absolute() else base / p
            if not is_unc(str(joined)):
                resolved.append(joined)
        except (OSError, ValueError):
            pass
    resolved.append(base)
    if state.ROOT not in resolved:
        resolved.append(state.ROOT)
    return resolved


# A UNC path as the operand of an option that names a directory or a file: `-C`,
# `-F`/`--file` (a cluster such as `-aF` counts), `--git-dir`, `--work-tree`.
UNC_OPTION_RE = re.compile(
    r"""(?<![\w-])(?:-[A-Za-z]*[CF]|--file|--git-dir|--work-tree)"""
    r"""(?:=|\s+|(?=[\\/]))["']?[\\/]{2,}[^\\/\s"']""")
UNC_CHECKED_GIT = ("commit", "push", "merge", "pull")


def unc_operand(command: str, cwd: str) -> str | None:
    """A UNC path a git commit / push / merge / pull command would stat, open or run
    in, as written, or None. Such a command is refused outright: the gate never
    touches a network host the agent named, and git itself would. Commit message
    values (-m / --message) are skipped, so a message that starts with `//` is
    not mistaken for a path."""
    if cwd and is_unc(cwd):
        return cwd
    for operand in repo_dir_operands(command):
        if is_unc(operand):
            return operand
    found = UNC_OPTION_RE.search(command)
    if found:
        return found.group(0).strip()
    for sub, args in git_invocations(command):
        if sub not in UNC_CHECKED_GIT:
            continue
        i = 0
        while i < len(args):
            tok = args[i]
            i += 1
            letters = re.match(r"-[A-Za-z]*m", tok) if not tok.startswith("--") else None
            if tok in ("--message", "-m") or (letters and len(tok) == letters.end()):
                i += 1  # the message is the next token
                continue
            if tok.startswith("--message=") or letters:
                continue
            for spelling in (tok, tok.partition("=")[2], re.sub(r"^-[A-Za-z]*", "", tok)):
                if is_unc(spelling):
                    return spelling
    return None


# One time budget for the whole push path (branch, branch list, repo, remote config,
# and the leak guard per pushed ref). A hook that outlasts its timeout is an allow,
# and the sum of the per-call git timeouts used to exceed it, so the push path
# shares ONE deadline instead and running out of it DENIES the push.
PUSH_BUDGET = 12.0
GIT_CALL_TIMEOUT = 10
_push_deadline: float | None = None


class PushBudgetExpired(Exception):
    """The push path's shared time budget ran out. Deliberately not a
    SubprocessError: the git helpers swallow those and move on to the next
    candidate, and an exhausted budget must reach handle_bash and deny."""


def start_push_budget(budget: float | None = None) -> None:
    global _push_deadline
    _push_deadline = time.monotonic() + (PUSH_BUDGET if budget is None else budget)


def end_push_budget() -> None:
    global _push_deadline
    _push_deadline = None


def git_run(argv: list, repo) -> subprocess.CompletedProcess:
    """subprocess.run for one git call, capped at GIT_CALL_TIMEOUT and, while a push
    budget is running, at what is left of it. Raises PushBudgetExpired when none is
    left or the call timed out inside the budget."""
    timeout = GIT_CALL_TIMEOUT
    if _push_deadline is not None:
        timeout = min(timeout, _push_deadline - time.monotonic())
        if timeout <= 0:
            raise PushBudgetExpired()
    try:
        return subprocess.run(argv, cwd=str(repo), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if _push_deadline is not None:
            raise PushBudgetExpired() from None
        raise


def current_branch(command: str = "", cwd: str = "") -> str:
    for repo in repo_candidates(command, cwd):
        try:
            if not Path(repo).is_dir():
                continue
            proc = git_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return ""


def task_from_branch(branch: str) -> str:
    # feature/task-0007 -> task-0007
    for part in branch.replace("\\", "/").split("/"):
        if part.startswith(TASK_PREFIX):
            return part
    if TASK_PREFIX in branch:
        idx = branch.index(TASK_PREFIX)
        return branch[idx:].split("/")[0]
    return ""


# A commit's task tag: `[task-NNNN]` leading a line of the message, the form
# git-workflow.md asks for. Line-anchored so a body sentence that merely cites
# another task ("depends on [task-0010]") cannot re-point the commit.
COMMIT_TAG_RE = re.compile(r"^[ \t]*\[(task-[a-z0-9]+)\]", re.MULTILINE | re.IGNORECASE)
MESSAGE_FILE_MAX_BYTES = 65536


def read_message_file(path: str, cwd: str) -> str:
    """Text of a `git commit -F <path>` message file, '' when it cannot be read
    (stdin, a missing file, a directory). Unreadable means untagged, which falls
    back to the branch's task - the stricter reading, never a looser one."""
    if not path or path == "-" or is_unc(path):
        return ""  # a UNC file is never opened: that contacts a host the agent named
    candidate = Path(path)
    if candidate.is_absolute():
        bases = [candidate]
    else:
        bases = ([Path(cwd) / path] if cwd and not is_unc(cwd) else []) + [state.ROOT / path]
    for base in bases:
        try:
            if base.is_file():
                with base.open("rb") as handle:
                    return handle.read(MESSAGE_FILE_MAX_BYTES).decode("utf-8", errors="replace")
        except OSError:
            continue
    return ""


def commit_message_texts(args: list, cwd: str) -> list:
    """Every message the arguments of ONE `git commit` supply: -m / --message
    (separate, glued or =), and -F / --file read from disk. Clusters such as -am
    are walked char by char, because the message belongs to the `m` inside."""
    texts = []
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            break
        if tok.startswith("--"):
            name, eq, value = tok.partition("=")
            if name in ("--message", "--file"):
                if not eq and i < len(args):
                    value = args[i]
                    i += 1
                texts.append(value if name == "--message" else read_message_file(value, cwd))
            continue
        if tok.startswith("-") and len(tok) > 1:
            for pos, ch in enumerate(tok[1:], start=1):
                if ch in "mFcCt":
                    value = tok[pos + 1:]
                    if not value and i < len(args):
                        value = args[i]
                        i += 1
                    if ch == "m":
                        texts.append(value)
                    elif ch == "F":
                        texts.append(read_message_file(value, cwd))
                    break
    return texts


def _commit_task_entries(command: str, fallback: str, cwd: str, _depth: int = 0) -> list:
    """One entry per `git commit` in `command`, nested `sh -c` / `bash -c` / eval
    bodies and substitutions included: argv walking sees a nested body as one
    token, so a commit hidden there was never counted, and the commit was judged
    as the branch's task while its message tagged another. Past the depth ceiling
    the body is unread and the commit falls back to the branch (the stricter
    outcome, as for a heredoc message)."""
    judged = []
    for sub, args in git_invocations(command):
        if sub != "commit":
            continue
        tag = ""
        for text in commit_message_texts(args, cwd):
            found = COMMIT_TAG_RE.search(text)
            if found:
                tag = found.group(1).lower()
                break
        judged.append(tag or fallback)
    if unscannable_depth(command, _depth):
        return [*judged, fallback]
    for body in nested_command_bodies(command):
        judged.extend(_commit_task_entries(body, fallback, cwd, _depth + 1))
    return judged


def commit_tasks(command: str, branch: str, cwd: str = "") -> list:
    """The task each `git commit` in `command` is judged as, one entry per commit.

    The `[task-NNNN]` tag of its message when it has one, else the checked-out
    branch's task. The branch alone was the old rule, and with two tasks worked
    on one branch (what a preemption creates) it answered about the wrong one: an
    approved task could not commit because an unfinished sibling shared its
    branch. A heredoc fed to `-F -` is not read, so that commit falls back to the
    branch, the stricter outcome. A commit inside a nested shell body is counted
    like any other (_commit_task_entries)."""
    fallback = task_from_branch(branch)
    return _commit_task_entries(command, fallback, cwd) or [fallback]


def valid_work_branch(name: str) -> bool:
    """A work branch must be <type>/...task-<id>... ; protected branches pass
    (checkout/switch to them is not new work)."""
    name = name.replace("\\", "/").strip()
    if name in PROTECTED_BRANCHES:
        return True
    if "/" not in name:
        return False
    prefix = name.split("/", 1)[0]
    if prefix not in BRANCH_TYPES:
        return False
    return bool(re.search(r"task-[a-z0-9]", name, re.IGNORECASE))


BRANCH_CREATE_FLAGS = {"checkout": ("-b", "-B"), "switch": ("-c", "-C")}


def new_branch_name(command: str) -> tuple:
    """(name, trailing_args) for a branch-CREATION command, else ('', []).

    Forms: `git checkout -b NAME [start-point]`, `git switch -c NAME [start-point]`,
    `git branch NAME [start-point]`. Resolved through git_invocations(), so a
    global option no longer hides the subcommand (`git -C dir checkout -b x`).

    Unparseable git text returns (GIT_UNKNOWN, []) - this used to return ('', [])
    and thereby ALLOW, the one caller that read the sentinel as "not a git
    command". The caller must gate it (see check_branch_creation)."""
    for sub, args in git_invocations(command):
        if sub == GIT_UNKNOWN:
            return GIT_UNKNOWN, []
        if sub == "branch":
            if args and not args[0].startswith("-"):
                return args[0], args[1:]
        elif sub in BRANCH_CREATE_FLAGS:
            for n, tok in enumerate(args):
                if tok == "--":
                    break
                if tok in BRANCH_CREATE_FLAGS[sub]:
                    rest = args[n + 1:]
                    if rest and not rest[0].startswith("-"):
                        return rest[0], rest[1:]
                    break
    return "", []


def check_branch_creation(command: str, cwd: str = "") -> int:
    """Deny creation of a work branch that breaks the <type>/task-XXXX naming
    rule [gate 4] or is cut from a base other than main [gate C]. Only fires on
    branch-creation forms; fail-open otherwise. Unparseable git text gates
    (GIT_UNKNOWN): the branch name cannot be read, so neither rule can be
    checked."""
    name, rest = new_branch_name(command)
    if name == GIT_UNKNOWN:
        return deny(UNPARSEABLE_GIT_MSG)
    if not name:
        return allow()
    if name.replace("\\", "/") in PROTECTED_BRANCHES:
        return allow()
    if not valid_work_branch(name):
        return deny(
            f"Branch '{name}' breaks the naming rule. Use <type>/task-<id> "
            f"(types: {', '.join(BRANCH_TYPES)}), e.g. 'feature/task-0007' or "
            f"'bugfix/task-rbac-0012'. The task number is mandatory - see "
            f".claude/rules/git-workflow.md.")
    pipeline = state.load_pipeline()
    main_branch = str(pipeline.get("main_branch", "main"))
    if main_branch.startswith("{{"):
        main_branch = "main"
    return check_branch_base(command, cwd, name, rest, main_branch)


def check_branch_base(command: str, cwd: str, name: str, rest: list, main_branch: str) -> int:
    """C: a work branch must be cut from up-to-date main. Verify the base is
    main (explicit start-point after the branch name, else the current branch).
    `rest` already stops at the first shell separator - anything past it is a
    new command (e.g. `&& echo ...`), not the branch's start-point.
    Fail-open when git is unavailable."""
    start_point = next((tok for tok in rest if tok and not tok.startswith("-")), None)
    if start_point is not None:
        ok = start_point == main_branch or start_point.split("/")[-1] == main_branch
    else:
        cur = current_branch(command, cwd)
        ok = (cur == main_branch) or (cur == "")  # unknown branch -> fail-open
    if ok:
        return allow()
    return deny(
        f"Branch '{name}' must be cut from up-to-date '{main_branch}', not from the "
        f"current branch. Run: git checkout {main_branch} && git pull, then create the "
        f"branch. (branch-base-must-be-main - see .claude/rules/git-workflow.md).")


# --- Workflow mode (config: pipeline.json "workflow") -----------------------
# Two shapes of project, named rather than flagged, because a name says what
# kind of repository this is while a flag only says what it does:
#   pr   - collaborative. Nothing commits on the trunk; the branch is pushed and
#          a human opens the pull request. The DEFAULT, so an adopter inherits
#          the safe shape and opts into the looser one deliberately.
#   solo - single-author. There is no second party to review or to merge, so a
#          LOCAL merge of an approved task branch into the trunk is allowed.
# Pushing to a protected branch is refused in BOTH modes - that is the one
# protection the mode must not touch. push_needs_approval is likewise
# independent of the mode: "I work alone" says nothing about whether an
# unattended run may write to the remote at four in the morning.
WORKFLOW_PR = "pr"
WORKFLOW_SOLO = "solo"

# `git merge` options that consume the NEXT argv entry, so their argument is
# never mistaken for the source branch (`git merge -m "msg" feature/task-1`).
MERGE_OPTS_WITH_ARG = frozenset({"-m", "-s", "--strategy", "-X",
                                 "--strategy-option", "-F", "--file"})


def workflow_cfg() -> dict:
    cfg = state.load_pipeline().get("workflow", {})
    return cfg if isinstance(cfg, dict) else {}


def workflow_mode() -> str:
    """`solo` only when configured so; anything else (missing, unknown value,
    unreadable config) is the collaborative default."""
    mode = str(workflow_cfg().get("mode") or WORKFLOW_PR).strip().lower()
    return WORKFLOW_SOLO if mode == WORKFLOW_SOLO else WORKFLOW_PR


def push_needs_approval() -> bool:
    """Default true, and only an explicit `false` turns it off - a typo or a
    junk value must not silently open the remote."""
    return workflow_cfg().get("push_needs_approval", True) is not False


def merge_sources(args: list) -> list:
    """Commit-ish positional arguments of a `git merge` argv."""
    sources = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            sources.extend(a for a in args[i + 1:] if a)
            break
        if tok.startswith("-"):
            i += 2 if tok in MERGE_OPTS_WITH_ARG else 1
            continue
        sources.append(tok)
        i += 1
    return sources


def merge_invocations(command: str) -> list:
    """Argv of every `git merge` call in `command`.

    Resolved through git_invocations() rather than matched textually: that is
    what sees `git -C dir merge x` and `git status&&git merge x`, both of which
    a hand-rolled check misses. It also distinguishes the `merge-base`
    subcommand token from `merge`, so read-only base checks are untouched."""
    return [args for sub, args in git_invocations(command) if sub == "merge"]


def pull_invocations(command: str) -> list:
    """Argv of every `git pull` call in `command`.

    A pull IS a merge - `git pull . bugfix/task-0090` merges that branch into
    HEAD with no `merge` token anywhere in the argv, so a gate that resolves
    only `sub == "merge"` lets an unreviewed branch onto the trunk."""
    return [args for sub, args in git_invocations(command) if sub == "pull"]


def pull_sources(args: list) -> list:
    """Commit-ish positional arguments of a `git pull` argv.

    Same shape as a merge argv with ONE extra leading positional: the
    repository (`.`, a remote name, a URL). Dropping it is what keeps the
    refusal naming the branch the caller asked for rather than the repo."""
    return merge_sources(args)[1:]


def check_merge_source(source: str, trunk: str) -> int:
    """The three conditions a source branch must meet to reach the trunk. Each
    failure names WHICH condition failed: repeating the generic branch-name
    message would send the reader hunting the wrong thing at three in the
    morning."""
    task = task_from_branch(source)
    if not task or not valid_work_branch(source):
        return deny(
            f"Merge into protected branch '{trunk}' refused - condition 1 of 3 failed "
            f"(source branch): '{source}' is not a task branch <type>/task-<id> "
            f"(types: {', '.join(BRANCH_TYPES)}). Only an approved task branch may "
            f"reach the trunk, even in solo workflow mode.")
    try:
        conn = state.connect()
        try:
            run = state.get_run(conn, task)
        finally:
            conn.close()
    except Exception:
        return deny(
            f"Merge into protected branch '{trunk}' refused - the pipeline run state "
            f"(.agentry/state/run.db) could not be read, so the commit approval for "
            f"{task} cannot be verified. This path fails closed on purpose: only an "
            f"approved task reaches the trunk.")
    if run is None:
        return deny(
            f"Merge into protected branch '{trunk}' refused - condition 2 of 3 failed "
            f"(pipeline run): {task} has no row in run.db, so it never went through "
            f"the pipeline. Register it first: python .claude/tools/pipeline/advance.py "
            f"--task {task} --type <type>.")
    if not run["commit_approved"]:
        return deny(
            f"Merge into protected branch '{trunk}' refused - condition 3 of 3 failed "
            f"(commit approval): the commit checkpoint for {task} was never approved. "
            f"After the CEO approves: python .claude/tools/pipeline/approve.py --task "
            f"{task} --gate commit.",
            human=human_notice("merge", task=task, trunk=trunk))
    return allow()


def check_trunk_merge(command: str, trunk: str) -> int:
    """Deny `git merge` while HEAD sits on a protected branch, except the one
    case solo mode exists for: merging an already-approved task branch locally.

    In `pr` mode every such merge is refused - the human opens the pull request.
    In `solo` mode the three conditions in check_merge_source() still gate it;
    they guard against merging UNREVIEWED work, which is not a collaboration
    question and therefore holds in both modes."""
    if workflow_mode() != WORKFLOW_SOLO:
        return deny(
            f"Merge into protected branch '{trunk}' is forbidden in '{WORKFLOW_PR}' "
            f"workflow mode: push the task branch and let the human open the pull "
            f"request. A single-author project can set workflow.mode to "
            f"'{WORKFLOW_SOLO}' in .agentry/pipeline.json to merge locally instead. "
            f"See .claude/rules/git-workflow.md.")
    sources = [src for args in merge_invocations(command) for src in merge_sources(args)]
    sources += [src for args in pull_invocations(command) for src in pull_sources(args)]
    if not sources:
        return deny(
            f"Merge into protected branch '{trunk}' refused - condition 1 of 3 failed "
            f"(source branch): this command names no source branch the gate can read "
            f"(a bare `git merge` takes FETCH_HEAD). Name the branch: git merge "
            f"<type>/task-<id>.")
    for source in sources:
        verdict = check_merge_source(source, trunk)
        if verdict != 0:
            return verdict
    return allow()


# --- Unattended sessions (env: AGENTRY_UNATTENDED) --------------------------
# supervisor.py's relaunch() starts `claude -p`, which is a new MAIN session:
# agent_gate.py's profiles bind subagents, so none of them reach it. At
# approvals level `auto` with workflow.mode `solo` every remaining link is
# automatic - the spawned session drives the task, advance.py clears the commit
# checkpoint itself, and check_trunk_merge() below asks only for a task-shaped
# branch, a run row and that flag - so the trunk gets written by a process
# nobody is watching. The push stays held by approvals.never_granted() (this
# project lists it in approvals_never_granted), so nothing leaves the machine,
# but the CEO reads the trunk before it moves and that is the point.
#
# The spawn therefore marks itself, and these steps are refused for as
# long as the mark is set, at every approvals level and in every workflow mode.
# A pull counts as a merge here: `git pull . <branch>` writes the trunk exactly
# as `git merge <branch>` does, and carries no `merge` token to resolve.
# The name must stay equal to supervisor.UNATTENDED_ENV (pinned by a test).
UNATTENDED_ENV = "AGENTRY_UNATTENDED"


def check_unattended(command: str) -> int:
    """Deny the irreversible steps inside a supervisor-spawned session.

    Not merge_invokes-by-substring: `git merge-base --is-ancestor` is the
    read-only base check every task runs, so the merge question goes through
    argv resolution exactly as handle_bash() does it."""
    if not os.environ.get(UNATTENDED_ENV):
        return allow()
    step = ("a merge" if merge_invocations(command)
            else "a pull" if git_invokes(command, "pull")
            else "a push" if git_invokes(command, "push")
            else "recording a checkpoint approval" if runs_approve_script(command)
            else "")
    if not step:
        return allow()
    return deny(
        f"This session was started UNATTENDED by the supervisor ({UNATTENDED_ENV}=1), so "
        f"{step} is refused - whatever the approvals level and workflow mode allow, there "
        f"is no human here to approve it. Finish the stage work, run advance.py, and stop: "
        f"the CEO reads the diff and takes this step himself. See "
        f".claude/rules/orchestration.md and .claude/tools/pipeline/supervisor.py.",
        human=human_notice("unattended", step=step))


def push_trunk_names() -> set:
    """The trunk: main + master always, plus the configured main_branch (read by
    leak_guard.trunk_name(), the one reader of that key; an unresolved `{{PLACEHOLDER}}`
    value reads as the default)."""
    try:
        import leak_guard

        trunk = leak_guard.trunk_name()
    except Exception:
        # A broken leak_guard must not raise out of the push path (an uncaught
        # exception in the hook reads as allow): read the key directly so the
        # protected set stays complete, and push_leak_reason() denies the push.
        value = state.load_pipeline().get("main_branch")
        trunk = value.strip() if isinstance(value, str) and not value.startswith("{{") else ""
    return set(PUSH_PROTECTED_ALWAYS) | ({trunk} if trunk else set())


def push_protected_branches() -> set:
    """Every branch a push may never target: the trunk plus pipeline.json
    "protected_branches" (default staging / production)."""
    pipeline = state.load_pipeline()
    names = push_trunk_names()
    extra = pipeline.get("protected_branches")
    if not isinstance(extra, (list, tuple)):
        extra = PUSH_PROTECTED_DEFAULT
    for name in extra:
        name = str(name).strip()
        if name and not name.startswith("{{"):
            names.add(name)
    return names


def push_refspec_pairs(args: list) -> list:
    """(source, destination) branch names of each refspec in a `git push` argv.

    The first non-flag token is the remote; each remaining one is a refspec.
    `main` is main:main; `:main` has an empty source (a deletion); a leading `+`
    and `refs/heads/` or `heads/` are stripped, so `main`, `+main`, `HEAD:main`,
    `HEAD:heads/main` and `refs/heads/main` all have the destination 'main'. `HEAD` and `@` are left as
    written - only the caller knows the checked-out branch. An empty result means
    the command named no refspec, so the caller falls back to the checked-out
    branch."""
    pairs = []
    for spec in push_positionals(args)[1:]:  # positional[0] is the remote
        src, sep, dst = spec.lstrip("+").rpartition(":")
        if not sep:
            src = dst
        if dst:
            pairs.append((HEADS_PREFIX_RE.sub("", src), HEADS_PREFIX_RE.sub("", dst)))
    return pairs


def wildcard_branch_spec(spec: str) -> bool:
    """True for a refspec like `refs/heads/*:refs/heads/*` that lands on branches."""
    dst = spec.lstrip("+").rpartition(":")[2]
    return "*" in spec and (dst.startswith("refs/heads/") or not dst.startswith("refs/"))


def push_fans_out(args: list) -> bool:
    """True when this `git push` argv pushes every local branch: `--all`,
    `--branches`, `--mirror` (or an abbreviation of one), a bare `:` (the matching
    refspec), or a wildcard refspec over branches."""
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            break
        if push_option_in(tok, PUSH_FANOUT_LONG):
            return True
        i += 2 if tok in PUSH_OPTS_WITH_ARG else 1
    return any(wildcard_branch_spec(spec) or spec.lstrip("+") == ":"
               for spec in push_positionals(args)[1:])


def cut_closer(tok: str) -> str:
    """`tok` up to a `)` or backtick that closes a substitution glued to it.

    `echo $(echo x; git push origin main)/foo` keeps `main)/foo` as one word: the
    closer stays in its word when a path follows (see ends_substitution), because
    `python $(pwd)/x/approve.py` must keep its script. For a push that word is the
    ref, and `main)/foo` is no protected branch - so the push to `main` read as a
    push to something else and was allowed (task-0112). The text behind the closer
    belongs to the outer command, so it goes.

    This cuts wherever a closer stands, so it is only safe on a token whose closer
    is known to be a substitution's: push_ref_with_closer() refuses every other
    `)` or backtick first (task-0120 FX-4: a quoted ref `x)y:main` was cut to `x`).
    The marker pad_separators puts in front of a real glued closer is cut with it."""
    return re.split(r"[)`" + GROUP_CLOSE_MARK + "]", tok, maxsplit=1)[0]


def push_raw_positionals(args: list) -> list:
    """The non-flag tokens of a `git push` argv exactly as shlex left them."""
    positional = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            positional.extend(args[i + 1:])
            break
        if tok.startswith("-"):
            i += 2 if tok in PUSH_OPTS_WITH_ARG else 1
            continue
        positional.append(tok)
        i += 1
    return positional


def push_positionals(args: list) -> list:
    """The non-flag tokens of a `git push` argv: the remote, then the refspecs."""
    return [tok for tok in map(cut_closer, push_raw_positionals(args)) if tok]


SHELL_CLOSER_RE = re.compile(r"[)`]|\$\(")


def push_ref_with_closer(args: list) -> str | None:
    """The first remote or refspec of a `git push` argv that holds a shell closer
    (`)`, a backtick, `$(`) once the quotes are gone, or None.

    Git ref names may hold `)` and a backtick, so a quoted `'x)y:main'` is a real
    ref and cutting it at the `)` read it as `x` while git published `main`
    (task-0120 FX-4). The only closer that is NOT part of the ref is the one
    pad_separators marked as an unquoted substitution closer, and the text from
    that marker on is the outer command's; everything in front of it must be clean.
    A ref that cannot be told apart from a closer is refused, never cut."""
    for tok in push_raw_positionals(args):
        head = tok.split(GROUP_CLOSE_MARK, 1)[0]
        if SHELL_CLOSER_RE.search(head):
            return head
    return None


def push_destructive_reason(args: list) -> str | None:
    """Why this `git push` argv rewrites or removes a remote ref, or None.

    Resolving `--force origin main` and `origin :main` to the target 'main' is
    what lets the trunk-push approval see them at all - and both were denied
    unconditionally before that approval existed. Publishing the trunk is not
    overwriting it and not deleting it, so these keep their old refusal."""
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            break
        if tok.startswith("--"):
            name = push_option_in(tok, PUSH_DESTRUCTIVE_LONG)
            if name:
                return f"it carries {name}" + (
                    "" if tok.split("=", 1)[0] == name else f" (spelled {tok.split('=', 1)[0]})")
        elif tok.startswith("-") and len(tok) > 1:
            hit = sorted(set(tok[1:]) & PUSH_DESTRUCTIVE_SHORT)
            if hit:
                return f"it carries -{''.join(hit)}"
        if tok.startswith("-"):
            i += 2 if tok in PUSH_OPTS_WITH_ARG else 1
            continue
        i += 1
    for spec in push_positionals(args)[1:]:
        if spec.startswith("+"):
            return f"its refspec '{spec}' is forced by the leading +"
        src, sep, _ = spec.rpartition(":")
        if sep and not src and spec != ":":  # a bare `:` is the matching refspec, not a deletion
            return f"its refspec '{spec}' has an empty source side (a deletion)"
    return None


def local_branches(command: str, cwd: str) -> list | None:
    """Local branch names of the repo the command runs in, or None when unreadable."""
    for repo in repo_candidates(command, cwd):
        try:
            if not Path(repo).is_dir():
                continue
            proc = git_run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"], repo)
            if proc.returncode == 0:
                return proc.stdout.split()
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return None


def push_remote(args: list) -> str | None:
    """The remote a `git push` argv names: its first positional (a remote name or
    a URL), else the `--repo` value, else None - git then falls back to the
    branch's configured remote, which the gate does not resolve."""
    positional = push_positionals(args)
    if positional:
        return positional[0]
    for i, tok in enumerate(args):
        if tok == "--":
            break
        if tok == "--repo" and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith("--repo="):
            return tok.partition("=")[2] or None
    return None


def push_pairs(command: str, branch: str, local: list | None = None) -> list | None:
    """(source, destination) branch names the push moves, or None when it cannot
    be resolved. See push_remote_pairs, which also carries the remote."""
    triples = push_remote_pairs(command, branch, local)
    return None if triples is None else [(src, dst) for _, src, dst in triples]


def push_remote_pairs(command: str, branch: str, local: list | None = None) -> list | None:
    """(remote, source, destination) of every ref the push moves, or None when it
    cannot be resolved. `remote` is what that git call names (push_remote), so a
    chain pushing to two places keeps each ref with its own remote.

    Fail-CLOSED by design: None means the caller must DENY. Fail-open belongs at
    the top-level exception handler, not in the step that decides whether a push
    is gated - guessing "probably fine" there is exactly how a protected branch
    gets pushed. Returns None for a push visible only to the substring net
    (`bash -c "git push origin main"`), for a refspec-less push or a `HEAD` / `@`
    whose checked-out branch could not be read, and for a fan-out push (`--all`,
    `--mirror`, a wildcard) whose local branches (`local`) could not be listed.

    `HEAD` and `@` are the checked-out branch, not branches of that name: left as
    is, `git push origin HEAD` slipped past the protected-branch check while
    sitting on the trunk. A fan-out push counts as pushing the configured trunk
    and every local branch."""
    invocations = [args for sub, args in git_invocations(command) if sub == "push"]
    if not invocations:
        return None
    if any(push_ref_with_closer(args) is not None for args in invocations):
        return None
    pairs = []
    for args in invocations:
        remote = push_remote(args)
        fans_out = push_fans_out(args)
        specs = push_refspec_pairs(args)
        if fans_out:
            if local is None:
                return None
            trunk = str(state.load_pipeline().get("main_branch") or "main")
            pairs.extend((remote, name, name) for name in dict.fromkeys([*local, trunk])
                         if not name.startswith("{{"))
            specs = [(src, dst) for src, dst in specs if "*" not in src + dst]
        elif not specs:
            specs = [(branch, branch)]
        for spec in specs:
            names = []
            for name in spec:
                if name in ("HEAD", "@"):
                    name = branch
                    if not name:
                        return None
                names.append(name)
            if not any(names):
                return None
            pairs.append((remote, *names))
    return pairs


def push_target_branches(command: str, branch: str, local: list | None = None) -> list | None:
    """Destination branch names the push would land on, or None when it cannot
    be resolved (see push_pairs)."""
    pairs = push_pairs(command, branch, local)
    return None if pairs is None else [dst for _, dst in pairs]


def pushed_tasks(command: str, branch: str, local: list | None = None) -> list | None:
    """Task ids whose push approval this push needs, or None when it moves a ref
    that cannot be tied to any task. Judged by the refs it pushes and not by the
    checked-out branch (task-0111): every pair needs the task of its SOURCE plus
    the task of its DESTINATION.

    The trunk as a source needs no task only when the destination is protected:
    that is the publication the trunk marker covers (check_push_target judges it).
    A trunk source sent to any other destination (`main:other`) falls back to the
    checked-out task like any source. Any other source is the work being shipped
    and needs its own task, whatever the destination; a source that names none
    (a sha, `HEAD~1`, `scratch`) falls back to the checked-out branch's task, and
    when that names none either and the destination is protected the push is
    unverifiable (None). The destination's own task is needed as well, so
    `HEAD:bugfix/task-0200` needs both."""
    trunk = push_trunk_names()
    protected = push_protected_branches()
    head_task = task_from_branch(branch)
    tasks = []
    for src, dst in push_pairs(command, branch, local) or []:
        found = []
        if src not in trunk or dst not in protected:
            src_task = task_from_branch(src) or head_task
            if not src_task and dst in protected:
                return None
            found.append(src_task)
        found.append(task_from_branch(dst))
        for task in found:
            if task and task not in tasks:
                tasks.append(task)
    return tasks


# --- Publishing the trunk in solo mode (approve.py --trunk-push) ------------
# In `solo` mode the trunk is written by the gated local merge, so the only way
# its commits reach the remote is a push OF the trunk - which this gate refused
# unconditionally, leaving a solo project's remote permanently stale (task-0092,
# measured at 55 commits behind). The refusal's stated reason ("a pull request
# reaches the trunk") does not exist in solo mode.
#
# What it needs is what every push needs: the CEO's explicit approval, recorded
# where the gate can read it. The marker is ONE-SHOT - the push that it allows
# deletes it - so an approval covers a push rather than a mode. In `pr` mode the
# marker means nothing: approve.py refuses to write one, and this reader refuses
# to honour one that exists anyway.
def trunk_push_marker_path() -> Path:
    """The one-shot trunk-push approval for this lane. A function, not a
    constant: the tests repoint state.STATE_DIR. Suffixed exactly as state.py
    suffixes run.db / mode / approvals - one conveyor per lane means one
    approval per lane."""
    return state.STATE_DIR / f"trunk_push{state.LANE_SUFFIX}"


TRUNK_PUSH_DEFAULT_REMOTE = "origin"


def trunk_push_remote(target: str) -> str | None:
    """The remote the CEO approved publishing `target` to from this lane, or None
    when no approval stands. A marker written before the remote was recorded names
    none, which reads as the default remote.

    Fail-CLOSED on every doubt: wrong mode, missing marker, unreadable or
    malformed JSON, a marker naming a different trunk, or a remote that is not a
    non-empty string all read as "not approved". `main` and `master` only - a push
    to staging or production is not what solo mode leaves unreachable."""
    if target not in PUSH_PROTECTED_ALWAYS or workflow_mode() != WORKFLOW_SOLO:
        return None
    try:
        data = json.loads(trunk_push_marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or str(data.get("trunk") or "") != target:
        return None
    remote = data.get("remote", TRUNK_PUSH_DEFAULT_REMOTE)
    return remote if isinstance(remote, str) and remote.strip() else None


def trunk_push_approved(target: str, remote: str | None) -> bool:
    """True when the CEO approved publishing `target` to `remote` from this lane.
    `remote` is what the push names (push_remote); a push that names none cannot
    be matched to the approval and is not covered by it."""
    approved = trunk_push_remote(target)
    return approved is not None and remote == approved


def allow_push_to_main() -> bool:
    """pipeline.json `allow_push_to_main`: a project-level decision to let a plain
    push reach the trunk without the one-shot marker. Default false, and only a
    literal `true` turns it on - a typo, a string or an unreadable config must
    not open the remote, the same reading push_needs_approval() gives its key.
    It never covers force or delete pushes, and never an extra protected branch."""
    return state.load_pipeline().get("allow_push_to_main", False) is True


def consume_trunk_push_marker() -> bool:
    """Spend the approval on the push it allowed: one push per approval.

    The marker is renamed to a unique spent name (os.replace is atomic), so two
    pushes racing for one approval cannot both win: the loser finds no marker. True
    only when THIS call took the marker out of the way. False on any failure (the
    marker already gone, or a rename that failed), and the caller must then deny:
    an unlink that failed silently left a one-shot approval standing for the next
    push (task-0120 SEC-B M1)."""
    marker = trunk_push_marker_path()
    spent = marker.with_name(f"{marker.name}.spent-{uuid.uuid4().hex}")
    try:
        os.replace(marker, spent)
    except OSError:
        return False
    try:
        spent.unlink()  # tidy only; the approval is gone either way
    except OSError:
        pass
    return True


def leak_check(ref: str, repo) -> tuple:
    """leak_guard.check(ref, repo): (0, []) clean, (1, leaked paths), (2, [error]).
    One seam, so the import itself sits inside the caller's fail-closed handler and
    a test can stand in for the git lookups."""
    import leak_guard

    return leak_guard.check(ref, repo, deadline=_push_deadline)


def push_repo(command: str, cwd: str) -> str | None:
    """The working tree the push runs in: the first repo_candidates() entry git
    accepts as one, or None when none is."""
    for repo in repo_candidates(command, cwd):
        try:
            if not Path(repo).is_dir():
                continue
            proc = git_run(["git", "rev-parse", "--is-inside-work-tree"], repo)
            if proc.returncode == 0 and proc.stdout.strip() == "true":
                return str(repo)
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return None


LEAK_PATHS_SHOWN = 10


def implicit_push_remote(command: str, cwd: str, branch: str) -> str | None:
    """The remote a push that names none goes to, in git's own order:
    branch.<b>.pushRemote, remote.pushDefault, branch.<b>.remote, else origin.
    None when git cannot be asked, which the caller treats as not matching."""
    repo = push_repo(command, cwd)
    if repo is None:
        return None
    keys = [f"branch.{branch}.pushRemote", "remote.pushDefault", f"branch.{branch}.remote"]
    try:
        for key in keys:
            proc = git_run(["git", "config", "--get", key], repo)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
            if proc.returncode not in (0, 1):  # 1 is "key not set"
                return None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return TRUNK_PUSH_DEFAULT_REMOTE


def push_leak_reason(command: str, cwd: str, sources: list) -> str:
    """Why this push may not go out because of what it would publish (task-0041
    leak guard), or '' when every pushed source is clean. Fail-CLOSED: the guard
    raising, answering anything but 0, or the repo being unfindable all read as a
    leak, because the caller would otherwise allow on an exception."""
    try:
        repo = push_repo(command, cwd)
        if repo is None:
            return "the repository this push runs in could not be found"
        for ref in dict.fromkeys(src for src in sources if src):
            code, lines = leak_check(ref, repo)
            if code == 0:
                continue
            shown = ", ".join(str(line) for line in lines[:LEAK_PATHS_SHOWN])
            if code == 1:
                import leak_guard

                return (f"'{ref}' would publish project-internal files that {leak_guard.base_ref()} "
                        f"does not have: {shown}")
            return f"'{ref}' could not be checked ({shown})"
        return ""
    except PushBudgetExpired:
        return f"the push checks did not finish within their {PUSH_BUDGET:g}s time budget"
    except Exception as exc:  # any failure here must deny, not allow
        return f"the check itself failed ({type(exc).__name__})"


def check_push_target(command: str, branch: str, local: list | None = None,
                      cwd: str = "", orch: bool = True) -> tuple:
    """(verdict, spend) - deny a push landing on any protected branch, in every
    spelling the argv resolver can see: `origin main`, `HEAD:main`, `+main`,
    `--force origin main`, `feature:main`, and the refspec-less form while
    sitting on a protected branch. The one exception is the approved trunk push
    above, and `spend` is true when this push may consume that approval. The
    approval names the remote it was given for, so the same ref sent to another
    remote or a URL is a different push.

    A push that passes all of that is then checked by the leak guard, whatever
    branch it publishes (task-0041).

    The marker is NOT consumed here: handle_bash can still refuse the command
    afterwards (wrong branch, no run row, task push unapproved), and an approval
    burned by a push that never happened is an approval the CEO has to give
    twice. The caller spends it only on the way out through allow().

    `orch` is False for a subagent payload. The marker belongs to the orchestrator
    (the CEO approved the ORCHESTRATOR's push), and nothing binds it to a caller, so
    a subagent that pushed the trunk would spend it. Subagents never push the trunk:
    that is denied before the marker, allow_push_to_main or the remote is read."""
    protected = push_protected_branches()
    for args in (a for sub, a in git_invocations(command) if sub == "push"):
        closer_ref = push_ref_with_closer(args)
        if closer_ref is not None:
            return deny(
                f"This push names '{closer_ref}', a remote or ref that holds a shell "
                f"closer (a parenthesis, a backtick or $( ). Git allows those in a branch "
                f"name, but the gate cannot tell such a name from the end of a command "
                f"substitution, so it refuses rather than cut the ref and guess where it "
                f"lands. Push a branch whose name has none of them: git push -u origin "
                f"<type>/task-<id>."), False
    triples = push_remote_pairs(command, branch, local)
    if triples is None:
        return deny(
            "This push cannot be resolved to a target branch, so the gate refuses "
            "rather than guessing (an unresolvable push gates). Run git push "
            "directly instead of wrapping it in another shell, and name the branch: "
            "git push -u origin <type>/task-<id>. The trunk is reached by a pull "
            "request in 'pr' mode or by an approved local merge in 'solo' mode, "
            "never by a push."), False
    destructive = None
    for args in (a for sub, a in git_invocations(command) if sub == "push"):
        destructive = destructive or push_destructive_reason(args)
    approved = False
    for remote, _src, name in triples:
        if name not in protected:
            continue
        # A subagent has no approval to use: neither the marker nor allow_push_to_main
        # is read for it, and the marker is left as the CEO recorded it.
        subagent_trunk = not orch and name in push_trunk_names()
        if remote is None and not destructive and not subagent_trunk and trunk_push_remote(name):
            remote = implicit_push_remote(command, cwd, branch)
        if not destructive and not subagent_trunk and trunk_push_approved(name, remote):
            approved = True
            continue
        if (not destructive and not subagent_trunk and name in push_trunk_names()
                and allow_push_to_main()):
            continue
        approved_remote = None if destructive or subagent_trunk else trunk_push_remote(name)
        return deny(
            f"Push to protected branch '{name}' is forbidden (protected: "
            f"{', '.join(sorted(protected))}) in every workflow mode. Push your "
            f"work branch instead - '{name}' is reached by a pull request in 'pr' "
            f"mode or by an approved local merge in 'solo' mode, never by a push. "
            f"See .claude/rules/git-workflow.md."
            + (" A subagent never pushes the trunk, approved or not: the approval is the "
               "CEO's for the orchestrator's one push and is left unspent. Report to the "
               "orchestrator instead." if subagent_trunk else "")
            + (f" An approved trunk push cannot cover this one: {destructive}, and "
               f"publishing the trunk is neither rewriting nor deleting it - the "
               f"approval is left unspent." if destructive else "")
            + (f" The trunk-push approval on file is for the remote '{approved_remote}', "
               f"and this push goes to {remote or 'a remote the gate could not resolve'}; "
               f"it is left unspent. Name that remote in the command, or record a new "
               f"approval with --remote." if approved_remote else "")), False
    # A push naming an approved trunk AND an unapproved protected branch is
    # denied above, and must not have burned the approval on the way out.
    leak = push_leak_reason(command, cwd, [src for _, src, _ in triples])
    if leak:
        return deny(
            f"Push refused by the leak guard: {leak}. A real project's memory, overlay "
            f"and task files must never reach the public repository, and a check that "
            f"cannot run counts as a leak. See .claude/rules/git-workflow.md."), False
    return allow(), approved


def repo_bootstrap(command: str, cwd: str, main_branch: str) -> bool:
    """FR-1: true when the repo has no commits yet (unborn HEAD), or the
    configured main branch does not exist yet - there is no main to cut a
    task branch from, so the branch-naming/base rule cannot apply. Fail-open
    means "no exemption" here: any git error falls back to the existing,
    unchanged branch-gate behavior rather than silently granting a bypass."""
    for repo in repo_candidates(command, cwd):
        try:
            if not Path(repo).is_dir():
                continue
            # A non-zero exit from `rev-parse --verify HEAD` means EITHER
            # "unborn HEAD" OR "not a git repository at all" - the two are
            # indistinguishable from that command alone. Confirm this really
            # is a repo first; if not, fall through to the next candidate
            # (same fall-through discipline as current_branch/staged_files),
            # instead of granting a bootstrap exemption for a path that has
            # no repo here (e.g. the workspace root in a multi-repo layout).
            is_repo = git_run(["git", "rev-parse", "--is-inside-work-tree"], repo)
            if is_repo.returncode != 0 or is_repo.stdout.strip() != "true":
                continue
            head = git_run(["git", "rev-parse", "--verify", "-q", "HEAD"], repo)
            if head.returncode != 0:
                return True  # unborn HEAD - zero commits
            branch = git_run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{main_branch}"], repo)
            return branch.returncode != 0
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return False


# --- Contract C-2: planning-and-documentation path set (FR-21, FR-22) ---
# The work product moved to .agentry/ in FR-13. The pre-move spellings under
# .claude/ were carried here alongside these so the exemption survived the move;
# they are gone now that it has landed, because those paths no longer exist - a
# write to one is a stray, not bookkeeping, and must not be exempt.
C2_PREFIXES = (
    ".agentry/plans/",
    ".agentry/specs/",
    ".agentry/tasks/",
    "docs/",
)
C2_ROOT_MD_RE = re.compile(r"^[^/]+\.md$", re.IGNORECASE)


def matches_c2(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    if C2_ROOT_MD_RE.match(p):
        return True
    return any(p.startswith(prefix) for prefix in C2_PREFIXES)


def staged_files(command: str, cwd: str) -> list:
    for repo in repo_candidates(command, cwd):
        try:
            if not Path(repo).is_dir():
                continue
            # --no-renames: without it, git prints only the post-image path
            # for a detected rename (e.g. `git mv real/code.py .agentry/plans/x.md`
            # shows as one line, `.agentry/plans/x.md`), which would let renamed
            # code slip through the C-2 exemption undetected (FR-22).
            proc = git_run(["git", "diff", "--cached", "--name-only", "--no-renames"], repo)
            if proc.returncode == 0:
                return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return []


def commit_uses_dash_a(command: str) -> bool:
    """git commit -a / -am / --all stages every dirty tracked file at commit
    time, after the index was already inspected - an unrelated dirty code file
    would ship silently through the C-2 exemption. Chosen fix: refuse the
    exemption outright for -a/-am/--all rather than diff against `git diff
    HEAD --name-only`, because that HEAD-diff already includes everything the
    plain index check does PLUS every unstaged tracked change, i.e. it is just
    a more expensive way to reach "not all C-2" - refusing directly is the
    same outcome with less surface. Tokenized via git_invocations() so a
    flag-looking substring inside a quoted commit message isn't mistaken for a
    real flag, and a global option can't hide the subcommand.

    KNOWN LIMITATION - this check is textual and NOT total. It only sees flags
    present in the command itself. An `-a` that arrives through git config or an
    alias (`git -c alias.ci='!git commit -a' ci`, or an `[alias]` entry in
    .gitconfig) is invisible here, so such a commit could still be granted the
    C-2 exemption. Resolving aliases would mean shelling out to `git config` per
    repo on every hook call; the trade was judged not worth it. Do not assume
    this function catches every `-a` commit."""
    for sub, args in git_invocations(command):
        if sub == GIT_UNKNOWN:
            return True  # unparseable git text - refuse the exemption
        if sub != "commit":
            continue
        for tok in args:
            if tok == "--":
                break
            if tok == "--all":
                return True
            if tok.startswith("-") and not tok.startswith("--") and "a" in tok[1:]:
                return True
    return False


def planning_only_commit(command: str, cwd: str) -> bool:
    """FR-21/FR-22: the exemption applies only when every staged path matches
    C-2 - all-or-nothing by construction, so one path outside the set
    disqualifies the whole commit. No staged files means nothing to exempt.
    A `-a`/`-am`/`--all` commit never qualifies (see commit_uses_dash_a)."""
    if commit_uses_dash_a(command):
        return False
    files = staged_files(command, cwd)
    return bool(files) and all(matches_c2(f) for f in files)


# The forbidden target reached by a REAL redirect operator: `>`, `>>`, `2>`,
# `&>`, `<`, with or without a space before the target, and with the target
# optionally quoted. Not `in command`: that read the literal inside a commit
# message, a heredoc body or a prose argument as the act itself (task-0078).
# The lookbehind keeps arrows (`->`, `-->`, `=>`) out; a word glued to the
# operator (`echo hi>/dev/null`) is still a redirect and still matches.
DEV_NULL_REDIR_RE = re.compile(
    r"(?<![-<>=])\d*[<>&]{0,2}[<>]\s*['\"]?/dev/null['\"]?(?![\w./-])", re.IGNORECASE)


def redirects_to_dev_null(command: str, _depth: int = 0) -> bool:
    """True when `command` really redirects to the forbidden target.

    Heredoc bodies are dropped first, and a match is kept only when the
    OPERATOR sits outside quotes - mask_quoted() preserves length, so the
    masked string can be indexed by the match offset in the original. That is
    what separates `ls 2>/dev/null` (a redirect) from
    `memory.py --fix "never write 2>/dev/null"` (a sentence about one), while a
    quoted TARGET (`> '/dev/null'`) stays caught because its operator is bare.

    Nested bodies are then scanned recursively, the same layering
    redirect_write_target() and git_invokes() carry. Without it the quoting
    awareness above WAS the bypass: `bash -c 'ls >/dev/null'` has its operator
    inside a quoted span, so every match was discarded and the wrapped form was
    allowed where the bare one is denied."""
    text = strip_heredocs(command)
    masked = mask_quoted(text)
    if any(masked[m.start()] == text[m.start()]
           for m in DEV_NULL_REDIR_RE.finditer(text)):
        return True
    if unscannable_depth(command, _depth):
        return True
    return any(redirects_to_dev_null(body, _depth + 1)
               for body in nested_command_bodies(command))


def check_piped_test_suite(command: str) -> int:
    """H: deny piping a test-suite run into a pager/filter (task-0082)."""
    if not gates_cfg().get("forbid_piped_test_suite"):
        return allow()
    sink = piped_test_suite_sink(command)
    if not sink:
        return allow()
    return deny(
        f"Piping a test-suite run (unittest discover / pytest) into '{sink}' is denied "
        f"(gates.forbid_piped_test_suite). In Git Bash on Windows this deadlocks: a test "
        f"child can inherit the pipe's write end, so '{sink}' never sees EOF and blocks past "
        f"any timeout, holding a live process behind it. Run the suite unpiped and let the "
        f"output through, capture it instead (`out=$(cmd 2>&1)`), or redirect it into a file "
        f"under the project's tmp/.")


def check_destructive_and_repl(command: str, low: str) -> int:
    """A + B + G, all data-driven from pipeline.json 'gates' so no project/stack
    scope leaks into the code."""
    cfg = gates_cfg()

    if cfg.get("forbid_dev_null"):  # B
        operand = eval_redirect(command)
        if operand:
            return deny(
                f"`eval` carries a redirect operator in a string this gate cannot "
                f"resolve ('{operand}'). The target of an eval'd redirect is only "
                f"known once it runs, so the redirect policy (gates.forbid_dev_null) "
                f"cannot be checked and the gate refuses rather than guessing. Write "
                f"the command out instead of eval'ing it.")
    if cfg.get("forbid_dev_null") and redirects_to_dev_null(command):  # B
        return deny("Redirect to /dev/null is denied by project policy "
                    "(gates.forbid_dev_null). The reason once given on this deny was "
                    "wrong and has been removed: measured on this host 2026-09-14 under "
                    "git-bash MINGW64, /dev/null IS a real character device here, the "
                    "output is discarded and NO file is created. The deny stands anyway - "
                    "it is the CEO's standing rule and only he relaxes it. `>NUL` / "
                    "`2>NUL` is NOT the fix, and is the spelling that genuinely breaks: "
                    "measured the same day, `ls missing 2>NUL` in an empty directory "
                    "leaves an entry named NUL holding the 63 bytes of output that was "
                    "meant to vanish, which Windows then refuses to unlink by name. Drop "
                    "the redirect and let the output through. If you genuinely must "
                    "suppress it, capture it instead - `out=$(cmd 2>&1)` - or redirect "
                    "into a file under the project's tmp/.")

    allow_if = [str(s).lower() for s in cfg.get("destructive_allow_if", [])]  # A
    for pat in cfg.get("destructive_command_patterns", []):
        try:
            if re.search(pat, command, re.IGNORECASE):
                if any(a in low for a in allow_if):
                    return allow()
                return deny(f"Destructive command blocked by project policy (matched '{pat}'): "
                            f"it can irreversibly wipe data. Use the safe, additive alternative "
                            f"for this stack. See .claude project rules.")
        except re.error:
            continue

    kw = str(cfg.get("repl_write_keyword", "")).lower()  # G
    if kw and kw in low:
        for pat in cfg.get("repl_write_patterns", []):
            try:
                if re.search(pat, command, re.IGNORECASE):
                    return deny("Write operation inside the live REPL hits the real dev "
                                "database and persists junk. Use a rolled-back test or "
                                "read-only queries instead. See .claude testing rules.")
            except re.error:
                continue
    return allow()


def check_commit_attribution(command: str) -> int:
    """D: no AI-authorship trailer in commit messages."""
    if not git_invokes(command, "commit"):
        return allow()
    if AI_ATTRIB_RE.search(command):
        return deny("Commit message carries an AI-authorship trailer (Co-Authored-By / "
                    "Generated by / AI-assisted). Remove it - no AI attribution in commits, ever.")
    return allow()


def has_forbidden_dash(text: str) -> bool:
    return any(d in text for d in EM_EN_DASH)


def under_home_claude(file_path: str) -> bool:
    """H: writes under the runtime home ~/.claude/ (never the project's own
    .claude/) store project data outside the repo - forbidden. Exception:
    ~/.claude/plans/ is the harness's own plan-mode scratch area (Claude Code
    forces the plan file there); denying it bricks plan mode, and the final
    plan is copied into <project>/.agentry/plans/ anyway."""
    if is_unc(file_path):
        return False
    try:
        p = Path(file_path).resolve()
        home_claude = (Path.home() / ".claude").resolve()
        if p == home_claude or home_claude in p.parents:
            if (home_claude / "plans") in p.parents:
                return False  # harness plan-mode scratch file - allowed
            # allow if it is actually the project's own .claude (repo under home)
            root_claude = (state.ROOT / ".claude").resolve()
            return not (p == root_claude or root_claude in p.parents)
        return False
    except (OSError, ValueError):
        return False


def active_review_run(conn) -> dict | None:
    """The 'review' stage is read-only: the reviewer analyzes the tree, so a code
    edit underneath it would invalidate the findings it is producing."""
    for r in state.all_runs(conn):
        if (r["stage"] == "review" and r["stage_status"] != state.ST_BLOCKED
                and not state.is_paused(r)):
            return r
    return None


def is_bookkeeping(path: str) -> bool:
    # .agentry/ carries the work product since FR-13 (tasks, plans, specs,
    # state); it is bookkeeping for the same reason .claude/ is.
    # Same question as orch_allowed_path(), so the same answers: README files and
    # the root CLAUDE.md are the documents the docs profile owns, and `..` is
    # resolved first so `docs/../src/a.py` cannot ride the docs/ prefix.
    p = normalize(path)
    return (any(p.startswith(x) or "/" + x in p for x in ORCH_ALLOW_PREFIXES)
            or bool(README_RE.search(p)) or is_root_claude_md(path))


def handoff_freeze_task() -> str:
    """Hard edit gate (config flag handoff.hard_edit_gate): while a completed
    task lacks its handoff doc AND no task is mid-stage, code edits are frozen -
    the debt must be paid first. A task already in an editing stage keeps
    working (it registered before the debt appeared). Lazy import + fail-open:
    '' means no freeze."""
    try:
        import handoff
        if not handoff.cfg().get("hard_edit_gate"):
            return ""
        pipeline = state.load_pipeline()
        conn = state.connect()
        try:
            editing = any(r["stage"] in state.editing_stages(pipeline, state.run_pipeline(r))
                          and r["stage_status"] != state.ST_BLOCKED
                          and not state.is_paused(r)
                          for r in state.all_runs(conn))
        finally:
            conn.close()
        if editing:
            return ""
        debt = handoff.uncovered_done_tasks()
        return debt[0]["task"] if debt else ""
    except Exception:
        return ""


# --- per-task plan approval (task-0045) ---------------------------------------
# The build flow's first steps are `plan` (the owning agent writes what it intends
# to change and why) and `plan-approval` (the CEO's checkpoint). Until the plan
# approval is recorded in run.db, nothing outside .agentry/ may be written for
# that task: the commit gate cannot stand in for it, because by the time a commit
# is attempted every stage has already run. Edit and Write are denied here, the
# way a commit is denied without its approval, and so are the shell spellings of
# a file write the gate can read. Interpreter-written files (`python -c
# "open(...).write(...)"`) are not seen - the stated limit shared with every
# other command check in this file.
#
# Read from config, never from a stage name: a flow with no stage that declares
# the `plan` checkpoint has nothing pending, so a pipeline.json written before
# this step existed behaves exactly as it did. Fail-open on any error (NFR-4).
PLAN_CHECKPOINT = "plan"
PLAN_WRITE_PREFIX = ".agentry/"
PLAN_GIT_WRITERS = frozenset({"restore", "reset", "clean", "rebase"})
PLAN_GIT_STASH_ARGS = ("pop", "apply", "drop")
PLAN_PATH_COMMANDS = frozenset({"rm", "mv", "cp", "mkdir", "ln", "chmod", "truncate"})


def plan_checkpoint_index(pipeline: dict, which: str = state.BUILD) -> int | None:
    """Position of the first stage of `which` that declares the plan checkpoint."""
    for i, stage in enumerate(state.stages(pipeline, which)):
        declared = stage.get("checkpoints")
        if isinstance(declared, list) and PLAN_CHECKPOINT in {str(c).lower() for c in declared}:
            return i
    return None


def plan_pending(run: dict | None, pipeline: dict) -> bool:
    """True for a build-flow run that has not yet passed its plan checkpoint: it
    stands at or before the stage declaring it and the approval is not recorded.
    A run past the stage, a run on another flow, and a config without the stage
    are all False. The plan flow keeps its own `approval` stage and its own
    parking in advance.py."""
    if not run or run.get("plan_approved") or state.run_pipeline(run) != state.BUILD:
        return False
    gate = plan_checkpoint_index(pipeline, state.BUILD)
    names = state.stage_names(pipeline, state.BUILD)
    stage = run.get("stage")
    return gate is not None and stage in names and names.index(stage) <= gate


def plan_pending_tasks() -> list[str]:
    """Every task whose plan approval is still owed. [] when the config has no plan
    step (answered before the run store is opened) and on any error."""
    try:
        pipeline = state.load_pipeline()
        if plan_checkpoint_index(pipeline) is None:
            return []
        conn = state.connect()
        try:
            runs = state.all_runs(conn)
        finally:
            conn.close()
        return [r["task"] for r in runs if plan_pending(r, pipeline)]
    except Exception:
        return []


def plan_pending_task(branch: str, pending: list[str] | None = None) -> str:
    """The task `branch` belongs to when its plan approval is owed, else ''."""
    task = task_from_branch(branch)
    owed = plan_pending_tasks() if pending is None else pending
    return task if task and task in owed else ""


def plan_write_allowed(path: str) -> bool:
    """Whether a file may be written while a plan is unapproved: only under the
    project's .agentry/ (plans, task files, specs) and the harness plan scratch
    ~/.claude/plans/. `.claude/` and docs/ are NOT on the list - in this
    repository the code itself lives under .claude/tools."""
    if is_unc(path):
        return False
    try:
        p = Path(path)
        if not p.is_absolute():
            p = state.ROOT / p
        p = p.resolve()
        try:
            rel = p.relative_to(state.ROOT.resolve()).as_posix().lower()
        except ValueError:
            home_plans = (Path.home() / ".claude" / "plans").resolve()
            return p == home_plans or home_plans in p.parents
        return rel.startswith(PLAN_WRITE_PREFIX)
    except (OSError, ValueError):
        return True  # fail-open


def plan_blocked_task(path: str = "", command: str = "", cwd: str = "") -> str:
    """The task whose unapproved plan forbids a write on the checked-out branch,
    else ''. A file edit is placed by the repository holding the file (its nearest
    existing parent), a shell command by the usual cwd / -C / cd resolution."""
    try:
        pending = plan_pending_tasks()
        if not pending:
            return ""
        if path:
            if not is_unc(path):  # a network path is never probed (see UNC_RE): the caller's cwd stands
                here = Path(path) if Path(path).is_absolute() else state.ROOT / path
                while not here.is_dir() and here != here.parent:
                    here = here.parent
                cwd = str(here)
        return plan_pending_task(current_branch(command, cwd), pending)
    except Exception:
        return ""


def plan_operands_allowed(segment: list, hit: str) -> bool:
    """Whether a path-operand command (`hit` is its name) writes only where the
    plan phase allows: it has at least one operand and every operand passes
    plan_write_allowed(). `rm` of the bare Windows device names `nul` / `NUL` in
    any case is always allowed, because the session start and end cleanup runs
    it. An operand the shell would expand (`$`, backtick) is not judged by its
    text, and a command with no operand (`xargs rm`) takes its paths from stdin."""
    if hit not in PLAN_PATH_COMMANDS:
        return False
    i = argv0_index(segment)
    operands = [t for t in segment[i + 1:] if not t.startswith("-")] if i is not None else []
    if not operands or any("$" in t or "`" in t for t in operands):
        return False
    if hit == "rm" and all(t.lower() == "nul" for t in operands):
        return True
    return all(plan_write_allowed(t) for t in operands)


def plan_mutating_command(command: str, _depth: int = 0) -> str:
    """A file-mutating simple command (rm, mv, cp, mkdir, ln, chmod, truncate,
    installers, and git commands that rewrite tracked files), nested shell
    bodies included, else ''. `tee` and `sed -i` are judged by the caller, which
    knows their targets. The command list is agent_gate's, imported lazily
    because agent_gate imports this module."""
    try:
        import agent_gate
    except Exception:
        return ""
    for segment in command_segments(command) or []:
        hit = agent_gate.mutating_command(segment)
        if hit and hit not in ("tee", "sed -i") and not plan_operands_allowed(segment, hit):
            return hit
    for sub, args in git_invocations(command):
        if sub in PLAN_GIT_WRITERS or (sub == "checkout" and "--" in args):
            return f"git {sub}"
        if sub == "stash" and args and args[0].lower() in PLAN_GIT_STASH_ARGS:
            return f"git stash {args[0].lower()}"
    if unscannable_depth(command, _depth):
        return DEPTH_EXCEEDED
    for body in nested_command_bodies(command):
        hit = plan_mutating_command(body, _depth + 1)
        if hit:
            return hit
    return ""


def plan_bash_write(command: str) -> str:
    """The fragment of a Bash command that writes a file outside the plan-phase
    allowance, else ''. Answered before anything is parsed or imported when the
    config has no plan step: nothing is pending then (plan_pending_tasks)."""
    try:
        if plan_checkpoint_index(state.load_pipeline()) is None:
            return ""
    except Exception:
        return ""  # fail-open, like plan_pending_tasks
    for frag in redirect_write_fragments(command):
        if frag == DEPTH_EXCEEDED or not plan_write_allowed(redirect_target(frag).strip("'\"")):
            return frag
    m = ORCH_TEE_RE.search(command)
    if m and not plan_write_allowed(m.group("t")):
        return m.group(0)
    if ORCH_SED_RE.search(command):
        return "sed -i"
    if ORCH_PATCH_RE.search(command):
        return "patch / git apply"
    return plan_mutating_command(command)


def plan_deny(task: str, what: str) -> int:
    return deny(f"{task} is at its plan step and the CEO has not approved the plan, so nothing "
                f"outside .agentry/ may be written yet ({what}). Write the plan to "
                f".agentry/plans/<date>-<slug>-{task}.md, run advance.py, and wait for the "
                f"approval (approve.py --task {task} --gate plan). Rejecting the plan costs "
                f"nothing but the plan.", human=human_notice("plan", task=task))


TOO_COMPLEX_MSG = (
    "This command is too complex to check: it nests more shells, command substitutions "
    "and quotes than the gate scans (a budget of {budget} characters of work). The gate "
    "cannot tell what it runs, so it refuses rather than guessing. Split it into simpler "
    "commands, or write the script to a file with Write/Edit and run that.")


def handle_bash(command: str, cwd: str = "", orch: bool = False) -> int:
    # Before anything that scans: a 300-character command used to outlast the hook
    # timeout (task-0112), and a hook that times out is an allow.
    if too_complex(command):
        return deny(TOO_COMPLEX_MSG.format(budget=SCAN_BUDGET))
    # A command that mentions git runs under the push time budget from its first git
    # call: the branch-base check, the bootstrap check, the staged-files read and the
    # push checks all draw on it, so none of them can outlast the hook timeout, which is
    # an allow. The test is the git word alone: a subcommand spelled `pu""sh` or hidden
    # behind an alias would slip past any word list, and the cost of over-arming is only
    # that a slow git call denies where it would have waited.
    if MENTIONS_GIT_RE.search(command):
        start_push_budget()
    try:
        low = " ".join(command.lower().split())

        # Parse failure gates, but it must say WHY: this used to fall through to the
        # commit/push block and deny an unparseable heredoc with "Commit for task-XXXX
        # is not approved yet", which names the wrong cause entirely.
        if git_unparseable(command):
            return deny(UNPARSEABLE_GIT_MSG)

        for check in (
            check_unattended(command),                 # supervisor-spawned session
            check_branch_creation(command, cwd),      # 4 (naming) + C (base)
            check_destructive_and_repl(command, low),  # A + B + G
            check_bare_nul(command),                    # NUL is a real file
            check_piped_test_suite(command),            # H
            check_commit_attribution(command),         # D
            orch_check_bash(command) if orch else allow(),  # orchestrator profile
        ):
            if check != 0:
                return check

        # A write while the plan is unapproved. Text first (cheap), run store second.
        written = plan_bash_write(command)
        if written:
            blocked = plan_blocked_task(command=command, cwd=cwd)
            if blocked:
                return plan_deny(blocked, written)

        is_commit = git_invokes(command, "commit")
        is_push = git_invokes(command, "push")
        # Not git_invokes(command, "merge"): its substring safety net would match
        # `git merge-base --is-ancestor main HEAD`, the read-only base check every
        # task runs. Argv resolution tells the two subcommand tokens apart.
        # A pull merges too, and carries no `merge` token: without it here a
        # `git pull . <branch>` on the trunk never reaches check_trunk_merge().
        is_merge = bool(merge_invocations(command) or pull_invocations(command))
        if not (is_commit or is_push or is_merge):
            return allow()

        unc = unc_operand(command, cwd)
        if unc:
            return deny(
                f"This git command names a network path ({unc}). The gate never contacts a "
                f"host the command names - that can stall the hook past its timeout, and a "
                f"timeout is an allow - so it refuses rather than look. Run the command in "
                f"a local clone instead.")

        pipeline = state.load_pipeline()
        main_branch = str(pipeline.get("main_branch", "main"))
        if main_branch.startswith("{{"):
            main_branch = "main"

        spend_trunk_push = False
        # FR-1: checked before current_branch() - a repo with no commits has an
        # unborn HEAD, which makes `git rev-parse --abbrev-ref HEAD` fail there and
        # would otherwise fall through repo_candidates() to an unrelated repo.
        if is_commit and repo_bootstrap(command, cwd, main_branch):
            return allow()
        branch = current_branch(command, cwd)
        local = local_branches(command, cwd) if is_push else None
        if is_push:
            verdict, spend_trunk_push = check_push_target(command, branch, local, cwd, orch)
            if verdict != 0:
                return verdict

        def finish() -> int:
            """Allow, spending the trunk-push approval if this push earned it. Every
            deny between here and the end leaves the marker for the push the CEO
            actually approved."""
            # Spent BEFORE the allow, and a spend that fails is a deny: allowing on a
            # marker that could not be taken out of the way would leave the one-shot
            # approval standing for the next push (task-0120 SEC-B M1).
            if spend_trunk_push and not consume_trunk_push_marker():
                return deny(
                    f"The trunk-push approval ({trunk_push_marker_path().name}) could not be "
                    f"spent, so this push is refused: it was already spent by another push, or "
                    f"the state directory is not writable. Ask the CEO to record a new "
                    f"approval (approve.py --trunk-push) once the cause is fixed.")
            return allow()

        # A merge onto a protected branch commits there, which is exactly what the
        # trunk rule refuses - unless solo mode allows it for an approved task. A
        # merge while HEAD is a work branch (pulling main into a feature branch) is
        # untouched, as is a merge the gate cannot place (branch unreadable).
        if is_merge and branch in push_protected_branches():
            verdict = check_trunk_merge(command, branch)
            if verdict != 0:
                return verdict

        # A commit is judged by the checked-out branch; a push by the refs it pushes
        # (task-0111). A merge-only command needs no run row: check_trunk_merge above
        # is the whole check.
        head_task = task_from_branch(branch)
        planning = False
        if is_commit and not head_task:
            if not planning_only_commit(command, cwd):  # FR-21, FR-22
                return deny("Not on a feature branch (no task-XXXX). Create the task branch first "
                            "per .claude/rules/git-workflow.md.")
            # The exemption is for the commit only. A push chained to it still goes
            # through the checks below and still spends the trunk marker (task-0113).
            planning = True
        # A commit is judged as the task its message tags (task-0059), the branch's
        # task only when it carries no tag. A tag naming another task is a request to
        # borrow that task's approval, so it is checked below (R3).
        commit_ts = ([t for t in commit_tasks(command, branch, cwd) if t]
                     if is_commit and not planning else [])
        push_tasks = pushed_tasks(command, branch, local) if is_push else []
        if push_tasks is None:
            return deny(
                "This push sends a ref that names no task (a sha, HEAD~n or an untracked "
                "branch) to a protected branch, so there is no task approval to check it "
                "against. Push the task branch by name, merge it first, or publish the "
                "trunk itself: git push origin main.")
        borrowed = []
        for task in dict.fromkeys([t for t in [*commit_ts, *push_tasks] if t]):
            tagged_elsewhere = task in commit_ts and task != head_task
            # The commit and push checks fail CLOSED, following check_merge_source()
            # rather than inventing a second style for the same question. Both failure
            # modes below were silent exit 0 before, and a lane mismatch (the session
            # that registered the task is not the session committing it) reaches the
            # second one as an ordinary, expected event - not an exotic one.
            try:
                conn = state.connect()
                try:
                    run = state.get_run(conn, task)
                finally:
                    conn.close()
            except Exception:
                return deny(
                    f"The pipeline run state ({state.DB_PATH}) could not be read, so the "
                    f"checkpoint approval for {task} cannot be verified. This path fails "
                    f"closed on purpose. Check PIPELINE_LANE"
                    f"{f'={state.LANE!r}' if state.LANE else ' (unset)'} and that the state "
                    f"directory is writable.")
            if tagged_elsewhere:
                # R3: the tag must not let a commit pick another task's approval. The
                # branch and the tag disagree, so say so, and accept the tag only for a
                # task that is registered, unfinished, and declares (`branch:` in its
                # frontmatter, frozen into the run row at registration - the field
                # merge detection already resolves) that it rides on THIS branch.
                clash = (f"This commit is tagged [{task}] but the checked-out branch "
                         f"'{branch}' belongs to {head_task}.")
                if run is None:
                    return deny(f"{clash} {task} has no run row in {state.DB_PATH}, so there is no "
                                f"approval to judge the commit by. Tag the commit with {head_task}, "
                                f"or register {task} first.")
                if run["stage"] == "done":
                    return deny(f"{clash} {task} is already done, so it cannot approve a new "
                                f"commit. Tag it with {head_task}.")
                if state.task_branch(task) != branch:
                    return deny(f"{clash} {task} did not declare, when it was registered, that it "
                                f"rides on '{branch}', so its approval cannot cover work on this "
                                f"branch. Tag the commit {head_task}, which owns this branch. If "
                                f"{task} really shares it, ask the orchestrator: the declaration "
                                f"('branch:' in the task file) is frozen into the run row at "
                                f"registration, so editing the file now changes nothing and only "
                                f"the CEO can have the task registered again with it.")
                borrowed.append(f"{task} (branch is {head_task}'s)")
            if run is None:
                return deny(
                    f"{task} has no row in the run store ({state.DB_PATH}), so the "
                    f"checkpoint approval cannot be verified and the gate refuses rather "
                    f"than assuming approval. Either register the task - python "
                    f".claude/tools/pipeline/advance.py --task {task} --type <type> - or, if "
                    f"it IS registered, this session is in the wrong lane: PIPELINE_LANE"
                    f"{f'={state.LANE!r}' if state.LANE else ' is unset'} selects the store "
                    f"above, and a lane holds its own runs.")
            if task in commit_ts and not run["commit_approved"]:
                via = (f" (the commit is tagged [{task}]; the branch '{branch}' belongs to "
                       f"{head_task})" if tagged_elsewhere else "")
                return deny(f"Commit for {task} is not approved yet{via}. Surface the diff and wait "
                            f"for CEO approval, then: python .claude/tools/pipeline/approve.py "
                            f"--task {task} --gate commit.",
                            human=human_notice("commit", task=task))
            if task in push_tasks and push_needs_approval() and not run["push_approved"]:
                return deny(f"Push for {task} is not approved yet. Wait for CEO approval, then: "
                            f"python .claude/tools/pipeline/approve.py --task {task} --gate push.",
                            human=human_notice("push", task=task))
        for note in borrowed:
            print(f"commit gate: judged as {note} by its [task-NNNN] tag, not by the branch name.",
                  file=sys.stderr)
        return finish()
    except PushBudgetExpired:
        return deny(
            f"The git checks for this command (branch, refs, leak guard) did not finish "
            f"within their {PUSH_BUDGET:g}s time budget. A hook that times out is an "
            f"allow, so the gate refuses instead. Retry; if it keeps happening, run "
            f"python .claude/tools/pipeline/leak_guard.py --ref=<branch> by hand to see "
            f"which git call is slow.")
    finally:
        end_push_budget()


LIVE_HARNESS_MSG = ("{who}: '{path}' is the live harness (gate code, hook wiring, an agent "
                    "definition, a rule, skill or command under the project root's .claude/, "
                    "its .mcp.json, or the inside of a .git directory). A subagent that could "
                    "write it could switch the gates off for the next call. Change the "
                    "checkout under src/ and let the orchestrator deploy it, or report "
                    "the change you need to the orchestrator.")


def handle_edit(file_path: str, content: str = "", orch: bool = False, cwd: str = "") -> int:
    if not file_path:
        return allow()

    if not orch and is_live_harness(file_path, cwd):
        return deny(LIVE_HARNESS_MSG.format(who="Subagent", path=file_path))

    if under_home_claude(file_path):  # H
        return deny("Project files must not be written under the runtime home ~/.claude/. "
                    "Keep everything inside the project (its own .claude/, .agentry/, docs/, "
                    "src, ...).")

    if content and has_forbidden_dash(content):  # F
        return deny("Content contains an em dash or en dash (U+2014 / U+2013). Use a plain "
                    "hyphen '-' everywhere - code, comments, docs, strings. (global rule)")

    if orch:
        code = orch_check_edit(file_path)
        if code != 0:
            return code

    if not plan_write_allowed(file_path):
        blocked = plan_blocked_task(path=file_path)
        if blocked:
            return plan_deny(blocked, file_path)

    if is_bookkeeping(file_path):
        return allow()
    try:
        conn = state.connect()
        try:
            review = active_review_run(conn)
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        # An unreadable run store opens this check, as it always has (the plan gate
        # does the same). Said here so the subagent fail-closed handler in evaluate()
        # does not turn a damaged store into a deny for every code edit.
        review = None
    if review:
        return deny(f"{review['task']} is in the read-only '{review['stage']}' stage - code edits "
                    f"are blocked. To change code, send it back: python "
                    f".claude/tools/pipeline/approve.py --task {review['task']} --reject "
                    f"--reason \"<why>\"")
    frozen = handoff_freeze_task()
    if frozen:
        return deny(f"Handoff debt: completed task {frozen} has no valid handoff doc - code edits "
                    f"are frozen (handoff.hard_edit_gate) until the next task's assignee writes it. "
                    f"Scaffold: python .claude/tools/pipeline/handoff.py --for {frozen}, then fill "
                    f".agentry/tasks/handoffs/{frozen}.md. Check: handoff.py --check.")
    return allow()


def read_payload() -> dict:
    """The hook payload from stdin, decoded as UTF-8 explicitly.

    sys.stdin's own encoding follows the host locale - cp1252 on Windows, where
    byte 0x97 decodes to U+2014 and 0x96 to U+2013. Non-ASCII content (Cyrillic,
    in the case that surfaced this) therefore arrived carrying em dashes it never
    contained, and the dash gate refused the edit. Reading the raw bytes fixes
    the DECODE; the dash rule itself stays exactly as strict.

    The text fallback covers a stdin with no binary buffer (test doubles, some
    embedded runtimes) - it is the same JSON, not a bypass."""
    raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


# The hook's own timeout is 20 s in settings.json and a hook that times out is an
# allow. This watchdog answers first: a subagent call, and a command that runs git
# push / commit / merge / pull, that is still being judged after WATCHDOG_SECONDS
# is denied from a timer thread, which no blocking filesystem or sqlite call in
# the main thread can hold up. Other orchestrator commands keep the old behaviour
# (the timeout allows), because a stall there must not brick the orchestrator.
# 14 s leaves 6 s of the 20 s for the interpreter to start before the timer is even armed.
WATCHDOG_SECONDS = 14.0
WATCHDOG_MSG = ("The pretool gate did not finish checking this call within {seconds:g}s. A "
                "hook that times out is an allow, so the call is refused instead. Retry; "
                "if it keeps happening a filesystem or git call is stalling - report it to "
                "the orchestrator.")
_watchdog_lock = threading.Lock()
_watchdog_done = False


def watchdog_deny_and_exit(reason: str) -> None:
    """Write a PreToolUse deny and end the process with status 2. Both gates' watchdogs use
    it. os.write skips the stream locks the main thread may be holding, and os._exit skips
    its cleanup, so nothing the stalled thread owns can hold the verdict back."""
    try:
        verdict = json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": reason}})
        os.write(1, (verdict + "\n").encode("utf-8"))
        os.write(2, (reason + "\n").encode("utf-8"))
    finally:
        os._exit(2)


def _watchdog_fire() -> None:
    with _watchdog_lock:
        if _watchdog_done:
            return
        watchdog_deny_and_exit(WATCHDOG_MSG.format(seconds=WATCHDOG_SECONDS))


def start_watchdog_timer(callback, reason: str, seconds: float) -> threading.Timer:
    """Start a daemon timer for a gate's watchdog. If the thread cannot be started the gate
    would run unguarded, so the call is denied on the spot instead."""
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    try:
        timer.start()
    except Exception:  # RuntimeError: can't start new thread
        watchdog_deny_and_exit(reason)
    return timer


def arm_watchdog(tool: str, ti: dict, is_subagent: bool) -> threading.Timer | None:
    """Start the watchdog timer for a call that needs one, else None."""
    global _watchdog_done
    if not is_subagent:
        command = str(ti.get("command", "")) if tool == "Bash" else ""
        if not MENTIONS_GIT_RE.search(command):
            return None
    with _watchdog_lock:
        _watchdog_done = False
    return start_watchdog_timer(_watchdog_fire, WATCHDOG_MSG.format(seconds=WATCHDOG_SECONDS),
                                WATCHDOG_SECONDS)


def disarm_watchdog(timer: threading.Timer | None) -> None:
    """Stop the timer. Taking the lock waits out a callback that already started,
    and that callback ends the process, so a verdict is never written twice."""
    global _watchdog_done
    if timer is None:
        return
    with _watchdog_lock:
        _watchdog_done = True
    timer.cancel()


def main() -> int:
    del PENDING_HUMAN[:]
    code = evaluate()
    if code == 2:
        emit_human()
    return code


def evaluate() -> int:
    try:
        payload = read_payload()
    except (ValueError, OSError, UnicodeDecodeError):
        return allow()
    is_subagent = False
    try:
        tool = payload.get("tool_name", "")
        ti = payload.get("tool_input", {}) or {}
        # Subagent detection: settings-level hooks also fire inside subagents,
        # but the orchestrator profile must only bind the MAIN thread (subagents
        # carry their own frontmatter hooks -> agent_gate.py). Two signals:
        #   1. explicit agent identity keys in the payload (newer harnesses);
        #   2. the transcript path - subagent transcripts live under a
        #      .../subagents/ directory as agent-<id>.jsonl.
        is_subagent = any(str(payload.get(k) or "").strip()
                          for k in ("agent_type", "subagent_type", "agent_name", "agent_id"))
        tp = str(payload.get("transcript_path") or "").replace("\\", "/").lower()
        if "/subagents/" in tp or tp.rsplit("/", 1)[-1].startswith("agent-"):
            is_subagent = True
        orch = not is_subagent
        watchdog = arm_watchdog(tool, ti, is_subagent)
        try:
            if tool == "Bash":
                return handle_bash(str(ti.get("command", "")), str(payload.get("cwd", "")),
                                   orch=orch)
            if tool in EDIT_TOOLS:
                path, content = edit_target(tool, ti)
                return handle_edit(path, content, orch=orch, cwd=str(payload.get("cwd", "")))
            return allow()
        finally:
            disarm_watchdog(watchdog)
    except Exception as exc:
        # An exception in an early check skips every check behind it, so what the
        # gate does with it IS the policy. A subagent call denies: it is the
        # untrusted caller, and failing open here handed it whatever the broken
        # step would have refused (task-0120 SEC-B H2). The MAIN thread stays
        # fail-open on purpose: a bug in this file must not brick the orchestrator,
        # the only caller that can repair it.
        if is_subagent:
            return deny(f"The pretool gate failed while checking this subagent call "
                        f"({type(exc).__name__}), so the call is refused rather than "
                        f"allowed unchecked. Report it to the orchestrator.")
        return allow()


if __name__ == "__main__":
    raise SystemExit(main())
