---
name: pipeline-talk
description: Switch to talk mode - the conveyor is off entirely. Discussion, hypotheses, measurement and exploration, with nothing tracked and no task files, branches or product code written. Use when the CEO wants to think out loud.
---

# Talk mode

```bash
python .claude/tools/pipeline/mode.py talk
```

Then say in one line that the conveyor is off: reading code and running read-only
checks stay available, task files, branches and product edits do not happen. The
approval level is irrelevant here - there is no conveyor to approve anything on.

Full contract: `.claude/skills/pipeline/SKILL.md`.
