#!/usr/bin/env python3
"""PreToolUse brain - deterministic backstop for the human checkpoints.

Wired to Claude Code's `PreToolUse` event (matchers: Bash, Edit|Write). It denies
(exit code 2, reason on stderr) when:
  - `git commit` runs while the current task's commit is not yet approved;
  - `git commit` / `git push` runs on a task branch whose run row cannot be
    read, or is absent from the lane's run store - the approval cannot be
    verified, so these two checks fail CLOSED (see handle_bash);
  - `git push` runs while push is not approved, or targets ANY protected branch
    (main and master always, plus the configured trunk and pipeline.json
    "protected_branches" - default staging / production);
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
  - These are the profile gates' floor, not a proof of absence. A pass means
    "no forbidden thing is written in this text", never "this command cannot do
    the forbidden thing".
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
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
                                   "--delete"})
PUSH_DESTRUCTIVE_SHORT = frozenset("fd")  # -f, -d, and bundles like -fu

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
# `(?<![-<>])` skips arrows like `->` / `-->` and the second `>` of `>>` / `<>`.
# It does NOT skip a word character: `echo hi>src/app.py`, `cat a.txt>f` and
# `ls 2>&1>f` are real redirects, and refusing a glued word (task-0095's
# lookbehind did) let readonly and docs agents overwrite any file (task-0102).
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
_REDIR_OP = r"\d*(?:>&|>\||<>|>>?)"
REDIR_RE = re.compile(rf"(?<![-<>])(?P<op>{_REDIR_OP})\s*(?P<t>[^\s;|&<>]+)")
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
QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"", re.DOTALL)

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


def _mask_span(m: re.Match) -> str:
    """Blank ONE quoted span character for character, keeping its newlines.

    Equal length is not enough. split_heredocs() indexes the mask BY LINE, and
    a multi-line quoted argument whose newlines were masked away collapsed into
    one masked line, so `masked[i]` ran off the end and raised IndexError - and
    every main() here catches Exception and allows, so the raise did not fail
    the command, it disabled the whole gate. Measured on the first version of
    this file: `git commit -m 'msg<newline><<' && git push origin main` exited
    0 against a protected branch. Keeping the newlines keeps the line counts
    equal while the offsets stay exact."""
    return "".join("\n" if c == "\n" else "Q" for c in m.group(0))


def mask_quoted(command: str) -> str:
    """Blank quoted spans so a `>` inside a quoted SQL comparison or message is
    not read as a redirect.

    Observed, not theoretical: a tool call carrying `>` inside a Python format
    string was denied as shell file-authoring. Length is preserved, so match
    offsets still index the ORIGINAL command - which is what lets a quoted
    TARGET (`> "out.txt"`) stay detectable while a quoted OPERATOR (`n >= 2`)
    stops matching.

    Masking alone is NOT enough: a quoted span can be a whole nested command
    (`bash -c "echo x > f"`), and blanking it makes the redirect invisible. That
    is what shell_c_bodies() + the recursion in redirect_write_target() cover.

    NEWLINES inside the span survive the mask (see _mask_span). Do not
    "simplify" that back to plain filler."""
    return QUOTED_RE.sub(_mask_span, command)


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
SEPARATOR_TOKENS = frozenset({"&&", "||", ";", "|", "&"})
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
    r"^(?:.*[\\/])?(?:env|command|nohup|time|sudo|xargs|exec)(?:\.exe)?$", re.IGNORECASE)
# `< file`, `<file`, `0< file` - stdin redirection. An interpreter reading a
# script this way RUNS it, exactly as it would as an argument. The negative
# lookahead keeps a heredoc / herestring operator out.
STDIN_REDIR_RE = re.compile(r"^\d*<(?!<)(.*)$")
EVAL_RE = re.compile(r"^(?:.*[\\/])?eval$", re.IGNORECASE)
# Any redirect operator, target unread: what eval_redirect() looks for in an
# eval operand it cannot resolve.
ANY_REDIR_RE = re.compile(r"(?<![-<>=])\d*[<>&]{0,2}[<>](?!<)")


def runs_a_shell(command: str) -> bool:
    """True when any token is an sh-family shell - then a heredoc body may BE a
    script (`bash <<EOF ... EOF`) rather than data. Fail-closed on untokenisable
    text so an unbalanced quote cannot buy a heredoc strip."""
    try:
        return any(SHELL_TOKEN_RE.match(t)
                   for t in shlex.split(pad_separators(command), posix=True))
    except ValueError:
        return True


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
    separators padded. None when the text cannot be tokenised."""
    try:
        return shlex.split(pad_separators(strip_heredocs(command)), posix=True)
    except ValueError:
        return None


