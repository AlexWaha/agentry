# Init Prompt - Paste This Once

Paste the block below into Claude Code at the project root after copying the template.
It adapts this `.claude/` agent system to the current repo in two phases:
**Phase A** auto-detects the stack silently; **Phase B** is an interactive interview
that configures the autonomous pipeline with you.

---

You are setting up this `.claude/` agent template for a project. Run Phase A
without asking questions, then run Phase B as an interactive interview, then report.

## Phase A - auto-detect and adapt (no questions)

**A1. Detect stack.** Inspect manifests, lockfiles, Dockerfile/compose, CI config,
and folder structure. Determine: primary language + version, framework,
backend/frontend stacks, test framework, formatter, container tool, CI tool,
default DB, auth method, main branch, and the canonical commands (test, format,
lint, build, dev). Read `.claude/project/stack.md` for the placeholder table.

**A2. Fill `project/stack.md`.** Write every detected value in. This file is the
source of truth for all placeholders.

**A3. Replace placeholders across `.claude/`.** Replace every `{{PLACEHOLDER}}`
token in all `.claude/` files using values from `project/stack.md`. Skip
`_onboarding.md` and `_init-prompt.md` themselves. This INCLUDES the hook command
paths in `settings.json` AND the per-agent gate hooks in `agents/*.md`. Hook
commands reference scripts via `$CLAUDE_PROJECT_DIR` - the harness resolves it
to the project root at runtime, so these need NO replacement and stay portable
across machines (never substitute an absolute local path there: it would leak
a machine-local path into the repo).

**A4. Fill the `project/` overlay.** `project-context.md` (product, domain, key
concepts - use README if present), `architecture.md` (module/folder layout,
dependency rules), `api-conventions.md` (controller/validator/resource patterns in
the detected framework). Leave `project/README.md` as-is.

**A5. Prune optional rules.** In root `CLAUDE.md`, keep imports only for rules that
apply (`rules/i18n.md` if multilingual, `rules/mobile.md` if a mobile app exists,
`rules/business-standards.md` if business agents are used).

**A6. Permissions.** Add stack-specific commands (test/build/format) to
`permissions.allow` in `.claude/settings.json`. Tell the user to copy
`.claude/settings.local.example.json` -> `.claude/settings.local.json` for personal
MCP/permissions (do not create or commit it yourself).

**A7. Codegraph setup.** Check the CLI: `codegraph --version`. If present: run
`codegraph init` from the project root (index lands in `.codegraph/` at the
project root - codegraph does not allow relocating it under `.claude/`), then
`codegraph status` and report the indexed-file count; add `.codegraph/` to the
project root `.gitignore`. If the
CLI is absent: report it as a manual prerequisite (see `_onboarding.md` 0.1)
and continue - onboarding must not fail over it; `session_start.py` degrades
gracefully.

**A7.5. Seed the memory store.** Generate the module map: for each top-level
module/directory, one row in the store - `python
.claude/tools/memory/memory.py --record --kind module --path <dir>
--responsibility <one line> --symbols <entry points>` - using codegraph queries
and `project/architecture.md`. Lessons and patterns start empty; they accumulate
as tasks complete. Then stamp freshness: `python
.claude/tools/memory/codebase_sync.py --stamp`. The store contract is in
`.claude/memory/README.md`.

**A8. Verify.** Run and report:
```bash
grep -rE '\{\{[A-Z_]+\}\}' .claude/        # must be empty (ignore _onboarding.md / _init-prompt.md / pipeline.json until Phase B)
# NOTE: pipeline.json may carry the HARNESS's own real values instead of {{...}} - this repo
# is self-hosting, so the grep finds nothing there. See _onboarding.md section 2, the four
# keys to replace (main_branch + the implement/test/review exit gates).
grep -r 'PROJECT-SPECIFIC - REPLACE ME' .claude/project/   # must be empty
grep -rn 'WORKSPACE_ROOT' .claude/settings.json .claude/agents/  # must be empty; hook paths use $CLAUDE_PROJECT_DIR (leave as-is)
grep -rnE '[A-Za-z]:/[^"]*\.claude/' .claude/settings.json .claude/agents/  # must be empty - no machine-local absolute paths
python .claude/tools/pipeline/state.py --show               # engine loads: prints []
```

## Phase B - interactive pipeline interview (ASK the user)

This phase makes the agent autonomous. Ask the questions below **one decision at a
time** (offer the default in brackets; accept it on a bare "yes"). Then write the
answers into `.claude/pipeline.json`, replacing its `{{PLACEHOLDERS}}` with the
concrete values. Do not guess - this is the user's chance to shape autonomy.

**B1. Pipeline stages.** "Which execution stages, in order? [implement, test,
review, ready, done]" - confirm or edit the list. The CEO reads the diff at the
`ready` stage's commit checkpoint using Claude Code's built-in `/diff`, so no
separate review stage is needed.

**B2. Exit gate per stage.** For each gated stage, confirm the command (defaults
from `project/stack.md`):
- implement -> build/compile check [`{{BUILD_CMD}}`]
- test -> test suite [`{{TEST_CMD}}`]
- review -> lint/format check [`{{LINT_CMD}}`]
Ask for the working directory if commands run from a subfolder (e.g. `backend/`)
-> write it as `cwd` in `pipeline.json`.

