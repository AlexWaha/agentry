# GTM Strategy Templates

Full templates supporting `../SKILL.md`. Examples use a hypothetical
habit-tracking app with a print-and-QR referral loop; adapt to the actual
product.

## Segment profile template

```markdown
### Segment: [Name]

**Priority:** Primary / Secondary / Future
**Size Estimate:** [number of potential users with source/reasoning]
**Geography:** [primary markets]

**Profile:**
- Age: [range]
- Key characteristic: [what defines this segment]
- Pain intensity: [1-10 with justification]
- Willingness to pay: [$/mo range]
- Tech savviness: [low/medium/high]
- Device preference: [mobile-first / web-first / both]

**Acquisition Channel Affinity:**
- Where they discover new apps: [channels]
- Who influences their decisions: [influencers, communities, friends]
- What content resonates: [content types and topics]
- Purchase triggers: [what makes them subscribe]

**Messaging Angle:**
- Primary hook: [one sentence that would grab their attention]
- Key benefit: [what value proposition resonates most]
- Objection: [main reason they would NOT buy]
- Objection counter: [how to address it]
```

Define at least 3 primary and 2 secondary segments. Rank by priority based on
market size x willingness to pay x acquisition difficulty, alignment with the
product USP, and potential for viral/referral growth.

## Channel assessment tables

### Organic channels

| Channel | Strategy | Effort | Timeline to Results | Expected CAC |
|---------|----------|--------|-------------------|-------------|
| SEO | [specific strategy] | [hours/week] | [months] | [$/user] |
| Content Marketing | [specific strategy] | [hours/week] | [months] | [$/user] |
| Social Media (organic) | [specific strategy] | [hours/week] | [months] | [$/user] |
| Community Building | [specific strategy] | [hours/week] | [months] | [$/user] |
| App Store Optimization | [specific strategy] | [hours/week] | [months] | [$/user] |

For SEO define: target keywords (10-20 with monthly search volume), content
pillar topics (3-5 themes), link building strategy, technical SEO
requirements.

For content marketing define: blog post frequency and topics (first 3 months
editorial calendar), content formats (blog, templates, infographics, video),
distribution strategy per piece, repurposing plan (blog -> social -> email ->
video).

For social media define: platform priority and order, content style per
platform, posting frequency per platform, engagement strategy (comments,
DMs, community management).

### Paid channels

| Channel | Strategy | Min Budget/mo | Expected CPA | ROAS Target |
|---------|----------|-------------|-------------|-------------|
| Google Ads (Search) | [specific strategy] | [$] | [$] | [X:1] |
| Google Ads (Display) | [specific strategy] | [$] | [$] | [X:1] |
| Meta Ads (FB+IG) | [specific strategy] | [$] | [$] | [X:1] |
| TikTok Ads | [specific strategy] | [$] | [$] | [X:1] |
| Apple Search Ads | [specific strategy] | [$] | [$] | [X:1] |

For each paid channel define: target audience configuration (demographics,
interests, behaviors), ad creative strategy (formats, messaging, A/B test
plan), budget allocation (daily/monthly), bid strategy, conversion tracking
setup, scale-up criteria (metrics that trigger a budget increase).

### Partnership channels

| Partner Type | Strategy | Expected Reach | Cost Model |
|-------------|----------|---------------|------------|
| Fitness Apps | [cross-promotion plan] | [user base] | [rev share / flat fee] |
| Productivity Tools | [integration plan] | [user base] | [free / rev share] |
| Niche Communities | [sponsorship plan] | [community size] | [sponsorship cost] |
| Micro-influencers | [outreach plan] | [follower range] | [per post / affiliate] |
| Fulfillment Partners | [partnership plan] | [customer base] | [rev share] |

Referral channel: the growth loop (see below), word-of-mouth amplification
tactics, and in-app share mechanics.

## Referral/growth loop design

**How it works (example: print + QR loop):**

```
1. User creates a resource and prints it
2. Printed sheet has a QR code in the footer
3. QR code links to: https://app.example.com/c/{resource_id}?ref={user_referral_code}
4. Someone sees the printed sheet (on fridge, office wall, gym)
5. They scan the QR code with their phone
6. Landing page shows the resource and prompts signup
7. New user signs up -> referral attributed to the sheet owner
8. Both users receive rewards
```

