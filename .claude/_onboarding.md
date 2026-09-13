# Onboarding - Adapting This Template to a New Project

This file is the entry point for using this `.claude/` agent system on a brand-new project. Work through it top-to-bottom the first time you copy the template into a new repo.

## 0. What you just copied

```
.claude/
├── _onboarding.md           # this file
├── project/                # project-specific overlay (REPLACE THIS)
│   ├── README.md
│   ├── project-context.md   # product, domain, architecture summary
│   ├── architecture.md      # module/folder layout, dependency rules
│   ├── api-conventions.md   # framework-specific API patterns
│   └── stack.md             # stack, commands, env vars, routes
├── CLAUDE.md                # orchestrator instructions (keep as-is)
├── settings.json            # permissions (tune per project)
├── settings.local.example.json   # copy to settings.local.json, do NOT commit
├── agents/                  # agent role files (universal, with placeholders + per-agent gate hooks)
├── rules/                   # universal rules (with project-specific stubs)
├── skills/                  # reusable skills (incl. new-task, new-epic, self-learning)
├── hooks/                   # shell helpers (cleanup-nul.sh)
├── tools/
│   ├── pipeline/            # deterministic FSM: state/gate/advance/approve + agent_gate
│   ├── hooks/               # session_start.py, subagent_stop.py, dangerous_patterns.py
│   ├── memory/              # memory.py (store + CLI), inject.py (retrieval), update.py, codebase_sync.py
│   ├── review/              # diff_review.py (CEO visual diff-review UI)
│   └── setup/               # apply_optimization.py (fleet rollout tool)
├── pipeline.json            # declarative execution stage machine (finalized in onboarding Phase B)
├── specs/                   # specs written by spec-developer (_template.md inside)
├── state/                   # run.db pipeline state + review verdicts + memory stamps (gitignored)
├── tasks/
│   ├── active/              # empty - tasks you create
│   ├── done/                # empty - completed tasks
│   ├── epics/               # epics created by product-manager (spec decomposition)
│   └── templates/           # task + epic templates
├── memory/                  # project memory store: memory.db (lesson/pattern/module rows, gitignored) - see memory/README.md
└── plans/                   # empty - in-flight plans
```

Also at the PROJECT root (one level above `.claude/`): `.mcp.json` wires the
codegraph MCP server per-project - keep it committed.

## 0.1 Prerequisites (per machine, once)

- **Python 3** on PATH (pipeline engine and hooks)
- **codegraph CLI** - install via PowerShell (Windows) or shell (macOS/Linux)
  installer from github.com/colbymchenry/codegraph, or `npm i -g
  @colbymchenry/codegraph` (Node 22.5+). Verify: `codegraph --version`.
  Do NOT run `codegraph install` (it writes the user-global MCP config; this
  template wires MCP per-project via `.mcp.json`).

## 1. Replace `project/` contents

Everything under `.claude/project/` is placeholder content from the previous project. Delete or rewrite each file:

- [ ] `project/project-context.md` - write your product description, domain terminology, key concepts, phase activation
- [ ] `project/architecture.md` - draft once you have module/layer decisions (can be deferred until architect agent runs)
- [ ] `project/api-conventions.md` - draft once you know your framework's controller/validator/resource pattern
- [ ] `project/stack.md` - fill in stack, commands, env vars, routes, placeholder values
- [ ] `project/README.md` - usually left as-is (it explains what the folder is for)

## 2. Fill in placeholders across the template

Search-and-replace these across `.claude/` files. Values you define in `project/stack.md` are the source of truth.

