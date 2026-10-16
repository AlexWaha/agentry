# Configuration

Almost everything you configure lives in one file, `.agentry/pipeline.json`, plus the per-project overlay in `.agentry/project/`. Onboarding fills both. This page covers what you are likely to change later.

## Table of contents

- [Where things live](#where-things-live)
- [pipeline.json keys you change](#pipelinejson-keys-you-change)
- [Stage exit gates](#stage-exit-gates)
- [The project overlay](#the-project-overlay)
- [Keys to leave alone](#keys-to-leave-alone)

## Where things live

The harness splits between the plugin and your project.

| Location | Holds | Who updates it |
|---|---|---|
| Plugin cache, exposed as `$AGENTRY_ROOT` | Pipeline tools, skills, hooks | `claude plugin update` |
| `.claude/agents/` | The 31 agent definitions | You, from the repository template |
| `.claude/rules/` | The policy the hooks enforce | You |
| `.claude/settings.json` | Permissions and which rules load | You |
| `.agentry/pipeline.json` | Stages, gates, retry budget, workflow mode | You |
| `.agentry/project/` | Product, stack and architecture notes for agents | You |
| `.agentry/tasks/` | `backlog/`, `active/`, `done/`, `epics/`, `handoffs/`, `templates/` | The pipeline |
| `.agentry/plans/`, `.agentry/specs/` | Plans and specs agents write | Agents |
| `.agentry/state/` | The three dials (`mode`, `approvals`, `swarm`), `run.db`, `events.db` | The pipeline |
| `.agentry/memory/memory.db` | Lessons, patterns and a module map | Agents |

The task state is the folder, not a field. `advance.py` moves a task file from `backlog/` to `active/` when it registers, and to `done/` only after git confirms the merge.

`.agentry/state/` and `.agentry/memory/` hold one project's internals and are gitignored in a fresh setup. They do not travel to another machine, so back them up with your working tree if they matter to you. The plugin directory layout is described in [Plugin layout](technical/plugin-layout.md).

## pipeline.json keys you change

Values below are what this repository ships, which is a working example, not a recommendation for your project.

| Key | Shipped value | What it does |
|---|---|---|
| `main_branch` | `"main"` | The branch task branches are cut from and merged into. Set it to yours |
| `workflow.mode` | `"solo"` | `solo` merges approved task branches locally. `pr` expects a pull request. See [Pipeline](pipeline.md#workflow-mode-solo-or-pr) |
| `workflow.push_needs_approval` | `true` | A push needs your recorded approval. Independent of the mode |
| `retry_budget` | `3` | How many times a failed gate is retried before the task is parked as blocked |
| `branch_pattern` | `"{type}/{task}"` | Name of a task branch |
| `protected_branches` | `["staging", "production"]` | Branches no push may target |
| `approvals_never_granted` | `["push", "trunk_push"]` | Checkpoints that no approval level may grant. Treat edits here as your decision to make |
| `pipelines.build.stages[].exit_gate.cmd` | repository-specific | The command that decides whether a stage passed. See below |
| `pipelines.build.stages[ready].auto_approve` | `[]` | Checkpoints the pipeline approves itself, for example `["commit"]`. Empty means you approve both |
| `worktrees.enabled` | `false` | One git worktree per task, so tasks can edit in parallel |
| `worktrees.max_parallel` | `2` | How many tasks may be in an editing stage at once when worktrees are on |
| `handoff.baseline` | `""` | Highest task already done when you adopted Agentry. Tasks up to it need no handoff note. Empty means every done task is covered |
| `memory.baseline` | `""` | The same idea for the memory review |
| `orchestrator_gate.extra_allow` | `[]` | Extra path globs the orchestrator may write, such as `"CHANGELOG.md"` |
| `supervisor.enabled` | `true` | A background process that notices stalled or dead sessions and relaunches them. Set `false` to switch it off |

JSON has no comments, so the shipped file carries its explanations in `_comment` keys. Read them before editing the key they sit beside.

## Stage exit gates

Each build stage that runs a command has an `exit_gate` with a `cmd` and an `expect_exit` code (0). The command must exit with that code for the stage to pass.

| Stage | Put here |
|---|---|
| `implement` | Your build or compile step |
| `test` | Your test suite |
| `review` | Your linter or static check |

Check these three before the first real task. A gate that always passes proves nothing, and the shipped values test this harness, not your code. Gates run in the task's worktree when worktrees are on.

Run the test suite unpiped, because piping it into `head` or `tail` can hang a gate. Write one command per gate, joined with `&&` if you need several.

## The project overlay

`.agentry/project/` is what agents read to learn your project. Onboarding fills it.

| File | Content |
|---|---|
| `project-context.md` | Product, domain terms, features, which phases are active |
| `architecture.md` | Module and folder layout, dependency rules |
| `api-conventions.md` | Controller, validator and resource patterns for your framework |
| `stack.md` | Language, framework, test, format, lint and build commands, environment variables. The source of truth for every placeholder in `.claude/` |

If these files are empty or still contain `[PROJECT-SPECIFIC - REPLACE ME]` markers, the project has not been onboarded.

## Keys to leave alone

`continuation_ceiling`, `stop_hook_repeat_ceiling`, the `gates` block, the `handoff` size caps and the `supervisor` timing keys are tuned and tested together. Change them only when a specific symptom points at one, and read the `_comment` beside it first.
