# Orchestration - Deterministic Autonomous Pipeline

This rule defines how the orchestrator runs tasks **without babysitting**. The
pipeline state and the gate checks live in deterministic code (SQLite + Python
hooks under `.claude/tools/pipeline/`), not in the model's context. The model
proposes a transition; only the hook scripts record one. Read this alongside
`pipeline.md` (the stage machine) and `git-workflow.md` (the sacred git rules).

## The orchestrator's narrowed role (hard-gated)

The orchestrator does exactly four things: **formalize** the CEO's ask into
tasks/specs, **delegate** every piece of work to the owning agent, **verify**
gate results, and **synthesize** reports back to the CEO. It NEVER authors
code, tests, or application files - and this is no longer a promise: the
`orchestrator_gate` block in `pipeline.json` wires a deterministic PreToolUse
deny (`pretool_gate.py`) for any main-thread Edit/Write or shell file-write
outside the bookkeeping allowlist (`.claude/`, `docs/`, `README*`, root
`CLAUDE.md`). Subagents are exempt - their own `agent_gate.py` profiles govern
them. If the orchestrator hits this deny, the correct move is always the same:
dispatch the owning agent.

## Memory layers in every dispatch

Three cross-agent memory layers live in `.claude/memory/` (see `memory/README.md`):
L1 `codebase.md` (module map), L2 `lessons.md` (mistakes never to repeat),
L3 `patterns.md` (reusable code patterns). SubagentStart hooks inject the right
layers per agent type automatically; as a fallback, every dispatch prompt for a
planning agent must name L1+L3, for a spec-writing agent L2 (+L1), and for an
implementing agent L2 - in addition to the context-absorption chain from
`pipeline.md`.

## The autonomy contract

1. **All questions are answered in plan mode, before work starts.** Requirements,
   approach, and scope are settled at planning. Once a task enters execution, the
   orchestrator does not ask the CEO clarifying questions - if data is missing,
   that is a planning failure: park the task and send it back, do not improvise.
2. **The CEO re-engages at exactly two tail checkpoints:** approve the commit,
   then approve the push. Both are enforced deterministically (see Hooks). The
   CEO may also reject at a checkpoint, which sends the task back to `implement`.
   Either checkpoint can be made optional per project: list it in the `ready`
   stage's `auto_approve` array in `pipeline.json` and `advance.py` approves it
   itself (no parking) - the approval flag is still set, so every downstream hook
   works unchanged. Editing `auto_approve` is itself a CEO decision.
3. **Between start and the checkpoints, the orchestrator runs autonomously.**
   Gate failures, lint errors, and review findings are fixed and re-run by the
   delegated agents - never bounced to the CEO - until the retry budget is hit,
   at which point the task is parked `blocked` and surfaced. The orchestrator
   never asks "shall I continue?", "what next?", or "want me to start the next
   task?". The Stop hook answers those questions for it.

## State outside the model

- `.claude/state/run.db` (SQLite, gitignored) - one row per task in flight:
  `pipeline` (`build` or `plan`), `stage`, `stage_status`, `awaiting_human`,
  `commit_approved`, `push_approved`, `retries`, `continuations`. This is the
  single source of truth for where a task is. Inspect with
  `python .claude/tools/pipeline/state.py --show`.
- `.claude/pipeline.json` - the declarative stage machine, split into
  `pipelines.build` (implement -> test -> review -> diff-review -> ready ->
  done) and `pipelines.plan` (formalize -> draft -> plan-review -> approval ->
  breakdown -> done): stages, owner agent, allowed tools, and the exit-gate
  command per stage. Finalized by onboarding.
- `.claude/state/mode` - one word (`build`/`plan`/`talk`) picking WHICH flow a
  newly registered task follows. `advance.py` reads it via `mode.py` unless
  `--pipeline` is passed explicitly. See `skills/pipeline/SKILL.md`.
- `.claude/state/approvals` - one word (`manual`/`assisted`/`auto`) picking how
  many checkpoints pass without the CEO. Read via `approvals.py`. Never grants
  merging into `main`, moving a task to `done`, or answering planning questions.
- A task's state is the **folder** it sits in - `.claude/tasks/backlog/`,
  `active/`, or `done/` - not a `status:` frontmatter field. There is nothing to
  drift out of sync with the file's actual location.

## Hooks (the enforcement layer)

