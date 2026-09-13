---
id: XXXX
type: feature|bugfix|techdebt|enhancement
priority: high|medium|low
title: Short descriptive title
phase: phase-name-or-number
# repo: key of `repos{}` in pipeline.json; empty means the default lane. Read
# today by state.task_repo() to scope merge detection in a multi-repo project,
# and to dispatch a stage's gate once a project fills stack_gate.STACKS. An
# empty value in a multi-repo project can park a task forever. Lane routing on
# this field has no reader until FR-45 (task-0030).
repo:
depends_on: []
# blocked_on: a wait no other task can clear (CEO ruling, vendor answer). Leave
# bare - empty means no block. stop_gate.py reads ANY non-whitespace value as a
# block, `""` included, and a blocked task is never offered by the queue.
blocked_on:
# superseded_by: bare task id (task-0123) when this task's work moved to another
# task. Unquoted and unbracketed - `"task-0123"` and `[task-0123]` are not read,
# so the queue would keep offering a task that really was superseded.
superseded_by:
epic:
spec:
created: YYYY-MM-DD
completed:
assignee: agent-name
---
<!-- No status: or branch: field. A task's state IS the folder it sits in -
     .claude/tasks/backlog/ (queued), active/ (in flight), done/ (merged) -
     and the branch is created only when the pipeline starts the task. -->

## Description
What needs to be done, with context.

## Acceptance Criteria
Spec Trace: when `spec:` is set, every criterion cites the FR it verifies.
- [ ] (FR-1) Criterion 1
- [ ] (FR-2) Criterion 2

## Reason
Why this task exists - business motivation or technical necessity.

## Frontend Impact (BACKEND tasks only - MANDATORY)
See `.claude/rules/task-creation.md`. Replace this whole section with `## Backend Dependencies` for frontend tasks.

- **Exposes:** what new entity / field / endpoint / validation / permission becomes user-facing (or `none - internal only`)
- **Existing frontend task:** task-XXXX if a paired UI task already exists; otherwise `n/a`
- **New frontend task needed:** `yes - task-XXXX` (create in same session) or `no` with reason
- **Out of scope on frontend:** explicit non-goals so the paired task is bounded

## Backend Dependencies (FRONTEND tasks only - MANDATORY)
Replace the section above with this one for frontend tasks.

- **Needs endpoints:** list URIs + payload shape required
- **Backend status:** `exists in task-XXXX` / `to be built in task-XXXX` / `already shipped`
- **Out of scope on backend:** explicit non-goals

## Definition of Done

- [ ] Code follows conventions in `.claude/rules/coding-style.md` and the project overlay
- [ ] Backend tests pass: `{{BACKEND_TEST_CMD}}` (see `.claude/project/stack.md`)
- [ ] Frontend tests pass: `{{FRONTEND_TEST_CMD}}` (if applicable)
- [ ] Formatter clean: `{{FORMAT_CMD}}`
- [ ] Lint/format check clean: `{{LINT_CMD}}`
- [ ] No debug artifacts (see `rules/security.md` - language-specific debug functions list)
- [ ] Reviewer agent approved (see `.claude/agents/reviewer.md`)
- [ ] QA Engineer agent approved (see `.claude/agents/qa-engineer.md`)
- [ ] Deploy Actions section filled (see `rules/git-workflow.md`)

## i18n DoD (OPTIONAL - only if project is multilingual and task touches user-facing text or reference data)
Skip this entire section if the project is single-language. See `.claude/rules/i18n.md` for full rules.

- [ ] All new user-facing strings use the framework's translation helper on both frontend and backend - no hardcoded text in components, templates, views, or controllers
- [ ] New translation keys exist in ALL supported locale bundles (both frontend and backend where applicable)
- [ ] Keys follow dot-notation + camelCase, first segment matches the namespace (`common`, `auth`, `dashboard`, `settings`, `validation`, `errors`, plus project-defined domain namespaces)
- [ ] Placeholders use each side's native convention (frontend `{name}`, backend framework's syntax)
- [ ] Reference-data models use a `name_translations` JSON column with a localized accessor; API Resources return plain localized strings
- [ ] No heavy i18n library added without justification
- [ ] No `localStorage` / cookie writes for locale preference
- [ ] Authenticated requests resolve locale from `user.language`, guest requests from `Accept-Language`
- [ ] No hardcoded fallback locale - uses config / `DEFAULT_LOCALE` constant

## Deploy Actions (MANDATORY - see `rules/git-workflow.md`)
One or more categories, or `none`:
- `none` / Schema migration / Data migration / Cache or config clear / Queue restart / Dependency install / Manual step

## Notes
Implementation notes added by agents during work.
