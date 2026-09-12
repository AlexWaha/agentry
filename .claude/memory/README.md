# Cross-Agent Memory Layers

Three layers of shared project memory, injected per pipeline stage (SubagentStart
hooks in `.claude/settings.json` -> `tools/memory/inject.py`) and kept fresh by
deterministic gates. Distinct from `.claude/agent-memory/<agent>/MEMORY.md`
(per-agent private habits) and from handoff docs (per-task narrative - the
distillation SOURCE for these layers).

| Layer | File | Content | Injected into | Cap |
|---|---|---|---|---|
| L1 codebase | `codebase.md` | Module map: what lives where, key entry points, dependency notes | planning + spec agents | 200 lines |
| L2 lessons | `lessons.md` | Mistakes never to repeat (distilled from bugfixes, reviews, CEO corrections) | spec + implementing + reviewing agents | 150 lines |
| L3 patterns | `patterns.md` (index) + `patterns/*.md` (detail) | Reusable code patterns observed used more than once | planning agents | 60 lines index |

## Freshness contract (enforced, not hoped for)

- **Session start:** `session_start.py` runs `tools/memory/codebase_sync.py --check`.
  It compares stored git heads (`codebase.meta.json`) with the actual repos; on
  drift it prints the changed paths and instructs updating `codebase.md`, then
  `codebase_sync.py --stamp`.
- **After every completed task:** `stop_gate.py` blocks starting the next task
  until the finished task's memory review is stamped:
  `python .claude/tools/memory/update.py --stamp --task task-XXXX
  [--lessons N --patterns N --l1-rows N | --none]`. Distill from the task's
  handoff doc: Gotchas -> `lessons.md`, reusable code -> `patterns.md` +
  `patterns/P-NNN-<name>.md`, touched modules -> `codebase.md` rows.
  `--none` is legal - the gate forces the review, not fabricated content.

## Entry formats

- `lessons.md`: `- [L-012] 2026-07-08 <area>: <mistake> -> <rule> (task-0042)`
- `patterns.md`: `- [P-007] <name>: use when <situation> -> patterns/P-007-<name>.md`
- `patterns/P-NNN-<name>.md`: the pattern template + at least 2 observed usage
  paths (a pattern used once is not a pattern)
- `codebase.md`: one row per module/dir: path, responsibility, key symbols,
  entry points

## Curation

When a file exceeds its cap: merge duplicates, move stale lessons to
`lessons/archive.md`, keep the highest-value entries inside the injected window.
PII/secret guardrail: memory is committed to git - record methods, never values.
