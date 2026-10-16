# Design notes

This page is for readers who want to know how Agentry enforces its rules and where the enforcement stops. The user guide in [docs/](../README.md) covers what you do. This page covers what the machinery does, including its known gaps.

## Table of contents

- [The enforcement model](#the-enforcement-model)
- [State](#state)
- [Approvals](#approvals)
- [Orchestrator write limit](#orchestrator-write-limit)
- [Agent profiles](#agent-profiles)
- [Handoff validation](#handoff-validation)
- [Editing stages and exclusivity](#editing-stages-and-exclusivity)
- [Why there is no visual-diff stage](#why-there-is-no-visual-diff-stage)
- [Known gaps](#known-gaps)

## The enforcement model

Agentry does not ask the model to follow the pipeline. It puts Python scripts where the model's tool calls pass through, and the scripts decide.

| Hook event | Script | What it does |
|---|---|---|
| `PreToolUse` | `pretool_gate.py` | Denies commits and pushes without a recorded approval, edits before plan approval, writes outside the orchestrator's allowed folders, and a list of unsafe command shapes |
| `PreToolUse` (subagents) | `agent_gate.py` | Applies the agent's gate profile inside the subagent itself, independent of permission mode |
| `Stop` | `stop_gate.py` | Keeps the session driving in-flight work and blocks it from ending while a handoff or memory debt stands |
| `SubagentStart` | memory and handoff injectors | Adds the best-matching lessons and the newest handoff note to the agent's context |
| `SubagentStop` | reminder hook | Reminds the orchestrator to record any lesson from the agent's report |

The model proposes that a stage is done. `advance.py` runs the stage's exit command, reads its exit code and writes the transition. The hook layer and the script layer share one source of truth: `.agentry/state/run.db`.

## State

| Store | Contents |
|---|---|
| `run.db` | One row per task in flight: pipeline, stage, stage status, outstanding approvals |
| `events.db` | A journal of stage transitions, gate results, approvals, rejections with reasons, and merges. Read it with `events.py` |
| `memory.db` | SQLite with a full-text index: lesson, pattern and module rows |
| Task folders | `backlog/`, `active/`, `done/`. The folder is the state, so there is no status field to disagree with the run record |

Memory is queried with the text of the active task file, not with a fixed file whose first lines are injected whatever the task is. An absent or corrupt store injects nothing and does not fail the dispatch.

## Approvals

Each checkpoint has a field in `run.db` that only `approve.py` sets, and `approve.py` is run by the orchestrator only after you say yes in chat.

The approval level (`manual`, `assisted`, `auto`) decides which checkpoints clear without asking you. Before the level is consulted, the code checks the `approvals_never_granted` list in `pipeline.json`. A checkpoint on that list is withheld from every level and from any per-stage `auto_approve` entry. A missing, malformed or unreadable list withholds `push` and `trunk_push` too, so only an explicit list can loosen anything. The plan approval is withheld in code whatever the list says.

Subagent Write and Edit calls, and shell writes to an explicit path, are denied for `pipeline.json`, so an agent cannot edit the list. Shell writes that change directory first, and writes made by an interpreter, are not yet covered. Treat editing that file as something only you do.

At `assisted` and `auto`, the level itself supplies the commit approval. In `solo` mode that means no human decides each merge at those levels. A session launched unattended by the supervisor is refused the merge regardless of level.

## Orchestrator write limit

The `orchestrator_gate` block in `pipeline.json` lists the folders the main session may write: `.claude/`, `.agentry/`, `docs/`, any `README*` file at any depth, and the root `CLAUDE.md`. `pretool_gate.py` checks every tool call from the main thread against it, including shell redirects, `tee`, `sed -i` and `patch`. Subagent calls are exempt from this block, because their gate profiles apply instead. Dispatching the owning agent is the only path to a code change.

Interpreter-written files, for example `python -c "open(...)"`, are outside what the gate can read.

## Agent profiles

Each agent file declares `profile:` as `dev`, `docs` or `readonly`. The plugin's `hooks.json` runs `agent_gate.py` for subagents, and the profile is resolved from the agent's definition in the project. A caller the gate cannot resolve is treated as read-only. Because agents run with prompts bypassed so that nothing interrupts you, the profile gates are the actual restraint.

## Handoff validation

Before a new task registers, the previous completed task needs a handoff document in `.agentry/tasks/handoffs/`. The next task's assignee is instructed to write it in their own words from the merged diff and the gate log. The point is that writing it forces the reading.

The validator checks structure only:

- the frontmatter task id matches the file name
- six required headings are present
- there is no leftover scaffold marker
- each section has at least 40 non-space characters and the body at least 500
- the context-loaded confirmations are present
- the file is under the byte cap

It does not check authorship. A pasted task description that meets those checks passes. What the rule buys is that skipping the reading costs more effort than doing it, not a proof that it happened.

## Editing stages and exclusivity

`plan`, `implement`, `test` and `review` are editing stages. With worktrees off, only one task occupies one at a time, so two tasks never edit the same working tree. With worktrees on, each task has its own tree and up to `max_parallel` tasks can edit at once. An epic registered as a swarm umbrella task is the exception: it runs several agents inside `implement` and stays in the main tree. `done` is reached only when `git_state.py` confirms the branch is merged into the main branch, not merely pushed.

## Why there is no visual-diff stage

Earlier versions had a stage backed by a local browser UI for reading the diff. It was removed once Claude Code shipped a built-in `/diff`. You now read the diff at the commit checkpoint, where the approval already waits. Rejecting there sends the task back to `implement`.

## Known gaps

| Gap | Effect |
|---|---|
| Interpreter and change-directory shell writes to `pipeline.json` are not blocked for subagents | A determined agent could edit the never-granted list. Review the diff of that file |
| Interpreter-written files bypass the plan gate and the orchestrator write limit | The gates read tool calls and shell text, not what a script does when it runs |
| Handoff validation checks structure, not authorship | See [Handoff validation](#handoff-validation) |
| Tag-form pin, upgrade and rollback are not yet measured | See [Upgrade, pin and roll back](../upgrade.md) |
| Hooks fail open on an unreadable state file | A corrupt state file loosens a check rather than freezing every edit. The trade-off favors recoverability |