def segments_with_separators(command: str) -> list | None:
    """[(separator-before, tokens), ...] - one entry per simple command. The
    separator of the first entry is ''. None when the text cannot be tokenised.

    The separator is kept because a PIPE changes what the next command runs:
    `cat script.py | python` executes the script on the left."""
    tokens = gate_tokens(command)
    if tokens is None:
        return None
    pairs, current, sep = [], [], ""
    for tok in tokens:
        if tok in SEPARATOR_TOKENS:
            if current:
                pairs.append((sep, current))
            current, sep = [], tok
        else:
            current.append(tok)
    if current:
        pairs.append((sep, current))
    return pairs


def command_segments(command: str) -> list | None:
    """Tokens of `command` grouped into simple commands, split on the shell
    separators. None when the text cannot be tokenised."""
    pairs = segments_with_separators(command)
    return None if pairs is None else [seg for _, seg in pairs]


def argv0_index(segment: list) -> int | None:
    """Index of the word a simple command actually RUNS: past leading
    `VAR=value` assignments, past flags, and past wrapper commands that run
    their own argument (`env`, `sudo`, `nohup`, `xargs`, ...). None when the
    segment runs nothing."""
    i = 0
    while i < len(segment):
        tok = segment[i]
        if ASSIGNMENT_RE.match(tok) or tok.startswith("-") or WRAPPER_RE.match(tok):
            i += 1
            continue
        return i
    return None


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


def exec_names_of(segment: list) -> list:
    """The words ONE simple command actually runs: argv0 (see argv0_index),
    plus - when argv0 is an interpreter - the script it is handed, whether as
    its first non-flag argument, on stdin, or as a `-m` module. `python
    .../approve.py`, `python < .../approve.py` and `python -m approve` all run
    that script; `git show -- x.py` does not run x.py.

    The `-m` operand went unread, and that was the whole of a bypass: the flag
    loop skipped `-m` as a flag and `approve` as its value, so
    `cd .claude/tools/pipeline && python3 -m approve --gate commit` named no
    script and passed every profile. A module is resolved to its file path
    (`pipeline.approve` -> `pipeline/approve.py`) so the script regexes, which
    anchor on a path separator, match it the same way they match the argument
    spelling."""
    i = argv0_index(segment)
    if i is None:
        return []
    names = [segment[i]]
    if INTERPRETER_RE.match(segment[i]):
        stdin = stdin_operands(segment)
        names.extend(stdin)
        rest = segment[i + 1:]
        for n, tok in enumerate(rest):
            if tok == "-m" and n + 1 < len(rest):
                names.append(rest[n + 1].replace(".", "/") + ".py")
            if tok.startswith("-") or STDIN_REDIR_RE.match(tok) or tok in stdin:
                continue
            names.append(tok)
            break
    return names


def executed_names(command: str) -> list | None:
    """Every word `command` actually runs, across all its simple commands.
    None when the text cannot be tokenised (callers gate that).

    A pipe INTO an interpreter runs what the previous command emitted, so its
    operands count as executed: `cat x/approve.py | python` and
    `echo x/approve.py | xargs python` both run the script the gate would
    otherwise have read as a path argument."""
    pairs = segments_with_separators(command)
    if pairs is None:
        return None
    names = []
    for n, (sep, seg) in enumerate(pairs):
        seg_names = exec_names_of(seg)
        names.extend(seg_names)
        if n and sep == "|" and seg_names and INTERPRETER_RE.match(seg_names[0]):
            names.extend(tok for tok in pairs[n - 1][1][1:] if not tok.startswith("-"))
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
        if sep != "|" or not is_test_suite_segment(pairs[n - 1][1]):
            continue
        i = argv0_index(seg)
        if i is None:
            continue
        name = basename_no_ext(seg[i])
        if name in PIPED_SUITE_SINKS:
            return name
    return ""


