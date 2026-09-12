# Project Context

> **[PROJECT-SPECIFIC - REPLACE ME]**
> This file captures the current project's product, domain, and structure. When
> adapting the template to a new project, replace this entire file with your own
> product description, domain terminology, and architecture summary.

## Product

**Name:** `{{PROJECT_NAME}}`

**What it does:** `{{PRODUCT_DESCRIPTION}}`
[PROJECT-SPECIFIC - REPLACE ME] - one or two sentences describing what the product
does, for whom, and the core value it delivers.

**Key features:**
- [PROJECT-SPECIFIC - REPLACE ME]
- [PROJECT-SPECIFIC - REPLACE ME]
- [PROJECT-SPECIFIC - REPLACE ME]

**Monetization:** [PROJECT-SPECIFIC - REPLACE ME] (e.g. subscription, internal tool, one-off)

**Target audience:** [PROJECT-SPECIFIC - REPLACE ME]

## Architecture

> [PROJECT-SPECIFIC - REPLACE ME] - one-paragraph summary of how the system is
> organized (monolith, modular monolith, microservices, etc.) and the major
> top-level units. Example shape for a modular monolith:

- **{{MODULE_A}}** - [PROJECT-SPECIFIC - REPLACE ME] (responsibility of this module)
- **{{MODULE_B}}** - [PROJECT-SPECIFIC - REPLACE ME]
- **{{MODULE_C}}** - [PROJECT-SPECIFIC - REPLACE ME]

Full architecture details live in `project/architecture.md`.

## Domain terminology

> [PROJECT-SPECIFIC - REPLACE ME] - define the project's core domain terms so
> agents use them consistently. Example shape:

- `{{TERM_1}}` - [PROJECT-SPECIFIC - REPLACE ME] (what it means in this domain)
- `{{TERM_2}}` - [PROJECT-SPECIFIC - REPLACE ME]
- `{{TERM_3}}` - [PROJECT-SPECIFIC - REPLACE ME]

## Integration / external API knowledge (project-specific quirks)

> [PROJECT-SPECIFIC - REPLACE ME] - record any non-obvious behavior of external
> APIs or services this project integrates with, verified against the real API.
> This section prevents repeating integration mistakes. Delete it if the project
> has no external integrations. Example shape:

### {{EXTERNAL_SERVICE}}
- [PROJECT-SPECIFIC - REPLACE ME] (endpoint, auth header, pagination quirk, field mapping, etc.)

## Workspace layout

> [PROJECT-SPECIFIC - REPLACE ME] - describe how the repo / workspace is laid out
> and where CLI commands run from. Example shape:

| Directory | Description | Git repo? |
|-----------|-------------|-----------|
| [PROJECT-SPECIFIC - REPLACE ME] | Main application code | Yes |
| [PROJECT-SPECIFIC - REPLACE ME] | Frontend / secondary code | [PROJECT-SPECIFIC - REPLACE ME] |

## Project phases (current activation state)

### Active Phases (Backend API Focus)

| Phase | Name | Key Agents | Gate |
|-------|------|-----------|------|
| 6 | Technical Architecture | Architect, Sr. Backend Dev | CEO approves technical design |
| 7 | Infrastructure Setup | DevOps Engineer, Architect | CEO verifies infrastructure runs |
| 8 | Implementation | Sr. Backend Dev, DevOps Engineer | Features pass health checks |
| 9 | Testing & QA | QA Engineer, Reviewer | All tests pass, security audit clean |

### Deferred Phases

| Phase | Name | When to Activate |
|-------|------|-----------------|
| 1-5 | Ideation → Whitepaper | When business planning begins |
| 10 | Mobile App | When mobile development starts |
| 12 | Marketing & GTM | When go-to-market planning begins |
| 13 | Risk Management | When risk assessment is needed |
| 14 | Growth & Scaling | When scaling planning begins |
