---
name: financial-model
description: Build financial projections - explicit assumptions, revenue model, P&L, cash flow, breakeven, unit economics (CAC/LTV/MRR), three scenarios (best/base/worst), YAML data. Use when the CEO asks for a financial model, revenue/runway projections, pricing analysis, unit economics, or fundraising numbers.
allowed-tools: Read, Grep, Glob, Write, WebSearch, WebFetch
---

# Financial Model - Projections and Analysis

> STATUS: DEFERRED until business planning begins - do not invoke before the
> CEO activates the Business department.

Comprehensive financial model with explicit assumptions, multi-year projections,
unit economics, and scenario analysis. Outputs narrative markdown plus
structured YAML.

## Prerequisites

Ideation, market research, and business plan documents exist in
`docs/business/` and `docs/market/` (product vision, revenue model,
unit economics, TAM/SAM/SOM); pricing tiers and audience segments defined.

## Steps

1. **Read all inputs.** Every file in `docs/business/` and `docs/market/`.
   Extract: tiers and prices, growth assumptions, CAC/LTV estimates, market
   size, competitive pricing, team and infra costs.
2. **Define ALL assumptions explicitly** - the critical step. Fill the
   assumption tables (user growth, conversion/retention, revenue, costs,
   funding) from [references/model-templates.md](references/model-templates.md);
   every row needs base/best/worst values AND a source or reasoning.
3. **Revenue model.** Year 1 monthly walk (signups -> conversions -> churn ->
   MRR -> fees -> net revenue), Years 2-3 quarterly, Years 4-5 annually.
4. **Cost structure.** Infrastructure (scaled per 1K users), team (phased),
   marketing (by channel), operations (+10% buffer).
5. **P&L projection.** Monthly Y1, quarterly Y2-3, annual Y4-5 per the skeleton.
6. **Cash flow.** Burn rate per phase, runway, cumulative cash waterfall,
   minimum cash threshold.
7. **Breakeven analysis.** Contribution margin -> breakeven users/revenue/month,
   sensitivity at +/- 20% on churn, pricing, CAC.
8. **Unit economics.** CAC (by channel + blended), LTV (= ARPU / churn, by
   tier), LTV:CAC (flag if below 3:1), payback period, gross margin (target 70%+).
9. **Funding needs.** Amount, itemized allocation, runway bought, milestones
   enabled, next round timing, investor return projections when applicable.
10. **Three scenarios.** Fully model best/base/worst with the summary table;
    worst case includes the survival plan.
11. **Write deliverables:** `docs/finance/financial-model.md` (narrative),
    `docs/finance/projections.yaml` (structured data),
    `docs/finance/breakeven-analysis.md` (focused breakeven + runway).
12. **Present to CEO:** top-5 assumptions, Y1 base revenue, breakeven month,
    funding ask, unit economics, best/worst spread, biggest risk + mitigation.
    Wait for explicit approval.

## Quality checklist

- [ ] EVERY number traces to an explicit, sourced assumption
- [ ] Y1 monthly, Y2-3 quarterly, Y4-5 annual granularity
- [ ] P&L covers all four cost categories; cash flow shows when cash runs out
- [ ] Breakeven has a sensitivity table; unit economics complete
- [ ] Three fully modeled scenarios; valid YAML with all projection data
- [ ] Funding needs specific; summary presented and approval requested

## References (read only when needed)

- [references/model-templates.md](references/model-templates.md) - assumption
  tables, P&L/YAML skeletons, unit economics formulas, breakeven walk
