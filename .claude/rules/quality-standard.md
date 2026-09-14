# Rule #0: Quality Standard

This is the foundational rule. Every other rule builds on top of it. No exceptions, no shortcuts, no "good enough for now."

---

## FORBIDDEN: Em Dash

**NEVER use em dash (the long dash character) anywhere.** Use regular hyphen-dash (-) only. This applies to ALL output: code, comments, commit messages, documentation, UI strings, translations, chat, plans, task files - everything without exception.

## FORBIDDEN: Hardcoded Fallback Locale

- No hardcoded fallback locale - use the backend config key for the fallback locale and the frontend `DEFAULT_LOCALE` constant. Never inline `'en'` (or any locale string) as a fallback in accessors, resolvers, resources, factories, seeders, or tests. See `.claude/rules/i18n.md#fallback-locale-config-only`.

## FORBIDDEN: Self-Certified "Green"

**NEVER claim passing, done, or ready except from real command output you read THIS session, across EVERY stack the change touches.**

- A passing subset is not a pass. If the branch touches backend and frontend, both stacks' gates must pass before you call anything green.
- "It built so it's fine" is not verification. A successful build is not a passing test suite, a passing formatter, or a passing lint.
- A branch is only trustworthy if it was cut from up-to-date `main`. Off any other base the pipeline gates are not enforced - so its "green" is meaningless.
- Inherited or "NOT VERIFIED" code on your branch is yours to gate. The moment it is on your branch, you own it - run its stack's gate before any green claim.

Re-running a gate beats burning the CEO's time, money, and trust on rework.

### Verification Discipline (the three steps before any green claim)

1. **Confirm the branch base.** Run `git merge-base --is-ancestor origin/main HEAD` -
   it must exit `0` (up-to-date `main` is an ancestor of HEAD). If it does not,
   STOP and surface it to the Orchestrator: you may be on an unmerged side branch
   carrying unverified code, and the pipeline gates are not enforced off a
   non-`main` base. Never proceed on the wrong base.
2. **Run the FULL gate for EVERY stack the branch touches.** Backend test +
   formatter/lint for backend changes; frontend build + lint + tests when the
   branch also carries frontend changes. Commands live in `project/stack.md`.
3. **Report the actual command output, never an assertion.** "Green" is the real
   output you read this session - not "it should pass", not "it built so it's fine".

## Pre-Flight Checks (MANDATORY before creating ANY new artifact)

Before creating a new artifact (service, model, migration, controller, validator,
resource, command, job, factory, seeder, test file, document):

1. **Glob the target path** - does the file already exist?
2. **Grep the class/module name** - is it referenced or declared anywhere?
3. **Read the task file fully** - does it say "implement" or "extend/fix"?
4. **Check git history** for the path if Glob suggests it was once there

If ANY check turns up a hit: STOP, READ the existing artifact, and decide if the
task is "create" (rare) or "extend/refactor" (common). Report the finding before
writing. See `self-learning.md`.

## The Standard

No half-assing. This is a funded startup building a real product for real users. Every deliverable - code, document, analysis, diagram, commit message - must be **production-grade**.

Before submitting ANY result, ask yourself:

> "Would I present this to investors? Would I show this to the CEO in a board meeting?"

If the answer is no - redo it. There is no "draft quality" in this team.

## What Production-Grade Means

### Documents
- Detailed and structured with clear sections and table of contents
- Numbers backed by sources or explicitly marked as estimates with reasoning
- Alternatives considered and trade-offs documented
- No filler text, no vague statements, no "it depends" without follow-up analysis
- Professional formatting: headers, tables, bullet points, diagrams where appropriate

### Code
- Clean, readable, follows all coding conventions
- Handles edge cases and error conditions
- Tested with comprehensive coverage (auth, validation, happy path, edge cases)
- No TODO/FIXME left without a linked task
- No commented-out code
- No debug artifacts (any language: `dd`, `dump`, `console.log`, `print_r`, `print`, `println`, `fmt.Println`, `puts`, `p`, `pp`, `var_dump`, `breakpoint`, etc.)

