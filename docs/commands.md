# Commands

Every command on this page is a skill. 43 ship in the plugin, and `verify` lives in the project's `.claude/skills/`. Claude Code lists plugin skills with the plugin name as a prefix, for example `agentry:pipeline-show`. The prefix is optional when the skill name is unique, so `/pipeline-show` and `/agentry:pipeline-show` both work. This page uses the short form, as the skills do in their own docs.

## Table of contents

- [Pipeline dials](#pipeline-dials)
- [Planning and task intake](#planning-and-task-intake)
- [Build and quality](#build-and-quality)
- [Documents, business and growth](#documents-business-and-growth)
- [Command-line tools](#command-line-tools)

## Pipeline dials

Sixteen commands set the three dials: mode, approvals and swarm. Each command sets exactly the dials named in it. A dial that is not named keeps its value. Settings persist across sessions.

| Command | Sets |
|---|---|
| `/pipeline-show` | Nothing. Prints all three dials |
| `/pipeline-plan` | Mode to plan |
| `/pipeline-talk` | Mode to talk |
| `/pipeline-build` | Mode to build, swarm off |
| `/pipeline-build-swarm` | Mode to build, swarm on |
| `/pipeline-build-manual` | Build, approvals manual, swarm off |
| `/pipeline-build-assisted` | Build, approvals assisted, swarm off |
| `/pipeline-build-auto` | Build, approvals auto, swarm off |
| `/pipeline-build-manual-swarm` | Build, approvals manual, swarm on |
| `/pipeline-build-assisted-swarm` | Build, approvals assisted, swarm on |
| `/pipeline-build-auto-swarm` | Build, approvals auto, swarm on |
| `/pipeline-approvals-manual` | Approvals only |
| `/pipeline-approvals-assisted` | Approvals only |
| `/pipeline-approvals-auto` | Approvals only |
| `/pipeline-swarm-on` | Swarm only |
| `/pipeline-swarm-off` | Swarm only |

The build commands follow one pattern: `/pipeline-build`, then an optional approval level (`-manual`, `-assisted`, `-auto`), then an optional `-swarm`. What each setting means is in [Pipeline and approvals](pipeline.md#the-three-dials).

Example. You are about to run a normal feature and want commits to pass without a prompt:

```text
/pipeline-build-assisted
/pipeline-show
```

`/pipeline-show` prints three lines, one per dial, each with its value and a short description. The `pipeline` skill is the reference behind this family. Claude loads it when you ask to switch mode, approvals or swarm in plain words.

## Planning and task intake

Skills are triggered by naming them or by describing the job. The example column is a prompt you can paste.

| Skill | What it does | Example prompt |
|---|---|---|
| `ideation` | Turns a raw idea into a vision, audience and personas | "Use ideation on this idea: a habit tracker for climbers" |
| `new-task` | Writes a task file into the backlog | "Use new-task to file: add rate limiting to /login" |
| `new-epic` | Splits an approved spec into an epic with linked tasks | "Use new-epic on the approved search spec" |
| `api-design` | REST endpoints, schemas, an OpenAPI spec | "Use api-design for the invoices resource" |
| `db-design` | Schema, indexes, ER diagram, migration plan | "Use db-design for invoices and line items" |
| `status-report` | A status report built from real task files and pipeline state | "Use status-report for this week" |

## Build and quality

| Skill | What it does | Example prompt |
|---|---|---|
| `feature-scaffold` | A full feature across layers: migration, model, validators, service, controller, tests | "Use feature-scaffold for a Project resource" |
| `bug-fix` | Failing test first, then root cause, then the smallest fix | "Use bug-fix: login returns 500 on empty password" |
| `write-tests` | Coverage for auth, validation, happy path and not-found; TDD mode when no code exists yet | "Use write-tests for POST /projects" |
| `migration` | Safe, reversible database migrations | "Use migration to add a nullable due_date column" |
| `infrastructure` | Docker dev stack and CI/CD, with health checks | "Use infrastructure to add Postgres to compose" |
| `frontend-standards` | Design tokens, feature architecture, typed translations | "Use frontend-standards before building the settings page" |
| `health-check` | Runs format, lint, tests and a forbidden-pattern scan, reports pass or fail per check | "Use health-check" |
| `deep-review` | Multi-lens review with Critical, High and Medium findings | "Use deep-review on this branch" |
| `verify` | The pre-commit gate; it delegates to `health-check`. Lives in the project's `.claude/skills/`, not in the plugin | "Use verify" |
| `dashboard` | Starts or stops the read-only local dashboard of pipeline, memory and harness. See [Dashboard](dashboard.md) | `/agentry:dashboard`, then `/agentry:dashboard stop` |
| `swarm` | The protocol for running an epic as parallel agents | Turn it on with `/pipeline-swarm-on` |
| `risk-assessment` | A scored risk matrix with mitigations | "Use risk-assessment before the launch phase" |
| `self-learning` | Records, recalls and distills lessons in the project memory | "Use self-learning to record this mistake" |

## Documents, business and growth

These skills serve the business and content agents, which are dormant until you turn them on (see [Agents](agents.md)).

| Skill | What it does | Example prompt |
|---|---|---|
| `update-docs` | Audits documentation after a phase, creates missing pages, updates the changelog | "Use update-docs for the finished epic" |
| `whitepaper` | A technical and business whitepaper | "Use whitepaper from the existing market and product docs" |
| `pitch-deck` | Investor deck content with speaker notes and a script that builds the PPTX | "Use pitch-deck" |
| `stakeholder-html-deck` | A self-contained HTML slide deck with a presenter view | "Use stakeholder-html-deck for the demo day" |
| `market-research` | Market size, competitor matrix, SWOT, positioning | "Use market-research for a climbing app" |
| `business-plan` | A business plan assembled from earlier research | "Use business-plan" |
| `financial-model` | Projections and unit economics in best, base and worst cases | "Use financial-model" |
| `gtm-strategy` | Segments, channels, launch timeline, KPIs | "Use gtm-strategy" |

## Command-line tools

The slash commands cover daily use. These scripts sit behind them. You rarely run one yourself. `AGENTRY_ROOT` is set by the plugin's session start hook in every Bash call Claude Code makes, so ask Claude to run a script, or type it in the Claude Code prompt with the `!` prefix. A plain terminal outside Claude Code does not have the variable. Approvals are recorded by Claude (the orchestrator) after you approve in chat, so you do not run `approve.py` yourself.

| Script | Use |
|---|---|
| `python "$AGENTRY_ROOT/tools/pipeline/mode.py" --show` | Print all three dials. `mode.py build`, `plan` or `talk` sets the mode |
| `python "$AGENTRY_ROOT/tools/pipeline/approvals.py" assisted` | Set the approval level to `manual`, `assisted` or `auto` |
| `python "$AGENTRY_ROOT/tools/pipeline/swarm.py" on` | Set swarm to `on` or `off` |
| `python "$AGENTRY_ROOT/tools/pipeline/state.py" --show` | What is running and at which stage |
| `python "$AGENTRY_ROOT/tools/pipeline/advance.py" --task <task> --type feature` | Register a task or advance it. It runs the stage gate first |
| `python "$AGENTRY_ROOT/tools/pipeline/approve.py" --task <task> --gate plan` | Record a plan approval. Claude runs it after you approve in chat. `--gate commit` and `--gate push` work the same way |
| `python "$AGENTRY_ROOT/tools/pipeline/approve.py" --task <task> --reject --reason "..."` | Send a task back. The reason is required |
| `python "$AGENTRY_ROOT/tools/pipeline/events.py" --task <task>` | The journal of transitions, gate results and approvals. `--summary` gives an overview |
| `python "$AGENTRY_ROOT/tools/pipeline/worktree.py" --list` | List parallel-task worktrees |
| `python "$AGENTRY_ROOT/tools/dashboard/server.py"` | Start the dashboard server by hand. `/agentry:dashboard` runs this for you. See [Dashboard](dashboard.md) |

`<task>` is a task id: the file name of a task in `backlog/`, without `.md`.
