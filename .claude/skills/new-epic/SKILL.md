---
name: new-epic
description: Create an epic file under .claude/tasks/epics/ from an approved spec and decompose it into linked task files with bidirectional depends_on. Use when a spec-developer's spec has status approved and needs breaking into tasks, when the CEO says "turn this spec into tasks" or "plan the epic", or when a product-manager is starting sprint planning for a new spec.
allowed-tools: Read, Grep, Glob, Write
---

# New Epic - Spec Decomposition

Turn an APPROVED spec into an epic file plus its task breakdown. Owner:
`product-manager` (support: `sprint-prioritizer` for ordering). This is the
last planning step before the CEO releases work to the execution pipeline.

## Preconditions

1. The spec exists in `.claude/specs/` with `status: approved`. A draft spec
   MUST NOT be decomposed - send it back to the spec-developer / CEO instead.
2. The spec's Open Questions section is empty.
3. `.claude/tasks/epics/` and `.claude/tasks/templates/epic-template.md` exist.

## Process

### Step 1: Number the epic

Highest existing id across `.claude/tasks/epics/` plus 1, zero-padded to 4
digits. Epics and tasks have INDEPENDENT number sequences.

### Step 2: Create the epic file

Copy `.claude/tasks/templates/epic-template.md` to
`.claude/tasks/epics/epic-XXXX-<slug>.md`. Fill frontmatter (`spec:` points to
the approved spec id) and the Objective / Sequencing / Out of Scope sections
from the spec. `status: planned`.

### Step 3: Decompose into tasks

Apply `rules/task-creation.md` in full (cross-layer impact analysis, paired
backend/frontend tasks, bidirectional `depends_on`). For each task:

- Create it via `skills/new-task` with `status: backlog` (NOT active),
  `epic: epic-XXXX`, `spec: spec-XXXX`, and `depends_on` filled
- Every acceptance criterion in the task cites the spec FR it covers
- Every FR of the spec is covered by at least one task - N-of-N: list the FRs,
  map them, no orphans
- Record each task id in the epic's `tasks: []` frontmatter list and the
  Task Breakdown table

### Step 4: Present to the CEO

Show the breakdown table (task, title, assignee, depends_on, FRs covered) plus
the sequencing rationale. Give a recommendation per the checkpoint-advice rule
(rules/orchestration.md) if anything is contentious.

### Step 5: On CEO approval

Flip each task's `status: backlog` to `status: active` (this is exactly the
hold mechanism the Stop hook honors - backlog tasks are invisible to the
conveyor). Flip the epic to `status: active`. Work starts per the execution
pipeline; branches are created when a task STARTS, not here.

## Epic completion (bookkeeping)

When the last linked task's file moves to `tasks/done/`, flip the epic to
`status: done` and record the date. The execution pipeline itself remains
task-only - epics are grouping metadata, never run.db rows.

## Rules

- Never decompose a draft spec
- Never create tasks as `active` before CEO approval of the breakdown
- Never leave an FR uncovered by tasks, and never invent tasks with no FR
- One epic = one spec; if a spec is too big for one epic, that is a spec
  problem - send it back