**Referral rewards structure:**

| Tier | Referrals | Referrer Reward | New User Reward |
|------|-----------|----------------|-----------------|
| Bronze | 1-3 | 1 month free on current plan | 14-day trial (vs. standard 7-day) |
| Silver | 4-10 | 1 month free + upgrade to Pro for 1 month | 14-day trial + 20% off first month |
| Gold | 11-25 | 2 months free + permanent 20% discount | 30-day trial + 30% off first 3 months |
| Platinum | 26+ | Free Pro for life (while referrals stay active) | 30-day trial + 50% off first 3 months |

**Referral tracking:**
- Unique referral code per user (short, memorable: 6-8 alphanumeric characters)
- Referral URL includes: `ref` (referral code), `utm_source`, `utm_medium`, `utm_campaign`
- Attribution window: 30 days from first touch to signup
- Fraud prevention: one referral per unique email, rate limit per user per
  day, IP-based dedup

**Referral dashboard:** total referrals, active referrals, rewards earned,
current tier, shareable referral link (for digital sharing beyond the QR
code), referral activity timeline.

**Viral loop:**
```
User creates resource -> shares/prints it -> visible to others
-> someone engages -> signs up -> creates their own resource
-> shares/prints theirs -> visible to new people -> repeat
```

Key metrics: scan/engagement rate per shared unit, engagement-to-signup
conversion rate, viral coefficient (K-factor: referrals per user), time from
share to first engagement, geographic spread of referrals (organic check).

## Budget allocation tables

**Monthly budget by phase:**

| Phase | Months | Monthly Budget | Primary Channels |
|-------|--------|---------------|-----------------|
| Pre-Launch | -2 to 0 | $[X] | Content creation, community seeding, landing page |
| Launch | 1 | $[X] | PR, paid ads burst, influencer activation |
| Growth (Early) | 2-4 | $[X] | Paid ads optimization, content scaling, partnerships |
| Growth (Scale) | 5-8 | $[X] | Scaled paid, referral program promotion, ASO |
| Maturity | 9-12 | $[X] | Retention, brand, community, reduced paid |

**Channel budget split (percentage and absolute $ per phase):**

| Channel | Pre-Launch | Launch | Early Growth | Scale | Maturity |
|---------|-----------|--------|-------------|-------|----------|
| Content/SEO | X% ($X) | X% ($X) | X% ($X) | X% ($X) | X% ($X) |
| Paid Ads | X% ($X) | X% ($X) | X% ($X) | X% ($X) | X% ($X) |
| Social Media | X% ($X) | X% ($X) | X% ($X) | X% ($X) | X% ($X) |
| Influencers | X% ($X) | X% ($X) | X% ($X) | X% ($X) | X% ($X) |
| Partnerships | X% ($X) | X% ($X) | X% ($X) | X% ($X) | X% ($X) |
| Referral Program | X% ($X) | X% ($X) | X% ($X) | X% ($X) | X% ($X) |
| Tools/Software | X% ($X) | X% ($X) | X% ($X) | X% ($X) | X% ($X) |

All budget numbers must tie back to unit economics: total marketing spend /
expected new subscribers = blended CAC, which must be below LTV.

## Timeline template

**Pre-launch (weeks -8 to 0):**
- Week -8: brand identity finalized, landing page live
- Week -6: content creation begins (blog, social), SEO foundation
- Week -4: waitlist campaign, community seeding
- Week -2: beta tester recruitment, influencer outreach
- Week -1: PR materials prepared, press outreach begins
- Launch day: coordinated announcement across all channels

**Post-launch (months 1-12):**

| Month | Milestone | Target Users | Target MRR | Key Actions |
|-------|-----------|-------------|-----------|------------|
| 1 | Launch | [N] | $[X] | PR burst, paid ads start, beta feedback loop |
| 2 | Iterate | [N] | $[X] | Optimize based on data, double down on best channel |
| 3 | Referral | [N] | $[X] | Launch referral program, measure K-factor |
| 4-6 | Scale | [N] | $[X] | Scale winning channels, add partnerships |
| 7-9 | Expand | [N] | $[X] | New segments, international expansion prep |
| 10-12 | Mature | [N] | $[X] | Reduce CAC, increase retention, brand building |

