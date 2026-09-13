# Development Pipeline

Every task travels a gated finite-state machine. No stage is skipped, and a stage
is "done" only when its exit gate passes - verified by deterministic code
(`.claude/tools/pipeline/`), not asserted by the model. The enforcement mechanism
and the autonomy contract live in `orchestration.md`; this file is the stage map.
Gate command details are resolved from `.agentry/project/stack.md` and declared in
`.agentry/pipeline.json`.

## Two pipelines

Both are gated stage machines driven by the same `advance.py`, over different
stages (`pipeline.json` `pipelines.build` / `pipelines.plan`). Which one a new
task follows is picked by the session mode (`.agentry/state/mode`, see
`skills/pipeline/SKILL.md`) unless `advance.py --pipeline` is passed
explicitly. Neither pipeline asks the CEO a clarifying question mid-flow - the
`plan` pipeline is where all questions get answered, before any task registers
on `build`. `pipelines.plan`'s stock stages are `formalize -> draft ->
plan-review -> approval -> breakdown -> done`; this project layers its richer
epic/spec-developer workflow on top of that skeleton (knowledge -> plan
(`architect`) -> spec (`spec-developer`, `.agentry/specs/`, `status: draft`) ->
epic + task breakdown (`product-manager` via skills/new-epic, support:
`sprint-prioritizer`) -> CEO approval, spec flips to `approved`). Output: an
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
- **Build pipeline** (per task, autonomous): `implement -> test -> review ->
  ready -> done`. Driven by `advance.py`; see `orchestration.md`.

## Execution stages

| Stage | Owner agent | Exit gate (deterministic) | Human checkpoint |
|-------|-------------|---------------------------|------------------|
| implement | `senior-backend-dev` / `senior-frontend-dev` | `{{BUILD_CMD}}` succeeds; code in task scope | - |
| test | `qa-engineer` | `{{TEST_CMD}}` green; required cases per `testing.md` | - |
| review | `reviewer` (+ `security-engineer`) | `{{LINT_CMD}}` clean; all Critical/High findings resolved; read-only (no code edits) | - |
| ready | Orchestrator | gates green; diff prepared | **CEO reads the diff (`/diff`) and approves the commit**; rejecting sends the task back to `implement` |
| done | CEO | merged into `main` (confirmed by `git_state.py`, not just pushed) | **CEO approves push**; task file moved to `done/` once the merge is confirmed; next registration is blocked until this task's handoff doc exists AND its memory review is stamped (`tools/memory/update.py`) |

UI-bearing tasks add a `senior-frontend-dev` implement pass and an
`evidence-collector` screenshot check inside review. Performance-sensitive work
adds `performance-benchmarker` in review.

## Context absorption (MANDATORY before working a stage)

Every dispatched agent reads, in this order, BEFORE producing anything:

1. The last 1-3 handoff docs in `.agentry/tasks/handoffs/` - what the previous
   tasks shipped, decided, and warned about.
2. `.agentry/project/project-context.md` - the overall project context.
3. The task's spec (`spec:` in the task frontmatter -> `.agentry/specs/`), or the
   task file's acceptance criteria when `spec: none`.
4. Any `## CEO Review Feedback` sections at the end of the task file - comments
   the CEO recorded when sending the task back from the commit checkpoint; each
   comment must be addressed before the task returns there.

When the Orchestrator dispatches you to WRITE a handoff doc for the previous
completed task: write it in your own words from `tasks/done/<task>.md`, its
merge diff on main, and the gate log - the writing IS the context absorption.
A pasted task description fails validation (`handoff.py --check`).

## Discipline

- **Spec-driven.** No `implement` without a released task file (and an approved
  spec for multi-task work) - `advance.py` refuses registration otherwise.
  Missing data is a planning failure - send the task back, do not improvise
  mid-stage.
- **Spec drift is a defect.** During `implement`, any change that does not
  trace to a task acceptance criterion (or a spec FR when linked) is out of
  scope: park it and propose a follow-up task, never sneak it in. The reviewer
  treats out-of-scope changes as a High finding and uncovered criteria as
  Critical.
- **Branch from `main` only.** Every task branch is cut from up-to-date `main`,
  never from another branch (verify `git merge-base --is-ancestor origin/main HEAD`).
  Off a non-`main` base the deterministic gates are not enforced - the task is
  outside the pipeline and must not advance.
- **No silent skips.** A stage advances only when `advance.py` runs its gate and
  the gate passes. The orchestrator verifies; the agent does not self-certify. The
  gate must cover EVERY stack the change touches: a frontend change on a branch
  that also carries backend code is not green until the backend test + formatter
  pass too. A passing subset is not a pass.
- **Handoff-gated pickup.** Before a new task registers, the previous completed
  task must have a valid handoff doc in `.agentry/tasks/handoffs/`, written by the
  NEW task's assignee in its own words - forced context absorption. Enforced by
  `advance.py`, `stop_gate.py` and (hard_edit_gate) `pretool_gate.py`; details in
  `orchestration.md` step 0.
- **Gate failure.** The owning agent fixes and re-runs; after the retry budget
  (`pipeline.json`), the task is parked `blocked` and surfaced to the CEO.
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
