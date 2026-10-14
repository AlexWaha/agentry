# Documentation Templates

Directory audit checklist and document templates for the update-docs skill.
Read this when actually auditing directories or writing a document.

## Directory audit checklist

Walk each `docs/` subdirectory and check: do the files that should exist
based on completed work actually exist? Are existing files up to date with
the latest changes? Are cross-references still valid?

#### `docs/business/`
| File | Expected After | Check |
|------|---------------|-------|
| `product-vision.md` | Phase 1 | Problem, solution, USP, features, metrics |
| `target-audience.md` | Phase 1 | Segments, priority ranking, beachhead market |
| `user-personas.md` | Phase 1 | 3-5 detailed personas with scenarios |
| `business-plan.md` | Phase 3 | Full business plan with all sections |
| `revenue-model.md` | Phase 3 | Pricing tiers, monetization strategy |
| `unit-economics.md` | Phase 3 | CAC, LTV, churn, payback period |

For each file: read it, check if the content reflects the latest decisions.
If the pricing model changed in a later phase, update `revenue-model.md`.

#### `docs/market/`
| File | Expected After | Check |
|------|---------------|-------|
| `market-research.md` | Phase 2 | TAM/SAM/SOM, trends, market size |
| `competitive-landscape.md` | Phase 2 | Competitor matrix, SWOT, positioning gaps |
| `positioning.md` | Phase 2 | Brand positioning, messaging framework |

#### `docs/finance/`
| File | Expected After | Check |
|------|---------------|-------|
| `financial-model.md` | Phase 4 | Revenue/cost projections, P&L |
| `projections.yaml` | Phase 4 | Structured financial data |
| `breakeven-analysis.md` | Phase 4 | Breakeven scenarios |

#### `docs/product/`
| File | Expected After | Check |
|------|---------------|-------|
| `whitepaper.md` | Phase 5 | Technical + business whitepaper |
| `product-spec.md` | Phase 5 | Full product specification |
| `user-flows/` | Phase 5 | Mermaid diagrams for key user journeys |

#### `docs/technical/`
| File | Expected After | Check |
|------|---------------|-------|
| `architecture.md` | Phase 6 | System architecture with diagrams |
| `db-schema.md` | Phase 6 | ER diagrams, table descriptions |
| `api-contracts.md` | Phase 6 | API endpoint definitions |
| `tech-stack.md` | Phase 6 | Technology choices with rationale |

Verify that architecture docs match actual implementation once code exists.

#### `docs/api/`
| File | Expected After | Check |
|------|---------------|-------|
| `openapi.yaml` | Phase 6+ | OpenAPI spec matching actual endpoints |

If endpoints were implemented, verify the OpenAPI spec matches the actual
routes.

#### `docs/marketing/`
| File | Expected After | Check |
|------|---------------|-------|
| `marketing-plan.md` | Phase 12 | Content strategy, channels, email sequences |
| `gtm-strategy.md` | Phase 12 | GTM strategy, referral system, budget |

#### `docs/risk/`
| File | Expected After | Check |
|------|---------------|-------|
| `risk-matrix.md` | Phase 13 | Risk register with scores |
| `mitigation-plan.md` | Phase 13 | Mitigation strategies, contingency plans |
| `compliance.md` | Phase 13 | GDPR, privacy, app store compliance |

Also check: were any risks flagged during earlier phases that should be
documented here even before Phase 13?

#### `docs/growth/`
| File | Expected After | Check |
|------|---------------|-------|
| `growth-strategy.md` | Phase 14 | Growth levers, experiments |
| `scaling-plan.md` | Phase 14 | Technical and business scaling plan |
| `roadmap.md` | Phase 14 | Feature roadmap with timeline |

#### `docs/guides/`
| File | Expected After | Check |
|------|---------------|-------|
| `development-setup.md` | Phase 7+ | How to set up the dev environment |
| `deployment.md` | Phase 7+ | How to deploy the application |

#### `docs/presentations/`
| File | Expected After | Check |
|------|---------------|-------|
| `pitch-deck/` | When created | Pitch deck content and generator |

## Missing document stub template

Use when the expected content has not been produced anywhere yet:

```markdown
# [Document Title]

**Date:** [date]
**Status:** TODO

> This document needs to be created. Expected content:
> - [section 1]
> - [section 2]
> - [section 3]

This document should have been created during Phase [N] but was not
completed. Create a task to address this gap.
```

If a new task is needed to fill the gap, note it for the Orchestrator - do
not create tasks directly, flag them.

## Changelog entry template

```markdown
## [Date] - Phase [N] Completion

### Added
- [New document/feature/component]

### Changed
- [Updated document/decision/configuration]

### Fixed
- [Corrected error in document/code]

### Decisions
- [Key decision made during this phase with rationale]

### References
- Task: [task ids]
- Branch: [branch name if applicable]
```

If `docs/changelog/CHANGELOG.md` does not exist, create it with this header
first:

```markdown
# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/).

---

[entries here]
```

## Documentation status summary template

```markdown
# Documentation Update Summary

**Date:** [date]
**Trigger:** Phase [N] completion / [description of what prompted the update]

## Documents Created
| File | Reason |
|------|--------|
| [path] | [why it was created] |

## Documents Updated
| File | Changes |
|------|---------|
| [path] | [what was updated] |

## Gaps Identified
| File | Status | Action Needed |
|------|--------|--------------|
| [path] | Missing | [task to create] |
| [path] | Incomplete | [sections to add] |

## Cross-Reference Fixes
| Issue | Resolution |
|-------|-----------|
| [inconsistency] | [how it was fixed] |

## Changelog Updated
[Yes/No - link to entry]
```
