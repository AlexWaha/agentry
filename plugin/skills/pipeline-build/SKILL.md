---
name: pipeline-build
description: Switch the pipeline to build mode with swarm OFF (one task at a time). Approval level stays as it is. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-build

Switch the pipeline to build mode with swarm OFF (one task at a time). Approval level stays as it is.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_mode` with `build`.
2. `set_swarm` with `off`.
3. `show`, then tell the User the resulting state in one line.

Rules: `${CLAUDE_PLUGIN_ROOT}/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
