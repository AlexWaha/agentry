---
name: pipeline-swarm-on
description: Turn the swarm flag ON. Mode and approval level stay as they are. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-swarm-on

Turn the swarm flag ON. Mode and approval level stay as they are.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_swarm` with `on`.
2. `show`, then tell the User the resulting state in one line.

Rules: `.claude/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
