---
name: pipeline-build-assisted
description: Switch to build mode with assisted approvals - the conveyor commits on its own for a task the CEO started, then stops after that task; it does not take the next one. It waits at whichever checkpoint `workflow.mode` leaves last (the push in `pr` mode, the commit in `solo`). Use when the CEO wants one task run unattended to its last checkpoint, with the push still gated.
---

# Build, assisted approvals

```bash
python .claude/tools/pipeline/mode.py build
python .claude/tools/pipeline/approvals.py assisted
```

Then say in one line that build/assisted is active: the commit happens without
asking, the CEO starts each task, and the conveyor stops after the current one
instead of taking the next ready task (that is `auto`, `stop_gate.py` reads the
`take` checkpoint for it). The run waits for the CEO at the last checkpoint
`workflow.mode` leaves in place - the push in `pr` mode, the commit in `solo`.
Do not restate the mode tables.

Full contract: `.claude/skills/pipeline/SKILL.md`.
