# Project context

## Product

**Name:** Agentry - "It's a tool dude"

**What it does:** Agentry is a deterministic orchestration harness for Claude
Code. It runs a gated finite-state machine per task: a stage advances only
when its exit-gate command actually passes, state lives in SQLite
(`.agentry/state/run.db`), and enforcement runs in PreToolUse and Stop hooks
rather than inside the model's own context. The model proposes a transition;
only the hook scripts record one.

**Key behaviors:**
- Two pipelines: `build` (implement, test, review, ready, done) ships a task;
  `plan` (formalize, draft, plan-review, approval, breakdown, done) turns an
  idea into an approved spec and a task backlog.
- Two workflow modes: `pr` for a project with a second party to review and
  merge, `solo` for a single author with nobody to hand a pull request to.
  Pushing to the remote needs the human's explicit approval in chat in both
  modes, at every approval level - no level ever grants it.
- A task's state is the folder it sits in - `backlog/`, `active/`, or
  `done/` - not a `status:` field, so the file's location and the pipeline's
  record of it cannot drift apart.
- The next task's assignee writes the previous task's handoff doc in their
  own words before their own task can register; validation checks for six
  named sections, each with a minimum length, no leftover scaffold marker,
  and a 500-character minimum total, but it does not check whether the
  words are actually the writer's own. This makes skipping the context
  absorption the rule exists for cost more effort than doing it, rather than
  assuming it happened.
- Memory is one SQLite store with an FTS5 index, queried per dispatch with
  the actual task text, rather than a markdown file whose first lines get
  auto-injected regardless of relevance.

**Repository:** `github.com/AlexWaha/agentry`, public. No license has been
chosen yet, so all rights are reserved by the repository owner for now
(task-0054 tracks picking and adding one).

**Who uses it:** a solo developer or a small team running Claude Code on
real work - hobby projects, pet projects, anything with no dedicated ops
team - who wants Claude Code to keep advancing a task without being asked
"shall I continue?", and wants each stage's pass or fail to come from a real
command rather than a self-report. This repository is also Agentry's first
adopter: the harness under `.claude/` is the product, and the tasks tracked
in `.agentry/tasks/` are the harness improving itself.

## Architecture

Agentry has no application server and no request/response cycle. Its
architecture is the pipeline itself - the components that sit between the
CEO's instruction and the model's next tool call:

- **Stage machine** (`.agentry/pipeline.json` +
  `.claude/tools/pipeline/advance.py`) - declares each pipeline's stages,
  owning agent, and exit-gate command, and runs that command before
  recording an advance.
- **Run state** (`.agentry/state/run.db`, SQLite) - one row per task in
  flight: pipeline, stage, stage status, and which approvals are still
  outstanding.
- **Hooks** (`.claude/tools/pipeline/pretool_gate.py`, `agent_gate.py`,
  `stop_gate.py`) - PreToolUse hooks deny a commit or push until the
  matching approval is recorded, deny edits during the read-only review
  stage, and scope what each agent profile (`dev`, `readonly`, `docs`) may
  touch; the Stop hook keeps a session advancing in-flight work instead of
  asking what to do next.
- **Memory store** (`.agentry/memory/memory.db`, SQLite + FTS5) - lesson,
  pattern, and module rows, queried automatically at the start of every
  agent dispatch.
- **Rules and agents** (`.claude/rules/`, `.claude/agents/`) - the policy
  the hooks enforce, and the agent roster the orchestrator dispatches
  against.

The suite has 269 tests, in six modules under `.claude/tools`.

A fuller, component-by-component layout is meant to live in
`project/architecture.md`; that file is still the unfilled template and is
out of scope for this task (FR-12 names only this file).

## Domain terminology

- `stage` - one step of a pipeline (for example `implement`, `test`,
  `review`); a task moves to the next stage only when `advance.py` runs that
  stage's exit-gate command and it passes.
- `checkpoint` - a point where the pipeline stops and waits for the CEO's
  approval: commit and push in `pr` mode, commit only in `solo` mode (this
  repository runs `solo`, so the gated local merge follows commit instead of
  a push checkpoint - push approval is still required if a push ever
  happens, never weakened). It parks the task with `awaiting_human` set
  until `approve.py` records the approval.
- `lane` - an independent pipeline conveyor, named by the `PIPELINE_LANE`
  environment variable, so two sessions running in parallel do not share one
  `run.db`.
- `gate` - the actual command (test suite, formatter, lint) that decides
  whether a stage passed; "green" means this command's real output, read
  this session, not an assumption.
- `handoff doc` - the file in `.agentry/tasks/handoffs/` that the next task's
  assignee writes about the previous task, in their own words, before their
  own task can register. Validation checks structure and length (six named
  sections, no scaffold marker, minimum length), not whether the content is
  genuinely the writer's own.
- `approvals level` - one of `manual`, `assisted`, `auto`: how many
  checkpoints clear without asking the CEO. No level grants the push
  checkpoint.
- `workflow mode` - `pr` or `solo`: whether the trunk is reached through a
  human-created pull request or a locally gated merge.

## Workspace layout

Single-repository workspace; there is no separate application codebase.

| Directory | Description | Git repo? |
|-----------|-------------|-----------|
| `.claude/` | The harness itself: rules, agents, skills, pipeline tools, task files, specs, memory store. This is what Agentry ships. | Yes (same repo as root) |
| `docs/` | Session memory notes under `docs/memory/`; the business, technical, and other subtrees `rules/documentation.md` describes are not populated yet. | Yes (same repo as root) |
| `.codegraph/` | Code knowledge graph index, built by the codegraph CLI. | No (gitignored) |

## Project phases (current activation state)

Agentry has no product backend or frontend to build; the phases below
govern work on the harness itself.

### Active phases (harness self-development)

| Phase | Name | Key agents | Gate |
|-------|------|-----------|------|
| 6 | Technical architecture | Architect, senior-backend-dev | CEO approves technical design |
| 7 | Infrastructure setup | DevOps engineer, Architect | CEO verifies the pipeline hooks fire |
| 8 | Implementation | Senior backend dev, DevOps engineer | Exit gates pass: `compileall`, `unittest`, `ruff` |
| 9 | Testing and QA | QA engineer, Reviewer | All tests pass, security audit clean |

### Deferred phases

| Phase | Name | When to activate |
|-------|------|-----------------|
| 1-5 | Ideation to whitepaper | When business planning begins |
| 10 | Mobile app | Not applicable - Agentry has no mobile surface |
| 12 | Marketing and GTM | When go-to-market planning begins |
| 13 | Risk management | When risk assessment is needed |
| 14 | Growth and scaling | When scaling planning begins |
