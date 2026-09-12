---
name: new-task
description: Create a new task file in .claude/tasks/backlog/ with auto-incrementing ID and template-complete frontmatter (depends_on, epic, spec). Use when the CEO or orchestrator asks to create/add/file a task, when a product-manager is decomposing an epic into tasks, or when a standalone piece of work (not tied to a spec) needs a work order before an agent can start it.
allowed-tools: Read, Grep, Glob, Write
---

# New Task - Task Creation

Create a new task file in the file-based task management system. Used by the
Orchestrator directly, and by the product-manager during epic decomposition
(skills/new-epic).

Two things this skill deliberately does NOT do:

- **No BACKLOG.md, no `status:` field.** A task's state IS the folder it sits
  in: `tasks/backlog/` = queued, `tasks/active/` = in flight (moving through
  the pipeline), `tasks/done/` = merged. This skill always creates the file in
  `backlog/` - `advance.py` moves it to `active/` itself when the task starts
  (orchestration loop step 1), never before. Pipeline/in-flight run state is
  queried via `python .claude/tools/pipeline/state.py --resume` or `--show`,
  not a file.
- **No git branch.** The branch is created when the task STARTS (orchestration
  loop step 1), not when it is created. Creating branches at task-creation time
  litters the repo with dead branches for backlog items.

## Process

### Step 1: Determine the next task number

1. Scan `.claude/tasks/backlog/`, `active/` AND `done/` for `task-*.md`.
2. Take the highest 4-digit number found, add 1, zero-pad to 4 digits.
3. Task numbers are sequential - never skip, never reuse.

### Step 2: PRD-shaped intake

Before decomposing the request into task fields, capture in a few bullets
(5 max each, skip a category only if genuinely not applicable):

- **Problem statement** - what is broken or missing, in one or two sentences
- **Target user** - who hits this problem (role, persona, or system)
- **Success criteria** - observable signs the task is done, not just "works"
- **Constraints** - technical, timeline, or dependency limits to respect
- **Out of scope** - explicitly what this task will NOT cover

This is a compact intake, not a full spec - fold it straight into the
Description and Acceptance Criteria in Step 4, don't create a separate file.

### Step 3: Gather task information

Derive from context where possible; ask the CEO only for genuinely missing
requirements (and only during planning - never mid-pipeline).

| Field | Description | Example |
|-------|-------------|---------|
| **Type** | `feature`, `bugfix`, `techdebt`, `enhancement` | `feature` |
| **Title** | Short English title (< 80 chars) | `Implement user authentication` |
| **Description** | What needs doing, with context | 1-3 paragraphs |
| **Acceptance Criteria** | Testable checkboxes, 3-7 items; cite spec FRs when a spec exists | `- [ ] (FR-2) Users can register via API` |
| **Reason** | Business or technical motivation | `Required for MVP launch` |
| **Priority** | `high`, `medium`, `low` | `high` |
| **Assignee** | Owner agent | `senior-backend-dev` |
| **depends_on** | Task ids that must finish first | `[task-0006]` |
| **epic / spec** | Links when part of an epic (see skills/new-epic) | `epic-0001` / `spec-0001` |

Valid assignees: `architect`, `spec-developer`, `senior-backend-dev`,
`senior-frontend-dev`, `devops-engineer`, `data-engineer`, `qa-engineer`,
`technical-writer`, `ux-ui-designer`, `product-manager`, `reviewer`,
`business-analyst`, `financial-analyst`, `marketing-strategist`.

### Step 4: Create the task file

Copy `.claude/tasks/templates/task-template.md` to
`.claude/tasks/backlog/task-XXXX.md` and fill EVERY section the template
mandates - including Frontend Impact / Backend Dependencies (per
`rules/task-creation.md`), Definition of Done, and Deploy Actions. There is no
`status:` or `branch:` field - the task's folder is its state, and the branch
is created only when `advance.py` starts the task.

### Step 5: Present to the CEO

```
Task created:

  Number:     XXXX
  Type:       [type]        Priority: [priority]
  Title:      [title]
  Assignee:   [agent]
  depends_on: [...]         Epic: [epic or -]      Spec: [spec or -]
  File:       .claude/tasks/backlog/task-XXXX.md

  Acceptance Criteria:
  - [ ] ...
```

Never create a task without CEO visibility. During epic decomposition the
whole breakdown table is presented at once instead (skills/new-epic step 4).

## Error handling

- **Template missing**: recreate `.claude/tasks/` structure from the template
  repo before proceeding - do not improvise a format.
- **Number conflict** (file already exists): re-scan, take next number, note
  the conflict to the CEO.

## Rules

- Zero-pad to 4 digits: `0001`, not `1`
- One task = one future branch = one concern
- The task file is the work order: no work starts without it
- A task's state is its folder - never add a `status:` field back in
- Tasks born from an epic sit in `.claude/tasks/backlog/` until the CEO
  approves the breakdown (skills/new-epic step 5) and `advance.py` moves them
  to `active/`
