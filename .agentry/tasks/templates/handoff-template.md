---
task: {{TASK}}
title: {{TITLE}}
author: agent-name
date: {{DATE}}
merge_commit: {{MERGE_COMMIT}}
---

# Handoff: {{TASK}} - {{TITLE}}

> Written by the NEXT task's assignee BEFORE implementing anything. Write in
> your own words - the writing is the context absorption. A pasted task
> description fails validation (handoff.py --check). All {{...}} tokens are
> runtime tokens filled by `handoff.py --for`, not onboarding placeholders.

## What was done
FILL-ME: 3-8 sentences in your own words - what shipped, how it works now, what
behavior changed. Do not paste the task description; write what you understood
from tasks/done/{{TASK}}.md and the merge diff.

## Key decisions
FILL-ME: decisions made during implementation and WHY - trade-offs taken,
alternatives rejected, review/QA findings that changed the approach.

## Files touched
{{FILES}}

## Gotchas and lessons
FILL-ME: surprises, pitfalls, flaky areas, non-obvious couplings - anything the
next agent would otherwise rediscover the hard way.

## Impact on next tasks
FILL-ME: what this change enables or constrains for upcoming tasks; interfaces
and helpers the next task should build on instead of reinventing.

## Context loaded
FILL-ME: confirm each item with ONE concrete takeaway (a fact, not "I read it"):
- .agentry/project/project-context.md - <takeaway>
- spec for MY upcoming task (<spec-id>, or none with reason) - <takeaway>
- .agentry/tasks/done/{{TASK}}.md and its diff on main - <takeaway>
