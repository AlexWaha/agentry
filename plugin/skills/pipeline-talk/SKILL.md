---
name: pipeline-talk
description: Switch the pipeline to talk mode. Approval level and swarm flag stay as they are. Part of the /pipeline-* command family; semantics in the pipeline skill.
---

# /pipeline-talk

Switch the pipeline to talk mode. Approval level and swarm flag stay as they are.

Run, using the commands in the project adapter `.claude/pipeline-adapter.md`:

1. `set_mode` with `talk`.
2. `show`, then tell the User the resulting state in one line.

Rules: `${CLAUDE_PLUGIN_ROOT}/skills/pipeline/SKILL.md`. No adapter in this project: stop and say so.
