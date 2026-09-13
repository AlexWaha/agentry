# Git Workflow

Sacred rules for version control. These rules are absolute and cannot be overridden by any agent or circumstance.

> **Stack-specific commands** (format, lint, test, build, migrate, etc.) live in `.claude/project/stack.md`. This file uses placeholders like `{{FORMAT_CMD}}` - resolve them against the project overlay before running.

---

## The Sacred Rule

**`main` is SACRED.**

No direct commits. No direct pushes. No force pushes. No exceptions. Ever.

ALL changes reach `main` through Pull Requests only. This is non-negotiable.

---

## AI Agent Restrictions

These restrictions apply to all AI agents without exception:

- **NEVER commit without CEO approval** - always show the diff and ask before committing
- **NEVER push without CEO approval** - always ask before pushing
- **NEVER create PRs via CLI** - CEO creates PRs manually (no `gh` integration)
- **NEVER work on `main` branch** - no commits, no pushes, no direct changes
- **NEVER push to `main`** - not even with CEO approval; all changes go through PRs
- **NEVER force push** to any branch
- **NEVER delete remote branches** without CEO approval
- **NEVER skip pre-commit checks** - git hooks are disabled; linter and tests must be run manually before every commit

### Push is never automatic, at any approval level

`git push` requires the CEO's explicit approval in chat every single time. No
approvals level grants it, no per-stage `auto_approve` entry may list it, and no
"he approved the last one" carries over. A push is the moment work leaves the
machine, and the CEO has asked for that decision to stay his without exception.

This is enforced, not just written: `approvals.NEVER_GRANTED` refuses `PUSH` at
every level, and `granted()` checks it before both the level and the per-stage
`auto_approve` list, so neither can re-grant it. `advance.py` reads the push
checkpoint through `granted()` for the same reason.

The approval is its own question. Do not fold it into a card about something
else, however obvious the answer looks from the surrounding choice: a card that
asks "how do we unblock the epic" and takes the push as a side effect of the
answer has not asked about the push. One card, one decision, and the push gets
its own.

SIGNATURE: task-retired-without-a-merge
TRIGGER:   running advance.py at the `done` stage, for any task whose work shares a branch
WHAT:      task-0043 was moved to tasks/done/ with "No branch found for this task in any repo - nothing to merge", while its code sat unmerged on another task's branch.
WHY:       merge detection looks for a branch named after the task (`<type>/task-NNNN`) or a `[task-NNNN]` commit on main. A task whose work deliberately rides on a sibling's branch matches neither, and the absence of a branch is read as "nothing to merge" rather than as "cannot tell".
FIX:       no branch and no tagged commit means UNKNOWN, not DONE - park and say so. When two tasks share a branch, name the carrying branch in both task files so detection has something to resolve. Never let a task reach `done` on the absence of evidence.
DATE:      2026-09-13

SIGNATURE: push-always-needs-approval
TRIGGER:   reaching the push checkpoint, or reading any approvals-level description
WHAT:      ran with approvals at `auto`, which grants the push, and told the CEO that commit and push would both happen silently. He had to interrupt to say push is always his.
WHY:       `skills/pipeline/SKILL.md` describes `auto` as granting everything through the push, while `git-workflow.md` forbids pushing without approval. Two project documents disagreed and the orchestrator followed the permissive one.
FIX:       treat push as unconditionally CEO-gated. When two project documents disagree about a safety boundary, follow the stricter one and report the contradiction instead of quietly picking.
DATE:      2026-09-13

### Deterministic checkpoint enforcement

When a task runs under the execution pipeline (`rules/orchestration.md`), the
commit and push approvals are enforced by the `PreToolUse` hook, not by goodwill:

- `git commit` is blocked until you run
  `python .claude/tools/pipeline/approve.py --task task-XXXX --gate commit`.
- `git push` is blocked until you run the same with `--gate push`.
- Any `git push` to the protected branch is blocked outright.

Run `approve.py` **only after the CEO explicitly approves in chat**. To send a task
back for rework instead of approving, use `approve.py --task task-XXXX --reject`
(returns it to `implement`). These two approvals are the only points where the CEO
re-engages during execution.

### Multi-repo workspaces

