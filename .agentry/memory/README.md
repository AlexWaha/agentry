# Project Memory

One store, queried per dispatch: `.agentry/memory/memory.db` (SQLite with an FTS5
index, gitignored). It replaced four markdown files whose heads were injected
under a line cap - `codebase.md`, `lessons.md`, `patterns.md` and a per-agent
`agent-memory/<agent>/MEMORY.md`. That mechanism injected whatever sat at the top
of a file rather than what the task needed: on a real project the lessons file
reached 150 KB and travelled into every dev dispatch nearly whole, about 37k
tokens before the agent read a line of code.

Handoff docs (`.agentry/tasks/handoffs/`) are separate: per-task narrative, and
the distillation SOURCE for the rows below.

| Kind | Content | Injected into |
|---|---|---|
| `lesson` | A mistake never to repeat: signature, trigger, what, why, fix | every dispatch (dev, review, spec, planning) |
| `pattern` | A code shape used more than once: name, use_when, body | dev, review and planning agents |
| `module` | One row of the module map: path, responsibility, key symbols, notes | planning and spec agents |

## Retrieval (what an agent actually receives)

`SubagentStart` hooks in `.claude/settings.json` run
`tools/memory/inject.py --kinds <kinds> --limit <n>`. The hook takes the dispatch
text (the subagent prompt, falling back to the in-flight task file), turns it
into an FTS5 query, and injects the top-ranked rows - never a file head. The
injected block states its row count, so a thin result is visible instead of
silent, and the whole block is capped at 3800 bytes (`inject.BUDGET_BYTES`).
Zero matches inject nothing.

A missing, empty or corrupt store injects nothing and exits 0. Retrieval never
blocks a dispatch.

## CLI

```bash
M=.claude/tools/memory/memory.py

python $M --record --kind lesson --signature <kebab-tag> --trigger <when it applies> \
          --what <one sentence> --why <root cause> --fix <checkable rule> \
          [--area <tag>] [--task task-0044] [--agent <name>]
python $M --record --kind pattern --name <kebab-name> --use-when <situation> [--body <shape>]
python $M --record --kind module  --path <dir> --responsibility <one line> \
          [--symbols <entry points>] [--notes <dependency notes>]

python $M --query "text to match" [--limit 8] [--kinds lesson,pattern,module]
python $M --export [--out .agentry/memory/export.md]    # markdown, for human reading
python $M --stats
python $M --migrate                                    # legacy markdown -> store, idempotent
```

Every field is single-line and bounded; free-form prose is refused, and so is an
em or en dash. A signature must be a kebab-case tag naming the SITUATION, so a
future query hits it. Re-recording the same `(signature, agent)` is a no-op -
refine the existing row instead of adding a near-duplicate. Re-recording a known
module `path` updates that row, because the map tracks the tree.

## Freshness contract (enforced, not hoped for)

- **Session start:** `session_start.py` runs `tools/memory/codebase_sync.py --check`.
  It compares the git heads stored in the `meta` table with the actual repos; on
  drift it prints the changed paths and asks for the affected module rows to be
  re-recorded, then `codebase_sync.py --stamp`. No module rows at all -> it asks
  for the map.
- **After every completed task:** `stop_gate.py` blocks starting the next task
  until the finished task's memory review is stamped:
  `python .claude/tools/memory/update.py --stamp --task task-XXXX`. The stamp
  COUNTS ROWS in the store: it is refused unless the store gained at least one
  row since the previous stamp, or `--none` says the review found nothing worth
  recording. Distill from the task's handoff doc: Gotchas -> `lesson` rows,
  reusable code -> `pattern` rows, touched modules -> `module` rows.

## Guardrails

- The store is gitignored, like the markdown it replaced: it holds one project's
  internals and this repository is public. It does not travel to another machine
  and does not survive a fresh clone - back it up with the working tree if it
  matters, and put anything worth keeping permanently in `.claude/rules/`, which
  is committed.
- Record the METHOD, never a secret, credential or PII value.
- There is no curation rule any more. A queried store does not overflow an
  injection window, so rows accumulate; only duplicates and rows proven wrong
  get removed.