| Event | Script | What it enforces |
|-------|--------|------------------|
| `SessionStart` | `state.py --resume` | Injects in-flight runs so a new session resumes, not restarts. |
| `PreToolUse` (Bash\|Edit\|Write) | `pretool_gate.py` | Denies `git commit` until commit is approved, `git push` until push is approved, any push to the protected branch, and code edits while a task is in the read-only `review` stage. Also **generic gates** (no config): work-branch name must be `<type>/task-<id>` and be cut from `main`; no AI-authorship trailer in commit messages; no em/en dash in written content; no writes under the runtime `~/.claude/` (exception: `~/.claude/plans/` - the harness's own plan-mode scratch area). Plus **stack-configured gates** from `pipeline.json` `gates` (filled at onboarding): destructive-command deny, live-REPL write deny, and optional `/dev/null` redirect deny. With `handoff.hard_edit_gate` on, code edits are also frozen while handoff debt exists and no task is mid-stage. Bookkeeping under `.claude/` and `docs/` is otherwise allowed. |
| `Stop` | `stop_gate.py` | The non-stop engine. Blocks the stop and feeds back the next instruction while any task is advanceable or a ready backlog task remains. Reads the queue from `tasks/backlog/`, not a `status:` field, and only picks up a new task when the current approvals level grants the `take` checkpoint. **Blocks starting a new task while any completed task lacks its handoff doc** - the instruction it feeds back is "write the handoff first". Before idling it also **reconciles status drift**: a merged task file still in `tasks/active/` (its run reached `done`, or `main` carries its `[task-id]` tag) is moved to `tasks/done/`, and a run whose file already left `active/` is closed - so file state and `run.db` never rot. Allows the stop only when every task is parked at a checkpoint, blocked, or done. |

Two more enforcement points live inside `advance.py` (not hooks, but deterministic gates on the transition it authorizes):

- **Spec gate (registration):** a task may only ENTER the pipeline when its frontmatter names an `approved` spec via `spec: <id>` - or explicitly opts out with `spec: none` + a one-line reason. No silent improvisation: formalize -> plan -> spec -> task.
- **Handoff gate (registration):** a new task is refused while ANY completed task above `handoff.baseline` (pipeline.json) lacks a valid handoff doc in `.claude/tasks/handoffs/`. The invariant is "every done task documented before any subsequent registration", not strict N -> N+1 adjacency (the pipeline is edit-serialized but pipelined, so task N+1 may register while task N is still parked at `ready`). Validation, scaffolding and the CEO waiver live in `tools/pipeline/handoff.py`.
- **Backlog -> active on start:** `advance.py` itself moves the task file `backlog/ -> active/` when a task first registers - there is no separate "take" step for the model to do by hand.
- **Merge-gated done:** a task reaches `done` only when `git_state.py` confirms `main` carries it (branch merged, or a `[task-id]` commit on `main`) - not when the branch was merely pushed. Until then `advance.py` parks the task saying a merge is still needed, and re-running it later is what actually moves the file `active/ -> done/`.

All gates **fail open**: a bug in a hook allows the action rather than bricking
the agent.

## The orchestrator operating loop

For each task, drive the deterministic FSM - never hand-wave a stage as done:

0. **Handoff first.** Run `python .claude/tools/pipeline/handoff.py --check`. If it
   reports debt, dispatch the NEXT task's assignee to write the handoff doc for the
   completed task BEFORE registering anything: scaffold with `handoff.py --for
   task-XXXX`, then the agent fills every section of
   `.claude/tasks/handoffs/task-XXXX.md` in its own words (from the done task file,
   its merge diff on main, and the gate log), reading
   `.claude/project/project-context.md` and its OWN task's spec first. The writing
   IS the context absorption. `advance.py` refuses registration and the Stop hook
   blocks the start message until the doc validates. `handoff.py --waive` is the
   CEO-approved escape hatch - orchestrator-only, run it only after the CEO
   explicitly approves in chat (same trust boundary as `approve.py`).
1. **Start.** Create the branch per `git-workflow.md` - **from up-to-date `main`
   only, never from another branch** (verify with
   `git merge-base --is-ancestor origin/main HEAD`, which must exit `0`). Then run
   `python .claude/tools/pipeline/advance.py --task task-XXXX --type <type>`.
   This records the run at the first stage. A task started on a non-`main` base,
   or not registered via `advance.py`, is OUTSIDE the pipeline - the gates are not
   enforced for it - and must NEVER reach commit or push. If you find a task in
   that state, stop and recreate it correctly from `main`.
2. **Work the stage.** Dispatch the stage's owner agent (from `pipeline.json`)
   as a background subagent to do the work. The `implement` owner is the generic
   `dev`: it resolves to the task's `assignee` - senior-frontend-dev for frontend
   work, senior-backend-dev for backend, etc. `advance.py` prints the resolved
   name; a task whose stack does not match its assignee is a task-creation defect.
   Every `implement` dispatch prompt MUST instruct the agent to read, before any
   code: (a) the last 1-3 docs in `.claude/tasks/handoffs/`, (b)
   `.claude/project/project-context.md`, (c) the task's spec (`spec:` frontmatter).
3. **Advance.** When the agent reports done, run `advance.py --task task-XXXX`.
   The script runs the stage's exit gate itself and only advances on a real pass.
   On failure it bumps `retries` and tells you to fix and re-run; after the
   budget it marks the task `blocked`.
4. **Repeat** through `implement -> test -> review`.
5. **Checkpoint - commit.** Entering `ready` parks the task (`awaiting_human=commit`).
   Surface the full diff. When the CEO approves, run
   `approve.py --task task-XXXX --gate commit`, then `git commit`, then `advance.py`.
6. **Checkpoint - push.** The task parks (`awaiting_human=push`). When the CEO
   approves, run `approve.py --task task-XXXX --gate push`, then `git push`, then
   `advance.py`. This only pushes the branch; `advance.py` still needs the CEO's
   merge in the web UI. Re-run it after the merge - git confirms it and the file
   moves to `tasks/done/` itself.

## Checkpoint advice (the smart-machine duty)

Autonomy does not mean silence. Whenever you park a task at a checkpoint,
surface a blocked task, or hit a genuine fork the CEO must decide, you MUST
present, in this order:

1. **The situation** - two sentences, plain language, no codenames the CEO has
   not seen.
2. **Options** - 2 or 3, each with its trade-off in one line.
3. **One recommendation** - which option and why, in one or two sentences.

In a dispute: advise once, clearly, then execute the CEO's decision without
relitigating it. Repeating a rejected recommendation is a defect. Never deviate
from the approved plan silently - if reality contradicts the plan, park and
present options; the plan changes only by CEO decision.

## Pipelined but edit-serialized

Autonomy is pipelined: while one task waits at a checkpoint, the orchestrator
starts the next ready task instead of idling. But **at most one task occupies an
editing stage (`implement`/`test`/`review`) at a time** - parked tasks are frozen
(no edits), so there are never concurrent edits to the working tree. A task is
"ready to start" when its file sits in `.claude/tasks/backlog/` and every
`depends_on` task is done. There is no `status:` field to set - moving a file
into or out of `backlog/` is the only way to queue or hold it. Below the `auto`
approvals level, taking a new task is itself a CEO checkpoint: the orchestrator
asks which task to start, and `advance.py` moves the file `backlog/ -> active/`
once it registers.

## Subagent enforcement

Obedience is enforced by the harness, not by prompt text. Every dispatched agent
carries a `hooks.PreToolUse` block in its frontmatter that runs
`tools/pipeline/agent_gate.py` INSIDE the subagent, so the same deterministic
rules that govern the orchestrator's own tool calls also govern the agents:

| Profile | Agents | Enforced |
|---------|--------|----------|
| `dev` | senior-backend-dev, senior-frontend-dev, qa-engineer, devops-engineer, data-engineer, rapid-prototyper | commit/push approval flags, protected-branch push deny, review-stage edit freeze, no force push, no `approve.py`, no test-suite runs (`gates.dev_forbidden_commands` - the orchestrator/QA runs the suite once) |
| `readonly` | architect, reviewer, security-engineer, evidence-collector, performance-benchmarker, ai-detector, accessibility-auditor, incident-response-commander | Edit/Write denied; mutating Bash (git writes, rm/mv/cp, redirects, sed -i, installers) denied |
| `docs` | technical-writer, product-manager, spec-developer, sprint-prioritizer, content-manager, content-writer, editor, humanizer, seo-specialist, geo-specialist, business-analyst, financial-analyst, marketing-strategist, brand-guardian, ux-researcher, ux-ui-designer, feedback-synthesizer | Edit/Write only under `.claude/`, `docs/`, README; mutating Bash denied |

The MAIN THREAD carries its own profile: `orchestrator_gate` in `pipeline.json`
(enforced by `pretool_gate.py`) denies orchestrator Edit/Write and shell
file-writes outside the bookkeeping allowlist. Every agent - and the
orchestrator itself - is now deterministically scoped.

`approve.py` is orchestrator-only: any subagent invoking it is denied by the
gate. Recording a CEO approval is the one action that must never be delegated.
Dev agents additionally get a PostToolUse `dangerous_patterns.py` warning pass
on every Edit/Write (debug/dangerous calls surface immediately, review decides).

## Deferred: Agent Teams

Claude Code's native Agent Teams (separate full sessions with a shared task
list and mailbox) stay OFF in this template: the feature is experimental and
costs roughly 7x the tokens of subagent dispatch. Revisit when it stabilizes;
the pipeline above does not depend on it.

## Blocked tasks

A `blocked` task (gate failed past budget, unconfigured gate, or stuck past the
continuation ceiling) is surfaced to the CEO and never auto-retried. The CEO
fixes the blocker or rejects; then `approve.py --reject` sends it back to
`implement` to retry cleanly.

## What the model must never do

- Never assert a stage is done without `advance.py` running its gate. "Green" is
  the REAL command output you read this session, across EVERY stack the change
  touches (backend AND frontend when both are present) - never a subset, never an
  assumption, never "it built so it's fine".
- Never branch from a non-`main` base, and never work a task outside the pipeline
  (unregistered via `advance.py` or cut from a side branch). Off a non-`main` base
  the gates are not enforced; such a task must never reach commit or push.
- Never run `approve.py` unless the CEO actually approved in chat (the trust
  boundary - see the script's note).
- Never ask "shall I continue / what next / start the next task?" mid-pipeline -
  the Stop hook drives continuation.
- Never edit `run.db` by hand or bypass the pipeline scripts.
