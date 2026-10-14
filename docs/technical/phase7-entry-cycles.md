# Phase 7 entry condition: two green task cycles (FR-64, task-0037)

**Date:** 2026-10-14
**Author:** qa-engineer
**Status:** In Review
**Version:** 1.0

---

## Table of Contents

- [1. User decision on which tasks count](#1-user-decision-on-which-tasks-count-2026-10-13)
- [2. Cycle 1: task-0107](#2-cycle-1-task-0107-enhancement)
- [3. Cycle 2: task-0094](#3-cycle-2-task-0094-techdebt)
- [4. Verification that nothing failed or was worked around](#4-verification-that-nothing-failed-or-was-worked-around)
- [5. Acceptance criteria and Definition of Done](#5-acceptance-criteria-task-0037-and-definition-of-done)

---

Evidence that two real tasks ran the full pipeline on the current layout, from
registration to a merge-confirmed `done`, with no gate failure, rejection, block or
manual intervention. This record gates all of Phase 7 (ORD-8): task-0038 (FR-65)
does not start before it exists.

- Spec: spec-0001, FR-64. Task: task-0037.
- Layout: current (`.claude/tools/`, `.claude/agents/`, `.claude/skills/` in the
  repository root, state under `.agentry/`). No `.claude-plugin/` directory exists in
  `src/`, so no plugin file has moved.
- Mode: `solo` (`workflow.mode` in `.agentry/pipeline.json`). Approvals level:
  `auto` (`.agentry/state/approvals`, file mtime 2026-10-12, unchanged during both cycles).

## 1. User decision on which tasks count (2026-10-13)

The User named the two cycles. Source of the decision and its date: the User chose
task-0107 and task-0094 in the orchestrator session on 2026-10-13 (the orchestrator's
session record; the task-0037 file's "CEO Decisions 2026-10-12" section, written before
that choice, still names task-0106 and task-0094).

| Cycle | Task | Type | Why this one |
|---|---|---|---|
| 1 | task-0107 | enhancement | Exercises the plan approval AND the commit checkpoint. |
| 2 | task-0094 | techdebt | Exercises the commit checkpoint on a type that has no plan step. |

task-0106 was first named as a cycle, but its journal holds a `rejected` review row
(2026-10-13, review B1: `omitClaudeMd` finding, sent back to `implement`), so it was
not a clean pass and the User replaced it with task-0107.

The User also decided how the plan checkpoint is read for the techdebt cycle:
`techdebt` is listed in `skip_for_types` (`pipelines.build`, `plan` stage in
`.agentry/pipeline.json`, together with `bugfix`), so a techdebt task registers
directly at `implement` and has no plan checkpoint. That is the designed behavior of
the pipeline, NOT a skipped checkpoint, and it does not make cycle 2 less green.
Together the two cycles cover every checkpoint the `solo` build pipeline can raise:
the plan approval (cycle 1) and the commit approval (both cycles). There is no push
checkpoint in `solo` mode (task-0037 "CEO Decisions 2026-10-12" reading of "both
approval checkpoints").

## 2. Cycle 1: task-0107 (enhancement)

- Title: Expose the quality gate as a `verify` skill and run /doctor prompt-audit.
- Branch: `enhancement/task-0107`. Work commit `db68e95`.
- Merge commit: `c1b1216` (Merge enhancement/task-0107, 2026-10-13 20:16:44 -0400),
  an ancestor of `main`. Parents: `50c3525` and `db68e95`.
- Plan file: `.agentry/plans/2026-10-13-task-0107-verify-skill.md`.
- Journal: `python .claude/tools/pipeline/events.py --task task-0107` (from
  `E:/Personal/Agenty`). 16 events, `merged: true`, `rejections: []`.

Journal summary (`gates`): plan 1 run / 0 failures, implement 1 / 0, test 1 / 0,
review 1 / 0.

Stage transitions (raw rows from `.agentry/state/events.db`, UTC; local time is UTC-4):

| id | at (UTC) | kind | stage | from | outcome | via |
|---|---|---|---|---|---|---|
| 493 | 00:05:21 | registered | plan | | | advance |
| 494 | 00:05:24 | gate | plan | | pass | advance |
| 495 | 00:05:24 | transition | plan-approval | plan | | advance |
| 496 | 00:05:27 | parked | plan-approval | | plan | advance |
| 497 | 00:05:27 | approved | plan-approval | | plan | approve |
| 498 | 00:05:27 | transition | implement | plan-approval | | advance |
| 499 | 00:06:44 | gate | implement | | pass | advance |
| 500 | 00:06:44 | transition | test | implement | | advance |
| 501 | 00:16:32 | gate | test | | pass | advance |
| 502 | 00:16:32 | transition | review | test | | advance |
| 503 | 00:16:37 | gate | review | | pass | advance |
| 504 | 00:16:37 | transition | ready | review | | advance |
| 505 | 00:16:39 | parked | ready | | commit | advance |
| 506 | 00:16:39 | approved | ready | | commit | advance |
| 507 | 00:16:44 | transition | done | ready | | advance |
| 508 | 00:16:45 | merged | done | | | advance |

Gate rows, raw `detail` JSON (`kind: gate`, `outcome: pass`):

```
494 plan      {"cmd": "python E:/Personal/Agenty/.claude/tools/pipeline/artifact_gate.py --task task-0107 --kind plan", "duration_ms": 94, "exit": 0}
499 implement {"cmd": "python -m compileall -q .claude/tools", "duration_ms": 108, "exit": 0}
501 test      {"cmd": "python -m unittest discover -s .claude/tools && python E:/Personal/Agenty/.claude/tools/pipeline/ui_evidence.py --task task-0107", "duration_ms": 584672, "exit": 0}
503 review    {"cmd": "ruff check .claude/tools", "duration_ms": 62, "exit": 0}
```

Time spent per stage (from the journal `stages` section): plan 3 s, plan-approval
3 s, implement 77 s, test 588 s, review 5 s, ready 7 s, done 1 s.

Approvals recorded:

- Plan: event 497, `kind: approved`, `outcome: plan`, `via: approve`, 2026-10-14
  00:05:27 UTC. Recorded through `approve.py --gate plan` by the orchestrator after the
  User approved the plan. No approvals level and no `auto_approve` list grants it
  (`approvals.never_granted()`), so a recorded `via: approve` row is the only way it
  appears.
- Commit: events 505 (parked) and 506 (approved), `outcome: commit`, `via: advance`,
  same second (00:16:39 UTC). This is the `auto` level granting the commit checkpoint:
  `advance.py` records the approval itself, with no `approve.py` call. See the note
  in section 4.

Handoff doc: `.agentry/tasks/handoffs/task-0107.md` (frontmatter `task: task-0107`,
`merge_commit: c1b1216`, author technical-writer, 5808 bytes). Validated:
`python .claude/tools/pipeline/handoff.py --check` returned `handoff_debt: []`, exit 0
(run 2026-10-14).

Memory stamp: `.agentry/state/memory/task-0107.json`, `stamped_at`
`2026-10-14T00:16:52+00:00`, `counts` lesson 241 / pattern 2 / module 11,
`none: false` (a row was added). `python .claude/tools/memory/update.py --check`
returned `memory_debt: []`, exit 0 (run 2026-10-14).

## 3. Cycle 2: task-0094 (techdebt)

- Title: Rename the human role from CEO to User across the harness.
- Branch: `techdebt/task-0094`. Work commit `0b17725` (143 files, 703 insertions,
  703 deletions).
- Merge commit: `672c88c` (Merge techdebt/task-0094, 2026-10-13 20:35:44 -0400), an
  ancestor of `main`. Parents: `c1b1216` and `0b17725`.
- Journal: `python .claude/tools/pipeline/events.py --task task-0094` (from
  `E:/Personal/Agenty`). 11 events, `merged: true`, `rejections: []`.

Journal summary (`gates`): implement 1 run / 0 failures, test 1 / 0, review 1 / 0.
There is no `plan` entry, by design (section 1).

| id | at (UTC) | kind | stage | from | outcome | via |
|---|---|---|---|---|---|---|
| 509 | 00:18:01 | registered | implement | | | advance |
| 510 | 00:25:51 | gate | implement | | pass | advance |
| 511 | 00:25:51 | transition | test | implement | | advance |
| 512 | 00:35:32 | gate | test | | pass | advance |
| 513 | 00:35:32 | transition | review | test | | advance |
| 514 | 00:35:37 | gate | review | | pass | advance |
| 515 | 00:35:37 | transition | ready | review | | advance |
| 516 | 00:35:40 | parked | ready | | commit | advance |
| 517 | 00:35:40 | approved | ready | | commit | advance |
| 518 | 00:35:44 | transition | done | ready | | advance |
| 519 | 00:35:45 | merged | done | | | advance |

Registration row detail: `{"type": "techdebt"}` at stage `implement` (event 509).

Gate rows, raw `detail` JSON:

```
510 implement {"cmd": "python -m compileall -q .claude/tools", "duration_ms": 94, "exit": 0}
512 test      {"cmd": "python -m unittest discover -s .claude/tools && python E:/Personal/Agenty/.claude/tools/pipeline/ui_evidence.py --task task-0094", "duration_ms": 577217, "exit": 0}
514 review    {"cmd": "ruff check .claude/tools", "duration_ms": 62, "exit": 0}
```

Time spent per stage: implement 470 s, test 581 s, review 5 s, ready 7 s, done 1 s.

Approvals recorded: no plan approval (techdebt is in `skip_for_types`, section 1).
Commit: events 516 (parked) and 517 (approved), `outcome: commit`, `via: advance`,
same second (00:35:40 UTC), the `auto` level grant.

Handoff doc: `.agentry/tasks/handoffs/task-0094.md` (frontmatter `task: task-0094`,
`merge_commit: 672c88c`, author qa-engineer, 4953 bytes). Validated by the same
`handoff.py --check` run, `handoff_debt: []`, exit 0.

Memory stamp: `.agentry/state/memory/task-0094.json`, `stamped_at`
`2026-10-14T00:50:14+00:00`, `counts` lesson 242 / pattern 2 / module 11,
`none: false`. Covered by the same `update.py --check` run, `memory_debt: []`, exit 0.

Pickup chain: task-0094 registered at 00:18:01 UTC. The task-0107 memory stamp
was written at 00:16:52 UTC and its handoff doc last modified at 00:17:46 UTC (file
mtimes, 20:17:46 -0400). Registration is refused while either debt stands, so the
registration itself shows both were paid for cycle 1.

## 4. Verification that nothing failed or was worked around

Distinct `kind` values across both journals: `registered`, `gate`, `transition`,
`parked`, `approved`, `merged`. No `gate_failed`, `rejected`, `blocked` row exists for either task.
Every `gate` row has `outcome: pass` and `exit: 0`. Each build stage's gate ran exactly once (`runs: 1`, `failures: 0`). Both runs
end in `merged` and the task files sit in `.agentry/tasks/done/`.

The only `parked` rows are the checkpoints themselves (cycle 1: `plan-approval` and
`ready`; cycle 2: `ready`). A park at a checkpoint is the machine working, not a
block.

Notes and limits of this evidence, stated plainly:

1. The journal records command, exit status and duration. It does NOT retain the
   test runner's stdout, so the "real gate output" for each cycle is journal-level:
   the recorded command line plus exit 0 plus duration, not a captured console log.
   The User accepts journal-level output as the evidence FR-64 asks for.
2. Suite size is not in the journal. Two counts were measured now (2026-10-14), each
   from a `git archive` of the merge commit's `.claude/tools`, and both agree for the two
   merges. (a) Collected test cases: `unittest.TestLoader().discover('.claude/tools')`
   then `countTestCases()` (loads, does not run, the same discovery the gate uses) gives
   3528 at `c1b1216` and 3528 at `672c88c`; task-0094 swapped text in assertions and
   added no tests. (b) A textual count of `def test_` over `.claude/tools` via `git grep`
   gives 3543 at both merges; it differs from (a) because it counts definitions, not
   collected cases, and the gap is not analysed here. The figure "1711 tests" in
   `.agentry/project/project-context.md` (dated 2026-10-12) is stale and should be
   updated by whoever next touches that overlay. Collection is a count of tests, not
   proof that each passed; the pass is the exit 0 of events 501 and 512.
3. The commit approval in both cycles came from the `auto` approvals level
   (`via: advance`), not from an interactive `approve.py` call. The grant is
   `GRANTS[AUTO]` in `.claude/tools/pipeline/approvals.py`, which contains `COMMIT`
   (plan approval is in `ALWAYS_WITHHELD`, so the `auto` level cannot grant it). It is
   stated here so the record is not read as a human clicking in each cycle.
4. The plan approval in cycle 1 is recorded `via: approve`. The journal does not
   store who invoked it; `approve.py` is orchestrator-only and is run after the User
   approves the plan.
5. A reviewer agent ran for both cycles, outside the journal (the `review` stage gate
   in the journal is only `ruff check`). Cycle 1, task-0107: reviewer returned APPROVED,
   0 blocking. Cycle 2, task-0094: the orchestrator's session record is that a reviewer
   agent reviewed the full diff on 2026-10-13 before the commit and returned APPROVED,
   0 blocking (non-blocking: one leftover CEO in `.agentry/specs/_template.md`, fixed by
   the orchestrator, and roster wording). The task-0094 file's Definition of Done line
   "Reviewer: skipped per token-policy" is the plan written at task creation, not what
   happened; the orchestrator added a correction note under that line in
   `.agentry/tasks/done/task-0094.md` (dated 2026-10-14). The handoff doc
   `.agentry/tasks/handoffs/task-0094.md` still repeats the stale planned line ("The
   reviewer was skipped by token policy").
   Reviewer approval is a session record, not a journal row, so this record rests on the
   orchestrator's account for it.
6. The journal cannot show actions taken outside the pipeline (for example a human
   editing a file between stages). It shows that every stage gate passed first
   time, every transition was driven by `advance.py`, and no reject or block was
   recorded. No such outside action is known.

## 5. Acceptance criteria (task-0037) and Definition of Done

| AC | Verdict | Basis |
|---|---|---|
| (FR-64) Two task cycles are recorded as green on the current layout, with the real gate output for each, before any plugin file moves. | HOLDS, with scope note | Sections 2 and 3 quote the raw gate rows (command, exit 0, duration) for both. Output means exit status and duration, per note 1 in section 4: the journal keeps no console text. No `.claude-plugin/` exists in `src/` and task-0038 has not started. |
| (FR-64) Each cycle runs registration through to a merge-confirmed `done`, including both approval checkpoints, the handoff gate and the memory stamp. | HOLDS | Both reach `merged` (`c1b1216`, `672c88c`, both ancestors of `main`) and `done/`. Plan approval (event 497) and commit approval (506, 517) recorded; task-0094 has no plan step by the User's decision and `skip_for_types`. Handoff docs validated (`handoff_debt: []`), memory stamps present for both (`memory_debt: []`). |
| (FR-64) Any failure in either cycle means the condition is not met. A cycle that needed a manual intervention to complete is not green. | HOLDS | Zero gate failures, zero rejections, no `blocked` row, every transition via `advance`. Section 4 lists the limit of what a journal can prove. |
| (FR-64) The recorded evidence names the two tasks and the commits, so the claim is checkable after the fact. | HOLDS | task-0107 / `c1b1216` (work `db68e95`), task-0094 / `672c88c` (work `0b17725`); checkable with `git show`, `events.py --task` and the `events.db` row ids quoted above. |

| Definition of Done item | State |
|---|---|
| Two cycles run end to end, not simulated | Met (real merges on `main`) |
| Every stage's real command output captured | Met to the extent the journal records it: command, exit, duration (note 1) |
| No failure worked around or excused | Met |
| No em dash or en dash in any file written (NFR-5) | Met for this file |
| Reviewer agent approved | Met for both cycles per note 5 (APPROVED, 0 blocking each); this evidence record itself is pending review by the orchestrator |
| Deploy Actions section filled | Already filled in the task file: `none` |

Verdict for FR-64: the entry condition is met. Phase 7 (task-0038) may start once
this record is reviewed and merged.
