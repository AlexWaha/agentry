---
name: swarm
description: Protocol for running one epic as a supervised agent swarm - umbrella task, one branch, contract board with file ownership, waves of parallel dev agents, QA swarm after all agents report, parallel fixes, review, commit. Used when the pipeline swarm flag is on (/pipeline-build-swarm and friends) and an approved epic is ready, when a session restarts mid-swarm (resume.md), or when an interrupted agent must be relaunched. Triggers on "swarm", "start the epic", "запусти эпик", "продолжи swarm", "relaunch the agents".
---

# Swarm protocol

This skill belongs to this project and is edited here. Everything
project-specific (commands, paths, test and build, agent names) lives in the
project's adapter
`.claude/pipeline-adapter.md` (contract below). No adapter = create it first.

Why: an 18-subtask epic (backend + frontend) took about 40 minutes with ~16
parallel agents instead of hours with 1-2 agents in sequence; QA runs once as a
parallel swarm instead of after every agent.

## When it applies

Only in `build` mode with the swarm flag ON (`/pipeline-build-swarm`,
`/pipeline-build-auto-swarm`, ...). Swarm OFF = one task at a time with the
project's normal per-task flow.

## Adapter contract (`.claude/pipeline-adapter.md`)

A table with these keys, each a concrete command or path in this project:

| Key | Meaning |
|---|---|
| `show` | print mode, approval level, swarm flag |
| `set_mode` | set `build` / `plan` / `talk` |
| `set_approvals` | set `manual` / `assisted` / `auto` |
| `set_swarm` | set swarm `on` / `off`, persisted like mode and approvals |
| `start_epic` | register the umbrella task `task-<epic>` |
| `advance`, `approve_push`, `reject` | stage transition, CEO push approval, back to implement (stays at plan before the plan approval) |
| `backlog_dir`, `done_dir`, `state_dir` | subtask files and swarm state (`state_dir` must be writable by dev and QA agents under the hooks) |
| `branch` | epic branch pattern |
| `suite` | the test-stage exit gate (full suite), run once by the orchestrator through `advance`, with the green reference count; agents run narrowed runs only |
| `build` | the implement-stage build command and the expected output check |
| `self_check` | per-stack lint/compile commands dev agents run on their own files |
| `roles` | agent names: backend dev, frontend dev, QA, reviewer, security |
| `approvals_grants` | config listing checkpoints never granted at any level in this project (stricter than the pipeline skill default), and any local merge mode with its trigger; `none` if the defaults apply |
| `push_to_main` | config flag that allows pushing to the main branch; forbidden unless set |
| `trunk_based` | true when the project commits and pushes on the main branch; requires `push_to_main` allowed; the task is resolved from the single active run |

## Steps

