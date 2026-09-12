---
name: ideation
description: Structured brainstorm that produces a product vision, target audience analysis, and user personas from a raw idea. Use when a task says "define the product", "brainstorm the concept", "figure out who this is for", when starting a new product from a basic-idea.txt, or before market research or feature scaffolding can begin.
allowed-tools: Read, Grep, Glob, Write
---

# Ideation - Product Definition Brainstorm

> STATUS: DEFERRED. Not active in current project phase (backend API only). Will be activated when business planning/analysis work begins. Do not invoke until explicitly activated by CEO.

Structured process to define the product from scratch or refine an existing concept, producing three deliverables: product vision, target audience analysis, and user personas.

## Steps

1. **Gather existing context.** Read `basic-idea.txt` (the CEO's raw vision) if present, plus any files in `docs/business/`, `docs/market/`, `docs/product/`, and the product summary in CLAUDE.md. Summarize what is defined, what is missing, what needs validation.
2. **Define the core problem.** Write a sharp one-sentence primary problem, 3-5 secondary problems that compound it, the current alternatives and why they fail, quantify the pain if possible, and frame it as a "before" story - a day in the life of someone suffering from it.
3. **Propose the solution and USP.** One-paragraph elevator pitch, 5-8 core features each mapped to a problem from Step 2, a genuinely differentiated USP (never "better/faster/cheaper"), an "after" story, and a one-line description someone could repeat to a friend.
4. **Define target audience segments.** 3-5 segments, each covering demographics, psychographics, behaviors, pain intensity, willingness to pay, and acquisition channel. Rank by priority, name the beachhead market, and estimate segment sizes. Full dimension checklist: references/frameworks.md.
5. **Create user personas.** 3-5 personas, each with identity, profile, goals, pain points, behavioral patterns, a first-week usage scenario, and subscription likelihood. Full persona template: references/frameworks.md.
6. **Define success metrics (KPIs).** Acquisition, engagement, revenue, and product-specific metrics, each with a numeric target and timeframe, plus one justified North Star metric. Full metric checklist: references/frameworks.md.
7. **Write the three deliverables**: `docs/business/product-vision.md`, `docs/business/target-audience.md`, `docs/business/user-personas.md`. File skeletons: references/frameworks.md.
8. **Present a summary to the CEO/CTO**: problem, solution, USP, beachhead segment, and North Star metric in one sentence each, plus the top 3 risks or assumptions needing validation. Wait for explicit approval before moving to market research or any later phase.

## Output checklist

- [ ] Problem statement is specific and quantified, not vague
- [ ] USP is genuinely differentiated, not incremental
- [ ] Each segment covers demographics, psychographics, and behaviors
- [ ] Each persona feels like a real person, not a demographic bucket
- [ ] Every KPI has a numeric target and timeframe; North Star metric defined and justified
- [ ] Features map directly to problems, no orphan features
- [ ] All three documents created in `docs/business/`
- [ ] Summary presented to CEO and approval requested

## References (read only when needed)

- [references/frameworks.md](references/frameworks.md) - full segment dimension list, persona template, KPI category checklist, and deliverable file skeletons
