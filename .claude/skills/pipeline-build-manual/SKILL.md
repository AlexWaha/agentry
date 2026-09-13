---
name: pipeline-build-manual
description: Switch to build mode with manual approvals - the conveyor stops at every checkpoint `workflow.mode` leaves in place: which task to take, the commit, and - in `pr` mode only - the push. `solo` mode has no push checkpoint; the local merge follows the commit. Use when the CEO wants to watch each step.
---

# Build, manual approvals

```bash
python .claude/tools/pipeline/mode.py build
python .claude/tools/pipeline/approvals.py manual
```

Then say in one line that build/manual is active: every checkpoint comes back to
the CEO. Do not restate the mode tables.

Full contract: `.claude/skills/pipeline/SKILL.md`.
