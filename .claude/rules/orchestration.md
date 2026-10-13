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
outside the bookkeeping allowlist (`.claude/`, `.agentry/`, `docs/`, `README*`,
root `CLAUDE.md`). Subagents are exempt - their own `agent_gate.py` profiles govern
them. If the orchestrator hits this deny, the correct move is always the same:
dispatch the owning agent.

## Memory in every dispatch

Project memory is one queried store, `.agentry/memory/memory.db` (SQLite + FTS5,
see `memory/README.md`), holding `lesson`, `pattern` and `module` rows.
SubagentStart hooks query it with the text of the ACTIVE TASK FILE(S) and
inject the ranked matches per agent type automatically - nothing to name in the
prompt, and nothing you write in the prompt changes what comes back. The query
is not the dispatch text and cannot be: the `SubagentStart` payload carries
only the session fields plus `hook_event_name`, `agent_id` and `agent_type`,
with no prompt, description or task field anywhere in it (measured in the
2.1.269 binary, task-0081). Until that task the hook also printed its block as
plain stdout, which this event discards, so every dispatch received zero rows
silently at exit 0 while these paragraphs said otherwise - so if an agent
reports that no memory block reached it, treat that as a live defect, not as an
empty store. The practical consequence for a dispatch: what reaches the agent
follows from what the task file says, so a thin task file retrieves thin. When
a dispatch needs more than the injected rows, the agent queries the store
itself (`python .claude/tools/memory/memory.py --query "<topic>"`), in addition
to the context-absorption chain from `pipeline.md`.

## The autonomy contract

1. **All questions are answered in plan mode, before work starts.** Requirements,
   approach, and scope are settled at planning. Once a task enters execution, the
   orchestrator does not ask the CEO clarifying questions - if data is missing,
   that is a planning failure: park the task and send it back, do not improvise.
2. **The CEO re-engages at the tail checkpoints `workflow.mode` leaves in place:**
   the commit, and - in `pr` mode only - the push after it. Where the last one falls follows
   `workflow.mode` in `pipeline.json`: the push checkpoint in `pr` mode, the
   commit checkpoint in `solo` mode, where the local merge follows (`advance.py`
   `stage_checkpoints()` drops the push checkpoint there); the full split is in
   `git-workflow.md`. Solo mode does not weaken push approval:
   `workflow.push_needs_approval` is independent of the mode, and
   `approvals.never_granted()` refuses both `PUSH` and `TRUNK_PUSH` at every level
   (`approvals_never_granted` in `pipeline.json`, with `TRUNK_PUSH` and `PLAN`
   also withheld in code), so a push that does happen in solo mode, including the
   one-shot trunk push,
   still needs its recorded approval. Each checkpoint that exists is enforced
   deterministically (see Hooks). The CEO may also reject at a checkpoint, which
   sends the task back to `implement`. Every task type except those in
   `skip_for_types` (`bugfix`, `techdebt`) also has an earlier checkpoint, the plan approval (`approve.py --gate plan`), which no
   approvals level grants; rejecting there keeps the task at `plan`. The commit checkpoint can be made optional
   per project: list it in the `ready` stage's `auto_approve` array in
   `pipeline.json` and `advance.py` approves it itself (no parking) - the
   approval flag is still set, so every downstream hook works unchanged. The push
   cannot: `approvals.granted()` refuses it ahead of `auto_approve` for as long as
   `approvals_never_granted` lists it, which it does here. Editing `auto_approve`
   or `approvals_never_granted` is itself a CEO decision.
3. **Between start and the checkpoints, the orchestrator runs autonomously.**
   Gate failures, lint errors, and review findings are fixed and re-run by the
   delegated agents - never bounced to the CEO - until the retry budget is hit,
   at which point the task is parked `blocked` and surfaced. The orchestrator
   never asks "shall I continue?", "what next?", or "want me to start the next
   task?". The Stop hook answers those questions for it.

## State outside the model

- `.agentry/state/run.db` (SQLite, gitignored) - one row per task in flight:
  `pipeline` (`build` or `plan`), `stage`, `stage_status`, `awaiting_human`,
  `commit_approved`, `push_approved`, `retries`, `continuations`. This is the
  single source of truth for where a task is. Inspect with
  `python .claude/tools/pipeline/state.py --show`.
- `.agentry/pipeline.json` - the declarative stage machine, split into
  `pipelines.build` (plan -> plan-approval -> implement -> test -> review ->
  ready -> done; the types in `skip_for_types`, `bugfix` and `techdebt`, skip the first two) and
  `pipelines.plan` (formalize -> draft -> plan-review -> approval ->
  breakdown -> done): stages, owner agent, allowed tools, and the exit-gate
  command per stage. Finalized by onboarding.
- `.agentry/state/mode` - one word (`build`/`plan`/`talk`) picking WHICH flow a
  newly registered task follows. `advance.py` reads it via `mode.py` unless
  `--pipeline` is passed explicitly. See `skills/pipeline/SKILL.md`.
- `.agentry/state/swarm` - one word (`on`/`off`, default `off`), the third dial:
  whether an epic runs as a parallel agent swarm (see "Swarm mode" below). Read
  via `swarm.py`; `mode.py --show` prints all three dials.
- `.agentry/state/approvals` - one word (`manual`/`assisted`/`auto`) picking how
  many checkpoints pass without the CEO. Read via `approvals.py`. `manual`
  grants nothing; `assisted` grants the commit only and does not take the next
  task; `auto` grants take and commit, and the push of the work branch unless
  `approvals_never_granted` lists it (the shipped config lists `push` and
  `trunk_push`, so no level grants them). It grants nothing about merging into `main`: in `pr` mode the CEO
  merges in the web UI, and in `solo` mode the merge follows the commit approval
  and is gated by `pretool_gate.py`'s three conditions (branch name, run row,
  commit approved), so the approved commit authorizes it, not the level. At
  `assisted` and `auto` the level itself sets `commit_approved`
  (`approvals.py`, `advance.py`), which is the third of those conditions, so
  the level reaches the merge only through the commit approval. A
  supervisor-spawned unattended session is refused the merge regardless
  (`check_unattended`). Nor does it grant moving a task to `done` or answering
  planning questions.
- `PIPELINE_LANE` (environment variable, optional) - the name of an independent
  conveyor. It suffixes all three files above (`run.<lane>.db`, `mode.<lane>`,
  `approvals.<lane>`), so two parallel sessions do not share pipeline state.
  Read in `state.py` and nowhere else, and validated there against
  `^[A-Za-z0-9_-]{1,32}$` - an unusable name refuses the run rather than
  quietly opening an empty store. Unset is the default lane and behaves exactly
  as before lanes existed. A lane holds its OWN runs: commit and push in the
  same lane the task registered in, or the gate refuses (it fails closed on a
  missing run row) with the lane named in the message.
- A task's state is the **folder** it sits in - `.agentry/tasks/backlog/`,
  `active/`, or `done/` - not a `status:` frontmatter field. There is nothing to
  drift out of sync with the file's actual location.

## Hooks (the enforcement layer)

