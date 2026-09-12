---
name: bug-fix
description: Structured RED-GREEN debugging workflow - reproduce with a failing test, find root cause, fix minimally, verify green, check regressions. Use when a task is a bugfix, when behavior differs from expected, when an endpoint returns wrong data/errors, or when a regression is reported.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Bug Fix

RED-GREEN discipline: a bug is only fixed when a test that reproduced it turns
green and nothing else broke. Concrete commands (`{{TEST_CMD}}`,
`{{FORMAT_CMD}}`) live in `.claude/project/stack.md`.

## Steps

1. **Parse the bug report.** Extract: what is broken, expected vs actual
   behavior, steps to reproduce, environment, error message, severity. If the
   report lacks the affected endpoint/feature or reproduction steps, ask before
   proceeding. Summarize in one sentence: "When [action], [observed] instead of
   [expected]."
2. **Locate the code path.** Trace the request lifecycle (route -> controller ->
   validator -> service -> model -> resource) via codegraph
   (`rules/code-retrieval.md`) or Grep. For possible regressions check
   `git log --oneline -20` and `git diff HEAD~5 -- <path>`. Document:
   "Request hits `<route>` -> `<controller>` -> `<service>` -> `<query>`."
3. **Write a failing test (RED).** Reproduce the EXACT bug; the test asserts
   the CORRECT behavior and must fail before the fix. Name it after the correct
   behavior, never the bug number.
4. **Confirm the test fails** with `{{TEST_CMD}} --filter "<test name>"`. If it
   passes: the bug is not reproduced, already fixed, or the test is wrong -
   resolve that first. If it fails for a DIFFERENT reason, investigate that first.
5. **Identify the root cause.** With the path mapped and the bug reproduced,
   state: "The bug occurs because `<cause>` in `<file>:<line>`." Consult the
   root cause quick map in references/examples.md.
6. **Implement the minimal fix.** Fix ONLY what is broken: no refactoring, no
   features, no cleanup of surrounding code, no renames. Comment only if the
   fix is non-obvious.
7. **Verify green.** Re-run the filtered test - it must pass.
8. **Add edge case tests.** Boundary conditions around the fixed behavior
   (empty results, missing params, exact boundaries).
9. **Run the full related suite.** The changed test file plus its directory;
   the touched service's unit tests. A newly failing test means the fix
   regressed something - investigate before proceeding.
10. **Run the quality gate.** `{{FORMAT_CMD}}` -> `{{LINT_CMD}}` ->
    `{{TEST_CMD}}` -> grep forbidden patterns on changed files (see
    `health-check` skill).
11. **Report for review.** Structured summary: bug, root cause, fix, files
    changed, tests added, regressions (none), deploy actions
    (per `rules/git-workflow.md`). Never commit without approval.

## Anti-patterns

| DO NOT | Why |
|---|---|
| Refactor or "clean up" while fixing | Pollutes the diff, harder to review/revert |
| Fix without a failing test first | No proof; regresses silently |
| Skip the regression suite | Fix may break neighbors |
| Commit without CEO approval | Violates git workflow |

## Output checklist

- [ ] Bug summarized in one sentence; code path traced
- [ ] RED confirmed (failing test reproduces the bug)
- [ ] Root cause documented (file:line)
- [ ] Minimal fix applied; GREEN confirmed
- [ ] Edge case tests added; full related suite passes
- [ ] Quality gate clean; summary ready with deploy actions

## References (read only when needed)

- [references/examples.md](references/examples.md) - failing-test examples
  (Pest, Vitest), minimal-fix shapes, edge case ideas, root cause quick map
