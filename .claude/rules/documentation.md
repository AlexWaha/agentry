# Documentation

Standards for all project documentation - structure, format, maintenance, and quality requirements.

---

## Language

All documentation is written in **English**. No exceptions. This includes:
- Technical specifications
- Business documents
- API documentation
- Code comments and docblocks
- Commit messages and PR descriptions
- CHANGELOG entries
- ADR records

---

## Directory Structure

All documentation lives in `docs/` with domain-specific subdirectories. The tree below is a **suggested layout**; adapt section names and presence to match the project's actual concerns (e.g. a pure library may not need `business/` or `marketing/`).

```
docs/
├── business/               # Business plans, revenue models, unit economics
│   ├── product-vision.md
│   ├── business-plan.md
│   ├── revenue-model.md
│   └── unit-economics.md
├── market/                 # Market research, competitive analysis, positioning
│   ├── market-research.md
│   ├── competitive-landscape.md
│   └── positioning.md
├── finance/                # Financial models, projections, breakeven
│   ├── financial-model.md
│   ├── projections.yaml
│   └── breakeven-analysis.md
├── product/                # Product specs, whitepapers, user flows
│   ├── product-spec.md
│   ├── whitepaper.md
│   └── user-flows/         # Mermaid diagrams
├── technical/              # Architecture, DB schema, API contracts, ADRs
│   ├── architecture.md
│   ├── db-schema.md
│   ├── api-contracts.md
│   ├── tech-stack.md
│   └── adr/                # Architecture Decision Records
│       └── 0001-<decision-slug>.md
├── api/                    # OpenAPI specs, endpoint documentation
│   └── openapi.yaml
├── marketing/              # GTM strategy, content plan, referral system
│   ├── marketing-plan.md
│   └── gtm-strategy.md
├── risk/                   # Risk matrix, mitigation plans, compliance
│   ├── risk-matrix.md
│   ├── mitigation-plan.md
│   └── compliance.md
├── growth/                 # Growth strategy, scaling plan, roadmap
│   ├── growth-strategy.md
│   ├── scaling-plan.md
│   └── roadmap.md
├── guides/                 # Developer onboarding, setup guides
│   ├── getting-started.md
│   └── development-guide.md
├── memory/                 # Session-specific notes and context (see root CLAUDE.md workflow)
└── changelog/              # Or CHANGELOG.md at project root
```

---

## Document Format

Every document **must** include the following header:

```markdown
# Document Title

**Date:** YYYY-MM-DD
**Author:** Agent name or Human
**Status:** Draft | In Review | Approved | Superseded
**Version:** 1.0

---

## Table of Contents

- [Section 1](#section-1)
- [Section 2](#section-2)
- [Section 3](#section-3)

---
```

### Rules

- **Title**: Clear, descriptive, specific
- **Date**: When the document was last updated (ISO 8601)
- **Author**: Who created or last updated the document
- **Status**: Current lifecycle state
  - `Draft` - work in progress
  - `In Review` - awaiting CEO/team review
  - `Approved` - reviewed and accepted
  - `Superseded` - replaced by a newer version (link to replacement)
- **Version**: Semantic versioning for major documents (1.0, 1.1, 2.0)
- **Table of Contents**: Required for any document longer than one page (~40 lines)

---

## CHANGELOG

`CHANGELOG.md` lives at the project root and is updated after every PR merge.

### Format

