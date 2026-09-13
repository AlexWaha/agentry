---
name: self-learning
description: Standardized procedure for recording, recalling and distilling lessons and patterns in the project memory store and the shared rules. Use when an agent makes a mistake or hits a surprise (record), before an error-prone action (recall), or when a completed task needs its memory review (distill).
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Self-Learning

Record what went wrong so it never recurs, capture patterns worth reusing, and
recall past lessons before repeating a risky action. Modes: record, distill,
recall.

Everything lands in one store: `.agentry/memory/memory.db` (SQLite + FTS5), with
`lesson`, `pattern` and `module` rows. Contract and CLI:
`.agentry/memory/README.md`. The CLI is
`python .claude/tools/memory/memory.py`, referred to below as `$M`.

## Two tiers (where a lesson lands)

1. **This project** (operational gotcha, per-role habit, stack quirk) -> a
   `lesson` row in the store. Add `--agent <name>` when it is specific to one
   role, `--area <tag>` for a subject tag.
2. **Every future project** (convention, architecture, policy) -> the right
   shared rule: `.claude/rules/<area>.md`, which is committed.

Reusable CODE shapes go in as `--kind pattern` (a shape used once is not a
pattern). Module-map facts go in as `--kind module`.

## Mode: record (single lesson, any time)

```bash
python $M --record --kind lesson \
  --signature <kebab-case tag naming the SITUATION> \
  --trigger <when this lesson applies> \
  --what <what went wrong, one sentence> \
  --why <root cause, one sentence> \
  --fix <imperative, specific, checkable action> \
  [--area <tag>] [--task task-XXXX] [--agent <name>]
```

1. SIGNATURE describes the situation, not the symptom, so a future query hits it.
2. Every field is one line. The store refuses paragraphs, missing fields,
   oversized fields and em/en dashes - record structured fields or nothing.
3. Re-recording the same signature is a no-op: refine the existing row instead
   of adding a near-duplicate (`$M --query "<topic>"` first).
4. PII/secret guardrail: record the METHOD, never a sensitive value.

## Mode: distill (after every completed task - gate-enforced)

The stop gate blocks the next task until the finished task is distilled:

1. Read the task's handoff doc (`.agentry/tasks/handoffs/<task>.md`).
2. Gotchas and lessons -> `--kind lesson`; code used more than once ->
   `--kind pattern`; touched modules -> `--kind module` (recording a known path
   updates that row).
3. Stamp: `python .claude/tools/memory/update.py --stamp --task task-XXXX`. The
   stamp counts rows in the store and is REFUSED unless it gained at least one
   row since the previous stamp - or `--none`, which is legal: a reviewed task
   with nothing worth recording is an outcome, not a failure.
4. Refresh module-map freshness: `python .claude/tools/memory/codebase_sync.py --stamp`.

## Mode: recall (before error-prone actions)

Read the memory block injected at dispatch. For high-stakes actions query the
store directly - it holds more than the injected top rows:

```bash
python $M --query "<the action you are about to take>" --limit 8
python $M --export        # whole store as markdown, for a full read
```

Apply a matching FIX before acting.

## Honest expectation

This gives persistence + disciplined recall, not autonomous self-improvement.
Quality compounds because lessons accumulate, get queried at dispatch, and are
re-read - gated by the distill stamp and recall discipline. Rows accumulate
without a curation chore: retrieval ranks, so the store does not overflow an
injection window the way a capped markdown file did.
