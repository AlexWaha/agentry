# Incident Response Templates

Reference material for the `incident-response-commander` agent. Moved out of
the agent body to keep the role file compact.

## Severity Classification Matrix

| Level | Name | Criteria | Response Time | Update Cadence | Escalation |
|-------|------|----------|----------------|-----------------|------------|
| SEV1 | Critical | Full outage, data loss risk, security breach | < 5 min | Every 15 min | VP Eng + CTO immediately |
| SEV2 | Major | Degraded service for >25% users, key feature down | < 15 min | Every 30 min | Eng Manager within 15 min |
| SEV3 | Moderate | Minor feature broken, workaround available | < 1 hour | Every 2 hours | Team lead next standup |
| SEV4 | Low | Cosmetic issue, no user impact | Next business day | Daily | Backlog triage |

Auto-upgrade triggers: impact scope doubles; no root cause after 30 min (SEV1)
or 2 hours (SEV2); customer-reported incidents on paying accounts (minimum
SEV2); any data integrity concern (immediate SEV1).

## Runbook Template

```markdown
# Runbook: [Service/Failure Scenario]

## Quick Reference
Service, owner team, on-call schedule link, dashboard links, last tested date

## Detection
Alert name, symptoms, false-positive check

## Diagnosis
1. Check service health
2. Review error rate dashboard
3. Check recent deployments
4. Review dependency health

## Remediation
### Option A: Rollback (preferred if deploy-related)
### Option B: Restart (if state corruption suspected)
### Option C: Scale up (if capacity-related)

## Verification
Error rate at baseline, latency p99 within SLO, no new alerts for 10 minutes,
functionality manually verified

## Communication
Internal channel update, status page if customer-facing, post-mortem within 24h
```

Runbooks must be tested quarterly - an untested runbook is a false sense of
security.

## Post-Mortem Document Template

```markdown
# Post-Mortem: [Incident Title]

**Severity**: SEV[1-4]  **Duration**: [start] - [end]

## Executive Summary
[2-3 sentences: what happened, who was affected, how it was resolved]

## Impact
Users affected, revenue impact, SLO error budget consumed, support tickets

## Timeline (UTC)
| Time | Event |

## Root Cause Analysis
Immediate cause / underlying cause / systemic cause; 5 Whys

## What Went Well / What Went Poorly

## Action Items
| ID | Action | Owner | Priority | Due Date | Status |

## Lessons Learned
```

Blameless framing always: "the system allowed this failure mode", never "X
person caused the outage".

## SLO/SLI Definition Shape

```yaml
service: checkout-api
slis:
  availability: {metric: "success ratio", good_event: "status < 500"}
  latency: {metric: "p99 duration", threshold: "400ms"}
slos:
  - sli: availability
    target: 99.95%
    window: 30d
    error_budget: "21.6 minutes/month"
    burn_rate_alerts:
      - {severity: page, short_window: 5m, long_window: 1h, burn_rate: 14.4x}
      - {severity: ticket, short_window: 30m, long_window: 6h, burn_rate: 6x}
error_budget_policy:
  above_50pct: "normal feature development"
  25_to_50pct: "feature freeze review with Eng Manager"
  below_25pct: "all hands on reliability work"
  exhausted: "freeze non-critical deploys, VP Eng review"
```

## Stakeholder Communication Templates

```markdown
# SEV1 - Initial Notification (within 10 minutes)
Current Status / Impact / Next Update (in 15 min)

# SEV1 - Status Update (every 15 minutes)
Status: Investigating / Identified / Mitigating / Resolved
Current Understanding / Actions Taken / Next Steps / Next Update

# Incident Resolved
Resolution / Duration / Impact Summary / Follow-up (post-mortem link)
```

## On-Call Rotation Guidelines

- Minimum rotation size of 4 engineers; no one on-call more than 2 consecutive weeks
- New engineers shadow for 2 weeks before going primary
- Escalation policy: primary (5 min) -> secondary (10 min) -> eng manager (15 min) -> VP Eng (immediate)
- Pay on-call stipends, compensate after-hours incident work, mandate rest after long SEV1s
- Track pages per shift; more than 5 pages/week signals noisy alerts to fix
