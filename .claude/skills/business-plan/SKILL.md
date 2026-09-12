---
name: business-plan
description: Produce a full business plan (executive summary, problem/solution, market analysis, business model, go-to-market, operations, milestones, risks, unit economics) synthesizing prior ideation and market research into one strategy document. Use when the CEO asks for a business plan, investor-ready strategy doc, revenue model writeup, or unit economics analysis, or when ideation and market research phases are complete and need to be turned into a cohesive plan.
allowed-tools: Read, Grep, Glob, Write
---

# Business Plan

> STATUS: DEFERRED. Not active in current project phase (backend API only).
> Will be activated when business planning/analysis work begins. Do not
> invoke until explicitly activated by CEO.

Synthesizes ideation and market research documents into an investor-ready
business plan. Section-by-section templates and full output examples live in
`references/plan-template.md`.

## Prerequisites

Read before starting: `docs/business/product-vision.md`,
`docs/business/target-audience.md`, `docs/business/user-personas.md`,
`docs/market/market-research.md`, `docs/market/competitive-landscape.md`,
`docs/market/positioning.md`, and `basic-idea.txt` or CLAUDE.md for product
summary and pricing context.

## Steps

1. **Read all existing documentation.** Every file in `docs/business/` and
   `docs/market/`, plus `basic-idea.txt` and any `docs/finance/` files.
   Summarize key data points: problem/solution, TAM/SAM/SOM, top competitors
   and differentiators, target segments, pricing tiers, USP.
2. **Write the executive summary.** 1-2 pages: hook, problem, solution,
   market opportunity, business model, traction/validation, team, the ask.
   This is read first by investors - make it compelling and data-backed.
3. **Write the problem statement.** Primary and secondary pain points,
   current alternatives and why they fail, quantified cost of the problem,
   why now.
4. **Write the solution section.** Product overview, key features mapped to
   problems and personas, product-market fit hypothesis, USP, high-level
   roadmap (MVP, growth, scale phases).
5. **Summarize market analysis.** Pull from existing market research: TAM/SAM/SOM,
   growth drivers, target segments, top competitors and our edge, trends,
   risks. Reference the full documents rather than repeating them.
6. **Define the business model.** Revenue streams with pricing and
   justification, pricing rationale (competitor comparison, value-based
   analysis, sensitivity to price change), and high-level revenue
   projections at month 6/12/24/36.
7. **Summarize go-to-market strategy.** Launch phases (pre-launch, launch,
   post-launch), prioritized channels, and an early traction plan for the
   first 1,000 paying users. Full channel/campaign detail belongs in the
   `gtm-strategy` skill; here keep it to a business-plan-level summary.
8. **Write the operations plan.** Team requirements by phase with cost
   estimates, infrastructure and third-party service costs, key
   partnerships with value exchange and priority.
9. **Build the 12-month milestone timeline.** Each milestone specific,
   measurable, time-bound, tied to a KPI, grouped into 5 phases (foundation,
   MVP launch, growth init, PMF validation, scaling).
10. **Identify risks and mitigations.** 8-12 risks across market, technical,
    financial, operational, legal/regulatory, competitive categories; detailed
    mitigation for the top 3.
11. **Write the three output documents.** `docs/business/business-plan.md`
    (steps 2-10 combined), `docs/business/revenue-model.md` (revenue streams,
    pricing rationale, projections), `docs/business/unit-economics.md` (CAC,
    LTV, LTV:CAC ratio, payback period, churn, gross margin, breakeven,
    sensitivity analysis).
12. **Present a summary to the CEO and wait for approval.** Executive summary
    in 3 sentences, business model, GTM channels and early traction, top 5
    milestones, funding ask, top 3 risks, unit economics headline. Never
    proceed past this without explicit approval.

## Output checklist

- [ ] Executive summary compelling, fits 1-2 pages
- [ ] Problem statement vivid and quantified
- [ ] Solution maps features to problems
- [ ] Market analysis references existing research (not copy-pasted)
- [ ] Revenue model has exact pricing with justification
- [ ] Go-to-market has a concrete early traction plan
- [ ] Operations plan has realistic team and cost estimates
- [ ] All 12 months have specific, measurable milestones
- [ ] At least 8 risks identified with mitigations
- [ ] Unit economics calculated with explicit assumptions, LTV:CAC analyzed
- [ ] All three output documents created
- [ ] Summary presented to CEO, approval requested

## References (read only when needed)

- [references/plan-template.md](references/plan-template.md) - full
  section-by-section templates (executive summary, problem, solution, market
  analysis, business model, GTM, operations, milestones, risks) and the three
  output file structures
