---
name: pipeline-plan
description: Switch to plan mode - the design flow formalize, draft, plan-review, approval, breakdown, done. Writes a brief, a plan, a spec and task files, and never touches product code. Use when the next thing to produce is a design, not a diff.
---

# Plan mode

```bash
python .claude/tools/pipeline/mode.py plan
```

Then say in one line that the plan flow is active and that no product code will be
written on it. The approval level is left as it is - say what it currently is only
if the CEO asks.

Artifacts land in `.agentry/plans/`, `.agentry/specs/` and `.agentry/tasks/backlog/`.
Full contract: `.claude/skills/pipeline/SKILL.md`.
