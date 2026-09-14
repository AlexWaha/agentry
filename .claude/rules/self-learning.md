# Self-Learning

Meta-rule. The team must capture every mistake/surprise so it never repeats. Applies to Orchestrator and every agent.

---

## The Loop

After ANY of the following, update the knowledge base immediately - same session, before moving on:

1. **Mistake made** - wrong assumption, missed precondition, broke convention, redid existing work
2. **Surprise hit** - API behaved differently than expected, library has non-obvious quirk, tool failed unexpectedly
3. **CEO correction** - explicit "don't do that", "do it this way next time"
4. **Workflow friction** - same manual step repeated 3+ times, same clarification asked repeatedly

### Where to record

| Trigger | Update target |
|---------|---------------|
| Generalizable coding/architecture mistake | `.claude/rules/<area>.md` (add a "Common mistakes" or "Pre-flight checks" subsection) |
| Agent produced wrong output / missed step | `.claude/agents/<agent>.md` (tighten instructions) |
| Tool/API quirk specific to this project | `.agentry/project/` overlay (stack.md or a new "Quirks" section) |
| External library behavior | `.agentry/project/` overlay or rules file for that domain |
| Personal preference / workflow style | `.agentry/project/` overlay (project operational fact) or `.claude/rules/<area>.md` (generalizable norm) |