Some workspaces hold several independent git repos in subdirectories (for
example `backend/` and `frontend/`), and the workspace root itself is not a
repo. The gate resolves the repo a git command actually targets - `git -C`,
a `cd` inside the command, or the session cwd - so branch rules apply per
sub-repo. When working in such a workspace, run git commands from inside the
sub-repo (or via `git -C <subdir>`), and give each stage's `exit_gate` in
`pipeline.json` its own `cwd` when the gate must run inside one sub-repo.

---

## Branch Workflow

### Starting New Work

**Every new piece of work starts from a fresh `main`:**

1. Switch to `main`
2. Pull latest changes (`git pull origin main`)
3. Create a new branch from `main`
4. Never branch from another feature branch

```bash
# Always start from fresh main
git checkout main
git pull origin main
git checkout -b feature/task-XXXX

# Verify the new branch is based on up-to-date main (expect both to pass / print 0)
git merge-base --is-ancestor origin/main HEAD   # exit 0 => main is an ancestor
git rev-list --count main..HEAD                  # expect 0 => no commits ahead of main yet
```

**If already on a feature branch:** continue working there only if the task is the same. For new tasks - always go back to `main` and branch fresh.

#### FORBIDDEN: branching from a non-`main` branch

**`main` is the ONLY source of truth. Every branch starts from an up-to-date `main` - never from another branch.**

- **Never** branch from a feature, bugfix, hotfix, teammate, or any unmerged branch. Doing so inherits that branch's unverified, unmerged code - you make its bugs and its "NOT VERIFIED" code your responsibility without knowing it.
- Branching off a non-`main` base also **switches the pipeline gates off**: the deterministic enforcement hooks (`.claude/tools/pipeline/`) are only guaranteed to run for a branch cut from `main`. Off a side branch, the gate that runs another stack's test + formatter may never engage.
- If you need another branch's unmerged work, it must reach `main` via **its own PR first**. Then branch fresh from the updated `main`. There is no shortcut.
- **Verify the base before doing any work:** `git merge-base --is-ancestor origin/main HEAD` must exit `0` (up-to-date `main` is an ancestor of HEAD). If it does not, STOP - you are on the wrong base. Rebase onto `main` or recreate the branch from `main` before continuing.

### Branch Naming

Format: `<work-type>/task-XXXX`

| Prefix | Use Case |
|--------|----------|
| `feature/task-XXXX` | New features and functionality |
| `bugfix/task-XXXX` | Bug fixes |
| `hotfix/task-XXXX` | Urgent production fixes |
| `enhancement/task-XXXX` | Improvements to existing features |
| `techdebt/task-XXXX` | Technical debt reduction |

The task number `XXXX` corresponds to the task file in the task management system.

Examples:
```
feature/task-XXXX
bugfix/task-XXXX
enhancement/task-XXXX
techdebt/task-XXXX
```

### Push Rules

- Push is **only** allowed to the **current working branch**
- **Never** push to `main`, `staging`, `production`, or any protected branch
- Always use `-u` flag on first push to set upstream

```bash
# First push - set upstream
git push -u origin feature/task-XXXX

# Subsequent pushes
git push
```

---

## Before Every Commit

**Git hooks are disabled.** All pre-commit checks must be run manually. Five mandatory steps, in order.

> Exact commands depend on the stack; see `.claude/project/stack.md`.

**The gates must run for EVERY stack the branch touches - not just the one you worked in.** If a branch carries both backend and frontend changes (or inherited code in another stack), run every affected stack's gate. "Green" means the real command output you read THIS session - never an assumption, never a subset of the gates, never "it built so it's fine". Inherited or "NOT VERIFIED" code on your branch is yours to gate before any green claim; you own it the moment it is on your branch.

### 1. Run Linter / Formatter

```bash
{{FORMAT_CMD}}
```

If the formatter modifies files, stage them and include in the commit.

### 2. Run Tests

```bash
{{TEST_CMD}}
```

Tests must pass before committing. If tests fail - fix first, then commit.

### 3. Run Format Lint Check (No Modifications)

```bash
{{LINT_CMD}}
```

This verifies formatting without modifying files, catching anything the auto-formatter missed on already-committed files.

### 4. Show Diff to CEO

```bash
git diff --staged
```

If nothing is staged yet:
```bash
git diff
```

Show the complete diff. Do not summarize or abbreviate. The CEO reads the actual changes.

### 5. Ask for Approval

Explicitly ask: "Ready to commit. Approve?"