1. **Pre-flight.** Plan, spec (explicit API contracts: endpoints, payloads, class and route names) and subtask files approved; backend and frontend never share a subtask. Main-branch log and `done_dir` show nothing already shipped.
2. **Branch + umbrella task.** One branch per epic from fresh main, `start_epic` once. Subtask files stay in `backlog_dir` as the work list. The umbrella task file carries frontmatter `umbrella: true`, `epic: epic-XXXX` and `subtasks: [task-A, task-B]`; the Stop hook and `advance.py` recognise the umbrella by those keys. `umbrella`, `epic`, `subtasks`, `branch` and `repo` are frozen in the run row at registration, so list every subtask before `start_epic`; later edits to the file change nothing. Registered as `feature`, the umbrella starts at `plan`: the plan goes to `.agentry/plans/`, and the orchestrator must get the CEO's plan approval (`approve.py --gate plan`, then `advance`) BEFORE launching any wave, because writes outside `.agentry/` are denied to the branch until it is recorded.
3. **Board before launch.** `<state_dir>/board.md` from `references/templates.md`. Ownership table: every shared file (route files, observers, config, translations, env example, shared UI components, build and typecheck configs) has exactly one owner subtask. Then one append-only `## <subtask>` section per agent. Fill the dev template into `<state_dir>/dev-prompt.txt`.
   **Zones.** Add a `## Zones` table to the board, `| zone | paths |`: one row per agent, including `qa-engineer`, which needs its own zone to write at the `test` stage. Zone ids match `[A-Za-z0-9][A-Za-z0-9._-]*`; paths are comma-separated, each an exact repo-relative path or `dir/**` (no mid-path globs, `*.md`, trailing `/`, `..` or absolute paths). Freeze the map with `zones.py --prove --task <task>` (adapter: Notes); exit 1 names the overlaps, and a shared file is named exactly in one zone or in none. Every agent prompt then carries the line `Swarm zone: <id>` (the gate reads it from the agent's own transcript; agent identity was measured only for a headless foreground dispatch, so before wave 1 dispatch ONE zoned agent in the background and confirm its zone write works). To unfreeze a frozen map run `zones.py --release --task <task>` (orchestrator-only). The swarm state folder `.agentry/tasks/swarm/<task>/` of the zone map's own task is writable for every zoned agent (board posts, QA reports); the rest of `.agentry/tasks/` (task files, `done/`, handoffs, other tasks' swarm folders) stays denied.
4. **Waves.** Wave 1: hard prerequisites (schema, models) plus everything independent (frontend codes against the spec contract). Wave 2: the rest, as soon as the prerequisites report. A whole wave goes out in ONE message, every agent in the background. Append `<subtask> <agent-id>` to `<state_dir>/agents.txt`.
5. **Supervise.** Agents `SendMessage` to `main` with contract changes, blockers and spec gaps. Decide, write the decision on the board, relay by agent id to every affected agent; never agent to agent. Accepted deviations, items for QA, deploy actions and cleanup: one line each in `<state_dir>/qa-notes.md`.
6. **Resume note.** `<state_dir>/resume.md`, rewritten at every wave boundary and before any restart: done, in flight and where each stopped, next step, template paths. The SessionStart hook prints its path.
7. **Interrupted agent** (turn limit, restart): relaunch with the "continue from partial work" template, never a fresh prompt. Append the new id.
8. **Test stage** only after EVERY agent reported done: `advance`, then the QA swarm in one message - backend QA, static frontend QA, and build and browser QA only when the adapter's `build` is a real build. QA agents run narrowed test runs only (`-k` or a single module); the full suite runs once, in the test-stage exit gate the orchestrator drives through `advance`. Each writes `qa-<area>.md`; consolidate into `qa-report.md`: fix groups `FX-n` on disjoint file sets, an "accepted / not fixed" list for the CEO, "not yet checked".
9. **Fixes** in parallel, one fixer per FX group. Fixers run narrowed runs of their own tests only; the full suite is the exit gate. Re-QA only the touched areas. Repeat until no Critical/High and suite and build are green; `advance`.
10. **Review.** Reviewer + security in parallel, read-only, report in the reply; the supervisor saves it. Critical/High: `reject`, fixers, re-QA, back to review. Medium: to the CEO.
11. **Commit and push.** Format + lint, one commit on the epic branch, push per the approval level and the adapter's `approvals_grants`, PR text with deploy actions from `qa-notes.md`. After merge the umbrella task moves to `done_dir` and `advance.py` (or the Stop hook's drift reconciler) moves every subtask listed in its `subtasks: [...]` there too. No hand moves.

## Rules every agent prompt repeats

- Shared working tree: never `git checkout` / `switch` / `stash` / `reset` / `restore` / `commit` / `push`. With a frozen zone map the gate also denies `clean`, `apply`, `am`, `merge`, `rebase`, `cherry-pick`, `revert`, `pull`, `rm` and `mv`, zoned or not.
- One agent per zone, and exactly one writer for each shared-set file. Two agents given the same `Swarm zone:` id are not detected: give each id to one agent.
- If one zone's agent fails or stops mid-wave, the other zones are untouched (shared tree, no rollback). Relaunch that agent with the continue-from-partial template and report which zone failed. A zoned agent cannot revert other zones, because git rewrites are denied.
- Zones are a tripwire against mistakes, not a boundary against a hostile dev agent. Not covered: interpreter one-liners, agent-written scripts, `npm install`, and writers such as `touch`, `dd of=`, `install`, `rsync`, `curl -o`, `wget -O`, `patch`, `perl -pi`, `awk -i inplace`, `ed`, `sed` `w` files, `sort -o`, `tar -x`, `unzip -d`, `find -delete`/`-exec`, `git clone`, `git read-tree -u`, `git checkout-index -f -a`, `git sparse-checkout`, `git worktree add`, `git diff --output`.
- Touch only files the board assigns you; edits to others' files go through the supervisor. Under a frozen zone map the gate enforces it: writes outside your zone (and `tmp/`) are denied, and the deny names the owning zone.
- The first line of every prompt carries `Swarm zone: <id>` (the dev template has it); without it the agent cannot write at all. The QA and fixer prompts need the line too.
- Post what you expose (classes, signatures, payloads, props, config and translation keys) on the board EARLY.
- No agent runs the full suite; narrowed runs only (`-k` or a single module). Dev and fixer agents do not run the build; only `self_check` on their own files. Formatters with `--write` on own files only, never on a directory.
- Ambiguity: `SendMessage` to `main`, then continue with the option closest to the spec.

## Pitfalls already paid for (verify each in every project)

1. `state_dir` must be writable by dev and QA agents under the PreToolUse gates, or board posts and QA reports fail silently.
2. Dev-profile agents (QA included) are denied the unfiltered suite; they run narrowed runs. The suite runs once, in the exit gate. One build runner at a time.
3. Destructive-command hooks must not match innocent words (for example `truncate` as a CSS class or a plain identifier): match the destructive form in its real context only.
4. Turn limits: dev and QA 150, reviewer and security 100, planning (docs-profile) agents 30 in this project. Long QA writes its report early; an agent that hit the limit is resumed with "stop, write the report from what you have".
5. Agent definition changes (`tools` such as `SendMessage`, `maxTurns`, `model`) load only after a session restart. Stopping agents for a restart loses in-flight work: relaunch with "continue from partial work".
6. A read-only reviewer cannot write files: it replies, the supervisor persists the report.
7. Windows: `sed -i` and Python text-mode writes rewrite every line ending; use the Edit tool or byte-level writes and check `git diff --stat`. In Git Bash `>NUL` / `2>NUL` create a real file named `NUL`: do not redirect at all.
8. A global gitconfig with `color.status=always` puts ANSI codes into captured git output: use `--porcelain` or `git -c color.ui=false -c color.status=false`.
9. A project's default typecheck can silently skip source folders; static QA verifies coverage.
10. Specs that leave class and route names open force agents to invent them; the board plus supervisor relays catch it, but name them in the spec.
11. Pipeline scripts must honor the approval level at the commit checkpoint (a stage-level `auto_approve` list that ignores `manual` is a bug), and any stricter project grant from the adapter.

## References

- `references/templates.md` - dev, continue-from-partial-work, backend QA, static frontend QA, build and browser QA, fixer prompts; board template. Fill in the adapter values.
