# Financial Model: Assumption and Projection Templates

## Assumption tables (fill EVERY row per scenario, with source/reasoning)

### User growth

| Assumption | Base | Best | Worst | Source/Reasoning |
|---|---|---|---|---|
| Month 1 signups | | | | |
| Monthly signup growth rate (M1-6) | | | | |
| Monthly signup growth rate (M7-12) | | | | |
| Yearly signup growth rate (Y2-3) | | | | |
| Organic vs paid split | | | | |
| Referral viral coefficient | | | | |

### Conversion and retention

| Assumption | Base | Best | Worst | Source/Reasoning |
|---|---|---|---|---|
| Free to paid conversion rate | | | | Industry benchmark: X% |
| Trial to paid conversion (if trial) | | | | |
| Monthly churn rate (paid) | | | | Industry benchmark: X% |
| Free user churn rate | | | | |
| Tier upgrade rate | | | | |
| Annual vs monthly billing split | | | | |

### Revenue

| Assumption | Base | Best | Worst | Source/Reasoning |
|---|---|---|---|---|
| Tier prices (per tier) | | | | |
| Annual discount (% off) | | | | |
| Secondary revenue per user | | | | |
| Payment processing fee | | | | Stripe: 2.9% + $0.30 |
| App store commission (if mobile) | | | | Apple/Google: 15-30% |

### Costs

| Assumption | Base | Best | Worst | Source/Reasoning |
|---|---|---|---|---|
| Hosting cost per 1K users/month | | | | |
| Third-party services (monthly fixed) | | | | |
| CAC (blended) | | | | |
| Marketing budget (% of revenue) | | | | |
| Team costs (monthly, by phase) | | | | |
| Legal/accounting (monthly) | | | | |

### Funding

| Assumption | Value | Reasoning |
|---|---|---|
| Pre-seed / seed target | | |
| Founder investment | | |
| Monthly burn rate (pre-revenue) | | |
| Desired runway (months) | | |

## Monthly revenue walk (Year 1, per month M1-M12)

1. New signups (free) -> 2. Cumulative free (minus free churn) ->
3. New paid conversions (with time lag) -> 4. Churned paid -> 5. Net new paid ->
6-7. Cumulative subscribers per tier -> 8-9. Revenue per tier (with annual
discount adjustment) -> 10. Total MRR -> 11. Secondary revenue ->
12. Gross revenue -> 13. Payment fees -> 14. App store commission -> 15. Net revenue.

Years 2-3: same structure quarterly. Years 4-5: annually, conservative growth.

## P&L skeleton (Year 1 monthly; Y2-3 quarterly; Y4-5 annual)

| Line Item | M1 | ... | M12 | Total |
|---|---|---|---|---|
| Gross Revenue | | | | |
| - Payment processing | | | | |
| - App store commission | | | | |
| **Net Revenue** | | | | |
| - Infrastructure / - Team / - Marketing / - Operations | | | | |
| **Total Costs** | | | | |
| **Net Income** / **Cumulative** | | | | |

## Cost category checklists

- Infrastructure: hosting, managed DB, cache, CDN, email, SMS, analytics,
  monitoring, domains; scale with users (define cost per 1K users)
- Team: role, start month, monthly cost, FT/PT/contract; phase the composition
  (pre-launch / launch / growth / scale)
- Marketing: paid social, search, content/SEO, influencers, PR, referral
  program, ASO
- Operations: legal, accounting, insurance, licenses, app store fees, 10% buffer

## Unit economics formulas

- CAC: by channel + blended; trend over time (should decrease)
- LTV = ARPU / monthly churn rate; by tier + blended; optionally + referral value
- LTV:CAC: target >= 3:1 - flag below; by channel; timeline to reach 3:1
- Payback period: months to recover CAC; by tier + blended
- Gross margin = (Revenue - direct costs) / Revenue; target 70%+ for SaaS

## Breakeven walk

Fixed costs/month -> variable cost per user -> blended ARPU -> contribution
margin per user (ARPU - variable) -> breakeven paying users (fixed /
contribution) -> breakeven month at projected growth -> sensitivity at +/- 20%
on churn, pricing, CAC.

## Scenario summary table

| Metric | Best | Base | Worst |
|---|---|---|---|
| Year 1 Revenue | | | |
| Year 1 Users (paid) | | | |
| Breakeven Month | | | |
| Year 3 Revenue | | | |
| Funding Needed | | | |
| LTV:CAC | | | |

Worst case must include the survival plan: what costs can be cut, what pivots
are possible, how long until cash runs out if never breaking even.

## projections.yaml skeleton

```yaml
assumptions:
  user_growth: { month_1_signups: X, monthly_growth_rate_m1_m6: X% }
  conversion: { free_to_paid: X%, monthly_churn: X% }
  revenue: { tier1_monthly_price: X.XX, annual_discount: X% }
  costs: { hosting_per_1k_users: X.XX, blended_cac: X.XX, payment_processing_rate: X.X% }
projections:
  year_1:
    monthly:
      - { month: 1, new_signups: X, cumulative_free: X, cumulative_paid: X,
          mrr: X.XX, total_costs: X.XX, net_income: X.XX, cumulative_cash: X.XX }
  year_2: { quarterly: [...] }
scenarios:
  best_case: { year_1_revenue: X, breakeven_month: X, year_3_revenue: X }
  base_case: { year_1_revenue: X, breakeven_month: X, year_3_revenue: X }
  worst_case: { year_1_revenue: X, breakeven_month: X, year_3_revenue: X }
unit_economics:
  { cac_blended: X.XX, ltv_blended: X.XX, ltv_cac_ratio: X.X,
    payback_period_months: X, gross_margin: X% }
```
