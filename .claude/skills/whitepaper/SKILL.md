---
name: whitepaper
description: Produce a comprehensive technical and business whitepaper (vision, market context, solution, competitive advantages, monetization, roadmap) that bridges strategy and product for investors, partners, and strategic stakeholders. Use when the CEO asks for a "whitepaper", an investor deck's narrative backbone, a strategic overview combining market and product, or when business/market/financial docs are complete and need synthesis into one document.
allowed-tools: Read, Grep, Glob, Write, WebSearch, WebFetch
---

# Whitepaper

> STATUS: DEFERRED. Not active in current project phase (backend API only).
> Will be activated when business planning/analysis work begins. Do not
> invoke until explicitly activated by CEO.

Synthesizes business strategy, market research, and product vision into one
cohesive narrative document for investors and partners, without exposing
implementation details.

## Steps

1. **Gather inputs.** Read every existing doc before drafting anything:
   `docs/business/*` (product vision, target audience, personas, business
   plan, revenue model), `docs/market/*` (market research, competitive
   landscape, positioning), `docs/finance/*` (financial model,
   projections.yaml), `docs/product/*` and `docs/technical/*` if present,
   `basic-idea.txt`, and `CLAUDE.md` for stack context. If a prerequisite is
   missing, flag it before proceeding rather than inventing numbers.
2. **Draft Vision and Mission.** Vision (5-10 year horizon, aspirational but
   believable), mission (specific, actionable, customer-focused), 3-5 core
   values with a one-sentence rationale each. Full template:
   `references/whitepaper-template.md`.
3. **Draft Market Context and Opportunity.** Macro trend, TAM/SAM/SOM,
   growth drivers, the gap in current solutions, timing ("why now"), evidence
   of demand. Synthesize into a narrative, never copy-paste
   market-research.md.
4. **Draft The Problem.** Core problem paragraph, a human story grounded in
   persona details, statistics (prevalence, success rates, economic and
   emotional cost), why current solutions fail, the missing piece that sets
   up the product as the answer.
5. **Draft Our Solution.** Product overview for a non-technical audience,
   5-8 key features (name, one-line description, problem solved, why it
   matters, what's unique), the one differentiating innovation explained in
   depth, a full user journey (discovery through advocacy), platform
   strategy.
6. **Draft Technology Overview at a strategic level only** - no code, no
   implementation details. Architecture philosophy, stack choices and why
   each was picked, a 3-stage scalability plan, data security/privacy/
   compliance, any defensible technical innovation.
7. **Draft Competitive Advantages.** Top-5 feature comparison matrix,
   competitive moats (network effects, data advantage, brand, switching
   costs, technology barriers), likely competitor response and counter,
   first-mover advantages if applicable.
8. **Draft Monetization Strategy.** Pricing philosophy, subscription tiers,
   free tier strategy and paid conversion, revenue diversification beyond
   subscriptions, unit economics summary (CAC, LTV, margin), Year 1-3
   projection, path to profitability.
9. **Draft Product Roadmap.** Phased plan - MVP, Growth, Expansion, Scale,
   Platform - each with features, timeline, milestones, success metrics, and
   dependencies. Full phase breakdown: `references/whitepaper-template.md`.
10. **Draft Team and Execution.** Team philosophy, current founding-team
    capabilities, key hires in priority order, advisory needs, execution
    track record.
11. **Draft Conclusion and Call to Action.** 2-3 sentence recap of the
    opportunity, strongest argument for the product/team/timing, explicit
    call to action (invest, partner, join beta, schedule a meeting).
12. **Assemble the document** at `docs/product/whitepaper.md` following the
    full skeleton (title, version, date, status, table of contents, numbered
    sections 1-10, references list, appendix) in
    `references/whitepaper-template.md`.
13. **Cite every data point** back to its source document; never state a
    number without a reference.
14. **Present a summary to the CEO** and wait for explicit approval before
    considering the task done: document structure overview, a 4-sentence
    narrative (problem -> solution -> market -> monetization), strongest
    selling point, weakest section or data gap, recommended audience
    (investors, partners, or both).

## Output checklist

- [ ] All prerequisite docs read; missing inputs flagged before drafting
- [ ] Vision and mission are inspiring but realistic; 3-5 genuine core values
- [ ] Market context reads as a story, not a numbers dump
- [ ] Problem section makes the pain concrete and felt
- [ ] Every problem raised is answered somewhere in the solution section
- [ ] Technology overview is accessible to non-technical readers; no code or
      implementation details exposed
- [ ] Competitive advantages include a moats/defensibility analysis
- [ ] Monetization strategy has a clear path to profitability
- [ ] Roadmap is phased with concrete milestones per phase
- [ ] Every key number cites its source document
- [ ] Document reads as one narrative, not a stack of disconnected sections
- [ ] Table of contents and references list are accurate and complete
- [ ] CEO summary presented and explicit approval requested

## References (read only when needed)

- [references/whitepaper-template.md](references/whitepaper-template.md) -
  full section-by-section outline with sub-bullets for every step, plus the
  assembled document skeleton