## KPI and funnel tables

**Per-channel KPIs:**

| Channel | Primary KPI | Target | Measurement Frequency |
|---------|------------|--------|----------------------|
| SEO | Organic traffic | [X] visits/mo by M6 | Weekly |
| Content | Email subscribers | [X] by M3 | Weekly |
| Paid Ads | CPA | < $[X] | Daily |
| Social | Engagement rate | [X]% | Weekly |
| Referral | K-factor | > [X] | Weekly |
| App Store | Conversion rate | [X]% | Weekly |
| Partnerships | Referred users | [X]/mo | Monthly |

**Funnel metrics:**

| Stage | Metric | Target | Current |
|-------|--------|--------|---------|
| Awareness | Website visits | [X]/mo | - |
| Interest | Signup rate | [X]% | - |
| Activation | First resource created | [X]% of signups | - |
| Revenue | Free-to-paid conversion | [X]% | - |
| Retention | 30-day retention | [X]% | - |
| Referral | Referral rate | [X]% of paid users | - |
| Revenue | Monthly churn | < [X]% | - |

**North star metrics (example):** primary (e.g. weekly active resources),
secondary (e.g. physical prints per month, if relevant), revenue (MRR).

## Content plan templates

**Blog content calendar (first 3 months):**

| Week | Topic | Target Keyword | Format | Distribution |
|------|-------|---------------|--------|-------------|
| 1 | [topic] | [keyword] | [blog/video/infographic] | [channels] |
| 2 | [topic] | [keyword] | [format] | [channels] |
| ... | ... | ... | ... | ... |

**Content pillars (evergreen themes, example):**
1. Domain science (research-backed tips relevant to the product)
2. Productivity/usage systems (routines, planning methods)
3. Template/asset galleries (showcase what the product produces)
4. Success stories (testimonials, before/after)
5. Product tutorials (how to use specific features)

**Email sequences:**

| Sequence | Trigger | Emails | Goal |
|----------|---------|--------|------|
| Welcome | Signup | 5 emails over 7 days | Activate (create first resource) |
| Activation | First resource created | 3 emails over 5 days | Complete first key action |
| Conversion | Trial ending | 3 emails over 3 days | Convert to paid |
| Win-back | 14 days inactive | 3 emails over 7 days | Re-engage |
| Referral | 30 days active + paid | 2 emails over 3 days | Generate referrals |
| Upgrade | 3 months on base tier | 2 emails over 5 days | Upgrade to higher tier |

For each sequence, outline the key message of each email (subject line + one
sentence summary).

**Social media calendar:** posting frequency per platform, content mix ratio
(educational : promotional : community : entertainment), hashtag strategy
per platform, engagement response time target.

## Output file skeletons

**`docs/marketing/gtm-strategy.md`:**

```markdown
# Go-to-Market Strategy

**Date:** [date]
**Status:** Draft

## Executive Summary
[2-3 paragraph overview of the entire GTM strategy]

## Target Segments
[All segments with full profiles]

## Channel Strategy
[All channels with assessments]

## Referral System
[Complete referral mechanics]

## Budget
[All budget tables]

## Timeline
[12-month plan]

## KPIs
[All metrics and targets]

## Risks
[Marketing-specific risks and mitigations]
```

**`docs/marketing/marketing-plan.md`:**

```markdown
# Marketing Plan

**Date:** [date]
**Status:** Draft

## Content Strategy
[Content pillars, blog calendar]

## Email Marketing
[All email sequences]

## Social Media Plan
[Platform strategy, posting calendar]

## SEO Strategy
[Keyword targets, content pillar mapping]

## Paid Advertising
[Channel configs, creative strategy, budgets]

## Partnerships
[Partnership pipeline and outreach plan]

## Tools and Infrastructure
[Marketing tools needed: analytics, email, social scheduling, ad platforms]

## Team Requirements
[Who executes what - roles and time allocation]
```
