---
name: health-check
description: Run the full quality gate (format, lint, type check, tests, forbidden pattern scan) and report PASS/FAIL per check. Use before every commit, after a bug fix or feature scaffold, before showing a diff to the CEO, or when asked to "check code quality", "run the quality gate", or "verify this is ready to commit".
allowed-tools: Read, Grep, Glob, Bash
---

# Health Check

Run the full quality gate to verify code is production-ready; every check must pass before code is committed. Concrete commands (`{{FORMAT_CMD}}`, `{{LINT_CMD}}`, `{{TYPECHECK_CMD}}`, `{{TEST_CMD}}`) live in `.claude/project/stack.md`. The same chain runs in CI, so keep local and CI commands identical - "passes locally" should mean "passes in CI."

## Steps

1. **Run the formatter** (`{{FORMAT_CMD}}`). "No changes" = PASS; "N files reformatted" = FIXED, stage the changes.
2. **Run the linter** (`{{LINT_CMD}}`), in check-only mode if separate from the formatter, so already-committed unformatted files are also caught. PASS if clean, FAIL with the violations listed.
3. **Run the type check** (`{{TYPECHECK_CMD}}`) for statically-typed stacks only (`tsc --noEmit`, `mypy .`, `pyright`, `go vet ./...`, `cargo check`). Skip for dynamically-typed stacks with no static layer.
4. **Run tests** (`{{TEST_CMD}}`). Targeted tests if a specific path changed, otherwise the full suite. Record PASS with a count (e.g. "42 passed"), or FAIL with failing test names and reasons.
5. **Scan changed files for forbidden patterns.** Get the changed file list (`git diff --name-only HEAD`, `git diff --name-only --cached`), then Grep each file for: debug output (`dd(`, `dump(`, `console.log(`, `print(` in library code, `debugger;`, `binding.pry`); dangerous eval/shell (`eval(`, `exec(`, `shell_exec(`, `os.system(`, `subprocess...shell=True`); config bypass (raw `env(` / `process.env` used outside the config layer); commented-out code blocks (3+ consecutive lines); `TODO`/`FIXME` without a linked task id; hardcoded secrets (API keys, tokens, connection strings); Unix redirects on Windows (`>/dev/null`, `2>/dev/null`, `&>/dev/null`). Do not flag config-layer env reads, test files mocking dangerous functions, or vendor/node_modules/generated folders. Record CLEAN, or ISSUES FOUND with `file:line - snippet` per violation.
6. **Report.** One block per check (status + details), then an overall verdict: PASS only if every active check is PASS, FIXED, or CLEAN; any FAIL makes the overall result FAIL.

## Output checklist

- [ ] Formatter PASS or FIXED (changes staged)
- [ ] Linter PASS
- [ ] Type check PASS, or explicitly marked N/A for dynamically-typed stacks
- [ ] Tests PASS with a count
- [ ] Forbidden pattern scan CLEAN
- [ ] Overall PASS/FAIL reported with per-check details

## References (read only when needed)

- [references/incident-templates.md](references/incident-templates.md) - severity classification matrix and escalation cadence
- [references/perf-benchmarks.md](references/perf-benchmarks.md) - Core Web Vitals targets and benchmarking methodology