def command_substitutions(command: str) -> list:
    """The inner text of every `$( ... )` and backtick substitution.

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
        if ch == "$" and command.startswith("$(", i):
            if command.startswith("$((", i):
                j = command.find("))", i + 3)
                i = n if j < 0 else j + 2
                continue
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


APPROVE_SCRIPT_RE = re.compile(r"(?:^|[\\/])approve\.py$", re.IGNORECASE)


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
    return out


DEPTH_EXCEEDED = (f"nested shell deeper than MAX_SHELL_DEPTH ({MAX_SHELL_DEPTH}) "
                  f"- the inner body was never scanned, so it is refused")


def unscannable_depth(command: str, _depth: int) -> bool:
    """True when the recursion budget is spent and a nested body remains.

    Every recursing gate treats this as a HIT. Exceeding the depth used to be a
    silent allow, which made the ceiling a bypass instead of a limit: whatever
    the gate could not read, it waved through. Refusing costs a legitimate
    4-shells-deep command a deny it can rewrite; allowing costs the gate."""
    return _depth >= MAX_SHELL_DEPTH and bool(nested_command_bodies(command))


def redirect_write_fragments(command: str, _depth: int = 0) -> Iterator[str]:
    """Yield every file-writing redirect fragment of `command`, nested bodies
    included. The first one is redirect_write_target(); the whole sequence is
    what check_bare_nul() needs, because a bare NUL can follow a legitimate
    write (`ls > out.txt 2>NUL`)."""
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
NUL_REDIR_RE = re.compile(
    r"(?<![-<>=])\d*(?:&>>?|<>|>[>&|]?)\s*['\"]?nul['\"]?(?![\w./-])", re.IGNORECASE)


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
    """Deny the glued NUL redirect only. readonly and docs call this on top of
    bash_mutates(), which already denies every spaced file write; check_bare_nul()
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
    (`ls>NUL`) is check_nul_redirect()'s."""
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
README_RE = re.compile(r"(^|[\\/])readme[^\\/]*(\.md)?$", re.IGNORECASE)
ORCH_SED_RE = re.compile(r"\bsed\s+(-\w*\s+)*-i", re.IGNORECASE)
ORCH_TEE_RE = re.compile(r"\btee\s+(?:-\w+\s+)*(?P<t>[^\s;|&]+)", re.IGNORECASE)
ORCH_PATCH_RE = re.compile(r"(^|[\s;|&])(patch\b|git\s+apply\b)", re.IGNORECASE)


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
    try:
        raw = file_path.replace("\\", "/")
        low = raw.lower()
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
    m = ORCH_TEE_RE.search(command)
    if m and not orch_allowed_path(m.group("t")):
        return deny(f"Orchestrator never writes files via tee ('{m.group(0)}'). "
                    f"Dispatch the owning agent instead. (orchestrator_gate)")
    if ORCH_SED_RE.search(command):
        return deny("Orchestrator never edits files in place (sed -i). Use Edit for "
                    "bookkeeping files or dispatch the owning agent. (orchestrator_gate)")
    if ORCH_PATCH_RE.search(command):
        return deny("Orchestrator never applies patches (patch / git apply). Dispatch "
                    "the owning agent instead. (orchestrator_gate)")
    return allow()

# Multi-repo workspaces: the workspace root is often NOT a git repo (the repos
# are subdirectories like backend/ and frontend/). The branch must be read from
# the repo the command actually targets, in priority order:
#   git -C <dir>  >  last `cd <dir>` in the command  >  session cwd  >  ROOT.
GIT_C_RE = re.compile(r"git\s+-C\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")
CD_RE = re.compile(r"(?:^|&&|\|\||;)\s*cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")


def deny(reason: str) -> int:
    sys.stderr.write(reason + "\n")
    return 2


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
SHELL_SEPARATORS = ("&&", "||", ";", "|", "&")


