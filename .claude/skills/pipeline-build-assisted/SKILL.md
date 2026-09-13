---
name: pipeline-build-assisted
description: Switch to build mode with assisted approvals - the conveyor takes a task and commits on its own, and stops at whichever checkpoint `workflow.mode` leaves last (the push in `pr` mode, the commit in `solo`). Use when the CEO wants the routine automated but the push still gated.
---

# Build, assisted approvals

```bash
python .claude/tools/pipeline/mode.py build
python .claude/tools/pipeline/approvals.py assisted
```

Then say in one line that build/assisted is active: taking a task and committing
happen without asking, and the run waits for the CEO at the last checkpoint
`workflow.mode` leaves in place - the push in `pr` mode, the commit in `solo`.
Do not restate the mode tables.

Full contract: `.claude/skills/pipeline/SKILL.md`.