### Analysis
- Deep research with multiple sources
- Structured frameworks applied (SWOT, Porter's, TAM/SAM/SOM)
- Assumptions clearly stated and justified
- Best/base/worst case scenarios for projections
- Actionable conclusions, not just observations

### Architecture
- Thoughtful design with clear reasoning for every decision
- Trade-offs documented (what was chosen AND what was rejected, and why)
- Diagrams (Mermaid) for visual clarity
- Scalability and performance considerations addressed
- Security implications analyzed

### Tests
- Every endpoint: 401, 403, 422, 200/201, 404
- Edge cases covered (empty data, boundary values, concurrent access)
- Descriptive test names that explain the scenario and expected outcome
- Self-contained tests (DAMP over DRY)

## CEO Reviews Everything

The CEO/CTO reviews all output. Poor quality wastes:
- Time (rework cycles)
- Money (wasted compute and context)
- Momentum (delays block downstream phases)

Get it right the first time.

---

## Windows / Git Bash Environment Rules [OPTIONAL]

> Apply these rules when the development machine runs Windows with Git Bash. They are harmless no-ops on *nix systems and can be ignored on pure Linux/macOS setups.

### FORBIDDEN: Redirecting Output Away in Git Bash on Windows

**The reason, stated correctly, because getting it wrong is what produced three
broken versions of this rule.** The shell here is bash, not `cmd.exe`, and the
two spellings behave differently. Both were finally measured on 2026-09-14
(git-bash MINGW64 on Windows 11), each in its own empty directory.

`2>NUL` is the spelling that actually breaks. Bash has no device named `NUL`, so
the redirect opens a file of that name and the output lands in it:

```
$ ls -a                            # only . and ..
. ..
$ ls nonexistent_xyz 2>NUL
exit=2
$ ls -la
-rw-r--r-- 1 AlexWaha 197121 63 Sep 14 03:04 NUL
$ cat NUL
ls: cannot access 'nonexistent_xyz': No such file or directory
```

Nothing was discarded: the 63 bytes are the error text. Worse, the entry is hard
to remove, because bash and Windows disagree about what it is. To bash it is a
regular file (`test -f` succeeds, `test -c` fails). To the Win32 layer the name
`NUL` resolves to the reserved device, so Python reports `is_file()` as `False`
and a character device, and `os.unlink` / `shutil.rmtree` fail with
`PermissionError: [WinError 5] Access is denied`. A bash `rm -f` clears it;
Python cannot.

`2>/dev/null` does NOT do this, contrary to what this rule claimed for months.
MSYS2 mounts a real character device at that path (`test -c /dev/null`
succeeds), so the output is genuinely discarded and the directory is unchanged
afterwards. It is still forbidden here, but by **policy, not physics**: it is
the CEO's standing rule, `gates.forbid_dev_null` enforces it, and only the CEO
relaxes it. Do not restate the old mechanism as the justification.

**If you re-measure this, pin the shell first.** From Python a bare `bash` on
this host resolves to WSL bash, a Linux kernel with a genuine null device, so
it returns the result above for entirely the wrong reason - and this is the one
claim it would get right by accident, which is exactly what makes the trap
worth naming here. Use `C:/Program Files/Git/bin/bash.exe` explicitly and
confirm `uname` reports `MINGW` or `MSYS` before believing any of it. The first
attempt at this measurement did not, and WSL bash resolved the project path as
`/mnt/e/...` and created a literal directory named `E:` in the project root.

**So on git-bash, do not redirect output away.** One spelling leaves a file you
cannot delete with Python; the other is denied by policy. Do not go looking for
a third variant - the answer is to let the output through.

**NEVER** use any of these:

```bash
# FORBIDDEN by policy - the Unix spelling (denied by gates.forbid_dev_null).
# Measured: it works here and creates nothing. Denied anyway, by CEO rule.
command >/dev/null
command 2>/dev/null
command &>/dev/null
command > /dev/null 2>&1

# FORBIDDEN because it is broken - the `cmd.exe` spelling. Measured: writes the
# output into a file named NUL that Python then cannot delete.
command >NUL
command 2>NUL
command >NUL 2>&1
```

**What to do instead:**

1. **Run the command with no redirect and let the output through.** This is the
   answer almost every time. Noisy output costs a few tokens; a stray `NUL` file
   in the tree costs a debugging session.
2. If you genuinely need the output out of the way, **capture it instead of
   discarding it**: `out=$(command 2>&1)`, then test `$?` or inspect `$out`.
3. If it has to go to a file, put it **inside the project's `tmp/`** (gitignored)
   and delete it when done: `command > tmp/build.log 2>&1`.

```bash
# GOOD - just run it
{{BUILD_CMD}}

# GOOD - capture, do not discard
out=$({{BUILD_CMD}} 2>&1)

# GOOD - a real file, inside the project, cleaned up afterwards
{{BUILD_CMD}} > tmp/build.log 2>&1

# BAD - denied by gates.forbid_dev_null (policy; it does work on this host)
{{BUILD_CMD}} 2>/dev/null

# BAD - writes the output into a file named "NUL" that Python cannot delete,
# which is why it is no longer recommended
{{BUILD_CMD}} 2>NUL
```

### NUL File Cleanup

On Windows/Git Bash, at the **start** and **end** of every session, run:

```bash
rm -f nul NUL
```

This catches any accidental violations from previous sessions or tooling. No-op on *nix (files do not exist).

Clean up with bash (`rm -f`, or `find -iname nul -delete`), never from Python:
`os.unlink` on a `NUL` entry fails with `PermissionError: [WinError 5]`, and
`pathlib.Path.is_file()` reports `False` for it, so a Python guard skips the
entry entirely. Measured 2026-09-14.

### Path Separators

- Use forward slashes `/` in bash commands (Git Bash handles them correctly)
- Use backslashes `\` only when explicitly required by Windows-native tools
- Never mix separators in the same path

### Execution Context

Project-specific command invocations (build, test, format, lint, migrate) live in `.agentry/project/stack.md`. Run them as documented there.

---

## Latest Versions Policy

All packages, frameworks, tools, and dependencies must be the **latest stable version** at the time of installation. No pinning to old versions without explicit CEO approval.

Concrete versions for this project's stack ({{LANG}}, {{FRAMEWORK}}, database, cache, runtime) live in `.agentry/project/stack.md`. That file is the single source of truth for current versions.

### Rules

- All package manager dependencies (Composer / npm / pip / gem / cargo / go modules): **latest stable**
- Container images (Docker, etc.): **explicit version tags** (e.g., `{{LANG}}:{{VERSION}}`), never the `latest` tag
- Before adding any dependency: verify it supports the stack versions documented in `project/stack.md`
- Before upgrading: verify cross-compatibility with the existing stack
- If a package does not support the current stack: find an alternative or flag to CEO
- Security patches: apply immediately, no delays
- Major version upgrades: verify changelog and breaking changes before upgrading

### Why This Matters

Technical debt from outdated dependencies compounds exponentially. Starting with latest versions means:
- Longest possible support window
- Access to latest security patches
- Best performance and features
- Easiest hiring (developers want modern stacks)

---

## AI Authorship: FORBIDDEN

**NEVER** include any AI attribution in any deliverable:

- No `Co-Authored-By: Claude` or any AI co-author trailer in commits
- No "Generated by AI", "Written by Claude", "AI-assisted" in PR descriptions
- No "Generated by Claude", "AI-generated" in code comments
- No AI attribution in documents, presentations, or any other output

This applies to ALL generated text: commits, PRs, code comments, documents, presentations, emails - everything.
