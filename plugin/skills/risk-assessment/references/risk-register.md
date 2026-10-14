# Risk Register Templates and Scoring Model

Full templates and brainstorming prompts for the risk-assessment skill. Read
this when actually building the register, not just to recall the process.

## Risk category prompts

Use these as a starting framework per category, then go beyond them based on
project specifics. No category should end up with zero risks - if one does,
think harder.

**Business Risks:**
- Product-market fit: will users actually pay for this? What if the core assumption is wrong?
- Competition: what if a well-funded competitor launches a similar product, or an existing player adds this feature?
- Pricing: is the pricing model validated? What if willingness-to-pay is lower than assumed?
- Churn: what if retention is poor? What is the "aha moment" and how fragile is it?
- Revenue model: what if the monetization model does not work for this audience? Alternative paths?
- Partnerships: what if key partners are unreliable or too expensive?
- Unit economics: what if CAC exceeds LTV? At what scale does the model break?
- Customer concentration: is there risk of dependence on a single customer segment?

**Technical Risks:**
- Scalability: can the architecture handle 10x, 100x users? Where are the bottlenecks?
- Security: what are the attack surfaces? What data do we store and what is the breach impact?
- Data loss: what happens if the database fails? Is there backup/recovery?
- Tech debt: are there shortcuts being taken now that will compound?
- Integration reliability: what if external APIs (payments, notifications, third-party data) go down?
- Cross-platform complexity: what are the platform-specific failure modes?
- Performance: what if the app is slow on older devices or poor connections?

**Market Risks:**
- Timing: is the market ready for this product? Is there a trend we are riding or fighting?
- Market size: are the TAM/SAM/SOM estimates realistic? What if the addressable market is smaller?
- Regulation: are there upcoming regulations that could affect the product?
- Economic conditions: how does recession or inflation affect willingness to pay?
- Cultural fit: does the product work across cultures, or is it niche to specific regions?
- Seasonality: are there seasonal demand patterns?

**Legal Risks:**
- Privacy: GDPR, CCPA compliance - do we collect, store, or process personal data correctly?
- Terms of service: are ToS and privacy policy robust? Do they cover data usage, cancellation, refunds?
- Intellectual property: are there patents that could be infringed? Is the brand name available?
- Children's data: if any users are under 13/16, COPPA/GDPR-K requirements apply.
- Regulated claims: health, financial, or legal claims that trigger extra regulation.
- Payment processing: PCI DSS compliance - is it handled correctly?
- App store / platform policy: anything that could cause rejection or takedown?

**Operational Risks:**
- Team capacity: is the team sufficient? What are the limits?
- Key person risk: what happens if a critical person is unavailable?
- Vendor dependencies: what if a vendor changes terms or pricing?
- Infrastructure costs: what if hosting costs exceed projections?
- Support load: what if support volume is higher than expected?
- Launch timeline: what if development takes longer than planned? What is the critical path?
- Quality: what if bugs or poor UX damage early user trust?

## Scoring model

**Probability** (1-5):
1 = Rare (< 5% in next 12 months), 2 = Unlikely (5-20%), 3 = Possible (20-50%),
4 = Likely (50-80%), 5 = Almost Certain (> 80%).

**Impact** (1-5):
1 = Negligible (minor inconvenience), 2 = Minor (some rework, small cost increase),
3 = Moderate (significant delay, noticeable revenue impact), 4 = Major (large
financial loss, major feature failure, reputation damage), 5 = Catastrophic
(project failure, legal action, data breach, company-ending).

**Risk Score** = Probability x Impact (range 1-25).

**Risk Level:** 1-4 Low (accept and monitor), 5-9 Medium (actively mitigate),
10-15 High (priority mitigation required), 16-25 Critical (immediate action).

## Risk matrix table

```markdown
| # | Risk | Category | Probability | Impact | Score | Level | Status |
|---|------|----------|-------------|--------|-------|-------|--------|
| R1 | [description] | Technical | 4 | 5 | 20 | Critical | Open |
| R2 | [description] | Business | 3 | 4 | 12 | High | Open |
```

## Heat map (text form)

```
Impact ->
  5 |  M  |  H  |  H  |  C  |  C  |
  4 |  M  |  M  |  H  |  H  |  C  |
  3 |  L  |  M  |  M  |  H  |  H  |
  2 |  L  |  L  |  M  |  M  |  H  |
  1 |  L  |  L  |  L  |  M  |  M  |
     1     2     3     4     5
              Probability ->
```

Place each risk on the heat map by its coordinates. Also produce a Mermaid
quadrant chart or pie chart (category distribution) and a Gantt-style timeline
for when top risks are most likely to materialize. Validate with the Mermaid
Chart MCP tool if available.

## Mitigation strategy template

Required for every risk scored Medium or above. Must be specific and
actionable, never "monitor the situation."

```markdown
### R[X]: [Risk Title]

**Category:** [category]
**Score:** [probability] x [impact] = [score] ([level])

**Mitigation Strategy:**
- **Reduce Probability:** [specific actions to make this less likely]
- **Reduce Impact:** [specific actions to minimize damage if it happens]
- **Owner:** [who is responsible for executing the mitigation]
- **Timeline:** [when mitigation should be in place]
- **Cost:** [estimated cost - time, money, resources]
- **Monitoring:** [how to detect early if this risk is materializing]
```

## Contingency plan template

Required for the top 5 risks by score.

```markdown
### Contingency Plan: R[X] - [Risk Title]

**Trigger:** [What specific event/metric signals this risk has materialized?]
**Immediate Response (first 24 hours):**
1. [Action 1]
2. [Action 2]

**Short-term Response (first week):**
1. [Action 1]
2. [Action 2]

**Communication Plan:**
- Internal: [who to notify and how]
- External: [customer communication if needed]

**Recovery Timeline:** [estimated time to recover]
**Recovery Cost:** [estimated additional cost]
**Decision Point:** [at what point do we pivot/abandon vs. push through?]
```

## Deliverable file templates

**`docs/risk/risk-matrix.md`:**

```markdown
# Risk Matrix

**Date:** [date]
**Project Phase:** [current phase]
**Total Risks Identified:** [count]
**Risk Distribution:** [X Critical, Y High, Z Medium, W Low]

## Risk Heat Map
[Mermaid diagram or text-based heat map]

## Risk Register
[Full table]

## Risk Category Distribution
| Category | Count | Critical | High | Medium | Low |
|----------|-------|----------|------|--------|-----|
| Business | X | ... | ... | ... | ... |
| Technical | X | ... | ... | ... | ... |
| Market | X | ... | ... | ... | ... |
| Legal | X | ... | ... | ... | ... |
| Operational | X | ... | ... | ... | ... |

## Detailed Risk Descriptions
[For each risk: description, probability justification, impact justification, current status]
```

**`docs/risk/mitigation-plan.md`:**

```markdown
# Risk Mitigation Plan

**Date:** [date]
**Risks Requiring Mitigation:** [count of Medium+ risks]

## Mitigation Strategies
[All mitigation strategies, organized by risk level, Critical first]

## Contingency Plans
[Top 5 contingency plans]

## Risk Review Schedule
- Weekly: review Critical and High risks
- Bi-weekly: review Medium risks
- Monthly: full risk register review, add new risks, re-score existing ones

## Risk Acceptance
[List of Low risks accepted without active mitigation, with brief justification]
```
