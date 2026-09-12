---
name: deep-review
description: Multi-lens parallel review of code, architecture, documents, or configuration with severity-ranked findings (Critical/High/Medium). Use when reviewing a diff/PR/branch, auditing an architecture or business document, reviewing configs, or gating a completed phase before approval.
allowed-tools: Read, Grep, Glob, Bash
---

# Deep Review - Multi-Lens Parallel Analysis

Examine one subject through 3-4 orthogonal lenses simultaneously; produce a
unified severity-ranked report. Used by the Reviewer and Architect as a
quality gate.

## Steps

1. **Identify the subject.** One of: code (branch diff / files / PR),
   architecture (design docs, ER diagrams, API contracts), document (business
   plan, research, spec), config (Docker, CI/CD, env), or phase output (all
   deliverables of a finished phase, listed from the spec, the task files in
   `.claude/tasks/done/`, and git log). For code: scope with
   `git diff main...HEAD --stat`; when the work is uncommitted review the
   working tree (`git diff HEAD` + untracked via `git status --porcelain`).
2. **Select 3-4 lenses** from [references/lenses.md](references/lenses.md) per
   subject type. Present the chosen lenses to the CEO/Orchestrator for approval
   before analyzing.
3. **Gather material.** Read ONLY changed files for code (plus their tests,
   validators, services, resources, migrations, routes); referenced sibling
   documents for docs; `.env.example` and infra docs for configs.
4. **Analyze each lens.** For every finding record: lens, severity
   (Critical/High/Medium per the definitions in references/lenses.md),
   location (file:line or section), description, evidence, suggested fix,
   impact. A lens with no findings explicitly reports "No findings" - never
   fabricate.
5. **Generate Mermaid diagrams** only where they genuinely clarify a finding
   (dependency violations, data flow leaks, N+1 chains).
6. **Synthesize the unified report** using the skeleton in
   references/lenses.md: summary counts, findings by severity, positive
   observations (at least 2), diagrams.
7. **Present and verdict.** Walk Critical findings first, then High/Medium,
   then positives. Verdict: APPROVE (no Critical, minor High) /
   APPROVE WITH CONDITIONS (no Critical; High goes to a follow-up task) /
   REQUEST CHANGES (Critical exists). On Request Changes: create a follow-up
   task per Critical finding via the `new-task` skill.

## Rules

- NEVER suggest formatting changes - the project formatter owns formatting
- NEVER review unchanged code or suggest unrelated refactoring - diff scope only
- NEVER fabricate issues; NEVER give a finding without location, evidence, and fix
- Contradictions between documents are High severity
- Save the report to `docs/reviews/review-YYYY-MM-DD-<subject>.md` only if asked

## Quality checklist

- [ ] Lenses approved before analysis; only in-scope material reviewed
- [ ] Every finding: lens, severity, location, description, evidence, fix, impact
- [ ] Severity consistent with the definitions table
- [ ] No formatting-only suggestions; at least 2 positive observations
- [ ] Clear verdict stated

## References (read only when needed)

- [references/lenses.md](references/lenses.md) - full lens catalog per subject
  type, severity definitions, report skeleton
- [references/a11y-checklist.md](references/a11y-checklist.md) - WCAG audit
  checklists and tooling (used by accessibility-auditor)
