# Business Plan - Section Templates

Full section-by-section templates supporting `../SKILL.md`.

## Executive summary structure

1. Opening hook (1-2 sentences): a striking statement about the problem or
   opportunity.
2. The problem (2-3 sentences): what pain exists, how many people suffer from
   it, what the cost is.
3. The solution (2-3 sentences): what the product does, what makes it unique.
4. Market opportunity (2-3 sentences): TAM/SAM/SOM headline numbers, growth
   rate.
5. Business model (2-3 sentences): how the product makes money, pricing
   tiers, unit economics headline.
6. Traction / validation (2-3 sentences): early signals, waitlist numbers,
   MVP feedback. If pre-launch, describe the validation approach instead.
7. The team (2-3 sentences): key capabilities and why this team can execute.
8. The ask (2-3 sentences): funding needed, use of funds, expected outcome.

## Problem statement template

1. **Primary pain point**: describe in vivid detail what users struggle with
   today.
2. **Secondary pain points**: 3-5 compounding problems.
3. **Current alternatives and their failures**: for each alternative
   (competitor or workaround) explain what it is, how people use it, why it
   partially works, why it ultimately falls short, and what users wish it did
   differently.
4. **Cost of the problem**: quantify in time, money, frustration, health
   impact, missed goals.
5. **Why now**: what changed (technology, market, behavior, regulation) that
   makes this the right time to solve it.

## Solution template

1. Product overview: one paragraph.
2. Key features (5-8): name, description, problem solved, persona that
   benefits most.
3. Product-market fit hypothesis: why this fits the target market better than
   alternatives.
4. Technology enablers: what makes the solution possible now (high level).
5. Unique selling proposition: the one thing that makes it fundamentally
   different.
6. Product roadmap overview: Phase 1 (MVP core features), Phase 2 (growth:
   engagement/retention features), Phase 3 (scale: market-expanding features).

## Market analysis summary template

1. Market size summary: TAM, SAM, SOM with one-line explanation each.
2. Market growth: CAGR and key growth drivers.
3. Target segments: priority segments with size estimates.
4. Competitive landscape summary: top 3 competitors and our advantage over
   each.
5. Market trends: 3-5 trends working in our favor.
6. Market risks: 2-3 risks to be aware of.

Reference the full market research documents for detailed analysis instead of
repeating them here.

## Business model template

**Revenue streams** - for each: description of the source, pricing structure
(exact numbers), target segment, revenue potential (percent of total revenue
year 1 vs year 3), and justification (competitor benchmarks, willingness to
pay research, value-based reasoning).

Example primary stream shape: subscription model with Tier 1 (price, limits,
segment), Tier 2 (price, limits, segment), and a free tier (what's included,
purpose: conversion funnel or viral growth).

Example secondary streams: partner commissions/referral fees, premium
templates or content, enterprise/team plans, API access for integrations.

**Pricing rationale**: competitor pricing comparison table; value-based
pricing analysis per persona; price sensitivity at +/-30% change; pricing
evolution plan as features grow.

**Revenue projections summary** (detailed model lives in the
`financial-model` skill):
- Month 6: X users, Y% paid, $Z MRR
- Month 12: X users, Y% paid, $Z MRR
- Month 24: X users, Y% paid, $Z MRR
- Month 36: X users, Y% paid, $Z MRR

## Go-to-market summary template

**Launch strategy phases:**
1. Pre-launch (2-3 months before): landing page with email capture, social
   presence building, community engagement, beta tester recruitment, press
   outreach prep.
2. Launch (week 1-4): launch platform submission, press release, influencer
   partnerships, social campaign, waitlist email, App Store Optimization.
3. Post-launch (month 2-6): content marketing ramp-up, SEO, paid acquisition
   testing, referral program activation, community building.

**Early traction plan**: week-by-week activities for the first 12 weeks,
budget allocation per week, expected results per week, decision points (what
to double down on, what to cut) to reach the first 1,000 paying users.

Full channel-by-channel detail (organic, paid, partnership, referral
mechanics, budget split, content calendar) belongs in the `gtm-strategy`
skill output - link to it rather than duplicating.

## Operations plan template

**Team requirements table:**

| Role | When Needed | Full-time/Part-time | Estimated Cost |
|------|-------------|---------------------|----------------|
| [Role] | [Timeline] | [FT/PT/Contract] | [$X/month] |

Phases: pre-launch team (minimal), launch team, growth team (month 6+), scale
team (year 2+).

**Infrastructure**: hosting/cloud services (estimated monthly cost),
third-party services (payment processing, email, SMS, analytics),
development tools and licenses, any physical/partner fulfillment costs.

**Key partnerships**: for each - partner type, value exchange (what we offer,
what they offer), priority (critical/high/nice-to-have), timeline to
establish.

## Milestones and timeline template

12-month roadmap in 5 phases, each with specific/measurable/time-bound
milestones and KPIs:

- **Month 1-2: Foundation** - milestones + KPIs
- **Month 3-4: MVP Launch** - milestones + KPIs
- **Month 5-6: Growth Initialization** - milestones + KPIs
- **Month 7-9: Product-Market Fit Validation** - milestones + KPIs
- **Month 10-12: Scaling** - milestones + KPIs

Each milestone must be specific (not vague), measurable (has a number or
binary yes/no), time-bound (assigned to a specific month), and aligned with
business goals.

## Risks and mitigations template

| Risk | Category | Probability | Impact | Mitigation Strategy | Contingency Plan |
|------|----------|-------------|--------|---------------------|------------------|

Categories: Market, Technical, Financial, Operational, Legal/Regulatory,
Competitive. Identify 8-12 risks; give the top 3 a one-paragraph detailed
mitigation plan each.

## Output file structures

**`docs/business/business-plan.md`** - full document containing, in order:
Executive Summary, Problem Statement, Solution, Market Analysis, Business
Model, Go-to-Market Strategy, Operations Plan, Milestones and Timeline, Key
Risks and Mitigations.

**`docs/business/revenue-model.md`** - revenue streams, pricing rationale,
revenue projections, pricing evolution plan, revenue stream diversification
timeline.

**`docs/business/unit-economics.md`** - Customer Acquisition Cost (by channel
and blended), Lifetime Value (by tier and blended), LTV:CAC ratio (target
3:1+), payback period, monthly churn rate (target and benchmarks), gross
margin analysis, contribution margin per user per month, breakeven point
(paying users needed), and a sensitivity analysis (churn 2x, CAC 1.5x,
conversion 0.5x).

## CEO summary format

When presenting for approval, cover: executive summary in 3 sentences,
business model (revenue streams and pricing), go-to-market (primary channels
and early traction plan), 12-month milestones (top 5 most critical), funding
need (how much, for what, runway), top 3 risks, unit economics headline (CAC,
LTV, LTV:CAC ratio), then request explicit approval.
