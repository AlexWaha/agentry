---
name: pipeline-build-manual
description: Switch the pipeline to build mode, approval level manual, swarm OFF, in one command. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-build-manual

Switch the pipeline to build mode, approval level manual, swarm OFF, in one command.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_mode` with `build`.
2. `set_approvals` with `manual`.
3. `set_swarm` with `off`.
4. `show`, then tell the User the resulting state in one line.

Rules: `.claude/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