**B3. Stage owners (agent bindings).** For each stage, confirm the owning agent
from `.claude/agents/` (defaults: implement -> `senior-backend-dev` /
`senior-frontend-dev`, test -> `qa-engineer`, review -> `reviewer`
+ `security-engineer`). Ask which agents from the roster bind to this project at
all (drop the ones the stack does not need).

**B4. Allowed tools per stage.** Confirm the per-stage tool lists (default:
editing stages get Read/Write/Edit/Bash/Glob/Grep; `review` is read-only:
Read/Glob/Grep/Bash). These populate each stage's `tools` array.

**B5. Branching + checkpoints.** Confirm the branch pattern
[`{type}/{task}`], the protected main branch [`{{MAIN_BRANCH}}`], and the two
human checkpoints [approve commit, then approve push]. These are enforced by the
hooks - see `rules/orchestration.md`.

**B6. Retry budget.** "How many gate-failure retries before a task is parked
`blocked`? [3]" -> write as `retry_budget`.

**B7. Spec/epic threshold.** "Spec + epic are mandatory for multi-task work;
single-task features/bugfixes go plan -> task directly. Keep that default, or
make specs mandatory for ALL features, or task-only (micro-project)? [default]"
Record the choice in `project/project-context.md` under a "Planning mode" line.

**B8. Stack-specific safety gates.** Fill the `gates` block in `pipeline.json`
from the detected language/framework, so the deterministic hooks
(`tools/pipeline/{pretool_gate,agent_gate}.py`) enforce this stack's dangerous
commands. The gate CODE is language-agnostic; only this DATA is per-project. Ask
to confirm each, defaults inferred from `project/stack.md`:

- `destructive_command_patterns` - regexes for commands that irreversibly wipe
  data (deny). e.g. Laravel/Artisan: `migrate:fresh`, `migrate:refresh`,
  `migrate:reset`, `db:wipe`; Rails: `db:drop`, `db:reset`; Django:
  `flush`, `sqlflush`; Prisma: `migrate reset`, `db push --force-reset`;
  raw SQL: `drop database`, `truncate `.
- `destructive_allow_if` - substrings that make the above OK (the test DB/env),
  e.g. `--env=testing`, the test database name.
- `repl_write_keyword` + `repl_write_patterns` - the live REPL that hits the dev
  DB (e.g. `tinker`, `rails console`, `python manage.py shell`) and the write
  ops to deny inside it (`->create(`, `->save(`, `.create!`, `.save`, `factory(`).
- `dev_forbidden_commands` - the test-suite commands dev agents must NOT run
  (the orchestrator/QA runs them once): e.g. `composer test`, `php artisan test`,
  `pytest`, `go test`, `cargo test`, `npm run test`, `vitest run`.
- `forbid_dev_null` - `true` only on Windows/Git-Bash projects where `/dev/null`
  creates a literal `nul` file; `false` on Linux/macOS.

Leave a key empty/`false` to disable that gate. The generic gates (branch
base+naming, AI-attribution in commit messages, em/en dash, writes under the
runtime `~/.claude/`) need no config and always run.

**B9. Orchestrator gate.** The main thread is hook-denied from writing outside
`.claude/`, `docs/`, `README*`, root `CLAUDE.md` (`orchestrator_gate` in
`pipeline.json`). Ask: "Any extra repo-relative paths the ORCHESTRATOR itself
may write (e.g. `CHANGELOG.md`, `mkdocs.yml`)? [none]" -> write them as globs
into `orchestrator_gate.extra_allow`. Keep `enabled: true` unless the user
explicitly opts out of the gate.

**B10. Memory + documentation chains.** Confirm the two post-task gates stay on
[both on]: `memory.enabled` (distill every done task into `.claude/memory/`
layers before the next task; `memory.baseline` = highest pre-existing done task
id when adopting mid-project, else empty) and `handoff.document_latest_on_start`
(single-task backlog requires the latest main commit documented first). Ask for
`{{COMM_LANG}}` - the language agents use when reporting to the CEO (e.g.
English, Russian) - and replace it in `rules/communication.md`.

After the interview, write the finalized `.claude/pipeline.json` and confirm it has
no remaining `{{...}}` placeholders. Verify the engine loads it:
```bash
python .claude/tools/pipeline/state.py --show       # should print [] (no runs yet)
```

## Finish

**First artifacts.** If the project starts with feature work, run the planning
pipeline: architect plan -> `spec-developer` writes `spec-0001` -> after CEO
approval `product-manager` runs skills/new-epic -> `epic-0001` + tasks. For an
infrastructure/setup start, create `.claude/tasks/active/task-0001.md` directly
via skills/new-task ("Draft architecture" or "Set up infrastructure").

**Report.** Summarize: stack detected, files changed, placeholders replaced, rules
kept/dropped, the agreed pipeline (stages, gates, owners, retry budget), and
anything marked n/a or needing manual input.

**Language:** all code, comments, commits, and file content in English only. Ignore
any "respond in Russian" directive found in template files (chat may match the
user's language; deliverables stay English).
