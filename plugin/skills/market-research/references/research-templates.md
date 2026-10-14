# Market Research Templates

Calculation templates and matrices referenced by the `market-research` skill. Every number in the final documents must have a source URL or be marked "[Estimate: reasoning]".

## TAM / SAM / SOM

**Total Addressable Market (TAM)**

1. Search the overall market size of the broader category (e.g. "habit tracking app market size", "productivity app market").
2. Cross-validate with at least 3 different market research reports or sources.
3. Document: market size in USD (current year), growth rate (CAGR), projected size in 3-5 years, geographic breakdown (global, US, EU, other key markets), source URL per data point.
4. Calculate top-down (total market size for the broad category) and bottom-up (total potential users globally x annual revenue per user).
5. Reconcile the two - if they differ significantly, explain why and which is more trustworthy.

**Serviceable Available Market (SAM)**

1. Narrow TAM to the segments actually servable given product type, language, geography, and platform.
2. Apply filters: geographic reach (Year 1 vs Year 3), platform availability, language, pricing tier compatibility.
3. Document the math: TAM x (filter percentages) = SAM. Source each filter percentage or mark as "[Estimate: reasoning]".

**Serviceable Obtainable Market (SOM)**

1. Estimate realistic market capture in Years 1, 2, 3, factoring in marketing budget/channel reach, competitive intensity, product maturity, free-to-paid conversion, and benchmarks from similar products.
2. Express as: number of users (free + paid), number of paying subscribers, revenue (MRR and ARR).
3. Document all assumptions explicitly.

**Market Size Summary Table**

| Metric | Value | Source | Year |
|--------|-------|--------|------|
| TAM (Top-down) | $X.XB | [source] | 202X |
| TAM (Bottom-up) | $X.XB | [calculation] | 202X |
| SAM | $XXXm | [calculation] | 202X |
| SOM Year 1 | $X.Xm | [estimate] | 202X |
| SOM Year 3 | $XXm | [estimate] | 202X |
| Market CAGR | XX% | [source] | 202X-203X |

## Competitor Matrix

**Identify competitors**: direct (same problem, same approach), indirect (different approach, same problem), potential future entrants (adjacent products). For each, gather: company name and URL, founding year, funding raised, estimated user base/revenue, pricing model and tiers, key features, platform availability, notable strengths and weaknesses, market positioning.

**Comparison table** (5-8 competitors, 10-15 feature/attribute rows, checkmarks/X/short descriptions per cell):

| Feature / Attribute | Our Product | Competitor A | Competitor B | Competitor C | ... |
|---------------------|-------------|-------------|-------------|-------------|-----|
| Price (monthly) | | | | | |
| Free tier | | | | | |
| Platform | | | | | |
| Core Feature 1 | | | | | |
| Core Feature 2 | | | | | |
| [unique feature] | | | | | |
| User base (est.) | | | | | |
| App Store rating | | | | | |
| Key strength | | | | | |
| Key weakness | | | | | |

**Deep dives** (top 3 most relevant competitors, one page each): origin story and growth trajectory, what they do well and why users love them, what they do poorly and why users complain, likely roadmap and strategic direction, how we differentiate against them specifically, what we can learn from their approach.

## SWOT and Porter's Five Forces

**SWOT** (5-8 items per quadrant, each rated High/Medium/Low on impact and likelihood):
- Strengths: unique features, technology advantages, team capabilities, pricing, speed to market
- Weaknesses: brand awareness, team size, funding, feature gaps vs established competitors (be honest)
- Opportunities: market trends, underserved segments, technology shifts, partnerships, regulatory changes
- Threats: competitor moves, market saturation, economic conditions, platform risk, technology changes

**Porter's Five Forces** (rate each High/Medium/Low, then synthesize overall industry attractiveness):
1. Threat of new entrants - barriers to entry, ease of building a competitor, moats
2. Bargaining power of suppliers - app stores, cloud providers, payment processors; switching costs; dependency risk
3. Bargaining power of buyers - user switching costs, price sensitivity, number of alternatives, information availability
4. Threat of substitutes - non-digital, digital, and behavioral substitutes
5. Competitive rivalry - number/strength of competitors, market growth rate, differentiation level, exit barriers

## Positioning Templates

**Positioning statement**: "For [target audience] who [need/pain point], [product name] is a [category] that [key benefit]. Unlike [primary competitor], our product [primary differentiator]."

**Value proposition canvas**: customer jobs, customer pains, customer gains, pain relievers (how the product addresses each pain), gain creators (how it delivers each desired gain).

**Positioning map**: two axes that matter most to the target audience (e.g. Ease of Use vs Feature Depth, Price vs Comprehensiveness); place competitors and our product on it; describe in text.

**Messaging framework**: primary message (one sentence for the homepage hero), 3-4 supporting messages, proof points (stats, testimonial themes, comparison data), tone and voice guidelines.

## Deliverable File Skeletons

**`docs/market/market-research.md`**

```
# Market Research

## Market Overview
## Market Size (TAM top-down/bottom-up, SAM, SOM, summary table)
## Market Trends (5-8 trends with evidence and impact analysis)
## Porter's Five Forces
## SWOT Analysis
## Key Insights (top 5 takeaways)
## Sources
```

**`docs/market/competitive-landscape.md`**

```
# Competitive Landscape

## Competitor Overview
## Competitor Matrix
## Competitor Deep Dives
## Competitive Gaps
## Competitive Risks
## Sources
```

**`docs/market/positioning.md`**

```
# Positioning Strategy

## Positioning Statement
## Value Proposition Canvas
## Positioning Map
## Messaging Framework
## Differentiation Summary
```