| Placeholder | Meaning | Example |
|-------------|---------|---------|
| `{{PROJECT_NAME}}` | Product name | Acme SaaS Platform |
| `{{STACK}}` | Primary stack summary | Laravel 12 + React 19, Next.js 15, Django 5, Rails 7, etc. |
| `{{LANG}}` | Primary language + version | PHP 8.4, TypeScript 5.x, Python 3.12, Go 1.23 |
| `{{FRAMEWORK}}` | Main server framework | Laravel 12, Next.js 15, Django 5, NestJS 10 |
| `{{BACKEND_STACK}}` | Backend details | Laravel 12 + nwidart/laravel-modules v12 |
| `{{TEST_CMD}}` | How to run tests | `composer test`, `npm test`, `pytest`, `go test ./...` |
| `{{FORMAT_CMD}}` | How to auto-format | `composer format`, `npm run format`, `black .`, `gofmt -w .` |
| `{{LINT_CMD}}` | Format/lint check (no fix) | `composer format-lint`, `npm run lint`, `ruff check .` |
| `{{BUILD_CMD}}` | Build command | `npm run build`, `go build ./...`, `mvn package` |
| `{{DEV_CMD}}` | Dev server command | `npm run dev`, `php artisan serve`, `python manage.py runserver` |
| `{{MAIN_BRANCH}}` | Primary branch | `main` or `master` |
| `{{DEFAULT_DB}}` | Default database | PostgreSQL 16, MySQL 8, SQLite |
| `{{AUTH_METHOD}}` | Auth mechanism | Laravel Sanctum, NextAuth, JWT, OAuth2 |
| `{{TEST_FRAMEWORK}}` | Test framework | Pest 4, Jest, Vitest, pytest, RSpec |
| `{{FORMATTER}}` | Code formatter | Laravel Pint, Prettier, Black, gofmt |
| `{{CONTAINER_TOOL}}` | Container/orchestration | Docker Compose, Podman, Kubernetes |
| `{{CI_TOOL}}` | CI/CD | GitHub Actions, GitLab CI, CircleCI |

| `{{FRAMEWORK_DOCS_URL}}` | Link to framework docs | `https://laravel.com/docs/12.x` |

### Files that typically contain placeholders

- `.claude/settings.json` and `.claude/agents/*.md` - hook commands use `$CLAUDE_PROJECT_DIR` (runtime-resolved by the harness); they need no replacement and must NEVER be rewritten to absolute local paths (those leak into the repo and break on other machines)
- `.claude/project/stack.md` - the table there is the canonical map
- `.claude/agents/senior-backend-dev.md`
- `.claude/agents/senior-frontend-dev.md`
- `.claude/agents/qa-engineer.md`
- `.claude/agents/devops-engineer.md`
- `.claude/agents/architect.md`
- `.claude/agents/reviewer.md`
- `.claude/agents/technical-writer.md`
- `.claude/skills/feature-scaffold/SKILL.md`
- `.claude/skills/health-check/SKILL.md`
- `.claude/skills/migration/SKILL.md`
- `.claude/skills/write-tests/SKILL.md`
- `.claude/skills/infrastructure/SKILL.md`
- `.claude/skills/bug-fix/SKILL.md`
- `.claude/tasks/templates/task-template.md`

Grep to find anything still un-replaced:

```bash
grep -rE '\{\{[A-Z_]+\}\}' .claude/
```

### `pipeline.json` is already filled in - this repo is also a live project

This repository is self-hosting: it is the template AND a project the harness
runs on itself. So `.claude/pipeline.json` carries this harness's own real
values, and the grep above will NOT flag them. Four keys must be replaced with
your project's commands - the originals from the pristine template are on the
right:

| Key in `pipeline.json` | Value you inherit (this harness) | Template original |
|---|---|---|
| `main_branch` | `main` | `{{MAIN_BRANCH}}` |
| `pipelines.build.stages[implement].exit_gate.cmd` | `python -m compileall -q .claude/tools` | `{{BUILD_CMD}}` |
| `pipelines.build.stages[test].exit_gate.cmd` | `python -m unittest discover -s .claude/tools && python .claude/tools/pipeline/ui_evidence.py --task {task}` | `{{TEST_CMD}}` |
| `pipelines.build.stages[review].exit_gate.cmd` | `ruff check .claude/tools` | `{{LINT_CMD}}` |

Left as-is, your `implement` stage compiles the harness's Python and reports
green without touching your code at all - a gate that passes for the wrong
reason is worse than no gate. Replace all four before the first task.

## 3. Decide which optional rules apply

