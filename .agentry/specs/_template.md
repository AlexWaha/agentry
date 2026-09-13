---
id: XXXX
title: Short descriptive title
status: draft
plan: .agentry/plans/YYYY-MM-DD-description.md
epic:
created: YYYY-MM-DD
author: spec-developer
---

# Spec XXXX: Title

## Problem and Goals

What problem this solves and what outcome counts as success. 2-4 sentences,
specific, sourced from the approved plan.

## Non-Goals

What this spec deliberately does NOT cover. Explicit non-goals keep the epic
bounded and give the reviewer a scope fence.

## Functional Requirements

Numbered, testable, one observable behavior each.

- **FR-1**: ...
- **FR-2**: ...

## Data and API Contracts

Exact shapes. Tables/migrations, endpoints with request/response payloads,
status codes, error formats. No placeholders left unresolved at approval time.

## Acceptance Criteria

Each criterion cites the FR it verifies.

- [ ] (FR-1) ...
- [ ] (FR-2) ...

## Affected Code

From codegraph: files and symbols this change touches, callers affected,
blast radius notes. Confirmed against the real codebase, not assumed.

| File / Symbol | Change | Callers affected |
|---------------|--------|------------------|
| ... | ... | ... |

## Rollout and Migration Notes

Deploy actions, migrations, feature flags, backward compatibility. `none` is a
positive assertion, not a skip.

## Open Questions

Questions only the CEO can answer. MUST be empty before `status: approved`.

- (none)