Follow [Keep a Changelog](https://keepachangelog.com/) format:

```markdown
# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- New resource creation endpoint with full validation (#PR-15)
- Export service for generated artifacts (#PR-18)

### Changed
- Improved aggregation calculation to handle edge cases (#PR-17)

### Fixed
- Timezone handling in scheduling logic (#PR-16)

### Removed
- Deprecated v0 API endpoints (#PR-19)

## [0.1.0] - YYYY-MM-DD

### Added
- Initial project setup with infrastructure
- User authentication
- Basic resource CRUD API
```

### Categories

- **Added**: New features
- **Changed**: Changes to existing functionality
- **Deprecated**: Features that will be removed in future
- **Removed**: Removed features
- **Fixed**: Bug fixes
- **Security**: Security-related changes

---

## Architecture Decision Records (ADRs)

For significant technical decisions, create an ADR in `docs/technical/adr/`.

### ADR Format

```markdown
# ADR-XXXX: Decision Title

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Deprecated | Superseded by ADR-YYYY
**Author:** Agent or Human

## Context

What is the problem or decision we need to make? What constraints exist?

## Decision

What did we decide? Be specific about the choice made.

## Alternatives Considered

### Alternative A: [Name]
- **Pros:** ...
- **Cons:** ...
- **Why rejected:** ...

### Alternative B: [Name]
- **Pros:** ...
- **Cons:** ...
- **Why rejected:** ...

## Consequences

### Positive
- ...

### Negative
- ...

### Risks
- ...

## References

- Links to relevant documentation, articles, benchmarks
```

### ADR Naming

```
0001-<decision-slug>.md
0002-<decision-slug>.md
0003-<decision-slug>.md
```

---

## API Documentation

### OpenAPI Spec

API documentation is maintained in OpenAPI 3.0 format at `docs/api/openapi.yaml`:

```yaml
openapi: 3.0.3
info:
  title: Project API
  version: 1.0.0
  description: REST API for the project

servers:
  - url: http://localhost:8080/api/v1
    description: Local development

paths:
  /resources:
    get:
      summary: List resources
      operationId: listResources
      tags: [Resources]
      security:
        - bearerAuth: []
      parameters:
        - name: type
          in: query
          schema:
            type: string
        - name: per_page
          in: query
          schema:
            type: integer
            default: 20
            minimum: 1
            maximum: 100
      responses:
        '200':
          description: Paginated list of resources
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ResourceCollection'
        '401':
          $ref: '#/components/responses/Unauthorized'
```

### Keeping API Docs in Sync

- Update OpenAPI spec when adding/modifying endpoints
- Response DTOs / API resources define the response shape - OpenAPI spec must match
- Request validators define validation rules - OpenAPI spec must match
- Technical Writer agent reviews API docs at the end of each phase

---

## Diagrams

Use **Mermaid** for all diagrams. Mermaid renders natively in GitHub markdown.

### Types of Diagrams

```markdown
### Entity Relationship Diagram

​```mermaid
erDiagram
    USER ||--o{ RESOURCE : creates
    RESOURCE ||--o{ ITEM : contains
    RESOURCE }o--|| TEMPLATE : "based on"
    ITEM ||--o{ EVENT : tracks
​```

### User Flow

​```mermaid
flowchart TD
    A[Open App] --> B{Authenticated?}
    B -->|No| C[Login Screen]
    B -->|Yes| D[Dashboard]
    D --> E[Create Resource]
    E --> F[Select Template]
    F --> G[Customize]
    G --> H[Save]
​```

### System Architecture

​```mermaid
graph TD
    Client[Web / Mobile Client]
    API[API Server]
    DB[(Database)]
    Cache[(Cache)]
    Queue[Job Queue]

    Client --> API
    API --> DB
    API --> Cache
    API --> Queue
    Queue --> Worker
​```
```

---

## Document Quality Standards

Every document must meet these criteria:

### Completeness
- All sections filled in - no "TBD" or "TODO" placeholders left in approved documents
- Sources cited for all external data (market sizes, competitor info, statistics)
- Assumptions explicitly stated

### Accuracy
- Numbers match across documents (e.g., TAM in market research = TAM in business plan)
- Technical specs match implementation
- Dates and versions are current

### Clarity
- One idea per paragraph
- Active voice preferred
- No jargon without definition on first use
- Tables for structured data, not prose
- Human voice: prose must not read as AI-generated. Apply `human-voice.md` (the 33 patterns, So-What rule). Investor- and customer-facing deliverables pass through `humanizer` -> `ai-detector` before review. Genuine tables/lists stay; only prose-disguised-as-bullets and AI tells get stripped.

### Formatting
- Consistent heading hierarchy (H1 for title, H2 for sections, H3 for subsections)
- Code blocks with language tags for syntax highlighting
- Tables for comparisons and structured data
- Bullet points for lists, numbered lists for sequences

---

## Cross-Reference Rules

When documents reference each other:
- Use relative links: `See [Architecture](../technical/architecture.md)`
- Include the specific section when relevant: `See [Database Schema - Resource Table](../technical/db-schema.md#resource-table)`
- When a document is superseded, add a notice at the top linking to the replacement

---

## Review Cycle

1. **Agent creates** document → status: `Draft`
2. **Orchestrator reviews** for completeness → status: `In Review`
3. **CEO/CTO approves** → status: `Approved`
4. **If rejected** → back to `Draft` with feedback
5. **If outdated** → update and re-submit for review

Documents in `Approved` status are the source of truth. Draft documents are work in progress and may change.
