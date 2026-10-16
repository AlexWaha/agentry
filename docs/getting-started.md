# Getting started

## Table of contents

- [Requirements](#requirements)
- [Install the plugin](#install-the-plugin)
- [Onboard your project](#onboard-your-project)
- [Your first task](#your-first-task)
- [What to read next](#what-to-read-next)

## Requirements

| Need | Why |
|---|---|
| Claude Code | Agentry is a Claude Code plugin and relies on its hooks and subagent dispatch |
| Python 3.12 or newer on PATH | The pipeline tools are Python. They use only the standard library, so there is no `pip install` step |
| git | Branch checks, merge detection and the commit checkpoint |
| ruff (optional) | Only used by this repository's own `review` gate. Your gate can be any lint command |
| codegraph (optional) | A code index for faster structural queries. Without it, agents fall back to Grep and Glob |

## Install the plugin

Run these from your project root. `--scope local` limits the install to this project. Without it, Claude Code declares the marketplace at user scope and writes it into `~/.claude/settings.json`.

```bash
claude plugin marketplace add AlexWaha/agentry --scope local
claude plugin install agentry@agentry --scope local
claude plugin list
```

`claude plugin list` should show `agentry@agentry`, scope `local`, status `enabled`. Restart Claude Code, because `hooks.json` is read at session start. On start, the plugin exports `AGENTRY_ROOT` (the cached plugin copy) and `CLAUDE_PROJECT_DIR` into every Bash call.

The clone is anonymous HTTPS, so the install needs no login. If you have no SSH key for GitHub, set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` before the first command.

Status: measured on Claude Code 2.1.299 with HTTPS forced. To install a specific release instead of the latest `main`, see [Upgrade, pin and roll back](upgrade.md).

## Onboard your project

The plugin carries the tools, skills and hooks. The agents, rules and pipeline config come from this repository and are tailored to your stack once.

1. Clone this repository somewhere outside your project. Copy `.claude/`, `.agentry/` and `CLAUDE.md` into your project root. Back up any `CLAUDE.md` you already have.
2. Open Claude Code at your project root.
3. Paste the prompt from `.claude/_init-prompt.md`. It runs in two phases. Phase A detects your language, framework, test, format and build commands, database and CI, and fills in the placeholders. Phase B is an interview that sets the stages, their exit commands, the retry budget and which checkpoints you want to approve yourself.
4. Follow `.claude/_onboarding.md` for what the prompt did not cover: optional rules, permissions in `settings.json`, and the codegraph check.

> **Check four keys before you trust the gates.** This repository ships with its own values in `pipeline.json`, so a search for leftover `{{...}}` tokens finds nothing. Four keys still need yours: `main_branch` and the exit commands of `implement`, `test` and `review`. If you keep the shipped ones, your `implement` gate compiles this harness instead of building your code. It passes and proves nothing. "Decision 2: gates per repository" in `.claude/_onboarding.md` lists each key with its shipped value.

After onboarding, `/pipeline-show` should print three lines, one per dial: mode, approvals and swarm.

## Your first task

This walk-through uses the default build flow. Words in `code` are stages or commands you can look up in the [pipeline page](pipeline.md) and the [command reference](commands.md).

**1. Pick your dials.** Type `/pipeline-build-manual`. That sets mode to build, keeps every checkpoint with you, and keeps swarm off. Then `/pipeline-show` confirms it. Avoid `/pipeline-build-assisted` for this walk-through: assisted skips the commit prompt, so step 6 would never happen. The `auto` level also skips the take step and the commit prompt. A push is never automatic at any level.

**2. File a task.** Ask Claude: "Use the new-task skill to file a task: add a /health endpoint that returns 200." It writes a file into `.agentry/tasks/backlog/`.

**3. Start it.** Tell Claude to start the task. The orchestrator works on a branch named `<type>/<task>`, which must be cut from up-to-date `main`, registers the task, and moves its file to `active/`.

**4. Approve the plan.** The task's owner agent writes a plan, and the pipeline parks at `plan-approval`. Claude shows you the plan and asks. Nothing outside `.agentry/` can be edited until you say yes. If you say no, the task goes back to `plan` and you have lost nothing but a document.

**5. Let it run.** `implement`, `test` and `review` run on their own. At each stage end, a script runs your gate command and records the result. If a gate fails, the owning agent fixes it and the script runs it again. After three failures the task is parked as blocked and Claude tells you why.

**6. Read the diff.** At `ready`, type `/diff` in the session to read what changed. Approve the commit, or reject it with a reason and the task returns to `implement`.

**7. Merge.** What happens next depends on the workflow mode in `pipeline.json`:

| Workflow mode | After the commit approval |
|---|---|
| `solo` | The pipeline merges the task branch into `main` locally. There is no push step |
| `pr` | You push the branch with a second, separate approval, open a pull request and merge it yourself |

The task reaches `done` only when git confirms the branch is merged. The file moves to `done/`.

**8. Let the handoff run.** Before the next task can start, the previous one needs a short handoff note and a memory review. Claude writes both, so you only need to know why the next task starts a little late.

## What to read next

- [Pipeline and approvals](pipeline.md) to understand each stage and every checkpoint.
- [Commands](commands.md) for the full list.
- [Troubleshooting](troubleshooting.md) when a hook says no.
