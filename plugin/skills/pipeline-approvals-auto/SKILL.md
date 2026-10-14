---
name: pipeline-approvals-auto
description: Set the pipeline approval level to auto. Mode and swarm flag stay as they are. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-approvals-auto

Set the pipeline approval level to auto. Mode and swarm flag stay as they are.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_approvals` with `auto`.
2. `show`, then tell the User the resulting state in one line.

Rules: `${CLAUDE_PLUGIN_ROOT}/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
