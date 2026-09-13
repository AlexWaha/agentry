---
name: risk-assessment
description: Identify, score, and mitigate project risks across business, technical, market, legal, and operational categories into a scored risk matrix with mitigation and contingency plans. Use when the CEO asks for a risk assessment, before a funding or investor conversation, when starting a phase with major unknowns, when a "what could go wrong" review is requested, or when a security/compliance audit surfaces new threats.
allowed-tools: Read, Grep, Glob, Write, Bash
---

# Risk Assessment

> STATUS: DEFERRED. Not active in the current project phase (backend API
> only). Will be activated when business planning/analysis work begins. Do
> not invoke until explicitly activated by CEO.

Comprehensive risk identification and analysis covering all risk categories.
Produces a scored risk matrix with mitigation strategies and contingency
plans for the highest risks.

## Steps

1. **Gather project context.** Read `docs/business/product-vision.md`,
   `docs/business/business-plan.md`, `docs/business/revenue-model.md`,
   `docs/technical/architecture.md`, `docs/technical/tech-stack.md`,
   `docs/finance/financial-model.md`, `docs/market/market-research.md`,
   `docs/market/competitive-landscape.md` where they exist. Glob
   `.agentry/tasks/active/*.md` and `.agentry/tasks/done/*.md`, parse each
   file's YAML frontmatter (status, assignee, depends_on, epic, spec) for
   current risk signals such as stalled or blocked tasks. Cross-reference
   `.agentry/tasks/epics/*.md` for epic-level status. Run
   `python .claude/tools/pipeline/state.py --resume` for in-flight pipeline
   state. A missing context file is itself a risk signal (incomplete
   planning) - note it.
2. **Brainstorm risks by category.** Business, Technical, Market, Legal,
   Operational. Use the prompting questions in
   `references/risk-register.md`, then go beyond them based on project
   specifics. Target at least 15-20 risks; no category should end up empty.
3. **Score each risk.** Probability (1-5) x Impact (1-5) = Score (1-25),
   mapped to Low/Medium/High/Critical. Full scale definitions in
   `references/risk-register.md`. Every score needs a one-line
   justification, not an arbitrary number.
4. **Build the risk matrix.** Table sorted by score (highest first), plus a
   heat map (Mermaid quadrant chart or the text form in
   `references/risk-register.md`).
5. **Define mitigation strategies.** For every risk scored Medium or above:
   reduce-probability actions, reduce-impact actions, owner, timeline, cost,
   monitoring signal. Must be specific and actionable, never "monitor the
   situation." Template in `references/risk-register.md`.
6. **Define contingency plans for the top 5 risks.** Trigger, immediate
   response (first 24 hours), short-term response (first week),
   communication plan, recovery timeline/cost, decision point (pivot vs.
   push through). Template in `references/risk-register.md`.
7. **Generate Mermaid diagrams.** Risk heat map, risk category distribution
   (pie), top risks timeline (Gantt). Validate with the Mermaid Chart MCP
   tool if available.
8. **Write the deliverables.** `docs/risk/risk-matrix.md` (register, heat
   map, category distribution, detailed descriptions) and
   `docs/risk/mitigation-plan.md` (mitigation strategies, contingency plans,
   review schedule). Full file templates in `references/risk-register.md`.
9. **Present a summary to the CEO.** Total risks and distribution by
   category/level, top 3 Critical/High risks in one line each, mitigation
   actions needing budget or decision, any currently unmitigable risks, and
   the recommended review cadence (weekly Critical/High, bi-weekly Medium,
   monthly full review). Wait for CEO/CTO approval before finalizing the
   documents.

## Output checklist

- [ ] At least 15-20 risks identified across all 5 categories, none empty
- [ ] Every risk has probability, impact, and score with justification
- [ ] Mitigation strategies are specific and actionable, with owner and timeline
- [ ] Top 5 risks have contingency plans with triggers and timelines
- [ ] Risk scores are internally consistent (similar risks, similar scores)
- [ ] Mermaid diagrams render correctly
- [ ] Both `docs/risk/risk-matrix.md` and `docs/risk/mitigation-plan.md` are complete and cross-referenced
- [ ] Summary presented to CEO with clear action items; approval requested

## References (read only when needed)

- [references/risk-register.md](references/risk-register.md) - risk category
  brainstorming prompts, scoring model, risk matrix/heat map format,
  mitigation and contingency plan templates, deliverable file templates
- [references/security-checklists.md](references/security-checklists.md) -
  threat model document template and security/compliance checklists