These rules are included but gated. Leave them imported in `CLAUDE.md` only if the project actually needs them.

| Rule | Keep if… |
|------|----------|
| `rules/i18n.md` | Project ships multilingual UI/API |
| `rules/mobile.md` | Project includes a mobile app (React Native, Flutter, native) |
| `rules/business-standards.md` | Business planning agents will be activated |

Unused rules can stay imported - they're marked `[DEFERRED]` or `[OPTIONAL]` and take no action on their own.

## 4. Configure permissions

- Keep `.claude/settings.json` committed. Add stack-specific permissions (e.g. your test/build/format commands) to its `permissions.allow` list.
- Copy `.claude/settings.local.example.json` → `.claude/settings.local.json` and fill in personal preferences. Never commit `settings.local.json` - add it to `.gitignore`.

## 5. Verify hooks work on your OS

- **Hook script paths are portable.** The `settings.json` and agent hooks invoke Python scripts via `$CLAUDE_PROJECT_DIR/.claude/tools/...` - the harness sets this variable for every hook run, on every machine. Do NOT replace it with an absolute path (leaks a machine-local path into the repo) and do NOT make paths relative (`python .claude/tools/...` breaks when the shell cwd drifts, and a missing-file exit bricks Bash/Edit/Write via the PreToolUse gate).
- Per-agent gate hooks (`agents/*.md`, `hooks.PreToolUse` blocks) use `$CLAUDE_PROJECT_DIR` the same way - leave them untouched.
- `tools/hooks/dangerous_patterns.py` - wired in dev-agent frontmatter (PostToolUse); scans edited files for debug/eval/exec patterns across PHP, JS/TS, Python, Ruby, Go. Extend the pattern table if your language is not covered.
- `hooks/cleanup-nul.sh` - Windows-only cleanup for accidental `nul` files from `/dev/null` redirects. Harmless on *nix.

## 5.1 Code knowledge graph (codegraph)

- From the project root run `codegraph init` - the index builds into
  `.codegraph/` at the project root. Codegraph does not allow relocating it
  under `.claude/` (`CODEGRAPH_DIR` accepts a plain directory name only), so
  add `.codegraph/` to the PROJECT root `.gitignore` - it is a per-machine
  artifact and stays per-project either way.
- Confirm `.mcp.json` exists at the project root (codegraph MCP server entry).
- Verify: `codegraph status` shows indexed files; in a session the
  `codegraph_explore` MCP tool answers a code question in one call.
- The index dir is gitignored (per-machine artifact). SessionStart runs
  `codegraph sync` automatically; the CLI's own watcher keeps it fresh.

## 6. Initial task

For feature work the planning pipeline produces the first artifacts:
`spec-0001` (spec-developer) -> `epic-0001` + tasks (product-manager via
skills/new-epic). For a single-task start (e.g. "Set up infrastructure"),
create `.claude/tasks/active/task-0001.md` directly via skills/new-task.
Remember: `advance.py` refuses tasks without acceptance criteria or with an
unapproved spec.

## 7. Self-check

Before starting real work, confirm:

- [ ] `project/project-context.md` describes your product, not the previous one
- [ ] `project/stack.md` has concrete commands (no placeholders left)
- [ ] `grep -rE '\{\{[A-Z_]+\}\}' .claude/` returns zero matches (or only inside `_onboarding.md` itself and `tasks/templates/handoff-template.md`, whose `{{...}}` are runtime tokens for `handoff.py --for`)
- [ ] `grep -r 'PROJECT-SPECIFIC - REPLACE ME' .claude/project/` is empty (you filled everything in)
- [ ] `.claude/settings.local.json` is gitignored
- [ ] `CLAUDE.md` imports only the rules that apply to this project

## 8. Autonomous pipeline (deterministic orchestration)

This template ships a deterministic orchestration layer so the agent runs tasks
end-to-end without babysitting. State and gate checks live in code, not the
model's context (`.claude/tools/pipeline/` + `.claude/state/run.db`). The full
contract is in `rules/orchestration.md`; the stage machine in `rules/pipeline.md`.

