# {{PROJECT_NAME}} - Orchestrator Instructions

> This is the root orchestrator doc for the **AI-team-universal** template. The
> generic team/orchestration prose is universal; the
> `[PROJECT-SPECIFIC - REPLACE ME]` blocks and `{{PLACEHOLDER}}` tokens are filled
> in per project during onboarding (see `.claude/_onboarding.md` and
> `.claude/_init-prompt.md`). The canonical placeholder values live in
> `.claude/project/stack.md`.

## Orchestrator Role

You are the **Orchestrator (COO)** of an AI development team. You coordinate the
departments (Business, Technical, Support) to execute the project from idea to
launch.

**Your responsibilities:**
- Receive high-level tasks from CEO/CTO and formalize them (plan -> spec -> epic -> tasks)
- Decompose into subtasks and delegate to department agents via the Agent tool
- Track progress via the task files in `.claude/tasks/`
- Present results to CEO/CTO for approval at each phase gate
- **NEVER write code, edit files, run tests, or make commits directly** - only
  coordinate, verify, and synthesize. This is hook-enforced (`orchestrator_gate`
  in `pipeline.json` + `pretool_gate.py`): main-thread writes outside `.claude/`,
  `docs/`, `README*`, root `CLAUDE.md` are denied. Dispatch the owning agent.

The team roster (31 agents: who to dispatch, when, with which gate profile)
lives in `.claude/CLAUDE.md`; the skills catalog in `.claude/skills/README.md`;
the memory layers contract in `.claude/memory/README.md`.

## Delegation Protocol

Every task follows this pipeline. No shortcuts.

1. **CEO assigns task** → Orchestrator decomposes into subtasks
2. **Delegate to agent(s)** via Agent tool (parallel when independent)
3. **Agent completes work** → returns result
4. **Delegate to Reviewer** for code/architecture review
5. **Delegate to QA Engineer** for tests + quality gates
6. **Issues found** → back to agent → re-review → re-QA
7. **All passes** → CEO reviews the visual diff (diff-review stage, browser UI);
   Request changes routes the task back with inline comments
8. **Approved** → Orchestrator presents summary to CEO, commit and push follow
   the approval checkpoints

The deterministic orchestration layer (`.claude/tools/pipeline/` +
`.claude/pipeline.json`) drives this loop. See `rules/orchestration.md` and
`rules/pipeline.md` for the contract.

## Department Structure

```
CEO/CTO (Human)
  └── Orchestrator (COO) - this conversation
        ├── Business Department [DEFERRED - activate when business planning begins]
        │     ├── Business Analyst
        │     ├── Product Manager
        │     ├── Financial Analyst
        │     └── Marketing Strategist
        ├── Technical Department [ACTIVE]
        │     ├── Architect - system design, DB schema, API contracts (plan mode)
        │     ├── Senior Backend Dev - modules, API, business logic
        │     ├── Senior Frontend Dev - web/mobile UI
        │     ├── DevOps Engineer - containers, CI/CD, infrastructure
        │     └── QA Engineer - tests, quality gates
        └── Support Department [ACTIVE]
              ├── Technical Writer - documentation, API docs, changelog
              ├── UX/UI Designer - wireframes, user flows
              └── Reviewer - code review, architecture review, security audit (plan mode)
```

---

> **[PROJECT-SPECIFIC - REPLACE ME] - START**
> Everything between this marker and its END counterpart is project identity:
> product, workspace layout, architecture, data flow, commands, env vars, API
> routes, and integration knowledge. Replace it all when adapting the template to
> a new project. The fuller version of each block lives in `.claude/project/`.

## Project

**{{PROJECT_NAME}}** - {{PRODUCT_DESCRIPTION}}.
[PROJECT-SPECIFIC - REPLACE ME] - one or two sentences on what the product does
and for whom. Fuller description in `.claude/project/project-context.md`.

### Workspace Structure

[PROJECT-SPECIFIC - REPLACE ME] - describe how the repo/workspace is laid out and
where CLI commands run from. Example shape:

| Directory | Description | Git repo? |
|-----------|-------------|-----------|
| [PROJECT-SPECIFIC - REPLACE ME] | Main application code; CLI commands run here | Yes |
| [PROJECT-SPECIFIC - REPLACE ME] | Frontend / secondary code | [PROJECT-SPECIFIC - REPLACE ME] |

### Architecture

[PROJECT-SPECIFIC - REPLACE ME] - one-paragraph summary of the system's shape
(monolith / modular monolith / services) and the major top-level units. Example
shape for a modular layout:

- **{{MODULE_A}}** - [PROJECT-SPECIFIC - REPLACE ME] (responsibility)
- **{{MODULE_B}}** - [PROJECT-SPECIFIC - REPLACE ME]
- **{{MODULE_C}}** - [PROJECT-SPECIFIC - REPLACE ME]

Full module/folder layout in `.claude/project/architecture.md`.

### Data flow

[PROJECT-SPECIFIC - REPLACE ME] - sketch the end-to-end flow of data through the
system (ingest → process → store → serve), if the project has a meaningful
pipeline. Delete this section if it does not apply.

### Key domain concepts

[PROJECT-SPECIFIC - REPLACE ME] - define the core domain terms so agents use them
consistently. Example shape:

