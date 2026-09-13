---
id: XXXX
title: Short descriptive title
status: planned
spec: spec-XXXX
created: YYYY-MM-DD
tasks: []
---

# Epic XXXX: Title

## Objective

What this epic delivers when every task below is done. One paragraph, traceable
to the spec's Problem and Goals.

## Task Breakdown

Every task is created via skills/new-task with `epic:` and `spec:` frontmatter
pointing back here. Tasks are born `status: backlog`; CEO approval of this
breakdown flips them to `active` (that flip is what releases them to the
execution pipeline).

| Task | Title | Assignee | depends_on | FRs covered |
|------|-------|----------|------------|-------------|
| task-XXXX | ... | senior-backend-dev | - | FR-1, FR-2 |
| task-XXXX | ... | senior-frontend-dev | task-XXXX | FR-3 |

## Sequencing Rationale

Why the dependency order above - what must exist before what, and which tasks
can run in parallel.

## Out of Scope

Work that belongs to the spec's Non-Goals or to a future epic. Explicit, so
spec drift is detectable at review.

## Completion

status: planned -> active (first task starts) -> done (last task file moved to
tasks/done/). The product-manager or orchestrator flips `status: done` and
records the completion date here - bookkeeping, not code.
