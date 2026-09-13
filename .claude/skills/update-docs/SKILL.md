---
name: update-docs
description: Audit and update all project documentation after a phase or significant task batch completes - verify existing docs, create missing ones, keep the changelog current. Use when a phase or epic finishes, when a task is marked done and its deliverable should land in docs/, when numbers or decisions changed and docs/ may be stale, or when the Technical Writer agent is asked to sync documentation with reality.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Update Docs

Systematic process to update all project documentation after a phase is
completed or significant changes are made, so documentation stays in sync
with the actual state of the project. Used by the Technical Writer agent.

## Steps

1. **Identify what changed.** Glob `.agentry/tasks/done/*.md` for recently
   completed tasks and `.agentry/tasks/active/*.md` for in-flight ones; parse
   each file's YAML frontmatter (status, completed, epic, spec) to see what
   was delivered. Cross-reference `.agentry/tasks/epics/*.md` for epic-level
   completion. Run `python .claude/tools/pipeline/state.py --resume` for
   in-flight pipeline state. If a branch was merged, check
   `git log --oneline --since="[phase start date]" main` and
   `git diff --stat [phase-start-commit]..HEAD` for changed files (fall back
   to file modification timestamps via Glob if no git history). Summarize:
   new features, new documents, architecture decisions, config/infra
   changes, business decisions finalized.
2. **Audit documentation directories.** Walk each `docs/` subdirectory and
   check whether the files expected for completed work exist, are current,
   and have valid cross-references. The full expected-file table by phase is
   in `references/doc-templates.md`.
3. **Create missing documents.** For each expected file that does not exist:
   check task files and agent outputs for content that was produced but
   never saved, and create the file from it. If no content exists anywhere,
   create a stub with the TODO template in `references/doc-templates.md` and
   flag the gap for the Orchestrator to turn into a task (do not create
   tasks directly).
4. **Update existing documents.** Read each fully; identify what is
   outdated (changed numbers, revised decisions, evolved architecture,
   added/removed features, shifted timelines). Update the `Date` and
   `Status` fields and only the sections that changed. Do NOT rewrite the
   entire document - preserve the original author's structure and style.
5. **Update the changelog.** Append a dated entry to
   `docs/changelog/CHANGELOG.md` (create it with the standard header from
   `references/doc-templates.md` if missing) using the changelog entry
   template there.
6. **Verify cross-references.** Numbers (pricing, projections, targets),
   terminology, and feature lists must match across every document that
   mentions them. All documents touched in this pass get the current date.
   Check markdown links between documents resolve, and that any Mermaid
   diagrams render (use the Mermaid Chart MCP tool if available). Fix
   inconsistencies in the outdated document; if it is unclear which version
   is correct, flag it for CEO decision rather than deciding.
7. **Generate the documentation status summary.** Use the template in
   `references/doc-templates.md`: documents created, documents updated, gaps
   identified, cross-reference fixes, changelog status.
8. **Present to the CEO.** Show the summary, highlight gaps that need new
   tasks, flag inconsistencies requiring a decision, and confirm all
   documents are current for the completed phase.

## Output checklist

- [ ] Every `docs/` subdirectory was audited against `references/doc-templates.md`
- [ ] All expected documents for completed phases exist, or gaps are flagged
- [ ] Updated documents only changed what was outdated; structure/style preserved
- [ ] Updated documents have current dates
- [ ] Cross-references are consistent (numbers, terminology, features)
- [ ] Mermaid diagrams render correctly; no broken links
- [ ] Changelog entry added for this pass
- [ ] Gaps are clearly flagged with action items, not silently dropped
- [ ] Summary presented to CEO

## References (read only when needed)

- [references/doc-templates.md](references/doc-templates.md) - per-directory
  expected-file table by phase, missing-document stub template, changelog
  entry template, documentation status summary template
