---
name: pipeline-swarm-off
description: Turn the swarm flag OFF (one task at a time). Mode and approval level stay as they are. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-swarm-off

Turn the swarm flag OFF (one task at a time). Mode and approval level stay as they are.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_swarm` with `off`.
2. `show`, then tell the User the resulting state in one line.

Rules: `.claude/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
