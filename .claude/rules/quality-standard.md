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

### FORBIDDEN: Unix-Style Redirects in Git Bash on Windows

Unix redirects do not work correctly in Git Bash on Windows. They create a literal file named `nul` instead of discarding output. **NEVER** use any of these on a Windows/Git Bash setup:

```bash
# ALL OF THESE ARE FORBIDDEN on Windows/Git Bash
command >/dev/null
command 2>/dev/null
command &>/dev/null
command 2>&1 >/dev/null
command > /dev/null 2>&1
```

**What to do instead:**
- Run the command without any redirects (preferred and simplest)
- If you absolutely must suppress output, use Windows-native `2>NUL` or `>NUL`

```bash
# GOOD - just run it
{{BUILD_CMD}}

# GOOD - if you must suppress (Windows)
{{BUILD_CMD}} 2>NUL

# BAD on Windows/Git Bash - creates a literal "nul" file
{{BUILD_CMD}} 2>/dev/null
```

### NUL File Cleanup

On Windows/Git Bash, at the **start** and **end** of every session, run:

```bash
rm -f nul NUL
```

This catches any accidental violations from previous sessions or tooling. No-op on *nix (files do not exist).

### Path Separators

- Use forward slashes `/` in bash commands (Git Bash handles them correctly)
- Use backslashes `\` only when explicitly required by Windows-native tools
- Never mix separators in the same path

### Execution Context

Project-specific command invocations (build, test, format, lint, migrate) live in `.claude/project/stack.md`. Run them as documented there.

---

## Latest Versions Policy

All packages, frameworks, tools, and dependencies must be the **latest stable version** at the time of installation. No pinning to old versions without explicit CEO approval.

Concrete versions for this project's stack ({{LANG}}, {{FRAMEWORK}}, database, cache, runtime) live in `.claude/project/stack.md`. That file is the single source of truth for current versions.

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
