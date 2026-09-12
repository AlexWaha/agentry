---
name: status-report
description: Generate a project status report covering phase progress, task summary, blockers, risks, and next steps, sourced from actual task files rather than memory. Use when the CEO asks "what is the status", when the Orchestrator needs to brief the CEO, before a phase gate review, or after a batch of tasks completes and progress needs to be summarized.
allowed-tools: Read, Grep, Glob, Write, Bash
---

# Status Report

Generate a project status report by analyzing the task management system,
documentation state, and current phase progress. Used by the Orchestrator to
keep the CEO informed.

## Steps

1. **Gather task data.** A task's state is the folder it sits in - there is no
   `status:` field. Glob `.claude/tasks/backlog/*.md` (queued),
   `.claude/tasks/active/*.md` (in flight) and `.claude/tasks/done/*.md`
   (merged); parse each file's YAML frontmatter (assignee, depends_on, epic,
   spec, phase) to build the current picture. Cross-reference
   `.claude/tasks/epics/*.md` for epic-level status. Run
   `python .claude/tools/pipeline/state.py --resume` (or `--show`) for
   in-flight pipeline state (what stage each active task is at). Compute
   total tasks, backlog count, active count, done count, and completion
   percentage (`done / total * 100`).
2. **Determine the current phase.** Read the phase definitions from
   CLAUDE.md or the project spec. Use active tasks and epics to see which
   phase(s) are in progress, and done tasks to see which phases are fully
   completed (see completion rule in `references/templates.md`).
3. **Verify phase deliverables.** For the current phase, Glob the expected
   output files listed in `references/templates.md` and Read each to
   confirm it is non-empty and current. Mark each Present / Missing /
   Incomplete.
4. **Identify blockers.** A task is blocked if: it is in `active/` with no
   recent commits on its branch (`git log --oneline --since="3 days ago"
   <branch>`); it depends on a task (via `depends_on`) that is not yet
   `done`; it is finished but awaiting CEO approval; it needs an external
   resource not yet available; or its Notes section records a technical
   problem. Document what is blocked, why, how long, and a suggested fix.
5. **Identify risks.** Phase delay (current phase started longer ago than
   its complexity justifies), scope creep (new tasks added to an
   already-started phase or epic), quality concerns (tasks moved to `done`
   without their Definition of Done checked), stale branches
   (`git branch --list` for branches that should have been merged or
   deleted).
6. **Generate the report.** Fill the output skeleton in
   `references/templates.md`: phase status, task summary, completed this
   session, in progress, blockers, risks, next steps, recommendations.
   Quantify everything (counts, percentages), never "some" or "several."
7. **Present to the CEO.** Show the report in the conversation (do not save
   to a file unless explicitly asked). Call out any Critical blockers or
   risks needing a CEO decision. Recommend next actions and ask for
   direction. If the current phase is complete and all deliverables are
   verified, recommend a phase gate review.

## Output checklist

- [ ] Every task file was actually read, not assumed from prior context
- [ ] Task counts cross-checked between active/done directories and epics
- [ ] Pipeline state checked via `state.py --resume`
- [ ] Phase deliverables verified by checking file existence and content
- [ ] Blockers and risks are real, not hypothetical
- [ ] Next steps are specific and actionable, not "continue working"
- [ ] Report presented in clean table format, scannable in 2 minutes
- [ ] Any CEO decisions needed are clearly called out

## References (read only when needed)

- [references/templates.md](references/templates.md) - phase deliverables
  table and the full status report output skeleton
