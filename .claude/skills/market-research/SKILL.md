---
name: market-research
description: Produce market size (TAM/SAM/SOM), competitor matrix, SWOT, Porter's Five Forces, and positioning strategy for a defined product. Use when a task says "size the market", "research competitors", "how big is this market", "who are we up against", or after the ideation phase completes and before pricing/go-to-market decisions are made.
allowed-tools: Read, Grep, Glob, Write, WebSearch, WebFetch
---

# Market Research - Market and Competitive Analysis

> STATUS: DEFERRED. Not active in current project phase (backend API only). Will be activated when business planning/analysis work begins. Do not invoke until explicitly activated by CEO.

Comprehensive market research producing actionable intelligence on market size, competitive landscape, and positioning strategy. Requires the ideation phase to be complete (`docs/business/product-vision.md`, `target-audience.md`, `user-personas.md`). Every claim must be sourced or explicitly marked as an estimate.

## Steps

1. **Read existing context.** `docs/business/product-vision.md`, `target-audience.md`, `user-personas.md`, and `basic-idea.txt` if present. Summarize the market being entered, who is being served, and the price point.
2. **Research market size (TAM/SAM/SOM)** via WebSearch. Calculate TAM top-down and bottom-up, narrow to SAM with explicit filters, project SOM for Years 1-3. Every number sourced or marked "[Estimate]". Full method and summary table: references/research-templates.md.
3. **Research competitors** via WebSearch: direct, indirect, and potential future entrants. Build a comparison matrix (5-8 competitors, 10-15 attributes) and write a one-page deep dive for the top 3. Matrix and deep-dive structure: references/research-templates.md.
4. **Run a SWOT analysis** of our product: 5-8 items per quadrant (strengths, weaknesses, opportunities, threats), each rated High/Medium/Low on impact and likelihood. Template: references/research-templates.md.
5. **Run Porter's Five Forces**: rate each of the five forces High/Medium/Low, then synthesize an overall industry attractiveness verdict. Template: references/research-templates.md.
6. **Identify market trends.** 5-8 trends with supporting evidence/sources and impact on the product (positive/negative/neutral), plus 3-5 opportunities competitors are not addressing.
7. **Define the positioning strategy**: positioning statement, value proposition canvas, two-axis positioning map, messaging framework. Templates: references/research-templates.md.
8. **Write the three deliverables**: `docs/market/market-research.md`, `docs/market/competitive-landscape.md`, `docs/market/positioning.md`. File skeletons: references/research-templates.md.
9. **Present a summary to the CEO/CTO**: TAM/SAM/SOM Year 1 in one line each, top 3 competitors and our advantage over each, biggest opportunity, biggest risk, recommended positioning in one sentence, and any assumptions needing validation. Wait for explicit approval before proceeding.

## Output checklist

- [ ] TAM calculated top-down AND bottom-up, with sources
- [ ] SAM narrowing logic explicit with filter percentages
- [ ] SOM covers Year 1, 2, 3 with documented assumptions
- [ ] At least 5 competitors in the matrix; top 3 have deep dives
- [ ] SWOT has 5+ items per quadrant with impact/likelihood ratings
- [ ] All five Porter's forces rated High/Medium/Low
- [ ] 5+ market trends identified with evidence
- [ ] Positioning statement follows the standard template
- [ ] Every number sourced or marked "[Estimate: reasoning]"
- [ ] All three documents created in `docs/market/`
- [ ] Summary presented to CEO and approval requested

## References (read only when needed)

- [references/research-templates.md](references/research-templates.md) - TAM/SAM/SOM calculation method, competitor matrix and deep-dive structure, SWOT/Porter's templates, positioning templates, and deliverable file skeletons
- [references/ux-research-templates.md](references/ux-research-templates.md) - user research study plan, persona documentation, and usability testing protocol templates
