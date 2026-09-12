---
name: gtm-strategy
description: Produce a go-to-market strategy - target segments, channel selection and budget, referral system mechanics, launch timeline, and KPI targets - plus a supporting marketing plan (content, email, social, SEO, paid). Use when the CEO asks for a go-to-market plan, launch strategy, marketing channel plan, referral/growth loop design, or marketing budget allocation, or when revenue-model and unit-economics docs are ready and need a channel plan to hit them.
allowed-tools: Read, Grep, Glob, Write
---

# GTM Strategy

> STATUS: DEFERRED. Not active in current project phase (backend API only).
> Will be activated when business planning/analysis work begins. Do not
> invoke until explicitly activated by CEO.

Comprehensive go-to-market strategy: target segments, channel selection,
referral mechanics, budget, timeline, KPIs, and content plan. Full segment
profile templates, channel/budget/timeline tables, referral system design,
and content calendar templates live in `references/gtm-templates.md`.

## Prerequisites

Read before starting: `docs/business/user-personas.md`,
`docs/market/market-research.md`, `docs/market/positioning.md`,
`docs/business/revenue-model.md`, `docs/business/unit-economics.md`. If any
are missing, note the data gap, flag it as a risk, and proceed with clearly
marked assumptions.

## Steps

1. **Read existing research.** Personas (who, behaviors, pain points, where
   they spend time online), market research (TAM/SAM/SOM, growth, landscape),
   positioning (messaging framework), pricing tiers and conversion funnel
   assumptions, target CAC/LTV/payback/churn.
2. **Define target segments.** At least 3 primary and 2 secondary, each with
   priority, size estimate, geography, profile (age, pain intensity,
   willingness to pay, tech savviness, device preference), acquisition
   channel affinity, and messaging angle (hook, key benefit, objection,
   counter). Rank by size x willingness to pay x acquisition difficulty x
   fit with the product's USP. Template: `references/gtm-templates.md`.
3. **Select and prioritize channels.** Evaluate organic (SEO, content,
   social, community, ASO), paid (search, display, social, video), and
   partnership channels. For each: strategy, effort/budget, timeline to
   results, expected CAC/CPA, and (for paid) audience config, creative/A-B
   test plan, bid strategy, scale-up criteria.
4. **Design the referral/growth loop if the product has one.** How it works
   end to end, reward tiers, tracking (referral code, attribution window,
   fraud prevention), a referral dashboard, and the viral loop metrics
   (K-factor, scan/conversion rate, time to first action).
5. **Allocate budget.** Total monthly budget per phase (pre-launch, launch,
   early growth, scale, maturity) and the channel split within each phase.
   Every number must tie back to unit economics: total spend / new
   subscribers = blended CAC, which must stay below LTV.
6. **Build the 12-month timeline.** Pre-launch weekly plan (weeks -8 to 0)
   plus monthly milestones with target users, MRR, and key actions.
7. **Define KPIs.** Per-channel primary KPI and target, full funnel metrics
   (awareness through retention through revenue), and north star metrics.
8. **Write the content plan.** Blog calendar for the first 3 months, evergreen
   content pillars, email sequences (trigger, number of emails, goal) for
   welcome/activation/conversion/win-back/referral/upgrade, and a social
   media posting cadence per platform.
9. **Write the output documents.** `docs/marketing/gtm-strategy.md` (segments,
   channels, referral system, budget, timeline, KPIs, risks) and
   `docs/marketing/marketing-plan.md` (content strategy, email marketing,
   social plan, SEO, paid advertising, partnerships, tools, team).
10. **Present a summary to the CEO and wait for approval.** Beachhead segment
    and primary channel, top 3 channels by expected ROI, referral K-factor
    target, total budget and blended CAC target, 3/6/12-month user and MRR
    targets, and the decisions that need CEO input (budget, channel
    priority, referral rewards, launch date).

## Output checklist

- [ ] At least 3 primary and 2 secondary segments defined with full profiles
- [ ] At least 8 channels evaluated with structured assessments
- [ ] Referral/growth loop fully designed (mechanics, rewards, tracking, fraud prevention)
- [ ] Budget numbers are internally consistent and tie to unit economics
- [ ] CAC projections are realistic and below LTV
- [ ] 12-month timeline has specific milestones with numeric targets
- [ ] KPIs have specific targets and measurement frequency
- [ ] Content plan covers at least 3 months, email sequences defined
- [ ] Both output files complete
- [ ] Summary presented to CEO with clear decision points, approval requested

## References (read only when needed)

- [references/gtm-templates.md](references/gtm-templates.md) - segment
  profile template, channel assessment tables (organic/paid/partnership),
  referral system mechanics and rewards tables, budget allocation tables,
  12-month timeline table, KPI/funnel tables, content calendar and email
  sequence templates, output file skeletons