Only commit after receiving explicit CEO approval.

---

## Before Every Push

Two mandatory steps:

### 1. Confirm Branch is NOT main

```bash
git branch --show-current
```

If the current branch is `main` - **STOP. Do not push.** Switch to a feature branch first.

### 2. Show What Will Be Pushed

```bash
git log origin/$(git branch --show-current)..HEAD --oneline
```

Show the branch name and the commits that will be pushed. Ask for CEO approval.

---

## Before Every PR

**CEO creates PRs manually.** The AI agent does NOT have access to `gh` CLI or GitHub. The agent's role is to prepare the PR - push the branch and provide PR details.

### 1. Ensure Branch is Pushed

The branch must be pushed to origin before a PR can be created.

### 2. Prepare PR Details

Present to CEO for manual PR creation:
- **Title**: Short, descriptive (under 70 characters)
- **Body**: What changed, why, and how to test (use template below)
- **Target branch**: `main` (always)
- **Source branch**: Current feature branch

### PR Description Template

```markdown
## What
Brief description of the change.

## Why
Business motivation or technical necessity. Link to task file.

## How to Test
Step-by-step testing instructions.

## Checklist
- [ ] Code follows project conventions
- [ ] Tests added/updated
- [ ] Formatter applied
- [ ] No debug artifacts (debug prints, console logs, dump calls, etc.)
- [ ] No hardcoded secrets
```

---

## Commit Messages

Format: descriptive message in English.

```
feat: add resource creation endpoint with validation
fix: correct timezone handling in scheduling logic
refactor: extract PDF generation to dedicated service
test: add authorization tests for resource endpoints
docs: update API documentation for v1 endpoints
chore: update dependency versions
```

With task number (when available):
```
[task-XXXX] feat: add resource creation endpoint with validation
[task-XXXX] fix: correct timezone handling in scheduling logic
```

### Commit Message Rules

- Present tense ("add", not "added")
- Imperative mood ("fix", not "fixes")
- First line under 72 characters
- Optionally add a body after a blank line for complex changes
- **NEVER** include AI authorship - no `Co-Authored-By: Claude`, no "Generated by AI", no AI mentions of any kind

---

## Branch Deletion

### When to Delete

Branches are deleted **only** after they are merged into `main`.

### How to Verify

```bash
# List branches merged into main
git branch --merged main

# Safe to delete these (except main itself)
git branch -d feature/task-XXXX
```

### Rules

- **Never** delete a branch that is not merged into `main`
- **Never** delete remote branches without CEO approval
- **Never** use `git branch -D` (force delete) - use `git branch -d` (safe delete)
- After PR merge, delete the local branch
- Remote branch deletion: only after CEO confirmation

```bash
# Safe local delete (fails if not merged)
git branch -d feature/task-XXXX

# Remote delete (only with CEO approval)
git push origin --delete feature/task-XXXX
```

---

## Deploy Actions Reporting (MANDATORY)

Every task report (before push / before merge) **must** include a **Deploy Actions** section stating exactly what commands ops/CEO must run on production after merge. No task is "done" without this section.

> Exact command syntax depends on the stack; see `.claude/project/stack.md` for concrete commands. The categories below are framework-agnostic.

### Format

```
**Deploy actions:** <one of the categories below>
```

### Categories

| Category | When |
|----------|------|
| `none` | Only code changes, no migrations, no config, no queue surface. Pure refactor, or new endpoint with no schema change. |
| Schema migration command | New migration files added. List the migration files. `[EXAMPLE - Laravel]` `php artisan migrate` |
| Data migration command | New data-migration classes added. List the classes. `[EXAMPLE - Laravel]` `php artisan data:migrate` |
| Cache / config clear command | Config files or env example changed. `[EXAMPLE - Laravel]` `php artisan config:clear && php artisan cache:clear` |
| Queue / worker restart command | Jobs or services consumed by running workers changed. `[EXAMPLE - Laravel]` `php artisan queue:restart` |
| Dependency install command | Lockfile / manifest changed. `[EXAMPLE - Composer]` `composer install --no-dev`. `[EXAMPLE - Node]` `npm ci --omit=dev` |
| Manual step | Anything else (env var required, external service config, seed run). Describe exact command and context. |

### Rules