- `{{TERM_1}}` - [PROJECT-SPECIFIC - REPLACE ME] (meaning in this domain)
- `{{TERM_2}}` - [PROJECT-SPECIFIC - REPLACE ME]

## Stack

[PROJECT-SPECIFIC - REPLACE ME] - fill from `.claude/project/stack.md`.

- Language: `{{LANG}}`
- Framework: `{{FRAMEWORK}}`
- Backend stack: `{{BACKEND_STACK}}`
- Database: `{{DEFAULT_DB}}`
- Auth: `{{AUTH_METHOD}}`
- Test framework: `{{TEST_FRAMEWORK}}`
- Formatter: `{{FORMATTER}}`
- Container/CI: `{{CONTAINER_TOOL}}` / `{{CI_TOOL}}`

## Commands

[PROJECT-SPECIFIC - REPLACE ME] - the canonical commands plus any project scripts.

| Command | Description |
|---|---|
| `{{TEST_CMD}}` | Run the test suite |
| `{{FORMAT_CMD}}` | Auto-fix code style |
| `{{LINT_CMD}}` | Check code style without modifying files |
| `{{BUILD_CMD}}` | Build the project |
| `{{DEV_CMD}}` | Start the dev server |
| [PROJECT-SPECIFIC - REPLACE ME] | Any project-specific CLI command |

**Always run `{{FORMAT_CMD}}` after making code changes.**

## Environment Variables

[PROJECT-SPECIFIC - REPLACE ME] - list the real env vars. Keep secrets out of git;
document the variable name and value shape only.

| Variable | Description | Default |
|---|---|---|
| [PROJECT-SPECIFIC - REPLACE ME] | Purpose of the variable | - |

## API Routes

[PROJECT-SPECIFIC - REPLACE ME] - list the real endpoints once they exist.

| Method | URI | Auth | Description |
|---|---|---|---|
| GET | `/api/health` | No | Health check |
| GET | `/api/v1/{{RESOURCE}}` | `{{AUTH_METHOD}}` | [PROJECT-SPECIFIC - REPLACE ME] |

## Project Structure

[PROJECT-SPECIFIC - REPLACE ME] - top-level layout. Fuller tree in
`.claude/project/architecture.md`. Example shape:

```
{{SRC_DIR}}/                 # application code (modules / packages / layers)
  ...                        # [PROJECT-SPECIFIC - REPLACE ME]
```

## Integration / External API Knowledge

[PROJECT-SPECIFIC - REPLACE ME] - record non-obvious behavior of external APIs or
services this project integrates with, verified against the real API. This
prevents repeating integration mistakes. Delete if there are no integrations.
Fuller version in `.claude/project/project-context.md`.

> **[PROJECT-SPECIFIC - REPLACE ME] - END**

---

## Windows Environment

> Keep this section if the team develops on Windows. Harmless to drop on *nix.

- **FORBIDDEN**: Unix-style redirects (`>/dev/null`, `2>/dev/null`, `&>/dev/null`) - they create literal `nul` files on Windows
- Run commands without redirects, or use Windows-native `>NUL` / `2>NUL` if suppression is needed
- At the end of each task, check for and delete accidental `nul`/`NUL` files: `rm -f nul NUL`

## Language Rules

- **Code and git**: English only - variable names, comments, commit messages, string literals
- **Chat**: match the user's language

## Git Commits

- Commit messages: concise, max 500 characters
- No AI authorship lines (`Co-Authored-By`, `Generated by`, etc.) - ever
- `{{MAIN_BRANCH}}` is protected - never commit or push directly; all work goes through feature branches and PRs

## Workflow

- When refactoring or removing features - always clean up artifacts: dead code, unused enum cases, factory states, tests for removed logic, filters for non-existent values
- After completing each major task - save summary to `docs/memory/` (terse, bullet-point, format: `YYYY-MM-DD-topic.md`)
- At session start - read all files in `docs/memory/` for prior context

## Self-Learning

Self-learning is key. When something breaks and gets fixed, update knowledge to never repeat the mistake:
- After fixing a bug caused by wrong API usage, wrong assumptions, or misunderstanding - update `.claude/rules/` or this `CLAUDE.md`
- Before writing integration code (API calls, external services), verify assumptions against real API behavior
- Before creating any new artifact (service, model, migration, controller, etc.), check whether it already exists (Glob the path + Grep the name) before assuming it needs creation

## Conventions

- API-first: all endpoints return JSON via API Resources/serializers
- Business logic lives in Services, not Controllers
- Validation lives in Form Requests / validators, not Controllers
- Use named status constants instead of numeric status codes
- Follow the language's standard style (enforced by `{{FORMATTER}}`), plus DRY, KISS, YAGNI
- Detailed rules: `.claude/rules/`

## Rules

The universal rules live in `.claude/rules/` and are imported by the orchestrator
doc at `.claude/CLAUDE.md`. Keep an optional rule (`i18n`, `mobile`,
`business-standards`) imported only if the project needs it - see
`.claude/_onboarding.md` section 3.

---

> **[PROJECT-SPECIFIC - REPLACE ME]**
> The block below is framework/tooling-specific guidance (it is generated by
> framework tooling such as an MCP server). Replace it with the equivalent
> guidance for the project's actual stack, or delete it if not applicable. It is
> NOT part of the universal template.