def pad_separators(command: str) -> str:
    """Insert whitespace around glued shell separators, outside quotes.

    `git status&&git add -A` -> `git status && git add -A`, so shlex yields the
    second `git` as its own token. Quoted regions are copied verbatim: padding
    inside them would rewrite a commit message or a branch name.

    An unquoted NEWLINE terminates a command too, and shlex swallows it as
    ordinary whitespace - so `echo hi\\nrm -rf src` came back as one simple
    command whose argv0 is `echo`, and the second line read as an argument.
    It is emitted as `;` for that reason."""
    out = []
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
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
            out.append(" ; ")
            i += 1
            continue
        sep = next((s for s in SHELL_SEPARATORS if command.startswith(s, i)), "")
        if sep:
            out.append(" " + sep + " ")
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
    instead of hiding the second call inside one token.

    The failure mode is deliberately fail-CLOSED: when the text mentions git but
    shlex cannot tokenise it, the subcommand is GIT_UNKNOWN and callers must
    treat it as needing the gate. For these checks, failing open reopens the
    bypass. A git call carrying no subcommand at all (`git --version`) yields
    no entry - that is resolved, not unknown.
    """
    if not MENTIONS_GIT_RE.search(command):
        return []
    tokens = gate_tokens(command)
    if tokens is None:
        return [(GIT_UNKNOWN, [])]
    found = []
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


def repo_candidates(command: str, cwd: str) -> list:
    """Directories to try for the branch check, most specific first."""
    base = Path(cwd) if cwd else state.ROOT
    dirs = []
    m = GIT_C_RE.search(command)
    if m:
        dirs.append(_unquote(m.group(1)))
    git_at = command.find("git ")
    cds = CD_RE.findall(command if git_at < 0 else command[:git_at])
    if cds:
        dirs.append(_unquote(cds[-1]))
    resolved = []
    for d in dirs:
        try:
            p = Path(d)
            resolved.append(p if p.is_absolute() else base / p)
        except (OSError, ValueError):
            pass
    resolved.append(base)
    if state.ROOT not in resolved:
        resolved.append(state.ROOT)
    return resolved


def current_branch(command: str = "", cwd: str = "") -> str:
    for repo in repo_candidates(command, cwd):
        try:
            if not Path(repo).is_dir():
                continue
            proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(repo), capture_output=True, text=True, timeout=10,
            )
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
            f"{task} --gate commit.")
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
# nobody is watching. The push stays held by approvals.NEVER_GRANTED, so nothing
# leaves the machine, but the CEO reads the trunk before it moves and that is
# the point.
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
        f".claude/rules/orchestration.md and .claude/tools/pipeline/supervisor.py.")


def push_protected_branches() -> set:
    """Every branch a push may never target: main + master always, the
    configured trunk, and pipeline.json "protected_branches" (default staging /
    production). Unresolved `{{PLACEHOLDER}}` values are ignored."""
    pipeline = state.load_pipeline()
    names = set(PUSH_PROTECTED_ALWAYS)
    trunk = str(pipeline.get("main_branch") or "main")
    if not trunk.startswith("{{"):
        names.add(trunk)
    extra = pipeline.get("protected_branches")
    if not isinstance(extra, (list, tuple)):
        extra = PUSH_PROTECTED_DEFAULT
    for name in extra:
        name = str(name).strip()
        if name and not name.startswith("{{"):
            names.add(name)
    return names


def push_refspec_targets(args: list) -> list:
    """Destination branch names named by a `git push` argv.

    The first non-flag token is the remote; each remaining one is a refspec.
    `main`, `+main`, `HEAD:main`, `feature:main`, `:main` (delete) and
    `refs/heads/main` all resolve to 'main'. An empty result means the command
    named no refspec, so the caller falls back to the checked-out branch."""
    targets = []
    for spec in push_positionals(args)[1:]:  # positional[0] is the remote
        dst = spec.lstrip("+").rsplit(":", 1)[-1]
        dst = re.sub(r"^refs/heads/", "", dst)
        if dst:
            targets.append(dst)
    return targets


def push_positionals(args: list) -> list:
    """The non-flag tokens of a `git push` argv: the remote, then the refspecs."""
    positional = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            positional.extend(a for a in args[i + 1:] if a)
            break
        if tok.startswith("-"):
            i += 2 if tok in PUSH_OPTS_WITH_ARG else 1
            continue
        positional.append(tok)
        i += 1
    return positional


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
            name = tok.split("=", 1)[0]
            if name in PUSH_DESTRUCTIVE_LONG:
                return f"it carries {name}"
        elif tok.startswith("-") and len(tok) > 1:
            hit = sorted(set(tok[1:]) & PUSH_DESTRUCTIVE_SHORT)
            if hit:
                return f"it carries -{''.join(hit)}"
        if tok.startswith("-"):
            i += 2 if tok in PUSH_OPTS_WITH_ARG else 1
            continue
        i += 1
    for spec in push_positionals(args)[1:]:
        src, sep, _ = spec.rpartition(":")
        if sep and not src.lstrip("+"):
            return f"its refspec '{spec}' has an empty source side (a deletion)"
    return None


def push_target_branches(command: str, branch: str) -> list | None:
    """Branch names the push would land on, or None when it cannot be resolved.

    Fail-CLOSED by design: None means the caller must DENY. Fail-open belongs at
    the top-level exception handler, not in the step that decides whether a push
    is gated - guessing "probably fine" there is exactly how a protected branch
    gets pushed. Returns None for a push visible only to the substring net
    (`bash -c "git push origin main"`), and for a refspec-less push whose
    checked-out branch could not be read."""
    invocations = [args for sub, args in git_invocations(command) if sub == "push"]
    if not invocations:
        return None
    targets = []
    for args in invocations:
        specs = push_refspec_targets(args)
        if specs:
            targets.extend(specs)
        elif branch:
            targets.append(branch)
        else:
            return None
    return targets


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


def trunk_push_approved(target: str) -> bool:
    """True when the CEO approved publishing `target` from this lane.

    Fail-CLOSED on every doubt: wrong mode, missing marker, unreadable or
    malformed JSON, or a marker naming a different trunk all read as "not
    approved". `main` and `master` only - a push to staging or production is
    not what solo mode leaves unreachable."""
    if target not in PUSH_PROTECTED_ALWAYS or workflow_mode() != WORKFLOW_SOLO:
        return False
    try:
        data = json.loads(trunk_push_marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and str(data.get("trunk") or "") == target


def consume_trunk_push_marker() -> None:
    """Spend the approval on the push it allowed: one push per approval."""
    try:
        trunk_push_marker_path().unlink()
    except OSError:
        pass


def check_push_target(command: str, branch: str) -> tuple:
    """(verdict, spend) - deny a push landing on any protected branch, in every
    spelling the argv resolver can see: `origin main`, `HEAD:main`, `+main`,
    `--force origin main`, `feature:main`, and the refspec-less form while
    sitting on a protected branch. The one exception is the approved trunk push
    above, and `spend` is true when this push may consume that approval.

    The marker is NOT consumed here: handle_bash can still refuse the command
    afterwards (wrong branch, no run row, task push unapproved), and an approval
    burned by a push that never happened is an approval the CEO has to give
    twice. The caller spends it only on the way out through allow()."""
    protected = push_protected_branches()
    targets = push_target_branches(command, branch)
    if targets is None:
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
    for name in targets:
        if name not in protected:
            continue
        if not destructive and trunk_push_approved(name):
            approved = True
            continue
        return deny(
            f"Push to protected branch '{name}' is forbidden (protected: "
            f"{', '.join(sorted(protected))}) in every workflow mode. Push your "
            f"work branch instead - '{name}' is reached by a pull request in 'pr' "
            f"mode or by an approved local merge in 'solo' mode, never by a push. "
            f"See .claude/rules/git-workflow.md."
            + (f" An approved trunk push cannot cover this one: {destructive}, and "
               f"publishing the trunk is neither rewriting nor deleting it - the "
               f"approval is left unspent." if destructive else "")), False
    # A push naming an approved trunk AND an unapproved protected branch is
    # denied above, and must not have burned the approval on the way out.
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
            is_repo = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(repo), capture_output=True, text=True, timeout=10,
            )
            if is_repo.returncode != 0 or is_repo.stdout.strip() != "true":
                continue
            head = subprocess.run(
                ["git", "rev-parse", "--verify", "-q", "HEAD"],
                cwd=str(repo), capture_output=True, text=True, timeout=10,
            )
            if head.returncode != 0:
                return True  # unborn HEAD - zero commits
            branch = subprocess.run(
                ["git", "rev-parse", "--verify", "-q", f"refs/heads/{main_branch}"],
                cwd=str(repo), capture_output=True, text=True, timeout=10,
            )
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
            proc = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--no-renames"],
                cwd=str(repo), capture_output=True, text=True, timeout=10,
            )
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
        if r["stage"] == "review" and r["stage_status"] != state.ST_BLOCKED:
            return r
    return None


def is_bookkeeping(path: str) -> bool:
    # .agentry/ carries the work product since FR-13 (tasks, plans, specs,
    # state); it is bookkeeping for the same reason .claude/ is.
    p = path.replace("\\", "/").lower()
    return ("/.claude/" in p or "/.agentry/" in p or "/docs/" in p
            or p.startswith((".claude/", ".agentry/", "docs/")))


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
        conn = state.connect()
        try:
            editing = any(r["stage"] in state.EDITING_STAGES
                          and r["stage_status"] != state.ST_BLOCKED
                          for r in state.all_runs(conn))
        finally:
            conn.close()
        if editing:
            return ""
        debt = handoff.uncovered_done_tasks()
        return debt[0]["task"] if debt else ""
    except Exception:
        return ""


def handle_bash(command: str, cwd: str = "", orch: bool = False) -> int:
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

    pipeline = state.load_pipeline()
    main_branch = str(pipeline.get("main_branch", "main"))
    if main_branch.startswith("{{"):
        main_branch = "main"

    # FR-1: checked before current_branch() - a repo with no commits has an
    # unborn HEAD, which makes `git rev-parse --abbrev-ref HEAD` fail there and
    # would otherwise fall through repo_candidates() to an unrelated repo.
    if is_commit and repo_bootstrap(command, cwd, main_branch):
        return allow()

    branch = current_branch(command, cwd)

    spend_trunk_push = False
    if is_push:
        verdict, spend_trunk_push = check_push_target(command, branch)
        if verdict != 0:
            return verdict

    def finish() -> int:
        """Allow, spending the trunk-push approval if this push earned it. Every
        deny between here and the end leaves the marker for the push the CEO
        actually approved."""
        if spend_trunk_push:
            consume_trunk_push_marker()
        return allow()

    # A merge onto a protected branch commits there, which is exactly what the
    # trunk rule refuses - unless solo mode allows it for an approved task. A
    # merge while HEAD is a work branch (pulling main into a feature branch) is
    # untouched, as is a merge the gate cannot place (branch unreadable).
    if is_merge and branch in push_protected_branches():
        verdict = check_trunk_merge(command, branch)
        if verdict != 0:
            return verdict

    task = task_from_branch(branch)
    if not task:
        if is_commit:
            if planning_only_commit(command, cwd):  # FR-21, FR-22
                return allow()
            return deny("Not on a feature branch (no task-XXXX). Create the task branch first "
                        "per .claude/rules/git-workflow.md.")
        return finish()

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
        if is_commit or is_push:
            return deny(
                f"The pipeline run state ({state.DB_PATH}) could not be read, so the "
                f"checkpoint approval for {task} cannot be verified. This path fails "
                f"closed on purpose. Check PIPELINE_LANE"
                f"{f'={state.LANE!r}' if state.LANE else ' (unset)'} and that the state "
                f"directory is writable.")
        run = None
    if run is None:
        if is_commit or is_push:
            return deny(
                f"{task} has no row in the run store ({state.DB_PATH}), so the "
                f"checkpoint approval cannot be verified and the gate refuses rather "
                f"than assuming approval. Either register the task - python "
                f".claude/tools/pipeline/advance.py --task {task} --type <type> - or, if "
                f"it IS registered, this session is in the wrong lane: PIPELINE_LANE"
                f"{f'={state.LANE!r}' if state.LANE else ' is unset'} selects the store "
                f"above, and a lane holds its own runs.")
        return finish()  # merge-only: check_trunk_merge above is the whole check

    if is_commit and not run["commit_approved"]:
        return deny(f"Commit for {task} is not approved yet. Surface the diff and wait for CEO "
                    f"approval, then: python .claude/tools/pipeline/approve.py --task {task} --gate commit.")
    if is_push and push_needs_approval() and not run["push_approved"]:
        return deny(f"Push for {task} is not approved yet. Wait for CEO approval, then: "
                    f"python .claude/tools/pipeline/approve.py --task {task} --gate push.")
    return finish()


def handle_edit(file_path: str, content: str = "", orch: bool = False) -> int:
    if not file_path:
        return allow()

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

    if is_bookkeeping(file_path):
        return allow()
    conn = state.connect()
    review = active_review_run(conn)
    conn.close()
    if review:
        return deny(f"{review['task']} is in the read-only '{review['stage']}' stage - code edits "
                    f"are blocked. To change code, send it back: python "
                    f".claude/tools/pipeline/approve.py --task {review['task']} --reject")
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


def main() -> int:
    try:
        payload = read_payload()
    except (ValueError, OSError, UnicodeDecodeError):
        return allow()
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
        if tool == "Bash":
            return handle_bash(str(ti.get("command", "")), str(payload.get("cwd", "")), orch=orch)
        if tool in ("Edit", "Write"):
            content = str(ti.get("new_string", "") or ti.get("content", ""))
            return handle_edit(str(ti.get("file_path", "")), content, orch=orch)
        return allow()
    except Exception:
        return allow()


if __name__ == "__main__":
    raise SystemExit(main())
