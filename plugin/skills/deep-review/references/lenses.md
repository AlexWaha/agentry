# Deep Review: Lens Catalog

Pick 3-4 orthogonal lenses per subject type.

## Code lenses

| Lens | Focus |
|------|-------|
| Security | Injection, auth bypass, data exposure, PII leaks, mass assignment, hardcoded secrets |
| Performance | N+1 queries, missing eager loading, unbounded queries, missing pagination, unnecessary DB calls, missing indexes |
| Architecture Compliance | Layer violations, dependency direction, pattern adherence, naming conventions, file placement |
| Test Coverage | Missing test scenarios (401, 403, 422, 200, 404), edge cases, factory usage, assertion quality |
| Error Handling | Missing try/catch, silent failures, unclear error messages, missing validation |
| API Design | Resource structure, route naming, response consistency, pagination, status codes |
| Modular Monolith | Cross-module dependency violations, correct namespace usage, module boundary respect, shared contracts via Core |

## Architecture lenses

| Lens | Focus |
|------|-------|
| Scalability | Bottlenecks, horizontal scaling readiness, caching strategy, queue design |
| Maintainability | Complexity, coupling, separation of concerns, documentation quality |
| Security | Auth/authz design, data isolation, encryption, API security, OWASP compliance |
| Cost Efficiency | Infrastructure overhead, service granularity, resource utilization, vendor lock-in |
| Reliability | Single points of failure, retry strategies, data consistency, backup/recovery |

## Document lenses

| Lens | Focus |
|------|-------|
| Completeness | Missing sections, unexplored scenarios, gaps in reasoning |
| Accuracy | Factual errors, outdated data, unsupported claims, math errors |
| Consistency | Contradictions between documents, conflicting numbers, terminology drift |
| Actionability | Vague recommendations, missing next steps, unclear ownership, unmeasurable goals |
| Logical Rigor | Flawed assumptions, logical fallacies, missing alternatives, confirmation bias |

## Config lenses

| Lens | Focus |
|------|-------|
| Security | Exposed secrets, default credentials, permissive permissions, unencrypted channels |
| Correctness | Syntax errors, wrong values, missing required fields, version mismatches |
| Portability | Hardcoded paths, environment assumptions, platform-specific configs |
| Documentation | Missing comments, unclear variable names, undocumented overrides |
| Resilience | Missing health checks, no restart policies, missing resource limits, no logging |

## Severity definitions

| Severity | Code | Documents | Config |
|----------|------|-----------|--------|
| **Critical** | Security vulnerability, data loss risk, auth bypass, SQL injection | Factual errors that would mislead investors, missing legal/compliance sections | Exposed secrets, security holes, data loss risk |
| **High** | N+1 queries, missing validation, architecture violations, missing tests for critical paths | Significant gaps in reasoning, contradictions between documents, wrong financial calculations | Incorrect values that break functionality, missing critical configs |
| **Medium** | Non-optimal patterns, missing edge case tests, unclear naming, missing eager loading for non-critical paths | Minor inconsistencies, vague sections needing detail, formatting issues | Documentation gaps, non-optimal settings, portability concerns |

## Report skeleton

```markdown
# Deep Review Report

**Subject:** [what was reviewed]
**Date:** [date]
**Lenses:** [list]
**Scope:** [files/documents reviewed]

## Summary
- Critical: X / High: Y / Medium: Z / Total: X+Y+Z

## Critical Findings
> MUST be fixed before merge/approval.
### [C1] [Short title]
- **Lens:** / **Location:** `file:42` / **Description:** / **Evidence:** /
  **Suggested Fix:** / **Impact:**

## High Findings
> SHOULD be fixed soon - schedule for the current sprint.
### [H1] ... (same structure)

## Medium Findings
> Consider improving - not blocking.
### [M1] ... (same structure)

## Positive Observations
[2-3 things done well]

## Diagrams
[Mermaid diagrams only when they genuinely clarify a finding]
```
