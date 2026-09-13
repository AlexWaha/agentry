---
name: pipeline
description: Switch the session between build, plan and talk mode, and between manual, assisted and auto approval levels. Use when the CEO wants to discuss a project, test a hypothesis or design something without the task conveyor pushing code work forward, to switch back to implementation afterwards, or to change how many checkpoints the CEO has to answer. Triggers on "/pipeline", "/pipeline talk", "/pipeline plan", "/pipeline build", "/pipeline approvals", "давай обсудим", "просто поговорим", "без задач", "переключи режим", "смени уровень одобрения".
---

# Pipeline mode and approvals

The execution conveyor is built for coding tasks. It is the wrong tool when the
CEO wants to think out loud: the Stop hook keeps pushing the next task forward,
and every reply drags the backlog along with it.

Two independent dials govern the conveyor:

- **mode** (`.claude/state/mode`) - WHICH stage machine runs.
- **approvals** (`.claude/state/approvals`) - how many of that machine's
  checkpoints the CEO still has to answer.

Two dials rather than a matrix, because at three in the morning a matrix is
unreadable.

## Modes

| Mode | Conveyor | What it is for |
|---|---|---|
| `build` | on - `pipelines.build` | Default. Tasks advance implement -> test -> review -> diff-review -> ready -> done, commit and push gated by the CEO. |
| `plan` | on - `pipelines.plan` | Design. Tasks advance formalize -> draft -> plan-review -> approval -> breakdown -> done. Writes a brief, a plan, a spec, and task files into `.claude/tasks/backlog/`. No product code. |
| `talk` | off | Discussion, hypotheses, exploration. Nothing tracked, nothing written. |

`build` and `plan` both drive the conveyor, just over different stages - both
are gated stage machines registered and advanced through the same
`advance.py`. Only `talk` has no flow behind it at all.

## Approval levels

| Level | Grants without asking | What still needs the CEO |
|---|---|---|
| `manual` | nothing | every checkpoint: which task to take, the diff, the commit, the push |
| `assisted` | take a task, commit, skip diff-review when the code review is clean | the push |
| `auto` | all of the above, then it takes the next ready task | the push, merging into `main`, moving a task to `done`, answering planning questions - no level ever grants these |

The push is absent from every row on purpose: `approvals.NEVER_GRANTED` refuses
it at every level, and a per-stage `auto_approve` listing it does nothing (see
`rules/git-workflow.md`, "Push is never automatic, at any approval level").

A per-stage `auto_approve` list in `pipeline.json` layers on top of the level
and wins for every other checkpoint, so a single one can be automated without
raising the whole dial. An overnight `auto` run is still bounded by the push
approval and by the dependency chain: a
chain of dependent tasks advances by exactly one, since the next task needs
its predecessor merged into `main` first.

## How to run it

Read the argument the CEO passed after `/pipeline`.

**No argument** - report both the current mode and the current approval level, and stop:

```bash
python .claude/tools/pipeline/mode.py --show
python .claude/tools/pipeline/approvals.py --show
```

**`build` / `plan` / `talk`** - switch the mode and confirm:

```bash
python .claude/tools/pipeline/mode.py talk
```

**`approvals <level>`** (or a request phrased as changing how much is
auto-approved) - switch the approval level and confirm:

```bash
python .claude/tools/pipeline/approvals.py assisted
```

Then tell the CEO in one line which mode/level is active and what it means for
the work. Do not restate the whole table.

## What each mode changes for you

**build.** Business as usual. Follow `rules/orchestration.md` and
`rules/pipeline.md`: register tasks with `advance.py`, drive stages, park at the
CEO checkpoints.

**plan.** Its own gated flow (`formalize -> draft -> plan-review -> approval ->
breakdown -> done`, `pipelines.plan` in `pipeline.json`), registered with the
same `advance.py`. Write the brief and the plan under `.claude/plans/`, the
spec under `.claude/specs/`, and land task files in
`.claude/tasks/backlog/` - they sit there, queued, until the CEO switches to
`build` and says which to start. Do not implement product code on this flow.

**talk.** Answer, investigate, measure, prove or disprove. Read code freely,
run read-only queries, prototype in a scratch space if it helps you answer. Do
not write task files, do not create branches, do not edit product code. If the
discussion produces something worth keeping, say so and offer to capture it,
rather than silently creating files.

In both `plan` and `talk` the safety gates stay on: no push to a protected
branch, no destructive commands, no AI-authorship trailer, no em dash. Those are
not workflow, they are guardrails.

## Switching back

Nothing resets the mode or the approval level on its own. Both survive across
sessions on purpose, so a long design conversation - or an overnight `auto` run
- is not interrupted by a restart. The session-start hook prints the mode
whenever it is not `build`, so a forgotten switch stays visible.

When the CEO asks to implement something while `plan` or `talk` is active, do
not silently switch. Say which flow is active and ask whether to switch to
`build` first.

## What this is not

Neither dial disables the safety hooks, unlocks `main`, or skips review.
`mode` only decides which stage machine the Stop hook drives; `approvals` only
decides how many of that machine's checkpoints pass without asking. Merging
into `main`, moving a task to `done`, and answering questions raised during
planning always stay with the CEO, at every mode and every approval level.