> **STRICT - never store project knowledge in the runtime/home memory dir.** Every lesson, fact, preference, and workflow note lives INSIDE the project (`.claude/rules/`, `.agentry/project/`, or the memory store `.agentry/memory/memory.db`), next to the code it is about. NEVER write project knowledge to the host/runtime memory location (e.g. a per-project dir under the agent host's home `.claude/projects/.../memory/`) - it is machine-local, uncommitted, and lost on reinstall or when working from another machine. This mirrors the global file-storage rule. If your runtime auto-creates a home memory file, treat it as a redirect stub only: it must contain nothing but a pointer to the in-project locations above.

### Format

Each captured lesson must contain:
- **What happened** (the mistake/surprise) - one sentence
- **Why** (root cause) - one sentence
- **Rule** (what to do/not do next time) - imperative, specific, checkable

No vague "be careful with X". Bad: "be careful with services". Good: "before creating any new file/class/module, run `Glob` for the target path AND `Grep` for the class/symbol name - if either matches, READ the existing file before assuming it needs creation".

---

## The memory store (two-tier)

Project memory is one SQLite store with an FTS5 index:
`.agentry/memory/memory.db`, holding `lesson`, `pattern` and `module` rows.
Contract and CLI: `.agentry/memory/README.md`. Record with:

```bash
python .claude/tools/memory/memory.py --record --kind lesson \
  --signature <kebab-tag naming the situation> --trigger <when it applies> \
  --what <one sentence> --why <root cause> --fix <checkable rule> \
  [--area <tag>] [--task task-XXXX] [--agent <name>]
```

Every field is single-line and bounded: the store refuses free-form prose, so a
lesson is recorded as structured fields or not at all.

There is no per-agent `MEMORY.md` and no `memory: project` frontmatter any
more. Claude Code auto-injected the first ~200 lines of that file, which is the
same blind window the layer files had - it delivers the head of a file, not what
the task needs. Retrieval we control replaced it: a `SubagentStart` hook queries
the store with the active task file's text and injects the ranked matches.

Pick the tier when recording a lesson:

| Lesson scope | Where it goes |
|--------------|---------------|
| THIS project (operational gotcha, per-role pattern, stack quirk) | a `lesson` row in the store, `--agent <name>` when it is role-specific |
| EVERY future project (convention, architecture, policy) | `.claude/rules/<area>.md` (the table above) |

The `self-learning` skill standardizes the record / recall / distill procedure -
invoke it instead of free-handing the format.

**Guardrails:** the store is NOT committed. `.agentry/memory/` is gitignored,
because it holds one project's internals and this template ships publicly. Two
consequences to plan around: it does not travel to another machine or survive a
fresh clone, so back it up with the rest of the working tree if it matters to
you; and a lesson worth keeping permanently belongs in `.claude/rules/`, which
IS committed. Record the METHOD either way, never a secret, credential, or PII
value. There is no curation-at-overflow rule: a queried store does not overflow
an injection window, so rows accumulate and only duplicates or rows proven wrong
get removed. This is persistence + disciplined recall, NOT autonomous
self-improvement.

### Automated triggers

Two hooks keep the loop honest (both fail-open, both quiet unless actionable):

- **SubagentStart** (`tools/memory/inject.py`): queries the store with the text
  of the task file(s) in `.agentry/tasks/active/` - not the dispatch prompt,
  which this event's payload does not carry - and injects the top-ranked rows,
  with their count, under the `memory.inject_budget_bytes` ceiling in
  `.agentry/pipeline.json` (default 3800, measured on the block inside the
  hook's JSON envelope rather than on the whole emitted object). An absent or
  corrupt store injects nothing. Delivery needs that envelope:
  `{"hookSpecificOutput": {"hookEventName": "SubagentStart",
  "additionalContext": "..."}}`. Plain stdout is DISCARDED on this event, which
  is how this hook delivered nothing at all for as long as it printed raw text
  (task-0081) - so any hook added here injects through the envelope or it does
  not inject.
- **SubagentStop** (`tools/hooks/subagent_stop.py`): when a subagent finishes,
  the orchestrator gets a one-line reminder, with the exact command, to record any
  lesson from its report.

Note: the code knowledge graph (codegraph, see `rules/code-retrieval.md`) does
NOT store lessons - it indexes code structure only. Lessons live in the store;
policy lives in these rule files, under version control.

---

## Mandatory Pre-Flight Checks

Before creating ANY new artifact (a new file, class, module, migration, controller, request validator, resource, command, job, factory, seeder, test file, or any other named unit of code), do this check first:

1. **Glob the target path** - does the file already exist?
2. **Grep the class/symbol name** - is it referenced or declared anywhere?
3. **Read the task file fully** - does it say "implement" or "extend/fix"?
4. **Check `git log --all --oneline -- <path>`** if Glob suggests the file was once there and was removed

If ANY check turns up a hit: STOP, READ the existing artifact, and decide if the task is "create" (rare - only if the existing one is broken) or "extend/refactor" (common). Report the finding to Orchestrator before writing code.

**Why:** Recreating an existing artifact wastes a full agent cycle, creates merge conflicts, and produces a worse-quality second copy that the original tests don't cover. This has happened in practice: an agent started rewriting a service that already existed because it didn't Glob first. Reading takes seconds; rewriting takes minutes and introduces drift.

---

## Anti-patterns

- **"I'll just write it from scratch, faster than reading"** - false. Reading takes seconds; rewriting takes minutes and introduces bugs
- **"It's a small lesson, not worth recording"** - false. The 4th time you "almost" repeat a mistake, you wish you had written it down the first time
- **"I'll write it down later"** - false. Context window evaporates; "later" never arrives
- **Vague rules ("be careful with X")** - useless. Rules must be checkable: "before X, do Y"
- **Recording the symptom, not the cause** - useless. "Tests failed" is not a lesson. "This test framework requires explicit base-class binding in submodule tests because its auto-discovery doesn't cross module boundaries" is a lesson

---

## Orchestrator Responsibility

The Orchestrator must:
- Notice when an agent made a mistake/surprise during a task report
- Decide where the lesson belongs (rule / agent / `project/` overlay / memory)
- Write the update IN THE SAME SESSION, before delegating the next task
- If the mistake came from a missing pre-flight check, ALWAYS update agent prompts to include that check by default

If you finish a task without writing down at least one lesson, ask: "did really nothing surprise me?" If something did, capture it.
