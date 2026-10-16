# Onboarding: adopting the harness in a project

**Date:** 2026-10-09
**Author:** Technical Writer
**Status:** Draft
**Version:** 2.0

---

## Table of Contents

- [What this covers](#what-this-covers)
- [Layout](#layout)
- [Before you start](#before-you-start)
- [What the setup detects for you](#what-the-setup-detects-for-you)
- [Decision 1: repositories](#decision-1-repositories)
- [Decision 2: gates per repository](#decision-2-gates-per-repository)
- [Decision 3: lanes](#decision-3-lanes)
- [Decision 4: approval level](#decision-4-approval-level)
- [Decision 5: workflow mode](#decision-5-workflow-mode)
- [Checkpoints you will meet](#checkpoints-you-will-meet)
- [Adopting mid-project](#adopting-mid-project)
- [Self-check](#self-check)
- [Day-two commands](#day-two-commands)
- [Version history](#version-history)

---

## What this covers

Copy `.claude/` into a project, run the init prompt, and the setup detects most
of what it needs. Five things it cannot know are yours to decide, in this order:

1. Which repositories the workspace holds.
2. Which commands gate each repository.
3. Which lanes run, and which repository each lane serves.
4. How many checkpoints clear without you (the approval level).
5. Whether the project is `pr` or `solo` (the workflow mode).

The rest of this file explains those five, then lists the checkpoints you will
meet afterward and a self-check. The mechanical steps are in
`.claude/_init-prompt.md`: paste it into Claude Code at the project root. Phase A
runs without questions. Phase B asks you the five decisions through
`AskUserQuestion` cards.

## Layout

The harness and the project's own work product live in different trees.

| Path | Holds |
|---|---|
| `.claude/` | The project side of the harness: `CLAUDE.md`, `settings.json`, `agents/`, `rules/`, `pipeline-adapter.md`, and `skills/verify` |
| `$AGENTRY_ROOT` (the plugin root) | `tools/`, `skills/`, `hooks/hooks.json`. Source: `plugin/` in the harness repository. Until task-0039 switches the live harness to the plugin, `AGENTRY_ROOT` is `.claude/`, which still carries `tools/` and `skills/`. See `docs/technical/plugin-layout.md` |
| `$AGENTRY_ROOT/tools/pipeline/` | State machine, gates and approvals (`advance.py`, `approve.py`, `approvals.py`, `mode.py`, `swarm.py`, `stack_gate.py`, `supervisor.py`) |
| `$AGENTRY_ROOT/tools/setup/` | Onboarding helpers (`repo_scan.py`, `substitute_tokens.py`, `rebuild_pipeline.py`) |
| `.agentry/pipeline.json` | Stage machine, gates, `repos`, `lanes`, `workflow`, `handoff`, `memory` |
| `.agentry/project/` | Product, architecture, stack and API overlay for this project |
| `.agentry/tasks/` | `backlog/`, `active/`, `done/`, `epics/`, `handoffs/`, `templates/` |
| `.agentry/specs/`, `.agentry/plans/` | Specs and per-task plans |
| `.agentry/memory/` | Memory store (`memory.db`, gitignored) |
| `.agentry/state/` | Run store, mode, approvals, swarm flag (gitignored) |
| `.mcp.json` (project root) | Wires the codegraph MCP server for this project; commit it |

A task's state is the folder it sits in. There is no `status:` field.

## Before you start

- Python 3 on `PATH`. The pipeline and every hook are Python.
- The codegraph CLI (`codegraph --version`). Install it from
  github.com/colbymchenry/codegraph or with `npm i -g @colbymchenry/codegraph`
  (Node 22.5 or later). Do not run `codegraph install`: it writes the user-global
  MCP config, and this harness wires MCP per project in `.mcp.json`. Without the
  CLI, onboarding continues and `session_start.py` degrades to Grep and Glob.
- Copy `.claude/settings.local.example.json` to `.claude/settings.local.json` for
  personal permissions, and gitignore it.
- Hook commands use `$CLAUDE_PROJECT_DIR`. Leave them alone. An absolute path
  leaks your machine into the repo, and a relative one breaks when the shell
  directory drifts.

## What the setup detects for you

Phase A fills these without asking. Do not answer them by hand; correct the
output if it is wrong.

| Used to be asked | Now filled from |
|---|---|
| Which repositories exist | `repo_scan.py`: one row per `.git` directory, fields `key`, `path`, `manifests` |
| Language and stack of each repository | `repo_scan.py` field `stack` (`unknown` when a `.git` has no recognised manifest) |
| Framework, test framework, formatter, container tool, CI tool, database, auth, main branch | Phase A1 inspection, written to the placeholder table in `.agentry/project/stack.md` |
| Replacing `{{PLACEHOLDER}}` tokens file by file | `python "$AGENTRY_ROOT/tools/setup/substitute_tokens.py" --target .`, driven by that table |
| Test, format, lint, build and dev commands | The same table; they land in the gates you confirm in decision 2 |
| The four pipeline keys to rewrite by hand (`main_branch` and the three exit gates) | Decision 1 and decision 2 write them |

If `substitute_tokens.py` leaves a token behind, the row in `stack.md` is still
empty. Fill that row and rerun; the script skips rows that hold a marker.
`grep -rE '\{\{[A-Z_]+\}\}' .claude/` should then return only `_onboarding.md`,
`_init-prompt.md` and `tasks/templates/handoff-template.md`, whose tokens are
runtime values.

## Decision 1: repositories

A workspace may hold one repository or several. Run:

```bash
python "$AGENTRY_ROOT/tools/setup/repo_scan.py" --root .
```

It prints a table of key, path, stack and manifests, walking the root and two
levels down, and skipping `node_modules`, `vendor`, `.claude`, dot directories
and build output. The root repository is named after its directory, so rename
it to something you will recognise. A single-repository workspace is a
one-row table, not an error.

The init prompt shows you this table as a proposal with two options: "Looks
right" and "Correct it". Correct a key, a path, a stack, or drop a row. Save
the corrected rows as a JSON list in `tmp/repos.json` and write them:

```bash
AGENTRY_MAIN_THREAD=1 python "$AGENTRY_ROOT/tools/setup/repo_scan.py" --root . --table tmp/repos.json --write .agentry/pipeline.json
```

This rewrites only `repos` and `lanes`, reads the result back through the
pipeline's own readers, and restores the original bytes if either reader drops
an entry. If the hook denies the write to `pipeline.json`, run the command
yourself in a terminal.

Each task names its repository in the `repo:` field of its frontmatter. The
value is matched to a `repos` key exactly, then by the first path segment, then
by an entry's `path`. A `repo:` that matches nothing fails closed: the task
parks instead of falling back to another repository.

## Decision 2: gates per repository

A stage advances only when its gate command exits 0. Each repository gets its own
commands for `implement`, `test` and `review`, stored under `repos.<key>.stages`:

```json
"repos": {
  "api": {
    "path": "api",
    "stack": "php",
    "stages": {
      "implement": ["composer dump-autoload"],
      "test": ["composer test"],
      "review": ["composer format-lint"]
    }
  }
}
```

Three rules to know:

- An empty list is an explicit pass. A repository of YAML and markdown gets
  `[]`, never a command borrowed from another stack.
- `stack_gate.py` runs these commands. Wire it as the exit gate of each stage in
  `pipelines.build` in `.agentry/pipeline.json`, because the shipped config does
  not do it for you:
  `python "$AGENTRY_ROOT/tools/pipeline/stack_gate.py" --task {task} --stage test`.
  Until you do, the exit gates keep the harness's own commands (`compileall`,
  `unittest`, `ruff`), and a build stage passes without touching your code.
  A gate that passes for the wrong reason is worse than no gate.
- Do not put the full test suite in `repos.<key>.stages.test` if your agents are
  barred from running it. `gates.dev_forbidden_commands` lists the commands dev
  agents may not run, so the suite runs once, in the `test` stage's exit gate.

Confirm these `gates` keys in the same pass, with defaults inferred from your
stack: `destructive_command_patterns`, `destructive_allow_if`,
`repl_write_keyword`, `repl_write_patterns`, `dev_forbidden_commands`,
`forbid_dev_null` (true on Windows with Git Bash). The branch-base, attribution
and dash gates run without configuration.

Set `main_branch` and `retry_budget` (default 3 gate failures before a task is
parked `blocked`). Set `ui_evidence` to false on the `test` stage if the project
has no UI.

The stage order is `plan`, `plan-approval`, `implement`, `test`, `review`,
`ready`, `done`. There is no separate diff-review stage: you read the diff
yourself at `ready` with Claude Code's `/diff`.

## Decision 3: lanes

A lane is an independent conveyor: its own run store, mode, approval level,
swarm flag and trunk-push marker, so two sessions do not step on each other. A
session picks its lane with the `PIPELINE_LANE` environment variable; unset
means `default`.

`lanes` in `.agentry/pipeline.json` binds each lane to exactly one repository:

```json
"lanes": {
  "default": {"repo": "api"},
  "web": {"repo": "web"}
}
```

`repo_scan.py --write` generates this: the first repository gets lane `default`,
every other one a lane named after its key. A row in your corrected table may
carry `lane` to override the name. Rules that follow from the file:

- A backlog task is offered only to the lane whose repository equals its `repo:`.
  A task with an empty `repo:` goes to `default`.
- A task whose `repo:` is not a key of `repos` is offered to no lane, and the
  Stop hook names it once when the lane has nothing else ready.
- A session in a lane that `lanes` does not declare is offered nothing.
- Approval level, mode and swarm are per lane. A second lane starts at `manual`;
  raise it in that lane's own session:
  `PIPELINE_LANE=web python "$AGENTRY_ROOT/tools/pipeline/approvals.py" assisted`.
- With one repository you need no extra lanes. If you drop `lanes` entirely, every
  session is offered the whole backlog.

The coordination protocol between lanes is in
`docs/technical/cross-lane-coordination.md`.

## Decision 4: approval level

The level says how many checkpoints clear without you. Set it per lane:

```bash
python "$AGENTRY_ROOT/tools/pipeline/approvals.py" assisted
python "$AGENTRY_ROOT/tools/pipeline/approvals.py" --show
```

| Level | Clears without you | Waits for you |
|---|---|---|
| `manual` (default) | Nothing | Starting each task, the commit, the push |
| `assisted` | The commit | Starting each task; the push is never granted |
| `auto` | Starting the next ready task and the commit | The push, unless you remove it from `approvals_never_granted` |

Two checkpoints are withheld at every level: the plan approval and the one-shot
trunk push. `approvals_never_granted` in `pipeline.json` ships as
`["push", "trunk_push"]`, so no level pushes. Editing that list is your decision,
not the agent's. For finer control, list checkpoints in the `ready` stage's
`auto_approve` array; the default `[]` keeps the commit manual.

Start at `manual`. Move to `assisted` once the gates have proved they catch real
failures in your project.

## Decision 5: workflow mode

`workflow.mode` in `pipeline.json` describes how code reaches the trunk. Do not
confuse it with the session mode from `mode.py` (`build`, `plan`, `talk`), which
picks the stage machine for the current session.

| `workflow.mode` | Use when | Path to the trunk |
|---|---|---|
| `pr` (default when absent) | Someone else reviews and merges | Commit, push the branch, a human opens and merges the pull request |
| `solo` | One author, nobody to hand a pull request to | Commit, then a gated local merge of the task branch into the trunk |

In `solo` mode the local merge still needs a branch named `<type>/task-NNNN`, a
run in the store, and an approved commit checkpoint. `workflow.push_needs_approval`
stays `true` in both modes. Publishing the trunk takes your explicit say-so in
chat, recorded once with `python "$AGENTRY_ROOT/tools/pipeline/approve.py" --trunk-push`;
the push consumes the marker. An unattended session is refused it. Full rules:
`.claude/rules/git-workflow.md`.

## Checkpoints you will meet

| Checkpoint | When | Command the orchestrator runs after you agree |
|---|---|---|
| Plan | `feature` and `enhancement` tasks, before any code is written. `bugfix` and `techdebt` skip it | `approve.py --task task-XXXX --gate plan` |
| Commit | At `ready`, after you read the diff with `/diff` | `approve.py --task task-XXXX --gate commit` |
| Push | `pr` mode only; never granted by a level | `approve.py --task task-XXXX --gate push` |
| Trunk push | `solo` mode only, one push per approval | `approve.py --trunk-push` |

All of these live in `$AGENTRY_ROOT/tools/pipeline/approve.py`. While a plan is
unapproved, the gate denies every write outside `.agentry/` on the task's branch,
so the plan is the only thing that can change. Rejecting at the plan step keeps
the task at `plan`; rejecting the diff at `ready` returns it to `implement` and
keeps the plan approval.

Writing the checkpoint decision in chat is not enough. The orchestrator records
it with `approve.py`, and the `PreToolUse` hook blocks `git commit` and
`git push` until it does.

## Adopting mid-project

Skip this section on a fresh project.

- Set `handoff.baseline` and `memory.baseline` in `pipeline.json` to the highest
  existing done task id. Earlier tasks are grandfathered, so nobody writes
  retroactive handoff docs. On a fresh project both stay `""`.
- Before the next task registers, the previous completed task needs a handoff doc
  in `.agentry/tasks/handoffs/` and a memory stamp. Check with
  `python "$AGENTRY_ROOT/tools/pipeline/handoff.py" --check`, and after distilling the
  task into memory rows, stamp it with
  `python "$AGENTRY_ROOT/tools/memory/update.py" --stamp --task task-XXXX` and
  `python "$AGENTRY_ROOT/tools/memory/codebase_sync.py" --stamp`.
- Seed the module map (Phase A7.5 of the init prompt) before the first task.

## Self-check

- [ ] `.agentry/project/project-context.md` describes this product, not the previous one.
- [ ] `.agentry/project/stack.md` has concrete commands and no placeholders.
- [ ] `grep -r 'PROJECT-SPECIFIC - REPLACE ME' .agentry/project/` returns nothing.
- [ ] `grep -rE '\{\{[A-Z_]+\}\}' .claude/` returns only the three runtime-token files named above.
- [ ] `python "$AGENTRY_ROOT/tools/pipeline/state.py" --show` prints `[]`.
- [ ] `python "$AGENTRY_ROOT/tools/setup/repo_scan.py" --root .` agrees with `repos` in `pipeline.json`.
- [ ] Every stage in `pipelines.build` runs your commands, not the harness's.
- [ ] Every key of `lanes` points at a key of `repos`.
- [ ] `python "$AGENTRY_ROOT/tools/pipeline/mode.py" --show` prints the three dials as you intend.
- [ ] `workflow.mode` is the one you chose, and `approvals_never_granted` still lists `push` and `trunk_push`.
- [ ] `.claude/settings.local.json` is gitignored.

Then create the first task. For feature work, run the plan pipeline: architect
plan, `spec-developer` writes `spec-0001`, and after you approve it
`product-manager` produces the epic and the task files in `.agentry/tasks/backlog/`.
For a one-off start, write `.agentry/tasks/backlog/task-0001.md` with the
`new-task` skill. `advance.py` refuses a task with no acceptance criteria or an
unapproved spec.

## Day-two commands

| Need | Command |
|---|---|
| See where the pipeline stands | `python "$AGENTRY_ROOT/tools/pipeline/state.py" --show` dumps every run |
| Change the session mode | `python "$AGENTRY_ROOT/tools/pipeline/mode.py" plan` (`build`, `plan` or `talk`) |
| Run each epic as a parallel swarm | `python "$AGENTRY_ROOT/tools/pipeline/swarm.py" on` (`off` by default) |
| Set a task aside for an urgent one | `python "$AGENTRY_ROOT/tools/pipeline/approve.py" --task task-XXXX --pause "<reason>"`; refused while the tree has uncommitted work |
| Bring it back | `python "$AGENTRY_ROOT/tools/pipeline/approve.py" --task task-XXXX --resume` |
| Stop the background supervisor | `python "$AGENTRY_ROOT/tools/pipeline/supervisor.py" --stop` |

The template registers no status line: a plugin cannot ship one, and `AGENTRY_ROOT`
reaches the Bash tool through the env file, so it is not known to be set for a
status line command. To get the line (`task | stage | owed | lane | mode | approvals
| swarm`), add a `statusLine` command to your own settings that runs the installed
plugin's `tools/hooks/status_line.py` by absolute path, and restart the session.

## Version history

| Version | Date | Author | Change |
|---|---|---|---|
| 2.0 | 2026-10-09 | Technical Writer | Rewritten around five decisions (repositories, gates per repository, lanes, approval level, workflow mode); questions the autodetect answers removed; layout moved to `.agentry/` for work product and `.claude/` for the harness; plan checkpoint added, no separate diff-review stage |
| 1.0 | n/a | n/a | Single-repository, single-lane onboarding checklist |