- **Zero ambiguity.** Writing `none` is a positive assertion that no action is required - verified, not skipped.
- **Multiple categories** are allowed. List every applicable command in execution order.
- **New env var** → call it out explicitly with the variable name and value shape.
- **Destructive step** (drop column, wipe table) → CEO approval required separately, not buried in deploy actions.
- **Downtime expected** → flag with `⚠️ DOWNTIME` and estimate duration.
- This section appears in the task report AND in the MR description.

### Example

```
**Deploy actions:**
- Schema migration command - runs 2 new migrations (list filenames)
- Cache / config clear command - config file changed
- No queue restart needed (no job changes)
- No new env vars
```

---

## Protected Branches

These branches are **never** directly modified:

| Branch | Purpose | Direct Push |
|--------|---------|------------|
| `main` | Primary development branch, staging deployment | FORBIDDEN |
| `staging` | Staging environment (if used) | FORBIDDEN |
| `production` | Production deployment (if used) | FORBIDDEN |

All changes reach these branches through PRs only.

---

## Small Batches Philosophy

Ship small, incremental changes:

- **PR scope**: Single focus - one task, one concern
- **Signs a PR is too large**: Multiple unrelated changes, "while I was here..." modifications
- Break down large features into sequential PRs
- Each PR should be reviewable in under 30 minutes

---

## Merge Strategy

- **Squash merge** for feature branches into `main` (clean history)
- **Merge commit** for release branches (preserve history)
- **Never rebase** shared branches that others might have checked out

---

## Conflict Resolution

When conflicts arise:

1. Pull latest `main` into the feature branch
2. Resolve conflicts locally
3. Run tests after resolution
4. Show the resolution to CEO before pushing

```bash
git checkout feature/task-XXXX
git pull origin main
# Resolve conflicts in editor
git add .
# Show diff to CEO, get approval, then commit
```

---

## Plans and Task Management

### Plans

When a multi-step initiative is planned:

1. **Save plan** to `.claude/plans/YYYY-MM-DD-description.md` (date in filename is mandatory)
2. **Immediately create task files** for every actionable item in the plan - `.claude/tasks/backlog/task-XXXX.md`
3. **Never start executing** without task files created first
4. **Track progress** - update task checkboxes as work is done
5. A task's state IS the folder it sits in - `backlog/` queued, `active/` in
   flight, `done/` merged. There is no `status:` field to update separately.

### Task-Branch Integration

Each branch corresponds to a task in the task management system:

1. Task is created with a unique number (e.g., `task-XXXX`) and files into `.claude/tasks/backlog/`
2. `advance.py` moves the file `backlog/ -> active/` when the CEO says which task to start; a branch is created from `main` using the task number: `feature/task-XXXX`
3. Work proceeds on the branch
4. Commit when task is done (CEO approval required)
5. Push the feature branch to remote (CEO approval required)
6. **CEO creates the PR manually** in the GitHub web UI - agents never merge to `main` locally, never push to `main`, never use `gh` CLI
7. CEO merges the PR → `advance.py` confirms the merge via git and moves the task file `active/ -> done/` itself - "done" means merged, not pushed
8. Next task branches from fresh `main` (`git checkout main && git pull origin main`)

### File Locations

All project management files live in the **workspace root** `.claude/`:

| What | Where |
|------|-------|
| Plans | `.claude/plans/` |
| Backlog tasks | `.claude/tasks/backlog/` |
| Active tasks | `.claude/tasks/active/` |
| Done tasks | `.claude/tasks/done/` |
| Agents | `.claude/agents/` |
| Rules | `.claude/rules/` |

Workspace root only - never in a subdirectory's `.claude/` or in the user-global `~/.claude/`.

---

## Lessons (recorded)

SIGNATURE: branch-base-must-be-main
TRIGGER:   starting any task / creating any branch / before any commit or push
WHAT:      branched off an unmerged side branch, inherited its "NOT VERIFIED" backend code, ran only the frontend gates, self-certified green, and pushed - the backend pipeline then failed.
WHY:       a non-`main` base carried unverified/unmerged code AND predated the pipeline hooks, so the deterministic gate that runs the backend test + formatter was never engaged; "green" was asserted from a subset of gates.
FIX:       branch ONLY from up-to-date `main` (verify `git merge-base --is-ancestor origin/main HEAD`); before any green claim run EVERY touched stack's gates and read the real passing output; never build on top of an unmerged branch.
DATE:      2026-06-23
