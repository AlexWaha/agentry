# Development Pipeline

Every task travels a gated finite-state machine. No stage is skipped, and a stage
is "done" only when its exit gate passes - verified by deterministic code
(`$AGENTRY_ROOT/tools/pipeline/`), not asserted by the model. The enforcement mechanism
and the autonomy contract live in `orchestration.md`; this file is the stage map.
Gate command details are resolved from `.agentry/project/stack.md` and declared in
`.agentry/pipeline.json`.

## Two pipelines

Both are gated stage machines driven by the same `advance.py`, over different
stages (`pipeline.json` `pipelines.build` / `pipelines.plan`). Which one a new
task follows is picked by the session mode (`.agentry/state/mode`, see
`skills/pipeline/SKILL.md`) unless `advance.py --pipeline` is passed
explicitly. Neither pipeline asks the User a clarifying question mid-flow - the
`plan` pipeline is where all questions get answered, before any task registers
on `build`. `pipelines.plan`'s stock stages are `formalize -> draft ->
plan-review -> approval -> breakdown -> done`; this project layers its richer
epic/spec-developer workflow on top of that skeleton (knowledge -> plan
(`architect`) -> spec (`spec-developer`, `.agentry/specs/`, `status: draft`) ->
epic + task breakdown (`product-manager` via skills/new-epic, support:
`sprint-prioritizer`) -> User approval, spec flips to `approved`). Output: an
approved spec, an epic in `.agentry/tasks/epics/`, and task files queued in
`.agentry/tasks/backlog/`. This is where ALL questions are answered - a spec
cannot be approved with a non-empty Open Questions section.

Threshold: spec + epic are MANDATORY for multi-task work. A single-task
feature or bugfix may go plan -> task directly; the orchestrator advises at
the fork per the checkpoint-advice rule. `advance.py` enforces the floor
either way: no task enters the pipeline without a file in `backlog/` or
`active/`, non-empty acceptance criteria, and a task naming a `spec:` is
refused until that spec is approved.

- **Plan pipeline** (per epic / milestone): see above. Owner: `architect`
  (spec: `spec-developer`; breakdown: `product-manager`).
- **Build pipeline** (per task, autonomous): `plan -> plan-approval ->
  implement -> test -> review -> ready -> done`. `advance.py --type` accepts any
  type, and every type except those in `skip_for_types` (`pipeline.json`:
  `bugfix`, `techdebt`) plans; those two start at `implement`. Driven by
  `advance.py`; see `orchestration.md`. The stage
  table below is the per-task flow: one task in an editing stage at a time, QA
  at its own `test` stage. With `worktrees.enabled` that limit becomes slots:
  each task registers into its own worktree (`<repo path>-wt/<task>`), its gates
  run there, and up to `worktrees.max_parallel` tasks hold an editing stage at
  once; LEGACY runs and umbrellas stay in `src/` and exclusive (see "Parallel
  tasks in worktrees" in `orchestration.md`). With the swarm dial on (`swarm.py on`), an epic with
  two or more subtasks registers as ONE umbrella task and runs in swarm mode
  instead (the protocol is `skills/swarm/SKILL.md`; parallel agents inside
  `implement`, QA once after all of them report done); see "Swarm mode" in
  `orchestration.md`.

## Execution stages

| Stage | Owner agent | Exit gate (deterministic) | Human checkpoint |
|-------|-------------|---------------------------|------------------|
| plan | `senior-backend-dev` / `senior-frontend-dev` (the task's assignee) | `artifact_gate.py --kind plan`: `.agentry/plans/*<task>*.md` exists, names the task, at least 400 characters | - |
| plan-approval | Orchestrator | the `plan` checkpoint: `approve.py --task X --gate plan` recorded in `run.db`; no approvals level and no `auto_approve` list grants it | **User approves the plan before any code is written**; rejecting sends the task back to `plan` and costs nothing but the plan |
| implement | `senior-backend-dev` / `senior-frontend-dev` | `{{BUILD_CMD}}` succeeds; code in task scope | - |
| test | `qa-engineer` | `{{TEST_CMD}}` green; required cases per `testing.md` | - |
| review | `reviewer` (+ `security-engineer`) | `{{LINT_CMD}}` clean; all Critical/High findings resolved; read-only (no code edits) | - |
| ready | Orchestrator | gates green; diff prepared | **User reads the diff (`/diff`) and approves the commit**; rejecting sends the task back to `implement` |
| done | User in `pr` mode; the pipeline in `solo` mode | merged into `main` (confirmed by `git_state.py`, not just pushed) | **User approves push** (`pr` mode only; in `solo` mode the local merge follows the commit approval, see `git-workflow.md`); task file moved to `done/` once the merge is confirmed, then the task's worktree, if any, is removed and its branch kept; next registration is blocked until this task's handoff doc exists AND its memory review is stamped - both refused by `advance.py` at registration, both blocking the Stop hook before it lets the session end (`tools/pipeline/handoff.py`, `tools/memory/update.py`) |

UI-bearing tasks add a `senior-frontend-dev` implement pass and an
`evidence-collector` screenshot check inside review. Performance-sensitive work
adds `performance-benchmarker` in review.

## Context absorption (MANDATORY before working a stage)

Every dispatched agent reads, in this order, BEFORE producing anything:

1. The handoff chain, which ARRIVES INJECTED - do not go and read it. A
   `SubagentStart` hook (`handoff.py --inject`) delivers the newest handoff doc
   in full plus one index line per older document, under
   `handoff.inject_budget_bytes`. Read an older document from the index only
   when this task actually needs it. Opening the last three by habit pays for
   the same bytes twice.
2. `.agentry/project/project-context.md` - the overall project context.
3. The task's spec (`spec:` in the task frontmatter -> `.agentry/specs/`), or the
   task file's acceptance criteria when `spec: none`.
4. Any `## User Review Feedback` sections at the end of the task file - comments
   the User recorded when sending the task back from the commit checkpoint; each
   comment must be addressed before the task returns there.

When the Orchestrator dispatches you to WRITE a handoff doc for the previous
completed task: write it in your own words from `tasks/done/<task>.md`, its
merge diff on main, and the gate log, so that the writing is the context
absorption. That is an instruction, not a check. `handoff.py --check` verifies
structure only: the six required headings, no `FILL-ME` marker, at least 40
non-space characters per section and 500 in the body, and the `Context loaded`
confirmations (list in `orchestration.md` step 0). It does not compare the text
with the task file, so a pasted task description that meets those checks
passes.

## Discipline

- **Spec-driven.** No `implement` without a released task file (and an approved
  spec for multi-task work) - `advance.py` refuses registration otherwise.
  Missing data is a planning failure - send the task back, do not improvise
  mid-stage.
- **Plan before code.** Until the plan approval is recorded, `pretool_gate.py`
  denies Edit, Write and file-writing shell commands for that task's branch
  everywhere outside `.agentry/` - agents and the main thread alike - the way
  it denies a commit without its approval. It reads the stage from
  `pipelines.build` (the stage declaring the `plan` checkpoint), keeps the
  approval in `run.db` (so it survives a session restart), and fails open on an
  unreadable state file. Files written by an interpreter (`python -c
  "open(...)"`) are outside what it can read. `approve.py --reject --reason
  "<why>"` (the reason is required) at the plan
  step stays at `plan`; from the commit checkpoint it returns to `implement`
  and keeps the plan approval.
- **Spec drift is a defect.** During `implement`, any change that does not
  trace to a task acceptance criterion (or a spec FR when linked) is out of
  scope: park it and propose a follow-up task, never sneak it in. The reviewer
  treats out-of-scope changes as a High finding and uncovered criteria as
  Critical.
- **Branch from `main` only.** Every task branch is cut from up-to-date `main`,
  never from another branch (verify `git merge-base --is-ancestor origin/main HEAD`).
  Off a non-`main` base the deterministic gates are not enforced - the task is
  outside the pipeline and must not advance. With `worktrees.enabled`, registration
  does the cut itself (`git worktree add -b <type>/<task> <repo path>-wt/<task>
  <trunk>`) and records the tree in the run row.
- **Gates run where the work is.** A run with a recorded worktree has its stage
  commands run in that tree (`gate.py`, `stack_gate.py`); `{root}` in a command is
  the workspace root, used by `artifact_gate.py` and `ui_evidence.py` so they read
  the live plans. A recorded tree that is MISSING or INVALID blocks the gate; it
  never falls back to `src/`. At the transition into the commit-checkpoint stage a
  tree that lacks trunk commits sends the run back to `test` (`stale`), commit
  approval kept. Details, the dispatch line every implement, test and review
  dispatch must carry, and the rollout: "Parallel tasks in worktrees" in
  `orchestration.md`.
- **No silent skips.** A stage advances only when `advance.py` runs its gate and
  the gate passes. The orchestrator verifies; the agent does not self-certify. The
  gate must cover EVERY stack the change touches: a frontend change on a branch
  that also carries backend code is not green until the backend test + formatter
  pass too. A passing subset is not a pass.
- **Handoff-gated pickup.** Before a new task registers, the previous completed
  task must have a valid handoff doc in `.agentry/tasks/handoffs/`; the NEW
  task's assignee writes it, in its own words as an instruction, and validity is
  a structural check, not an authorship check. Enforced by `advance.py`,
  `stop_gate.py` and (hard_edit_gate) `pretool_gate.py`; details in
  `orchestration.md` step 0.
- **Memory-gated pickup.** The same registration is refused again while any
  completed task above `memory.baseline` lacks its stamp (`tools/memory/
  update.py --check`), and the Stop hook will not let the session end while
  either debt stands. Neither debt check reads what is in flight: a finished
  task's documentation is owed whether or not a neighbour is parked, which is
  precisely the conflation that switched both of them off (task-0072). Two
  behaviours to know rather than infer, stated as behaviour because no decision
  record explains either: the `pretool_gate.py` edit freeze does not engage
  while a run sits in an editing stage that is not `blocked`, so a blocked run
  does not suppress it; and the Stop hook reports the debt only after it has
  declined to drive an in-flight run, then repeats up to three stops before
  handing it to the User and going quiet.
- **Subagent limits.** What the hooks hold subagents to, and what they do not, is
  stated in `orchestration.md`, "Subagent enforcement". Read its NOT COVERED list
  before relying on a hook as a boundary.
- **History.** Stage transitions, gate results, approvals, rejections (with their
  reasons) and merges are journaled in `.agentry/state/events.db`; `gate_log` in
  `run.db` is retired. Read it with `python "$AGENTRY_ROOT/tools/pipeline/events.py"
  --task T` or `--summary [--since D]`. No gate reads the journal; only
  `supervisor.progress_at` reads it, and only to veto a relaunch.
- **Gate failure.** The owning agent fixes and re-runs; after the retry budget
  (`pipeline.json`), the task is parked `blocked` and surfaced to the User.
- **Self-learning.** At each stage/session end, capture lessons per
  `self-learning.md` (shared rules for universal policy, a `lesson` row in the
  memory store for anything project- or role-specific). A finished task with zero
  captured lessons should prompt "did nothing really surprise me?".

## Stage -> agent map

implement: `senior-backend-dev`, `senior-frontend-dev` · test: `qa-engineer` ·
review: `reviewer`, `security-engineer` (+ `evidence-collector`,
`performance-benchmarker` when relevant) · infra/build/packaging:
`devops-engineer` · docs: `technical-writer` · planning: `architect` (plan),
`spec-developer` (spec), `product-manager` (epic/tasks), `sprint-prioritizer`
(ordering). The orchestrator runs the machine;
each agent owns one stage.
