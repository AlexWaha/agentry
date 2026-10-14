---
name: pipeline-approvals-assisted
description: Set the pipeline approval level to assisted. Mode and swarm flag stay as they are. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-approvals-assisted

Set the pipeline approval level to assisted. Mode and swarm flag stay as they are.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_approvals` with `assisted`.
2. `show`, then tell the User the resulting state in one line.

Rules: `.claude/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
