# Agentry documentation

Start with [Getting started](getting-started.md) if you have not installed Agentry yet. Every other page stands alone, so jump to the one you need.

## User guide

| Page | Read it for |
|---|---|
| [Getting started](getting-started.md) | Requirements, install, onboarding a project, a first task from backlog to merge |
| [Commands](commands.md) | Every slash command and skill, grouped, with an example prompt each, plus the command-line tools |
| [Pipeline and approvals](pipeline.md) | The build and plan pipelines, checkpoints, modes, approval levels, swarm, worktrees |
| [Agents](agents.md) | The 31 agents by department, when each one is dispatched, what each may touch |
| [Configuration](configuration.md) | The `pipeline.json` keys you are likely to change and the `.agentry/` layout |
| [Extending Agentry](extending.md) | Add an agent, a rule or a skill, and run beside other plugins |
| [Upgrade, pin and roll back](upgrade.md) | Check your version, pin a release, upgrade, roll back, cut a release |
| [Troubleshooting](troubleshooting.md) | Symptoms and fixes, hook denies in plain words, Windows notes |

## For readers who want the mechanism

| Page | Read it for |
|---|---|
| [Design notes](technical/design-notes.md) | How enforcement works and the known limits |

Maintainer records of measured runs and past decisions are in `docs/technical/`, including [Plugin layout](technical/plugin-layout.md) (what the plugin directory contains and how paths resolve). They are working notes, not part of the user guide.

## Contributing to the docs

Preview the pages locally with `python scripts/docs_preview.py`.

## Conventions used in these pages

- A **stage** is one step of a pipeline, such as `implement`.
- A **gate** is the command that decides whether a stage passed.
- A **checkpoint** is a point where the pipeline stops and waits for your approval.
- A **dial** is one of the three settings you switch with a slash command: mode, approvals, swarm.
