---
name: pipeline
description: Semantics of the pipeline dials (mode, approval level, swarm flag) and the /pipeline-* command family. Use when the CEO asks to switch mode, approvals or swarm, or types "/pipeline", "переключи режим", "смени уровень одобрения", "включи swarm", "давай обсудим", "просто поговорим".
---

# Pipeline dials and commands

This skill and the `pipeline-*` command skills belong to this project and are
edited here. Project commands and paths live in the adapter
`.claude/pipeline-adapter.md`.

## Three dials

| Dial | Values | Meaning |
|---|---|---|
| mode | `build`, `plan`, `talk` | which flow runs: implementation, design (brief, plan, spec, task files; no product code), or plain discussion (nothing tracked) |
| approvals | `manual`, `assisted`, `auto` | how many checkpoints the CEO must still answer (default grants below) |
| swarm | `on`, `off` | build only: `on` runs each epic as a parallel agent swarm (`.claude/skills/swarm/SKILL.md`), `off` runs one task at a time |

Default grants per approval level (what passes WITHOUT asking the CEO):

| Level | Take a task | Commit | Push |
|---|---|---|---|
| `manual` | no | no | no |
| `assisted` | no | yes | no |
| `auto` | yes | yes | yes, work branch only, unless `approvals_never_granted` lists it |

`manual` grants nothing. `assisted` grants the commit only and does not take the
next task. `auto` grants take and commit, and the push of the work branch unless
`pipeline.json` `approvals_never_granted` lists it; the shipped config lists
`push` and `trunk_push`, so no level grants them and `auto` stops at the push.
Subagent Write/Edit and explicit-path shell writes to that file are denied;
cd-relative and interpreter writes are not yet covered (task-0116).

Push to the main branch is never granted at any level. Only the project config
can allow it (adapter key `push_to_main`, default forbidden), and the hooks
enforce it. In `solo` mode (`workflow.mode`) the merge into main happens locally
after the commit approval; there is no push step.
Trunk-based projects (adapter key `trunk_based`) commit and push on the main branch; the hooks take the task from the single active run and apply the same commit and push approvals, denying when zero or several runs are active.

Adapter key `approvals_grants` names the config that lists checkpoints never
granted at any level, enforced in code. Never answered by any level: planning
questions and moving a task to done. Merging into main needs the CEO unless the
adapter defines a local merge mode (e.g. `solo`) and its trigger.

All three dials persist across sessions until changed; the SessionStart hook
prints any non-default value.

## Commands

Each command sets exactly the dials in its name; the swarm flag is part of the
name for every build command (`-swarm` = on, absent = off). Dials not in the
name keep their value.

| Command | Sets |
|---|---|
| `/pipeline-show` | nothing, prints all three |
| `/pipeline-plan`, `/pipeline-talk` | mode |
| `/pipeline-build`, `/pipeline-build-swarm` | mode build, swarm off / on |
| `/pipeline-build-manual`, `-assisted`, `-auto` | mode build, approvals, swarm off |
| `/pipeline-build-manual-swarm`, `-assisted-swarm`, `-auto-swarm` | mode build, approvals, swarm on |
| `/pipeline-approvals-manual`, `-assisted`, `-auto` | approvals |
| `/pipeline-swarm-on`, `/pipeline-swarm-off` | swarm |

After any switch, run the adapter's `show` and report the state in one line.

## Behaviour per mode

- **build**: drive the build flow through the project's gated scripts; with swarm on follow the swarm skill, with swarm off one task at a time.
- **plan**: gated design flow; task files land in the backlog; no product code.
- **talk**: answer, investigate, prototype in scratch; no task files, no branches, no product edits. Offer to capture anything worth keeping.

When the CEO asks to implement something while `plan` or `talk` is active, say which mode is on and ask before switching. Safety gates stay on in every mode: no push to protected branches, no destructive commands, no AI attribution, no em dash.
