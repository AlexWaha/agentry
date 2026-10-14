# Stack

**Date:** 2026-10-14
**Author:** technical-writer (task-0052, swarm tasks 0118 and 0120; task-0132; task-0038)
**Status:** Draft
**Version:** 2.3

Agentry is a Python harness for Claude Code. It has no application server, no
database server, no frontend and no HTTP API. This file records the stack and
the commands every rule defers to. Gate commands are copied from
`.agentry/pipeline.json`; if the two ever differ, `pipeline.json` is what the
gates run and this file is the one that is wrong.

## Table of contents

- [Tech stack](#tech-stack)
- [Token values](#token-values)
- [Commands](#commands)
- [Environment variables](#environment-variables)
- [API routes](#api-routes)
- [Project structure](#project-structure)
- [Version history](#version-history)

## Tech stack

| Layer | Technology | Status |
|-------|-----------|--------|
| Language | Python 3.12 or newer (the scripts parse on 3.9; the README sets 3.12 as the supported floor) | ACTIVE |
| Runtime dependencies | None. Every script under `$AGENTRY_ROOT/tools/` (source: `plugin/tools/`) uses the standard library only (`json`, `sqlite3`, `subprocess`, `argparse`) | ACTIVE |
| Framework | None | n/a |
| State store | SQLite via stdlib `sqlite3`: `.agentry/state/run.db` (pipeline runs, one extra file per lane) and `.agentry/state/events.db` (append-only event journal, WAL mode, a report: only `supervisor.progress_at` reads it, and only to veto a relaunch (recent gate evidence keeps a run HEALTHY); no gate reads it; replaces the retired `gate_log` table) | ACTIVE |
| Memory store | SQLite with an FTS5 index: `.agentry/memory/memory.db` | ACTIVE |
| Host | Claude Code (hooks: PreToolUse, PostToolUse, SessionStart, SubagentStart, SubagentStop, Stop; measured against build 2.1.295) | ACTIVE |
| Cache or queue | None | n/a |
| Auth | None. Authority is the recorded approval in `run.db`, not a user identity | n/a |
| Testing | `unittest` (stdlib), suite in `plugin/tools/tests/` (3528 tests collected, 2026-10-14) | ACTIVE |
| Lint | `ruff` (dev dependency only, version recorded in `pyproject.toml` as 0.16.1), rule set in `pyproject.toml` | ACTIVE |
| Formatter | None configured. `pyproject.toml` has no `[tool.ruff.format]` section and no gate runs a formatter | n/a |
| Frontend | None | n/a |
| Containers and CI | None. There is no `Dockerfile`, compose file or `.github/` directory | n/a |

## Token values

Rules and agent files refer to these values by token name (written in double
braces in those files). The token set is copied from the onboarding template;
this table is where each one resolves for Agentry.

| Token | Value |
|-------|-------|
| `PROJECT_NAME` | Agentry |
| `PRODUCT_DESCRIPTION` | A deterministic orchestration harness for Claude Code: a gated stage machine whose transitions are recorded by hook scripts, not by the model |
| `STACK` | Python 3.12+, standard library only, SQLite |
| `LANG` | Python 3.12+ |
| `FRAMEWORK` | none |
| `BACKEND_STACK` | Python scripts run as Claude Code hooks and CLIs |
| `DEFAULT_DB` | SQLite (`run.db`, `memory.db`) |
| `AUTH_METHOD` | none |
| `TEST_FRAMEWORK` | unittest |
| `FORMATTER` | none configured (ruff is used as a linter only) |
| `CONTAINER_TOOL` | none |
| `CI_TOOL` | none |
| `MAIN_BRANCH` | `main` |
| `BUILD_CMD` | `python -m compileall -q plugin/tools` |
| `TEST_CMD` | `python -m unittest discover -s plugin/tools` |
| `LINT_CMD` | `ruff check plugin/tools` |
| `FORMAT_CMD` | none (see Commands) |
| `DEV_CMD` | none (see Commands) |

## Commands

Run every command from the repository root, the directory that holds `.claude/`,
`plugin/` and `.agentry/`. In the Agentry workspace that is `src/`; the
workspace-level `pipeline.json` sets `"cwd": "src"` and the `agentry` repo entry
has `path: "src"`.

Two path conventions appear in the table. The gate commands (build, test, lint)
use the repository-relative `plugin/tools`, so a worktree runs its own copy of
the code. Every other command uses `"$AGENTRY_ROOT/tools/..."`: `AGENTRY_ROOT` is
the plugin root, exported by the plugin's `session_start` hook, and until
task-0039 switches the live harness it is the workspace root `.claude`. See
`docs/technical/plugin-layout.md`.

"Exit status" is the real status of the command on the tree this file
describes, measured on 2026-10-12. "not run" marks a command that needs a
task or changes state, so it was deliberately left unexecuted.

| Command | What it does | Source | Exit status |
|---|---|---|---|
| `python -m compileall -q plugin/tools` | Build: byte-compiles every script, fails on a syntax error. The `implement` exit gate | `pipelines.build` stage `implement`, `repos.agentry.stages.implement` | 0 |
| `python -m unittest discover -s plugin/tools && python {root}/.claude/tools/pipeline/ui_evidence.py --task {task}` | Test: full suite, then the UI-evidence check for the task. The `test` exit gate; `{task}` and `{root}` are filled in by the gate. `{root}/.claude/tools` is the frozen workspace-root copy, kept through task-0038 and replaced by `{plugin}` in task-0039 | `pipelines.build` stage `test` | 0 (3528 tests collected, 2026-10-14; the last full green run was measured at 1711 on 2026-10-12) |
| `python -m unittest discover -s plugin/tools` | The suite alone, for the orchestrator or CI. Dev agents are barred from running it (`gates.dev_forbidden_commands`); they run named modules from `plugin/tools/tests`, for example `python -m unittest test_lanes test_lane_routing`, or let the `test` exit gate run the whole suite | derived from the row above | 3528 tests collected (2026-10-14) |
| `ruff check plugin/tools` | Lint. The `review` exit gate | `pipelines.build` stage `review`, `repos.agentry.stages.review` | 0 |
| `ruff check --fix plugin/tools` | Applies the safe lint fixes. This is the nearest thing to a formatter; no gate runs it | `pyproject.toml` rule set | not run (mutating) |
| `python "$AGENTRY_ROOT/tools/pipeline/advance.py" --task task-XXXX --type <type>` | Registers a task and advances it one stage by running the real exit gate | `rules/orchestration.md` | not run (needs a task) |
| `python "$AGENTRY_ROOT/tools/pipeline/state.py" --show` | Dumps every run in `run.db` as JSON | `state.py` docstring | 0 |
| `python "$AGENTRY_ROOT/tools/pipeline/approve.py" --task task-XXXX --gate commit` | Records a checkpoint approval. Orchestrator only, and only after the User approves | `rules/orchestration.md` | not run (changes state) |
| `python "$AGENTRY_ROOT/tools/pipeline/stack_gate.py" --task task-XXXX --stage implement\|test\|review [--repo KEY]` | Runs the per-repo gate commands from `repos{}` in `pipeline.json` | `stack_gate.py` | 0 (`--stage review --repo agentry`) |
| `python "$AGENTRY_ROOT/tools/pipeline/supervisor.py" --stop` | Stops the detached supervisor daemon | `pipeline.json` `supervisor._comment` | not run (changes state) |
| `python "$AGENTRY_ROOT/tools/memory/memory.py" --query "<topic>"` | Queries the memory store (`--record`, `--export`, `--stats` also exist) | `rules/self-learning.md` | 0 |
| `python "$AGENTRY_ROOT/tools/memory/memory.py" --refine --signature S [--agent A] [--trigger\|--what\|--why\|--fix\|--area\|--task ...]` | Corrects a recorded row in place instead of adding a near-duplicate. `--retire --signature S [--agent A] --reason TEXT` withdraws a wrong row from retrieval (flag plus removal from the FTS index). A duplicate `--record` is refused with these two commands printed | `skills/self-learning` | not run (changes state) |
| `python "$AGENTRY_ROOT/tools/pipeline/events.py" --task task-XXXX` / `--summary [--since DATE]` | Prints the event journal (`.agentry/state/events.db`) for one task or for all tasks, as JSON. `approve.py --reject` now requires `--reason "<why>"` | `rules/orchestration.md` "Event journal" | 0 |
| `python "$AGENTRY_ROOT/tools/pipeline/zones.py" --prove --task task-XXXX` / `--show` | Freezes the board's `## Zones` map for a swarm task (exit 1 names overlaps), or prints the frozen map (`--show` needs no task) | `skills/swarm/SKILL.md` | not run (needs a swarm task) |
| `python "$AGENTRY_ROOT/tools/pipeline/leak_guard.py" --ref REF` | Refuses a ref that adds a path under `.agentry/` that `<remote>/<trunk>` lacks (`origin/main` unless `pipeline.json` sets `remote` / `main_branch`; exit 0 clean, 1 leaked, 2 error; the whole push check has a 12 s budget and a slow git call denies). Also run by every `git push` and by `.githooks/pre-push`; enable the hook once per clone with `git config core.hooksPath .githooks` | `rules/git-workflow.md` | not run (needs `origin/main`) |
| `python "$AGENTRY_ROOT/tools/pipeline/approve.py" --task task-XXXX --trunk-push [--remote NAME]` | Records the one-shot User approval for pushing the trunk to the named remote (default `origin`). Orchestrator only, `solo` mode | `rules/git-workflow.md` | not run (changes state) |
| `python "$AGENTRY_ROOT/tools/pipeline/worktree.py" --list` | Prints `{"enabled", "max_parallel", "trees": [...]}` as JSON: each in-flight task's worktree path, branch, status (OK, LEGACY, MISSING, INVALID) and stage | `rules/orchestration.md` "Parallel tasks in worktrees" | not run (new in task-0129 to task-0131, not yet deployed to the live harness) |
| `python "$AGENTRY_ROOT/tools/pipeline/worktree.py" --sweep` | Reports `<repo>-wt/task-*` directories no in-flight run records; removes nothing | same | not run (as above) |
| `python "$AGENTRY_ROOT/tools/pipeline/worktree.py" --remove --task task-XXXX` / `--repair --task task-XXXX` | Removes a merged task's tree (no `--force`; exit 1 when it had to be left), or re-links a moved or dangling one. Orchestrator only: dev agents are denied both when `worktrees.enabled` | same | not run (changes state) |
| `python "$AGENTRY_ROOT/tools/setup/repo_scan.py" --root DIR [--json]` | Lists the repos found under a root, for onboarding | `repo_scan.py` | 0 |

Gate agreement: the build, test and lint commands in the table match
`.agentry/pipeline.json` character for character, in both the workspace copy
and `src/.agentry/pipeline.json`. One difference is deliberate and is reported
here rather than resolved: `repos.agentry.stages.test` is an empty list (an
explicit pass for `stack_gate.py`), while the `test` stage's `exit_gate` runs
the full suite. The `pipeline.json` `_comment` on `repos` gives the reason:
the suite is the pipeline's own `test` exit gate and agents may not run it
directly.

### Worktree config and the `{root}` placeholder

`pipeline.json` takes an optional top-level block, shipped with `enabled` false:

```json
"worktrees": {"enabled": false, "max_parallel": 2}
```

`enabled` counts only as a literal `true`; `max_parallel` is an integer of at least
1 and anything else reads 1. When on, a task's gate commands run with its worktree
(`src-wt/task-NNNN`) as cwd, so the commands in the table above need no change.
Two commands read plans and evidence that live at the workspace root and must not
run in a tree, so after the rollout they use `{root}`, filled in by `gate.py` with
the workspace root as a forward-slash path (refused when it holds whitespace or a
quote):

- plan stage: `python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind plan`
- test stage, the evidence half: `python {root}/.claude/tools/pipeline/ui_evidence.py --task {task}`

Both `{root}` commands address the frozen workspace-root copy of the tools, which
task-0038 leaves in place; task-0039 replaces `{root}/.claude/tools` with the
`{plugin}` placeholder (resolved to the plugin root by `gate.py`). `pipeline.json`
is what the gates run. The `{task}` and `{root}` tokens are both substituted,
`{task}` first.

Commands that do not exist here: a formatter command, a dev server and a
production build. `FORMAT_CMD` and `DEV_CMD` therefore resolve to "none", and a
rule that says "run the formatter after a code change" means "run
`ruff check plugin/tools` and fix what it reports".

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `PIPELINE_LANE` | Names the lane (an independent conveyor) of a session. A lane gets its own `run.<lane>.db`, mode and approvals files. Letters, digits, underscore and hyphen, 32 characters at most; an invalid value warns on stderr and falls back to the default lane. Read in `pipeline/state.py` | unset (the `default` lane) |
| `AGENTRY_UNATTENDED` | Set by the supervisor on a session it relaunches. While set, `pretool_gate.py` refuses `git merge`, `git pull`, `git push` and `approve.py` | unset |
| `AGENTRY_NO_CONSOLE_INJECT` | Any non-empty value stops `diff_checkpoint.py` from typing `/diff` into the console. The test suite sets it | unset |
| `CLAUDE_PROJECT_DIR` | The project root, which owns `.agentry/`, rules, agents and `settings.json`. Claude Code sets it for hook commands only, not for Bash tool calls (probe P2, `docs/technical/plugin-probes-2.1.299.md`); the plugin's `session_start` hook exports it to `$CLAUDE_ENV_FILE` so scripts run from Bash find it | set by Claude Code for hooks; exported by `session_start` for Bash |
| `AGENTRY_ROOT` | The plugin root: the directory holding `tools/` and `skills/`. Exported by the plugin's `session_start` hook to `$CLAUDE_ENV_FILE`; prose and Bash commands use `"$AGENTRY_ROOT/tools/..."`. Until task-0039 it is the workspace root `.claude`. Skills and plugin hooks use `${CLAUDE_PLUGIN_ROOT}` instead, which Claude Code substitutes itself | exported by `session_start` |

No variable holds a secret. The harness reads no API key.

## API routes

None. Agentry exposes no HTTP, RPC or socket interface. Its interfaces are
command-line scripts under `$AGENTRY_ROOT/tools/` (source `plugin/tools/`) and the Claude Code hook protocol
(JSON on stdin, exit status and JSON on stdout). See `api-conventions.md` for
why that file is a stub.

## Project structure

```
src/                         # the git repository (the workspace root holds a second copy of the harness)
├── .claude/                 # project side of the harness: CLAUDE.md, rules, agents, settings.json, pipeline-adapter.md, skills/verify
├── plugin/                  # the plugin root (${CLAUDE_PLUGIN_ROOT}, $AGENTRY_ROOT)
│   ├── .claude-plugin/      # plugin.json
│   ├── hooks/               # hooks.json and cleanup-nul.sh
│   ├── skills/              # all skills except verify
│   └── tools/               # all executable code, Python stdlib only
│       ├── pipeline/        # stage machine, gates, state, supervisor
│       ├── hooks/           # session, rule-injection and notice hooks
│       ├── memory/          # memory store CLI and injection
│       ├── setup/           # onboarding and fleet scripts
│       ├── tests/           # unittest suite
│       └── payload.py       # dispatch-payload measurement
├── .agentry/                # project work product: pipeline.json, tasks, specs, plans, project overlay
├── docs/                    # memory notes and technical documents
├── pyproject.toml           # ruff configuration
└── README.md
```

With `worktrees.enabled`, `src-wt/` sits beside `src/` at the workspace root (not
inside it): one linked worktree per in-flight task, each with its own `tmp/`.

The module-by-module layout, the dependency rules and the data flow are in
`architecture.md`.

## Version history

| Version | Date | Change |
|---|---|---|
| 2.0 | 2026-10-09 | Replaced the onboarding template with Agentry's real stack, commands and variables (task-0052). |
| 2.3 | 2026-10-14 | Plugin layout (task-0038): gate commands point at `plugin/tools`, other commands at `$AGENTRY_ROOT/tools`, `AGENTRY_ROOT` documented, test count 1711 to 3528, project structure redrawn. |
| 2.2 | 2026-10-13 | Added the `worktrees` config block, the `{root}` placeholder and the `worktree.py` commands (task-0132, epic-0004). |
| 2.1 | 2026-10-12 | Added the event journal, memory refine/retire, swarm zones, leak guard and trunk-push remote commands (task-0120 subtasks 0046, 0086, 0060, 0041, 0113). |
