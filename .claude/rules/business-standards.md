> **STATUS: DEFERRED**
> Not active in current project phase (backend API only).
> Will be activated when business planning/analysis work begins.
> Do not invoke until explicitly activated by CEO.

# Business Standards

Standards for all business deliverables - market research, financial models, business plans, competitive analysis, and strategic documents. Every number must be traceable. Every conclusion must be actionable.

---

## Market Research

### TAM / SAM / SOM

Every market sizing must include all three levels:

| Level | Definition | Requirement |
|-------|-----------|-------------|
| **TAM** (Total Addressable Market) | Total global market for the product category | Source required (industry report, government data, credible analysis) |
| **SAM** (Serviceable Addressable Market) | Portion of TAM reachable with current business model and geography | Calculation methodology documented |
| **SOM** (Serviceable Obtainable Market) | Realistic capture in first 1-3 years | Based on comparable company benchmarks or bottom-up analysis |

### Data Sources

Acceptable sources (in order of credibility):
1. Government statistics (census, labor data, economic reports)
2. Industry reports (Gartner, Statista, IBISWorld, McKinsey)
3. Public company filings (SEC, annual reports)
4. Academic research (peer-reviewed)
5. Credible news sources (TechCrunch, Bloomberg, WSJ)
6. Expert interviews and surveys (methodology must be stated)

Unacceptable sources:
- Wikipedia (use it to find primary sources only)
- Unsourced blog posts
- Social media posts without verification
- "Common knowledge" without backing data

### Growth Rates

- Historical growth rates: minimum 3 years of data with source
- Projected growth rates: source or methodology for the projection
- CAGR (Compound Annual Growth Rate) preferred over simple year-over-year

## Competitive Analysis

### Required Frameworks

At minimum, competitive analysis must include:

1. **Competitor Matrix** - structured table comparing:
   - Product features (scored 1-5 or present/absent)
   - Pricing tiers
   - Target audience
   - Market position
   - Strengths and weaknesses

2. **SWOT Analysis** - for each major competitor AND for our product:
   - Strengths (internal, positive)
   - Weaknesses (internal, negative)
   - Opportunities (external, positive)
   - Threats (external, negative)

3. **Porter's Five Forces** - applied to the specific market:
   - Threat of new entrants
   - Bargaining power of buyers
   - Bargaining power of suppliers
   - Threat of substitutes
   - Competitive rivalry

### Positioning Map

Visual positioning of competitors on 2 key dimensions (e.g., price vs features, simplicity vs power). Our product's target position must be clearly marked with reasoning.

## Financial Models

### Assumptions

Every financial model MUST have a clearly labeled "Assumptions" section at the top. Each assumption must include:

- The assumption itself (specific number or rate)
- The source or reasoning behind it
- Sensitivity: what happens if this assumption is wrong by ±20%

```markdown
## Assumptions

| # | Assumption | Value | Source | Sensitivity |
|---|-----------|-------|--------|-------------|
| 1 | Monthly churn rate | 5% | SaaS industry average (Recurly benchmark) | ±2% changes breakeven by 3 months |
| 2 | Organic signup rate | 500/month by Month 6 | Based on comparable app launches | ±200 changes CAC significantly |
```

### Projections: Three Scenarios

All financial projections MUST include three scenarios:

| Scenario | Description |
|----------|-----------|
| **Best case** | Optimistic assumptions - faster growth, lower churn, higher conversion |
| **Base case** | Realistic assumptions - industry averages, conservative estimates |
| **Worst case** | Pessimistic assumptions - slower growth, higher churn, lower conversion |

Never present only one scenario. Investors and CEOs need to understand the range of outcomes.

### Unit Economics

Required metrics for subscription businesses:

| Metric | Description |
|--------|-----------|
| **CAC** | Customer Acquisition Cost - total marketing + sales spend / new customers |
| **LTV** | Customer Lifetime Value - ARPU × average customer lifetime |
| **LTV:CAC ratio** | Must be > 3:1 for sustainable business |
| **Payback period** | Months to recover CAC |
| **MRR / ARR** | Monthly / Annual Recurring Revenue |
| **Churn rate** | Monthly customer churn percentage |
| **ARPU** | Average Revenue Per User (monthly) |
| **Conversion rate** | Free-to-paid conversion percentage |

### Output Format

Financial data should be presented in YAML for structured data and markdown tables for narrative:

```yaml
# projections.yaml
assumptions:
  monthly_churn: 0.05
  free_to_paid_conversion: 0.03

base_case:
  year_1:
    mrr_month_12: 15000
    total_users: 10000
    paying_users: 300
```

## Business Plans

### Required Structure

All business plans follow this structure:

1. **Executive Summary** - 1-2 pages, standalone, covers everything at high level
2. **Problem** - what pain exists, who suffers, current alternatives
3. **Solution** - what we build, how it solves the problem, USP
4. **Market** - TAM/SAM/SOM, growth trends, target segments
5. **Business Model** - how we make money, pricing, revenue streams
6. **Operations** - how the business runs day-to-day, team, processes
7. **Team** - who is needed, roles, hiring plan
8. **Financial** - projections, unit economics, funding needs
9. **Milestones** - key milestones with dates and success criteria
10. **Risks** - top risks with mitigation strategies

### Quality Checklist

Before submitting any business deliverable:

- [ ] All numbers have sources or are explicitly marked as estimates
- [ ] Assumptions are documented and sensitivity-tested
- [ ] Three scenarios presented (best/base/worst)
- [ ] Competitor analysis uses at least two frameworks
- [ ] Market sizing uses TAM/SAM/SOM with methodology
- [ ] Conclusions are actionable (not just observations)
- [ ] Executive summary is standalone (readable without the full document)
- [ ] No unsourced claims presented as facts