| Event | Script | What it enforces |
|-------|--------|------------------|
| `SessionStart` | `state.py --resume` | Injects in-flight runs so a new session resumes, not restarts. |
| `PreToolUse` (Bash\|Edit\|Write) | `pretool_gate.py` | Denies `git commit` until commit is approved, `git push` until push is approved, any push to the protected branch (in `solo` mode, except the one-shot trunk push the CEO approved with `approve.py --trunk-push`), and code edits while a task is in the read-only `review` stage. Also **generic gates** (no config): work-branch name must be `<type>/task-<id>` and be cut from `main`; no AI-authorship trailer in commit messages; no em/en dash in written content; no writes under the runtime `~/.claude/` (exception: `~/.claude/plans/` - the harness's own plan-mode scratch area). Plus **stack-configured gates** from `pipeline.json` `gates` (filled at onboarding): destructive-command deny, live-REPL write deny, and optional `/dev/null` redirect deny. With `handoff.hard_edit_gate` on, code edits are also frozen while handoff debt exists and no task is mid-stage. Bookkeeping under `.claude/`, `.agentry/` and `docs/` is otherwise allowed. |
| `Stop` | `stop_gate.py` | The non-stop engine. Blocks the stop and feeds back the next instruction while any task is advanceable or a ready backlog task remains. Reads the queue from `tasks/backlog/`, not a `status:` field, and only picks up a new task when the current approvals level grants the `take` checkpoint. **Blocks while any completed task lacks its handoff doc or its memory stamp** - the instruction it feeds back is "write the handoff first", then "distill and stamp it". Neither check reads whether a slot is free any more: they were once evaluated only when nothing was in flight, so a single task parked on the CEO - the normal state of a pipeline with a human in it - silenced both, and two done tasks went undocumented with nothing objecting (task-0072). Driving an in-flight run still comes first, and surfacing a BLOCKED run still comes first; the debt is the last thing before the session is allowed to end. Before idling it also **reconciles status drift**: a merged task file still in `tasks/active/` (its run reached `done`, or `main` carries its `[task-id]` tag) is moved to `tasks/done/`, and a run whose file already left `active/` is closed - so file state and `run.db` never rot. Allows the stop only when every task is parked at a checkpoint, blocked, or done. |

More enforcement points live inside `advance.py` (not hooks, but deterministic gates on the transition it authorizes):

- **Spec gate (registration):** a task may only ENTER the pipeline when its frontmatter names an `approved` spec via `spec: <id>` - or explicitly opts out with `spec: none` + a one-line reason. No silent improvisation: formalize -> plan -> spec -> task.
- **Handoff gate (registration):** a new task is refused while ANY completed task above `handoff.baseline` (pipeline.json) lacks a valid handoff doc in `.agentry/tasks/handoffs/`. The invariant is "every done task documented before any subsequent registration", not strict N -> N+1 adjacency (the pipeline is edit-serialized but pipelined, so task N+1 may register while task N is still parked at `ready`). Validation, scaffolding and the CEO waiver live in `tools/pipeline/handoff.py`.
- **Memory gate (registration):** and refused again while any completed task above `memory.baseline` lacks its stamp in `.agentry/state/memory/`. Same baseline semantics, same fail-open contract, checked immediately after the handoff gate because the rows are distilled FROM the doc. It did not exist until task-0066: the rule sentence named both halves, `advance.py` contained the word `memory` zero times, and two tasks closed undistilled. The stamp is refused by `tools/memory/update.py` unless the store actually gained a row (or `--none` is passed), so satisfying the gate cannot be faked by touching a file.
- **Backlog -> active on start:** `advance.py` itself moves the task file `backlog/ -> active/` when a task first registers - there is no separate "take" step for the model to do by hand.
- **Merge-gated done:** a task reaches `done` only when `git_state.py` confirms `main` carries it (branch merged, or a `[task-id]` commit on `main`) - not when the branch was merely pushed. Until then `advance.py` parks the task saying a merge is still needed, and re-running it later is what actually moves the file `active/ -> done/`.

Our gate scripts **fail open**: a bug inside a hook script allows the action
rather than bricking the agent. The platform layer in front of them does not: since
build 2.1.288 Claude Code BLOCKS a `PreToolUse` / `PermissionRequest` call when hook
matching fails or the tool input cannot be serialized to JSON, before any of our
scripts run. What that block looks like has not been measured here.

## The orchestrator operating loop

For each task, drive the deterministic FSM - never hand-wave a stage as done:

0. **Handoff first.** Run `python .claude/tools/pipeline/handoff.py --check`. It
   exits non-zero for two different reasons and they need different answers, so
   read the JSON rather than the exit code alone. `thread_problems` is an epic
   thread document that is malformed or over `handoff.thread_max_bytes`: fix
   that file yourself, there is no task to document and no agent to dispatch;
   registration is unaffected, since `advance.py` reads `uncovered_done_tasks()`
   and never this section. `handoff_debt` is the real chain debt. If it
   reports debt, dispatch the NEXT task's assignee to write the handoff doc for the
   completed task BEFORE registering anything: scaffold with `handoff.py --for
   task-XXXX`, then the agent fills every section of
   `.agentry/tasks/handoffs/task-XXXX.md`, and is instructed to write in its own
   words (from the done task file, its merge diff on main, and the gate log),
   reading `.agentry/project/project-context.md` and its OWN task's spec first,
   so that the writing is the context absorption. What `handoff.py` enforces is
   structure, not authorship: the frontmatter `task:` matches the filename, the
   six required headings are present, no `FILL-ME` marker survives, each section
   has at least 40 non-space characters and the body at least 500, `Context
   loaded` confirms project-context.md and the spec (or `none`), and the file is
   under the byte cap. Nothing compares the text with the task file, so a pasted
   task description that meets those checks passes. `advance.py` refuses registration and the Stop hook blocks
   the start message until the doc validates. `handoff.py --waive` is the
   CEO-approved escape hatch - orchestrator-only, run it only after the CEO
   explicitly approves in chat (same trust boundary as `approve.py`).
   Then run `python .claude/tools/memory/update.py --check` and clear what it
   reports the same way: registration is refused on an unstamped completed task
   as flatly as on an undocumented one, and the Stop hook will not let the
   session end while either debt stands. Distill first, stamp second - the
   stamp is refused unless the store really gained a row.
1. **Start.** Create the branch per `git-workflow.md` - **from up-to-date `main`
   only, never from another branch** (verify with
   `git merge-base --is-ancestor origin/main HEAD`, which must exit `0`). Then run
   `python .claude/tools/pipeline/advance.py --task task-XXXX --type <type>`.
   This records the run at the first stage: `plan` for every type except those in
   `skip_for_types`, `implement` for a `bugfix` or `techdebt`. A task started on a non-`main` base,
   or not registered via `advance.py`, is OUTSIDE the pipeline - the gates are not
   enforced for it - and must NEVER reach commit or push. If you find a task in
   that state, stop and recreate it correctly from `main`.
2. **Work the stage.** At stage `plan`, dispatch the task's assignee to write
   what it intends to change and why to `.agentry/plans/<date>-<slug>-<task>.md`
   (at least 400 characters; the file name must contain the task id). Edits
   outside `.agentry/` are denied until the CEO's plan approval is recorded.
   Then run `advance.py` (gate: `artifact_gate.py`), run it again to park at
   `plan-approval`, present the plan, and after the CEO approves run
   `approve.py --task task-XXXX --gate plan`, then `advance.py`. For every
   other stage, dispatch the stage's owner agent (from `pipeline.json`)
   as a background subagent to do the work. The `implement` owner is the generic
   `dev`: it resolves to the task's `assignee` - senior-frontend-dev for frontend
   work, senior-backend-dev for backend, etc. `advance.py` prints the resolved
   name; a task whose stack does not match its assignee is a task-creation defect.
   Every `implement` dispatch prompt MUST instruct the agent to read, before any
   code: (a) `.agentry/project/project-context.md`, (b) the task's spec (`spec:`
   frontmatter). Do NOT tell it to read the last 1-3 handoff docs - the
   `SubagentStart` hook (`handoff.py --inject`) already delivers the newest one
   in full plus an index line per older document, so an instruction to read them
   buys the same bytes a second time. Point it at the index instead: read an
   older handoff only when the task needs that one.
3. **Advance.** When the agent reports done, run `advance.py --task task-XXXX`.
   The script runs the stage's exit gate itself and only advances on a real pass.
   On failure it bumps `retries` and tells you to fix and re-run; after the
   budget it marks the task `blocked`.
4. **Repeat** through `plan -> plan-approval -> implement -> test -> review`
   (a `bugfix` or `techdebt` starts at `implement`).
5. **Checkpoint - commit.** Entering `ready` parks the task (`awaiting_human=commit`).
   Surface the full diff. When the CEO approves, run
   `approve.py --task task-XXXX --gate commit`, then `git commit`, then `advance.py`.
6. **Checkpoint - push (`pr` mode only).** The task parks (`awaiting_human=push`).
   When the CEO approves, run `approve.py --task task-XXXX --gate push`, then
   `git push`, then `advance.py`. This only pushes the branch; `advance.py` still
   needs the CEO's merge in the web UI. Re-run it after the merge - git confirms
   it and the file moves to `tasks/done/` itself. In `solo` mode there is no
   such step: after the commit approval you merge the task branch into the trunk
   locally, gated by `pretool_gate.py`'s three conditions (branch name, run row,
   commit approved), then re-run `advance.py`, and git confirms the merge the
   same way. Publishing the trunk is a separate one-shot approval, `approve.py
   --trunk-push`, per `git-workflow.md`.

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

