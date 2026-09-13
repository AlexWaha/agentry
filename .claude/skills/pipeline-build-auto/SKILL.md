---
name: pipeline-build-auto
description: Switch to build mode with auto approvals - the conveyor takes a task, implements, tests, reviews and commits on its own, then parks at whichever checkpoint `workflow.mode` leaves last (the push in `pr` mode, the commit in `solo`) and waits for the CEO. Use when the CEO wants a run to get as far as it can unattended.
---

# Build, auto approvals

```bash
python .claude/tools/pipeline/mode.py build
python .claude/tools/pipeline/approvals.py auto
```

Then say in one line that build/auto is active: everything through the commit
happens without asking, and the push still waits for the CEO in both modes. In
`pr` mode merging into `main` and moving a task to `done` wait for him as well;
in `solo` mode the local merge follows the approved commit, and `done` follows
the merge. Do not restate the mode tables.

This level does NOT grant the push, and no level does: `approvals.NEVER_GRANTED`
refuses it ahead of both the level and any per-stage `auto_approve` list (see
`rules/git-workflow.md`, "Push is never automatic, at any approval level"). Never
tell the CEO that an auto run will push on its own. Where an unattended run parks
follows `workflow.mode` in `pipeline.json`: the push checkpoint in `pr` mode, the
commit checkpoint in `solo` mode, where the local merge follows (`advance.py`
`stage_checkpoints()` drops the push checkpoint there).

Today this level differs from `assisted` in intent only: `approvals.GRANTS` gives
both the same `{take, commit}` set, so the two behave identically until the
difference is made real or the levels are merged (task-0050).

Full contract: `.claude/skills/pipeline/SKILL.md`.