- The interactive **Phase B** of `_init-prompt.md` is where you configure it:
  pipeline stages, exit-gate commands, stage owners, allowed tools, branching, and
  the retry budget - written into `.claude/pipeline.json`.
- The `Stop` hook keeps the agent advancing (it will not stop mid-pipeline to ask
  "shall I continue?"); the `PreToolUse` hook enforces the two human checkpoints
  (approve commit, then approve push) via `tools/pipeline/approve.py`.
- Optional automation: the `ready` stage's `auto_approve` array (pipeline.json)
  lists checkpoints that need NO human approval - `advance.py` approves them
  itself. Default `[]` keeps both manual; decide at Phase B per project.
- Requires Python 3 on PATH. Verify the engine:
  `python .claude/tools/pipeline/state.py --show` (prints `[]` when idle).
- Obedience is enforced inside subagents too: dev/readonly/docs agents carry
  `hooks.PreToolUse` -> `tools/pipeline/agent_gate.py` in their frontmatter
  (see `rules/orchestration.md`, "Subagent enforcement").
- `SubagentStart` queries the memory store and injects the rows that match the
  dispatch; `SubagentStop` injects a lesson-recording nudge after each subagent
  finishes; `PreCompact` re-injects pipeline state so compaction cannot lose it;
  SessionStart syncs codegraph and checks module-map drift.
- `autoMemoryEnabled` is off and no agent declares `memory: project`: lessons go
  into `.claude/memory/memory.db` through `tools/memory/memory.py --record`
  (gitignored, see `rules/self-learning.md`).
- Planning flow: plan (architect) -> spec (spec-developer, `.claude/specs/`) ->
  epic + tasks (product-manager, `.claude/tasks/epics/`) -> CEO approval flips
  tasks `backlog` -> `active`. Spec + epic are mandatory for multi-task work.
- **Handoff chain** (`pipeline.json` `handoff` block, checked at Phase B): confirm
  `enabled`, decide `hard_edit_gate` (default true: code edits freeze while a
  completed task lacks its handoff doc), and set `baseline` - `""` for a fresh
  project; when adopting the template mid-project, the highest pre-existing done
  task id (those are grandfathered, no retro-docs). The doc chain itself is
  enforced by `advance.py` / `stop_gate.py` / `pretool_gate.py` via
  `tools/pipeline/handoff.py`. NOTE: the `{{...}}` tokens inside
  `tasks/templates/handoff-template.md` are RUNTIME tokens substituted by
  `handoff.py --for` - do NOT fill them during onboarding.
- **Orchestrator gate** (`pipeline.json` `orchestrator_gate`): the main thread
  is hook-denied from writing outside `.claude/`, `docs/`, `README*`, root
  `CLAUDE.md` - the "orchestrator never writes code" rule is enforced, not
  prompted. Add project-specific exceptions to `extra_allow` at Phase B (B9).
- **Memory layers** (`.claude/memory/`, contract in `memory/README.md`): L1
  codebase map (synced at every session start via
  `tools/memory/codebase_sync.py`), L2 lessons, L3 reusable patterns.
  SubagentStart hooks inject the right layers per agent type; the stop gate
  blocks the next task until the finished one is distilled and stamped
  (`tools/memory/update.py`). Seed the L1 map at Phase A7.5; set
  `memory.baseline` like the handoff baseline when adopting mid-project.
- **CEO diff-review stage** (`pipeline.json` stage `diff-review`): between
  review and ready, `advance.py` launches `tools/review/diff_review.py` - a
  local, stdlib-only browser UI with side-by-side syntax-highlighted diff,
  inline comments, and Approve / Request changes. Request changes appends the
  comments to the task file and resets the task to implement; the verdict lands
  in `.claude/state/review/<task>.json`. Remove the stage at Phase B for
  headless environments.
- **Documentation-first pickup** (`handoff.document_latest_on_start`): when the
  ready backlog holds a single task, the latest task-tagged commit on main must
  have its handoff doc (read it, or generate it) before implementation starts.

You're done. Delete or keep this file - it's useful as a reference for future template refreshes.
