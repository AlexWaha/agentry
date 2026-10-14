---
name: verify
description: Pre-commit gate. Claude Code runs a skill named verify right before it commits (not for docs-only or tests-only commits); this one delegates to the health-check skill and adds no checks of its own. Use when about to commit, or when asked to "verify" the change.
allowed-tools: Read, Grep, Glob, Bash, Skill
---

# Verify

The platform entry point for the pre-commit gate. It holds no checks, so there is
nothing here to drift from `health-check`.

## Steps

1. **Run the gate.** Invoke the `health-check` skill and follow it unchanged. If the Skill tool is unavailable, Read `$AGENTRY_ROOT/skills/health-check/SKILL.md` and follow it. A dev agent that cannot run the full suite takes health-check's targeted-tests branch (named modules).
2. **Report.** Give health-check's overall PASS/FAIL verdict and its per-check details as your own. Commit only on PASS.

## Output checklist

- [ ] `health-check` was run, not summarised from memory
- [ ] Overall PASS/FAIL reported with the per-check details
