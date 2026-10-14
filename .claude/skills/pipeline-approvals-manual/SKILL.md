---
name: pipeline-approvals-manual
description: Set the pipeline approval level to manual. Mode and swarm flag stay as they are. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-approvals-manual

Set the pipeline approval level to manual. Mode and swarm flag stay as they are.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_approvals` with `manual`.
2. `show`, then tell the User the resulting state in one line.

Rules: `.claude/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
