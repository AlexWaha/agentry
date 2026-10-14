---
name: pipeline-plan
description: Switch the pipeline to plan mode. Approval level and swarm flag stay as they are. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-plan

Switch the pipeline to plan mode. Approval level and swarm flag stay as they are.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_mode` with `plan`.
2. `show`, then tell the User the resulting state in one line.

Rules: `${CLAUDE_PLUGIN_ROOT}/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
