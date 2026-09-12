---
name: self-learning
description: Standardized procedure for recording, recalling, and curating lessons and patterns across agent memory, shared memory layers, and rules. Use when an agent makes a mistake or hits a surprise (record), before an error-prone action (recall), when a completed task needs its memory review (distill), or when a memory file outgrows its cap (curate).
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Self-Learning

Record what went wrong so it never recurs, capture patterns worth reusing,
recall past lessons before repeating a risky action, and curate memory so
high-value entries stay visible. Modes: record, distill, recall, curate.

## Three-tier model (where a lesson lands)

1. **Per-agent operational lesson** (only this agent needs it) -> that agent's
   native memory: `.claude/agent-memory/<agent>/MEMORY.md` (agents with
   `memory: project`; Claude Code auto-injects the first ~200 lines / 25KB).
2. **Cross-agent project lesson** (any agent on THIS project needs it) ->
   `.claude/memory/lessons.md` (L2 layer, injected into spec/dev/review
   dispatches - see `.claude/memory/README.md`).
3. **Universal policy** (every future project needs it) -> the right shared
   rule: `.claude/rules/<area>.md`.

Reusable CODE shapes go to the patterns layer (L3): index line in
`.claude/memory/patterns.md` + detail file `patterns/P-NNN-<name>.md` with the
template and at least 2 observed usage paths.

## Mode: record (single lesson, any time)

1. Pick the tier (above). For agent memory append in canonical format:
   - SIGNATURE: <short kebab-case tag describing the SITUATION>
     TRIGGER:   <when this lesson applies>
     WHAT:      <what went wrong - one sentence>
     WHY:       <root cause - one sentence>
     FIX:       <imperative, specific, checkable action>
     DATE:      <YYYY-MM-DD>
   For L2 use the one-line format:
   `- [L-NNN] YYYY-MM-DD <area>: <mistake> -> <rule> (task-XXXX)`
2. SIGNATURE describes the situation, not the symptom, so a future grep hits it.
3. PII/secret guardrail: memory is committed to git - record the METHOD, never
   a sensitive value.
4. Refine an existing entry instead of adding a near-duplicate.

## Mode: distill (after every completed task - gate-enforced)

The stop gate blocks the next task until the finished task is distilled:

1. Read the task's handoff doc (`.claude/tasks/handoffs/<task>.md`).
2. Gotchas and lessons -> L2 `lessons.md` entries (or agent MEMORY.md if
   single-agent); code used more than once -> L3 pattern; touched modules ->
   update `codebase.md` rows.
3. Stamp: `python .claude/tools/memory/update.py --stamp --task task-XXXX
   --lessons N --patterns N --l1-rows N` (or `--none` - a reviewed task with
   nothing worth recording is a legal outcome).
4. Refresh L1 freshness: `python .claude/tools/memory/codebase_sync.py --stamp`.

## Mode: recall (before error-prone actions)

Scan the injected memory layers and your MEMORY.md; for high-stakes actions
grep the full files (`grep -i "<keyword>" .claude/memory/lessons.md
.claude/agent-memory/<agent>/MEMORY.md`). Apply a matching FIX before acting.

## Mode: curate (when a file exceeds its cap)

Caps: agent MEMORY.md 200 lines / 25KB; lessons.md 150; patterns.md 60;
codebase.md 200. Merge duplicate entries, archive stale lessons to
`lessons/archive.md`, keep the highest-value entries inside the injected
window. Only merge/delete/reorder what exists; do not invent.

## Honest expectation

This gives persistence + disciplined recall, not autonomous self-improvement.
Quality compounds because lessons accumulate, get injected at dispatch, and are
re-read - gated by the distill stamp, recall discipline, and curation.