This section is the per-task flow. A multi-subtask epic registered as one
umbrella task runs under [Swarm mode](#swarm-mode) instead.

Autonomy is pipelined: while one task waits at a checkpoint, the orchestrator
starts the next ready task instead of idling. But **at most one task occupies an
editing stage (`plan`/`implement`/`test`/`review`) at a time** - parked tasks are frozen
(no edits), so there are never concurrent edits to the working tree. This is the
default and the whole story while `worktrees.enabled` is false; with it on, the
rule becomes per-worktree, see [Parallel tasks in worktrees](#parallel-tasks-in-worktrees)
below. A task is
"ready to start" when its file sits in `.agentry/tasks/backlog/` and every
`depends_on` task is done. There is no `status:` field to set - moving a file
into or out of `backlog/` is the only way to queue or hold it. Below the `auto`
approvals level, taking a new task is itself a CEO checkpoint: the orchestrator
asks which task to start, and `advance.py` moves the file `backlog/ -> active/`
once it registers.

### A started task gets finished

The default is not negotiable by convenience: **once a task is started, it is
carried to done.** Do not set work aside because something looks more
interesting, because a better ordering occurred to you, or because a gate
failed - a gate failure is fixed and re-run, and after the retry budget the task
parks `blocked` and is surfaced. Half-finished tasks are how a working tree ends
up carrying changes nobody owns.

### Force majeure: the exact sequence

Only the CEO declares one, by asking for something immediately. Then, in this
order and without improvising:

1. **Commit what exists on the current task's branch.** Whatever state it is in,
   it gets committed so nothing is lost and the working tree is clean. This is
   the one place a commit is made mid-stage; say in the message that the task was
   preempted and what is incomplete.

   Two things this step needs stated, because the harness denies the naive
   reading of it and an instruction the gate refuses is worse than no
   instruction. `pretool_gate.py` blocks `git commit` until the commit
   checkpoint is recorded, and `git-workflow.md` puts a passing test run before
   every commit - a preempted mid-stage tree satisfies neither by definition:
   - **The CEO's force-majeure declaration IS the commit approval** for the
     preempted task. It is not a second question to ask him, and you do not park
     waiting for one. Record it the normal way and say what it was for: `python
     .claude/tools/pipeline/approve.py --task task-XXXX --gate commit`. That is
     the only step between the declaration and the commit.
   - **The pre-commit test gate is waived for a preempt commit, and for nothing
     else.** A mid-stage tree is expected to be red; the commit exists to lose
     no work, not to claim a green stack. Say so in the commit message ("gates
     not run - preempted mid-stage"), and run the full gate normally when the
     task resumes at step 4. No other commit in this pipeline may skip it.
2. **Pause the preempted task:** `python .claude/tools/pipeline/approve.py
   --task task-XXXX --pause "<why>"`. The run keeps its stage, `retries`,
   `continuations` and approval flags and gets `stage_status=paused` plus a
   record of the reason and of whether the branch had commits (`committed`) or
   nothing at all (`clean`). The command is refused while the tree holds
   uncommitted work, so step 1 cannot be skipped by accident. A paused run is
   not driven by the Stop hook, needs no busy marker to keep it quiet, does not
   count as occupying an editing stage, and `advance.py` refuses to move it.
   **Never use `advance.py --busy` to park a task:** the marker means a gate or
   a dispatched agent is running, and neither is true.
3. **Return to `main`.** Never branch the urgent task off the preempted one -
   `branch-base-must-be-main` still holds, and the gate enforces it.
4. **Branch and register the urgent task**, then work it normally.
5. **Resume the preempted task afterwards:** `approve.py --task task-XXXX
   --resume` (refused while another task holds an editing stage), switch back to
   its branch, then re-run `advance.py` on it. It continues at the stage it was
   paused at. Pausing never touches an approval flag. Resuming a run that sits
   at an editing stage clears its commit and push approvals, because a flag
   there can only be the preempt-commit approval from step 1, and leaving it
   would let the finished work skip the CEO's read of the diff at `ready`; a run
   paused at a checkpoint stage keeps its approvals.

**With worktrees on, force majeure shrinks.** A task that lives in its own
worktree is not preempted at all: the urgent task registers while a free slot
exists and gets its own tree, so steps 1-3 and 5 are not needed and nothing is
paused. The sequence above is still the procedure when (a) no slot is free
(`worktrees.max_parallel` tasks already hold one), (b) the preempted task is a
LEGACY run or an umbrella, which hold `src/` and are exclusive, or (c) the flag
is off. In case (a) pause one tree task the usual way (step 2; the step 1 commit
is made inside its tree) - a paused run frees its slot, a parked or blocked one
does not. Resuming a tree task is refused while every slot is in use
(`approve.py --resume`); resuming a LEGACY run ignores tree rivals.

If the preempted task has NO commits and no edits - nothing was written yet -
step 1 is skipped, and that is the only case where it may be. Say so explicitly
in the record - the pause records it as `clean` - because "nothing to commit"
and "forgot to commit" look identical in git a week later.

Two tasks worked on one branch (what a preemption can leave behind) are judged
by the commit message, not the branch name: a commit tagged `[task-NNNN]` is
approved by that task's run. A tag that names a task other than the branch's is
accepted only when that task is registered, not done, and declared `branch:
<the checked-out branch>` when it was registered (frozen in the run row; later
edits to the task file change nothing); otherwise the commit is
denied naming both ids. An untagged commit is judged by the branch, as before.
The drift reconciler (`stop_gate` step 4) is deliberately not reachable while a
run needs driving; a task file moved by hand does not stop the hook, pausing
does.

### Abandon: retiring a task without a merge

When the CEO drops a task for good (the work is cancelled, not preempted), the
only way to close it cleanly is `python .claude/tools/pipeline/approve.py --task
task-XXXX --abandon --reason "<why>"`. Like every approve.py action it is the
orchestrator's alone and runs only after the CEO has decided, in an
`AskUserQuestion` card; the gate denies it to every subagent profile. It:

- sets the run to stage `done` with `stage_status=abandoned` and stores the reason
  (`abandon_reason`; control characters become spaces, 500 characters at most),
  journals one `abandoned` row, and clears `commit_approved`, `push_approved` and
  `plan_approved` so the commit, push and solo-merge gates hold no standing
  permission for it;
- moves the task file to `tasks/done/` although nothing merged, stamped
  `abandoned: "<reason>"` and without a `completed:` date; dependents
  (`depends_on`) therefore see the task as met, become ready at once, and at the
  `auto` approvals level start unattended - the command prints the backlog tasks
  that depend on it so the CEO knows before they run;
- never touches git: the branch, its commits and the working tree stay as they
  were, so clean the tree before the next task starts. `advance.py` on an
  abandoned run reports it done without reading git or parking for a merge;
- is refused, before any write, for an unknown task, a task already merged or
  abandoned, a missing or blank `--reason`, and a `done/` file that already holds
  different text. A run parked at `done` awaiting its merge (`awaiting_human=merge`,
  file still in `active/`) can be abandoned. It stands alone: combined with
  `--gate`, `--reject`, `--pause` or `--resume` it exits 2, and `--gate` or
  `--reject` on an abandoned run exits 2 too.

The handoff and memory debt readers (`handoff.uncovered_done_tasks`,
`update.unstamped_done_tasks`) skip an abandoned task, so it owes no doc and no
stamp. An abandoned umbrella does not cover its subtasks' debt either
(`state.covering_umbrella`) and sweeps none of them (`state.move_swarm_subtasks`).
The drift reconciler (`stop_gate` step 4) is not changed: it works from the
folder and the run row as before, and an abandoned run is a `done` run to it.

### The busy marker is a choice between two failures, not a discipline problem

Until `task-0064` lands, both options are still broken and you should know
which failure you are buying. `task-0067` and `task-0057` have landed and
narrowed them - see below:

- **Set `advance.py --busy` and the Stop hook goes quiet.** If the marker
  outlives the reason it was written for - the dispatch returned, the preemption
  resolved, the task closed - the conveyor halts in total silence. Nothing
  reports it, because the marker's whole job is to suppress the report. This
  happened twice in one session, and the CEO noticed both times before the
  harness did.
- **Leave it unset and the Stop hook nags every turn.** Each nag writes
  `continuations` (`stop_gate.decide()`, the editing-stage branch - cited by
  symbol because the line number has already moved once), and the supervisor
  reads a climb as evidence of a session turning over on a frozen stage.
  Before `task-0067` the stall branch parked on ANY climb: measured,
  `stage_climbs` reached 1 inside ninety seconds, eighteen minutes short of the
  park. That park is gone - a frozen stage with a recent nag is now HEALTHY with
  no action, and the adjacency reading that parked after two nags was deleted.
  What remains is the pipeline's own budget: `continuations` reaching
  `continuation_ceiling` still parks the task `blocked`, about an hour at a nag
  every two minutes. So the cost of leaving the marker unset is an hour of
  nagging and then a park, not three minutes of it.

`task-0018` changed the price of both options. The choice is the same one; what
each side costs is not.

**The second bullet's park is per STAGE.** `continuations` is zeroed on every
stage transition by `advance.py` and by `approve.py --reject --reason "<why>"`, so the hour is an
hour per stage, and a task that keeps advancing never accumulates towards the
park at all.

**`task-0019` then made that hour theoretical, and this is the number to plan
against.** The nag text is byte-stable for a given task and stage, so the
reason-repeat backstop sees the same sentence every time and parks the task on
the THIRD armed repeat - roughly six minutes at a nag every two, not an hour.
Measured on a run at `test` with the gate failed and `continuation_ceiling` at
30: stops one and two printed the demand, stop three parked, and `continuations`
never exceeded 4. The practical consequences, in order of how likely they are to
bite:

- **`continuation_ceiling` no longer bites on a stable reason at all.** An
  operator who tunes that key to buy more patience will see no effect, because
  the backstop fires first every time. It still governs the case the backstop
  cannot see: a reason that CHANGES between stops, where the repeat count resets
  and only the per-stage budget accumulates.
- **Recovery got more expensive, so the park is not free noise.** The only exit
  from `blocked` is `approve.py --reject --reason "<why>"`, which returns the task to `implement`
  (to `plan` when the block happened before the plan approval) and clears
  `commit_approved`. A park at `review` therefore costs two stages of
  rework; a park at `ready` costs an approval the CEO had already given. That
  used to be the price of 30 unanswered nags and is now the price of 3.
- **The bound is on the SENTENCE, not the task.** Answer the instruction, or do
  anything that changes what the hook says, and the budget resets whole. The
  three repeats mean three stops on which the orchestrator was told exactly what
  to do and did not do it.

**The first bullet's blast radius grew, and this is the part to know before
setting a marker by hand.** A marker used to gate ONE branch, the in-flight
editing stage. It now short-circuits the Stop hook ahead of everything that
follows: both documentation-debt demands, the backlog queue and the drift
reconciler all go quiet while any marker is fresh. A stale marker therefore
silences four things rather than one. The bound is `state.BUSY_TIMEOUT`, 900
seconds, after which every one of them speaks again - which is why this is a
deferral and not the permanent silence of task-0072.

**And the orchestrator is no longer the only writer.** `handoff.py --for`
writes a marker for the task it scaffolds, so the debt demand stays quiet while
the agent fills the doc instead of ordering it to do what it is doing.
`handoff.py --check` drops that marker as soon as the doc validates, so the
normal path pays seconds rather than the full 900; the full 900 is paid only
when the scaffold is never filled.

**`task-0057` gave the marker an owner, and the file is no longer yours to
write.** `tools/pipeline/busy.py` is the only code that knows the marker's file
name and format; `advance.py` (`--busy`/`--idle`, and the bracket around every
gate), `handoff.py` and the Stop hook all go through it. Three consequences:

- **The marker names the SESSION, not the writer.** It records `owner_pid`, the
  Claude Code process, read from `CLAUDE_PID` (Claude Code exports it into every
  command it runs - measured, and it is the `claude.exe` three shells above the
  script). `advance.py` records nothing of its own, because it exits at once. A
  marker whose session has exited reads OWNER_GONE and is treated as released
  however young it is, probed through `supervisor.pid_alive()`. Outside Claude
  Code there is no `CLAUDE_PID`, so no owner is recorded and only the timeout
  ends the marker - `--busy` says so in its output. The probe's limit:
  `supervisor.pid_alive()` reads every "cannot tell" (an OpenProcess failure
  other than 87, a probe exception, and on POSIX a reused pid) as ALIVE, so such
  a marker stays FRESH until its timeout, and only the timeout ends it. On
  Windows a reused pid is caught: the probe is given the marker's start time and
  a process created after it cannot be the owner.
- **The timeout is a backstop and it is now reported.** When a marker has expired,
  lost its owner or is unreadable, the Stop hook's nag for that task ends with a
  sentence saying which, and the `--idle` command that clears it. A stale marker
  is therefore no longer indistinguishable from no marker. That sentence is
  byte-stable on purpose: the reason-repeat backstop compares nag text.
- **One marker covers one task.** `--busy task-0009,task-0010` is refused with
  exit 1, and a hand-written file whose `task` disagrees with its filename is
  ignored. A gate that raises still releases its marker (`busy.hold()`).

What it does NOT change: a marker that outlives its reason inside a live session
still silences the hook until its timeout, so `--idle` in the same command
sequence as whatever ended the reason is still the rule below.

The root is that two watchdogs share one counter with opposite meanings.
`continuations` is "how often I nagged" to the Stop hook and "the session is
looping" to the supervisor, and nagging about an idle task is indistinguishable
from the task looping. `task-0067` did not fix that root - it cannot be fixed
from that column - it removed the readings that claimed to see a loop in it and
left the budget reading, which claims only that the budget is spent. Separating a
loop from a slow worker needs progress evidence from outside `continuations`.
`task-0073` added it (the gate log, the working tree, the task file), but only to
tell a hung dispatch from a slow one: the supervisor reports that as HUNG and
never relaunches, parks or signals it (`supervisor.py`, HUNG and SLOW VERSUS
HUNG, MEASURED). The dispatch record is the busy marker, and it counts past its
900s timeout while its owner is on record and alive; an expired marker that
names no owner and shows no progress for `hung_seconds` is STALLED and is
relaunched, since nobody is known to be on the run.

**What to do meanwhile:** prefer the nagging. It is noise; the other is a halt,
and a halt that reports nothing is the only failure mode this pipeline cannot
recover from on its own. Clear a marker with `--idle` in the same command
sequence as whatever ended its reason, never later. Never hand-write the JSON -
the tool exists, and a file written by hand has no owner and is ignored when it
names the wrong task. And if a running daemon
carries code whose defect you are fixing, stop it for the duration
(`supervisor.py --stop`) and say so: a watchdog on known-buggy code can park the
task that fixes it.

**Known gap, do not paper over it:** `run.db` has no paused state. `blocked` is
reachable only by exhausting the retry budget, editing `run.db` by hand is
forbidden, `approve.py --reject --reason "<why>"` moves a task the wrong way (to `implement`), and
`stop_gate.decide()`'s in-flight branch outranks its drift reconciler, so moving
the file back to `backlog/` does not quiet the hook either. task-0059 closed
this gap: pause a preempted run with `approve.py --task task-XXXX --pause
"<reason>"` and bring it back with `--resume`; a paused run is never driven or
charged by the Stop hook. Do not silence a preempted run with a busy marker any
more - a signal you have to lie to is a missing state, and that state now
exists.

## Parallel tasks in worktrees

Shipped dark (task-0129 to task-0132, epic-0004; design in
`.agentry/plans/2026-10-13-task-0126-worktrees.md`). With `worktrees.enabled`
false or the block absent, everything above this section holds unchanged and
nothing below fires. With it on, the edit-serialization rule of the previous
section is replaced by isolation: each task edits its own checkout, so two
tasks can sit in editing stages at once.

**The model.** `src/` becomes the TRUNK checkout: it stays on `main`, nobody
edits it, and the solo merge runs there. Every task works in
`<repo path>-wt/<task>` under the workspace root (for example
`E:/Personal/Agenty/src-wt/task-0130`), cut from the trunk on branch
`<type>/<task>`. Pipeline state (`run.db`, task files, plans, markers) already
lives in the workspace-root `.agentry/`, outside every repo, so a worktree
carries code and nothing else. Hooks always run the LIVE harness scripts under
the workspace-root `.claude/`; the copies of `.claude/tools` and
`.agentry/pipeline.json` inside a tree are inert for hooks and are exactly what
the exit gates test. Each tree gets its own `tmp/`.

**Config** (`pipeline.json`, top level; the CEO adds it, agents never write that file):

```json
"worktrees": {"enabled": false, "max_parallel": 2}
```

`enabled` counts only as a literal `true`. `max_parallel` is an integer of at
least 1; anything else, or an absent block, reads as 1. The flag governs only
creation (registration) and the trunk-checkout freeze. The PATH recorded in the
run row drives every other behaviour, so setting `enabled` back to false is a
safe rollback: trees already recorded keep working, their review freeze, their
gate cwd and their freshness check stay on.

**Registration creates the branch and the tree.** For a build-flow task that is
not a swarm umbrella, `advance.py --task T --type <type>` runs `git worktree
add -b <type>/<task> <path> <trunk>`, verifies branch and base, records the
relative path in the run row's `worktree` column (written only at registration,
never from the task file) and ends its `started` message with the dispatch line.
The orchestrator does NOT run `git checkout -b` for such a task. The task's
`repo:` must resolve to exactly one repo. Re-registering reuses a tree only when
tree and branch are exactly the expected pair; any other state is refused with
the git command for the CEO and nothing is created, the task file stays in
`backlog/`.

**Gates run in the tree.** `gate.run_gate` and `stack_gate.py` run a stage
command with the tree as cwd when the stage's repo is the one the tree belongs
to, so code-under-test commands (compileall, unittest, ruff) judge the task's own
code. A LEGACY run (no recorded tree) runs in the repo as before. A recorded tree
that is missing or invalid blocks the gate, it never falls back to `src/`: the
output says `task worktree MISSING` or `INVALID` and names `worktree.py --repair
--task T`. Commands that must come from the live harness use `{root}`, replaced
by the workspace root as a forward-slash path (after `{task}`; a root containing
whitespace or a quote refuses the gate). Only the plan-stage `artifact_gate.py`
and the `ui_evidence.py` call inside the test stage use it; this is the CEO's
config step (see the rollout below), because a tree holds its own copy of
`.agentry/` that has no plans.

**Slots replace "one task in an editing stage".** With the flag on and
`max_parallel` N above 1, the Stop hook starts the next ready task when no
LEGACY run, umbrella or plan-flow run holds `src/` and fewer than N runs hold a
tree. A run holds its slot while it is in an editing stage, parked at a
checkpoint, `blocked` or busy; a `paused` run and a finished one hold none. This
also makes the old promise true that the queue pipelines past a task parked for
the CEO. A queued umbrella waits until every slot is free, and while it runs
nothing else starts; its start text is the unchanged one ("create its branch per
git-workflow.md"). Doc-scaffold busy markers still silence the hook; markers
of tree runs only count as slots. With `max_parallel` 1 or the flag off the old
serial code path runs line for line. `worktree.Doubt` from the slot count (run
store unreadable) means no free slot: no start, and `approve.py` refuses the
resume.

**What stays exclusive.** A LEGACY run (registered before the flag, no tree) and
a swarm umbrella both live in `src/` and exclude every other run in both
directions. Umbrellas get no worktree; a worktree per umbrella needs zone
re-rooting and is a follow-up (D3).

**Hook behaviour with trees.**

- Review freeze, per tree: a `review` run with a recorded tree freezes only the
  paths inside that tree (`worktree.owner_of`). A review run without one keeps
  the global freeze of today. The recorded path drives it, not the flag. Doubt
  keeps the freeze: `worktree.Doubt` (unreadable run store, unplaceable path, an
  invalid or unresolvable recorded tree) freezes; only a clean "in no tree" or
  another task's tree skips it.
- Trunk-checkout freeze (flag on only): Edit/Write and shell write operands that
  land in a repo whose `.git` is a directory with HEAD on the trunk branch are
  denied for everyone, including `src/.claude/`; a directory named `.agentry`
  stays exempt. Covered writers: redirect, tee, sed -i, cp, mv, rm, mkdir, ln,
  chmod, truncate, touch, install, dd of=, rmdir, unlink, find -delete, patch,
  and git checkout --, restore, apply, am, clean, rm, mv, stash pop. An operand
  with `$` or a backtick is refused. A repo whose HEAD is on a feature branch (a
  LEGACY run or an umbrella holds `src/`) is not trunk and stays editable. A
  lookup that fails is read as trunk for a subagent and as not trunk for the
  main thread.
- HEAD-moving git (flag on, subagents): checkout, switch, reset, rebase,
  symbolic-ref, update-ref and `branch -f/-m/-M` are denied in a primary
  checkout that no feature branch holds. A linked task tree is free.
- Placement per call (flag on, or a not-done run records a tree): each `git
  commit`, merge, pull and push is judged in the directory its own `-C` values
  and the `cd` walk before it name, nested bodies from every directory the
  outer command visits. A `cd` target or `-C` value that is not a literal path
  (`$`, `~`, none, `popd`) is refused. `--git-dir`, `--work-tree`, `-c
  core.worktree=` and `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` on such a
  call are refused.
- Dev agents are denied `git worktree add|remove|move|prune|repair|lock|unlock`
  and `worktree.py --remove` / `--repair` while the flag is on or a not-done run
  records a tree. `worktree.py --list` and `--sweep` stay open. The orchestrator
  creates and removes trees through `advance.py` and the CLI, not by hand.
- Not covered: one agent editing another task's tree. Agent identity per task
  is not detected (D7, follow-up); the dispatch line below is the guard.

**The dispatch line (MANDATORY in every implement, test and review dispatch).**
For a tree task, `advance.py` (the `started` message), the Stop hook nags, the
supervisor relaunch prompt and `state._resume_text` print one byte-stable line:

```
Task worktree: <abs path> (branch <type>/<task>). Begin every Bash call with cd "<abs path>", give Edit and Write absolute paths under it, and do not touch <repo path>/.
```

Copy it into the dispatch prompt unchanged. It is strict because of what was
measured (M3, 2026-10-13): the Bash cwd does NOT persist between calls, and the
hook payload's cwd follows that reset, not a `cd` made in an earlier call. A
`cd` inside the SAME command is read from the command text, so every Bash call
must carry its own `cd "<tree>" &&` (or `git -C <tree>`), and a relative Edit or
Write path resolves against the primary directory, so those paths must be
absolute under the tree. A bare `git commit` in a later call is judged from the
workspace root and is refused ("Not on a feature branch"); the gate does not
guess a tree. A `cd` target or `-C` value must be a literal path. For a MISSING or INVALID tree the line says to run
`worktree.py --repair --task T` and not to edit. A LEGACY task gets no line.

**Freshness.** The tree is cut from the trunk at registration; a sibling may
merge first. At the transition into the stage that asks for the commit approval
(`review` to `ready`) and again at merge time (a run waiting at `done` with its
branch unmerged), `advance.py` counts trunk commits the tree lacks
(`git rev-list --count HEAD..<trunk>`). More than 0 sends the run back to `test`
(action `stale`) with the exact text ``main moved <n> commits: `git merge main`
in the worktree``, commit approval kept: the CEO approved the work, not the base
(D4). The agent syncs inside the tree (commit or stash first), the test and
review gates re-run, and the run returns to `ready`. Each send-back is counted
(`stale_sendbacks`); when the count reaches `retry_budget` the run is parked
`blocked`. A count that cannot be read counts as fresh here; merge condition 4
repeats the check and fails closed (below).

**Merge and cleanup.** The solo merge is run by the orchestrator from `src/`,
after the commit approval, with the three existing conditions plus a fourth for
a task with a recorded tree: the trunk has no commit the source branch lacks
(one `git rev-list --count <source>..<trunk>` under the push budget; more than 0
denies with the stale text, an unreadable count denies too). A failed or refused
merge never touches the tree. Once git confirms the merge, `advance.py` moves the
task file, closes the run and calls `worktree.remove`: plain `git worktree
remove` without `--force`, up to three tries one second apart, after detaching
any junction or symlink inside the tree (git follows a junction in an ignored
path and deletes its target's files). It never deletes the branch and never
clears the run's `worktree` column. The Stop hook removes nothing: its drift
reconciler and idle check only report a merged task's tree or an orphan; removal
belongs to `advance.py` and `worktree.py --remove`. The task is done either way;
a tree left in place is reported with the command to retry it.

Windows removal, measured (M2, git 2.51, this host): a clean tree, or one with
ignored `__pycache__/` and `tmp/`, is removed (rc 0). Modified, staged or
untracked files give rc 128 and delete nothing; a locked tree gives rc 128 and
deletes nothing; neither is retried, both are reported as left. A native child
process (python) holding the tree as cwd, or an open file inside it, gives rc
255 with `failed to delete`, and the removal is NOT atomic: git has already
dropped `.git/worktrees/<name>`, so the directory is half deleted and no longer
a worktree, and a retry gives rc 128 `is not a working tree` forever. Inside the
same `remove()` call that saw the 255, once the holder is gone, the clean
leftover is cleared with `shutil.rmtree`; a leftover found by a LATER call is
reported `left` and never deleted blind, since it could hold unregistered edits.
A git-bash shell whose cwd is inside the tree does not block removal, and
`remove()` first moves its own process out of the tree. Fix for a left tree: end
whatever holds it, then `python .claude/tools/pipeline/worktree.py --remove --task T`.

**`worktree.py` CLI** (orchestrator use, JSON on stdout):

| Command | Does |
|---|---|
| `--list` | `{"enabled", "max_parallel", "trees": [{task, path, branch, status, stage}]}` |
| `--sweep` | `{"orphans": [...]}`: `task-*` directories under `<repo>-wt/` that no in-flight run records (no run, run done, run abandoned, or recorded by its run as a different tree); reports only, removes nothing, exit 0 |
| `--remove --task T` | `worktree.remove(T)`; exit 1 only when the tree was left |
| `--repair --task T` | re-links an existing directory (`git worktree repair`), or when the directory is gone and the branch exists re-adds it; exit 1 on failure |

Status values: `OK` (recorded, shape valid, directory and its `.git` link
exist), `LEGACY` (no run, or nothing recorded), `MISSING` (recorded, directory
or link gone), `INVALID` (recorded but the path fails the shape check, or
`run.db` is unreadable: fail closed). `--abandon` leaves tree and branch in
place; `--sweep` will list them.

Codegraph indexes the trunk (`src/`) only, so a symbol a task has just added in
its tree is not in the graph until it merges; read the tree with Grep and Read.

**Rollout (task-0132; the flag stays false until the CEO flips it).**

1. Merge dark. Deploy per the order in "Deploying a harness change" (the new
   modules `worktree.py`, `state.py`, `git_state.py` first; hooks last).
2. Verify live in a scratch lane with real git and real Windows removal; read
   the output of the full suite, `ruff`, `compileall`, an import smoke of every
   hook script and the dash scan.
3. CEO config step: add the `worktrees` block with `enabled` false to both
   `pipeline.json` copies, and change the plan-stage command to
   `python {root}/.claude/tools/pipeline/artifact_gate.py --task {task} --kind plan`
   and the `ui_evidence.py` call in the test stage to
   `python {root}/.claude/tools/pipeline/ui_evidence.py --task {task}`. Verify
   `advance.py` still passes both gates on a legacy run.
4. Flip `enabled` to true only with `src/` on `main`, no LEGACY run in flight,
   and task-0134 done. Do not enable worktrees before then.
5. Watch the first two tasks end to end (registration, parallel gates, stale
   send-back, merge, removal).
6. Rollback: `enabled` back to false. Recorded trees keep working until their
   tasks finish; new tasks register the old way.

## Steering a running agent (do not wait for the report)

A dispatched agent can be messaged while it works, and it keeps its full
context. That makes a mid-flight correction far cheaper than the alternative:
waiting for the report, rejecting the stage, and re-dispatching, which throws
away everything the agent had already read and pays for it again.

Use it when:

- **Your own dispatch was wrong.** The most common case. A reviewer was told to
  read `git diff main...HEAD` on a branch carrying zero commits, which returns
  nothing; correcting it mid-flight stopped it reporting "no changes to review".
- **You measured something that contradicts the agent's premise.** An agent
  justified a design with "os.kill(pid, 0) kills the process on Windows";
  measurement showed signal 0 is special-cased and does not. The correction
  reached it before it had built more prose on the false claim - and its own
  re-measurement then found a real bug.
- **You found a lead inside its scope.** A live run surfaced a defect in the
  stall detector; handing it to the QA agent already auditing that file cost one
  message instead of a second dispatch.
- **A decision lands mid-flight.** The CEO decides something the agent was told
  to leave alone. Say plainly that it overrides the earlier instruction.

Do NOT use it to:

- Add scope the dispatch did not have. New scope is a new task, not a message.
- Micromanage an agent that is working correctly. Every message costs its
  context too.
- Ask for progress. There is no progress to report between tool rounds, and a
  task notification arrives when it stops.

Write the message as you would the dispatch: the correction first, the evidence
under it, and an explicit statement of which earlier instruction it replaces. An
agent given a contradiction without being told which side wins will pick one,
and it may not be yours.

## Swarm mode

For an epic with two or more subtasks. The protocol is the skill
`.claude/skills/swarm/SKILL.md`, owned by this project and edited here. Everything project-specific (commands, paths, roles) is the
key table in `.claude/pipeline-adapter.md`, and the prompt and board templates
are `.claude/skills/swarm/references/templates.md`. Fill them from the adapter:
where a template hard-codes another project's path or command, the adapter's
value wins.

The dial is `python .claude/tools/pipeline/swarm.py on|off` (default `off`,
persisted per lane next to the mode and the approvals level, shown by
`mode.py --show`, printed by SessionStart whenever it is on). Swarm applies in
`build` mode with the dial on and an approved epic. With it off, and for any
single task, the per-task flow above applies.

This project's specifics:

- **One umbrella task.** The scripts do not change. The epic registers as ONE
  umbrella task `task-<epic>` on ONE branch
  (`advance.py --task task-<epic> --type feature`), and the parallelism lives
  inside that task's `implement` stage. Registered as `feature`, the umbrella
  starts at `plan`: write the plan, get the CEO's plan approval
  (`approve.py --gate plan`), and only then launch the waves, because every
  write outside `.agentry/` is denied to the branch until it is recorded.
  "Edit-serialized" holds between tasks,
  not between the agents inside the umbrella task; the supervisor is the main
  session. The subtask files stay in `.agentry/tasks/backlog/` as the work list
  and move to `done/` with the umbrella task after the merge.
- **How the scripts recognise an umbrella.** Its task file carries frontmatter
  `umbrella: true`, `epic: epic-XXXX` and `subtasks: [task-A, task-B]`. These
  are read once at registration and kept in the run row; the sweep and the debt
  cover read the row, so list every subtask before starting the umbrella. Swarm
  is single-lane: the umbrella cover and the sweep read the current lane's run
  db. With the dial on, the Stop hook never offers a backlog task whose `epic:` matches a
  registered umbrella. When the next ready task belongs to an epic with two or
  more non-umbrella backlog subtasks and no umbrella, it demands the swarm start
  (create the umbrella file if missing) instead of a single-task start, at the
  `auto` level only; a lone subtask is started as an ordinary task. A queued
  umbrella is started in its subtasks' place, or waited for while its own deps
  are open. When the umbrella reaches `done`, `state.move_swarm_subtasks()`
  moves every listed subtask still in `backlog/` or `active/` whose `epic:`
  matches, from `advance.py` and from the Stop hook's drift reconciler alike;
  an unlisted subtask stays. The sweep happens only when the umbrella's run row
  has `commit_approved` set, and a move never overwrites an existing
  destination file (task-0121).
- **Umbrella debt.** Handoff and memory-stamp debt is owed by the umbrella task
  alone. A done subtask whose covering umbrella (an umbrella run row at stage
  `done` with the commit approved, same non-empty `epic:`, the task in its
  recorded `subtasks` list) is itself debt-checked is skipped by `handoff.py` and
  `memory/update.py`, so one handoff doc and one stamp for the umbrella cover all
  of its subtasks and no per-subtask waiver is needed. The umbrella's own debt is
  still reported. `git_state.task_in_main` now matches the `[task-N]` tag only at
  the start of a commit message line: write `[task-N] feat: work`, not
  `feat: work [task-N]`.
- **State folder.** `.agentry/tasks/swarm/task-<epic>/` holds the board, the
  agent id map, `resume.md`, the QA notes and reports. It is not
  `.agentry/state/`, because the dev profile is denied every write there.
  SessionStart prints the path of `resume.md` for each in-flight umbrella task
  while the dial is on.
- **Zones (task-0060).** The board carries a `## Zones` table (`| zone | paths |`;
  a zone id like `0060-S2`, paths comma-separated, each an exact repo-relative
  path or a trailing `dir/**`; a mid-path glob, `*.md`, trailing `/`, `..`, an
  absolute path or a bare `**` is rejected). Before the first wave the
  orchestrator freezes it with `python .claude/tools/pipeline/zones.py --prove
  --task task-<epic>`: exit 0 frozen, 1 refused with the overlaps named, 2
  usage; it also refuses when the task is not at `implement` or `test`. Shared
  files (`swarm.shared_paths` in `pipeline.json`, else the in-code default) must
  be named exactly in one zone or in none; a zone glob that covers one without
  naming it is refused. Every dispatch prompt then carries a `Swarm zone: <id>`
  line, and `agent_gate.py` reads it from the agent's own transcript file
  (`<session>/subagents/agent-<agent_id>.jsonl`, line 1; measured on Claude Code
  2.1.295 in `docs/technical/agent-identity-2.1.295.md`). While a frozen map is in
  force at `implement` or `test`, a dev-profile agent writes only inside its own
  zone, a zone's own directory (`chmod -R app/a`), `<root>/tmp/**` and the swarm
  state folder `.agentry/tasks/swarm/<map's task>/**`; no zone
  line means no write at all, and an unreadable or malformed map denies. Zoned
  and unzoned agents alike are denied the git rewrites `checkout`, `switch`,
  `stash`, `reset`, `restore`, `clean`, `apply`, `am`, `merge`, `rebase`,
  `cherry-pick`, `revert`, `pull`, `rm` and `mv` (`git stash list|show` stays
  allowed). `qa-engineer` at the `test` stage needs its own `Swarm zone:` line
  to write at all. Every zoned agent can append to the board and write QA reports
  in the swarm state folder of the map's task, resolved under the workspace root
  like `.agentry`; task files, `done/`, handoffs and the swarm folder of any other
  task stay denied. The readonly and docs profiles are not
  zone-gated. Not covered: an
  interpreter one-liner, a script the agent wrote and `npm install` writes, and
  these writers, which QA verified are allowed across zones: `touch`, `dd of=`,
  `install`, `rsync`, `curl -o`, `wget -O`, `patch`, `perl -pi`, `awk -i inplace`,
  `ed`, `sed` with a `w` file, `sort -o`, `tar -x`, `unzip -d`, `find -delete` and
  `find -exec`, `git clone`, `git read-tree -u`, `git checkout-index -f -a`,
  `git sparse-checkout`, `git worktree add` (denied to every agent once
  `worktrees.enabled` is true, see "Parallel tasks in worktrees") and `git diff
  --output`. Zones are a
  tripwire against mistakes, not a boundary against a hostile dev agent.
  Agent identity (the `Swarm zone:` line read from the agent's own transcript)
  was measured only for a headless foreground dispatch. Before wave 1, dispatch
  ONE zoned agent in the background and confirm its zone write succeeds; only
  then launch the wave. To unfreeze a frozen map (a finished or abandoned
  wave, a corrected board), run `python .claude/tools/pipeline/zones.py
  --release --task T`. It is orchestrator-only: a dispatched agent cannot
  release the map that binds it.
- **One agent per zone, one writer for the shared set (task-0060).** The
  serialization rule above ("one task in an editing stage", relaxed per task
  by worktrees when enabled; an umbrella itself never gets a tree) exists because one
  working tree gives no way to know two agents will not collide. A proven
  disjoint zone map replaces that guess with a check, so the rule relaxes
  exactly as far as the proof reaches: one agent per zone, and exactly one writer
  for each shared-set file (the zone that names it exactly, or none). Two agents
  given the same `Swarm zone:` line are not detected; the proof covers the map,
  not the assignment, so the orchestrator gives each id to one agent.
- **A failed zone leaves the others alone (task-0060).** When one zone's agent
  fails or stops mid-wave, the other zones are untouched: the tree is shared,
  there is no rollback, and sibling work stays. The orchestrator reports which
  zone failed (the deny text and the board name it) and relaunches that agent
  with the continue-from-partial template. A zoned agent cannot revert other
  zones' files, because git rewrites are denied.
- **One suite run, in the exit gate.** The full suite runs once, in the
  test-stage exit gate that the orchestrator drives through `advance.py`.
  `gates.dev_forbidden_commands` denies it to every dev-profile agent, the QA
  agents included, so wherever the skill text has a QA agent run the suite,
  here that agent runs narrowed runs only (single modules or `-k`) and the gate
  runs the suite. The implement-stage build gate is
  `python -m compileall -q .claude/tools`; there is no other build.
- **Roles and turns.** Dev, QA and fixer agents carry `SendMessage` and run at
  `maxTurns: 150`; read-only agents, the reviewer included, at 100 (set per gate
  profile in `.claude/tools/setup/apply_optimization.py`). Both load only after a
  session restart.
- **Checkpoints are unchanged.** The commit checkpoint is the CEO's, the shipped
  `approvals_never_granted` withholds the push at every approvals level, and in `solo` mode the local merge
  follows the commit approval (`pipeline-adapter.md`, `approvals_grants`).

## Subagent enforcement

Obedience is enforced by the harness, not by prompt text. Every dispatched agent
carries a `hooks.PreToolUse` block in its frontmatter that runs
`tools/pipeline/agent_gate.py` INSIDE the subagent, so the same deterministic
rules that govern the orchestrator's own tool calls also govern the agents:

| Profile | Agents | Enforced |
|---------|--------|----------|
| `dev` | senior-backend-dev, senior-frontend-dev, qa-engineer, devops-engineer, data-engineer, rapid-prototyper | commit/push approval flags, protected-branch push deny, review-stage edit freeze, no force push, no git `update-ref` or `symbolic-ref`, no fetch/pull/push that writes `refs/remotes`, no `approve.py`, no test-suite runs (`gates.dev_forbidden_commands` - the full suite runs once, in the test-stage exit gate the orchestrator drives through `advance.py`; agents run narrowed runs only) |
| `readonly` | architect, reviewer, security-engineer, evidence-collector, performance-benchmarker, ai-detector, accessibility-auditor, incident-response-commander | Edit/Write denied; Bash default-deny on the program name plus a per-program flag allowlist (below); any gate crash denies |
| `docs` | technical-writer, product-manager, spec-developer, sprint-prioritizer, content-manager, content-writer, editor, humanizer, seo-specialist, geo-specialist, business-analyst, financial-analyst, marketing-strategist, brand-guardian, ux-researcher, ux-ui-designer, feedback-synthesizer | Edit/Write only under `.claude/`, `.agentry/`, `docs/`, README, never the live `.claude/rules\|skills\|commands` or a `.git` path; Bash default-deny as `readonly`; any gate crash denies |

Every profile: no write to any `.git` path; a crash in the `dev` gate allows the
call (see "Gate crash" below). A time-out is not a crash: the glob deadline (2 s)
and the 14 s hook watchdog deny in every profile, `dev` included.

The MAIN THREAD carries its own profile: `orchestrator_gate` in `pipeline.json`
(enforced by `pretool_gate.py`) denies orchestrator Edit/Write and shell
file-writes outside the bookkeeping allowlist. Every agent - and the
orchestrator itself - is now deterministically scoped.

`approve.py` is orchestrator-only: any subagent invoking it is denied by the
gate. Recording a CEO approval is the one action that must never be delegated.
Dev agents additionally get a PostToolUse `dangerous_patterns.py` warning pass
on every Edit/Write (debug/dangerous calls surface immediately, review decides).

These hooks read the command text. They are tripwires with one real boundary
(default-deny on the readonly and docs profiles) and one explicit hole (the dev
profile). What each layer does and does not cover (task-0116):

- **File tools (Write, Edit, MultiEdit, NotebookEdit), every profile.**
  `.agentry/state/**`, `.agentry/pipeline.json` and the live harness are refused
  by where the path RESOLVES: realpath, NTFS stream suffix removed, 8.3 names,
  junctions, case, the payload cwd, and a hard link to `pipeline.json` (by
  `samefile`). Spelling does not matter.
- **Live harness, every non-orchestrator writer.** `<project root>/.claude/tools/**`,
  `.claude/rules/**`, `.claude/skills/**`, `.claude/commands/**`,
  `.claude/settings*.json`, `.claude/agents/*.md` and `.mcp.json` cannot be
  written by a file tool or by an explicit-path Bash command (redirect, `tee`,
  `sed -i`, `cp`, `mv`, `rm`, a glob, an earlier `cd` in the same command).
  Harness edits are made in the `src/` checkout (`src/.claude/...` stays
  writable, CEO decision D4) and deployed by the orchestrator.
- **Any `.git` path, every profile.** A path with a `.git` component (any repo,
  the gitfile, `.git.` and `.GIT` spellings) is refused to file tools and to
  explicit-path Bash writes (including `cd .git && ...`). `.gitignore` and
  `.github/` stay writable. `rm .git/index.lock` goes to the orchestrator.
- **readonly and docs profiles, Bash: default-deny.** `program_hit` runs FIRST
  (`check_program`); the lexical checks run afterwards only to name the boundary
  also crossed. A simple command runs only if its argv0 is on
  `agent_gate.ALLOWED_PROGRAMS` (read-only utilities, `git`, `codegraph`) or it
  is `python` running one of `ALLOWED_SCRIPTS` from `<root>/.claude/tools/`.
  Each listed program also has a flag allowlist (`FLAG_SPECS`; `find` predicates,
  `codegraph` read commands); a flag not named is refused, long options match by
  whole name, and a listed program with no spec fails closed. Further rules:
  no `NAME=value` prefix in front of any program (a standalone assignment is
  allowed only to a new lower-case name); no shell expansion (`$x`, `${x}`,
  `$(..)`, backtick, leading `{`) in the arguments of find, sort, uniq, rg, git,
  codegraph, date, file, hostname and diff (after a bare `--` it is allowed for
  sort, rg, git, codegraph, file and diff); git runs only the read-only subcommand
  set. `python -c`, `python -`, `python -m` of a non-harness module, a script the
  agent wrote, `node`, `awk`, `perl`, `dd`, `curl`, `sed`, `tar`, `tee`, `xargs`
  as a wrapper for an unlisted command, and `bash -c` are refused in any spelling.
  An agent that needs a tool gets it by name on its own hook line,
  `--allow-script NAME` (today: performance-benchmarker `k6,lighthouse,npx`;
  evidence-collector `playwright,npx`; accessibility-auditor
  `axe,lighthouse,pa11y,npx`). A name grants the whole program, so `npx` can
  install and run any package. An unparseable command is denied with its own text.
- **Gate crash.** For readonly and docs, an exception while checking a call denies
  Bash and the four file tools (`FAIL_CLOSED_MSG`); reads and web tools stay
  allowed. The `dev` profile stays fail-open on a crash, on purpose: its job is
  running code, a gate bug must not brick the agent that builds the harness, and
  its commit and push are behind the approval gates. A crash in the main-thread
  gate also stays open for the orchestrator, but denies a subagent call.
- **Trunk push marker.** Only the orchestrator may use the `trunk_push` marker.
  A subagent push that names a trunk branch is denied before the marker, the
  remote or `allow_push_to_main` is read. The orchestrator spends the marker
  atomically (`os.replace` to a `.spent-<uuid>` file) before the push is allowed;
  if it cannot be spent, the push is denied.
- **UNC paths.** In readonly and docs, a path operand with two or more leading
  `/` or `\` is refused, and no gate ever stats one (a stat on a UNC host can
  block on the network).
- **A command over the scan budget** (`pretool_gate.too_complex`) is refused in
  every profile, never allowed; the deny says it is too complex to check.
- **Orchestrator gate.** Heredoc bodies and quoted arguments are data; a body is
  read as code only when a shell consumes it.

**NOT COVERED - do not rely on it:**

- **Dev `git config` and dev crashes.** A dev agent may still run
  `git config <key> <value>`, so `.git/config` (`core.hooksPath`,
  `core.fsmonitor`, `alias.*`) stays writable through git itself, despite the
  `.git/**` path rule. The dev profile also stays fail-open when its gate
  crashes. Both belong to the accepted dev residual (CEO D1(a)).

- **The dev profile.** A dev agent runs code, so `python -c`, `python - <<EOF`, a
  script it wrote, `python -m`, `node -e`, `awk`, `perl` and `dd` can write
  `.agentry/pipeline.json` and `.agentry/state/*` (approvals level, `trunk_push`,
  run flags) and pass the gate with exit 0. `tests/test_subagent_enforcement.py`
  pins those spellings at exit 0. The `test` exit gate also runs agent-written
  test code as the orchestrator, outside every hook. Dev interpreters are
  therefore an open hole, not a gap awaiting a fix.
- **No off-box bound.** The CEO declined GitHub branch protection (D2) and the
  project stays in `solo` mode. A forged `trunk_push` followed by a push to
  `main` is stopped only by the commit and push approval gates and the
  never-granted list, and detected by the CEO reading the diff at the commit
  checkpoint.
- **`npx` and any named program.** `--allow-script npx` lets that agent install
  and run any package from the registry.
- **readonly and docs git** is default-deny and read-only (FX-3, SEC-A,
  task-0120). Only the subcommands in `agent_gate.GIT_READ_SUBS` run (`branch`
  and `stash` in listing form only), and only with allowlisted options; global
  `-c`, `--config-env`, `-p`, `--paginate` and any `NAME=value` prefix
  (`GIT_*`, `PAGER`, `EDITOR`, `VISUAL` included) are refused. `config`, `tag`,
  `checkout`, `archive`, `bundle`, `apply`, `update-ref` and the rest are
  refused as subcommands.
- **dev git** stays open, but its file-naming options are checked against
  protected paths (FX-3b, task-0120). `--output`, `--output-directory`,
  `--export-marks`, `config --file`, `-o` of `archive`, `format-patch` and
  `index-pack`, and the file of `bundle create` are resolved like a redirect
  target (`-C` and an earlier `cd` included) and the command is refused when the
  target is `.agentry/state/**`, `.agentry/pipeline.json` or the live harness,
  or cannot be resolved (`$VAR`, a substitution, `~`, no value), or when a
  `-c`, `--config-env`, `--work-tree`, `--git-dir` option or a `GIT_*`
  assignment may have moved the working tree. A working file such as
  `git format-patch -o tmp/p` is allowed. Not covered: a git command that writes
  paths named inside its input (`apply`, `am` or `checkout-index` of a patch the
  agent wrote, `checkout`, `restore`, `reset --hard`; the `.git` rule is
  path-based and does not see them), and `core.worktree`, an alias or an include
  set earlier with `git config`. The dev profile does deny `update-ref`,
  `symbolic-ref` and any fetch/pull/push that writes `refs/remotes`, because
  `leak_guard` trusts the local `refs/remotes` base. A dev interpreter can
  still move that base.
- **UNC and nested shells.** A UNC positional inside a nested `bash -c` is not
  caught by the argument scan (the 14 s watchdog covers it), and the Read, Grep
  and Glob tools are not gated for UNC paths.
- **Directory-level operations** (`rm -r .agentry`, `.claude`), and a glob that
  expands to a file NAMED like an option (`sort *` beside a file called
  `--output=x`, which a docs agent can create). In readonly and docs every
  allowlisted program's flags are read (`FLAG_SPECS`); in the other profiles only
  `memory.py --out`/`--db` and the git file-naming options above are.
- **Built-in agents without the frontmatter hook** get only the settings-level
  gate: its Write/Edit deny on the live harness, no Bash allowlist.
- **Windows 8.3 short names** are not measured on this volume (E: has none); the
  mechanism was checked on C:, and its test skips here.
- **Capability cost.** A docs agent can no longer run a generator script it
  wrote (the pitch-deck skill's `docs/presentations/scripts/generate_pitch.py`);
  the orchestrator or a dev agent runs it until the CEO decides on a vetted
  generator under `.claude/tools`. Also lost to readonly and docs agents:
  - Flags: sort `-o --output --compress-program -T --parallel --files0-from`;
    rg `--pre --pre-glob -z --search-zip --hostname-bin --generate`; find
    `-exec -execdir -ok -okdir -delete -fprint* -fls -files0-from`; file `-C -z
    -Z --compile --uncompress`; date `-s --set` and any operand not starting with
    `+`; diff `-l --paginate`; tail `-f -F --follow --pid --retry`; a second
    uniq operand; wc and du `--files0-from`; hostname with an operand; `printf
    -v`; `ls --hyperlink`; and any GNU option not on a list until someone adds it.
  - Prefixes: every `NAME=value` prefix, so `PIPELINE_LANE=x python state.py
    --show` and `env X=y cmd` are refused.
  - Expansions: `git diff $(git merge-base HEAD main)`, `sort "$f"`, `find $dir`,
    `rg "$term" docs` are refused; run the inner command first and paste its
    output, or use `rg -- "$term" docs`.
  - Also refused: codegraph `init`, `index`, `sync`, `serve` and the other
    writing commands; `zones.py --prove` and `--release` for every subagent.
- **Swarm zones** (see "Swarm mode") bind the dev profile only, and inherit the
  interpreter and `npm install` holes above.

### Deploying a harness change from `src` to the live workspace

The scripts import each other at module top: `agent_gate.py` imports `zones`,
`approve.py` reads `pretool_gate.TRUNK_PUSH_DEFAULT_REMOTE` at import, `gate.py`
needs `state.emit_gate`, and `pretool_gate` denies every push without
`leak_guard.py`. A hook that fails to import exits 1, which is non-blocking, so
a wrong copy order silently switches the subagent gates off. Copy in this order:

1. New modules first: `events.py`, `zones.py`, `leak_guard.py`, `worktree.py`.
2. Then `state.py` and `git_state.py` (`worktree.py` imports both; `git_state`
   imports `worktree` lazily).
3. Then the other pipeline and memory scripts, except `approve.py`.
4. Then `pretool_gate.py`, then `agent_gate.py`, then `agents/*.md`.
5. Then `approve.py` (it reads `pretool_gate` at import), together with the
   adapter's `--reason` change.
6. Rules go together with `settings.json` (the chunk wiring must match the rule
   sizes).
7. Run `git config core.hooksPath .githooks` in `src`.
8. Run any live-store memory reconciliation the task names (for example
   `memory.py --refine ... --why ... --trigger ...` and `--retire ...`),
   confirm with `memory.py --query`, and record the result in the task Notes.

General rule: copy every script before any `settings.json` edit, because hook
wiring reloads live. Before editing settings, verify with `python -m
compileall` over `.claude/tools` and an import smoke test of every hook script
(import each one and confirm exit 0).

## Deferred: Agent Teams

Claude Code's native Agent Teams (separate full sessions with a shared task
list and mailbox) stay OFF in this template: the feature is experimental and
costs roughly 7x the tokens of subagent dispatch. Revisit when it stabilizes;
the pipeline above does not depend on it.

## Blocked tasks

A `blocked` task (gate failed past budget, unconfigured gate, or stuck past the
continuation ceiling) is surfaced to the CEO and never auto-retried. The CEO
fixes the blocker or rejects; then `approve.py --reject --reason "<why>"` sends
it back to `implement` to retry cleanly (to `plan` if it was blocked before the
plan approval). The reason is required (a bare `--reject` exits 2 and writes
nothing) and is journaled with the rejection. It is capped at 500 characters and
control characters become spaces.

## Event journal

History lives in `.agentry/state/events.db` (SQLite, WAL), written by
`tools/pipeline/events.py` through `state.py`, `gate.py`, `advance.py`,
`approve.py`, `stop_gate.py` and `supervisor.py`. One row per registration,
stage transition, gate result (command, exit status, duration; never the gate
output), park, block, approval, rejection (with its reason), continuation,
pause, resume, abandon, merge and close. Read it with
`python .claude/tools/pipeline/events.py --task T` (one task) or `--summary
[--since DATE]` (all tasks); both print JSON. Handoff authors run the `--task`
form instead of reconstructing a task's history by hand.

`gate_log` in `run.db` is retired: nothing writes it, and an old store keeps its
table and rows untouched (the journal copies them on its first creation). The
journal is a report. Only `supervisor.progress_at` reads it, and only to veto a
relaunch (recent gate evidence keeps a run HEALTHY); no gate reads it. A failed
write prints
`[event-skipped]` to stderr instead of failing the command that caused it.

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
