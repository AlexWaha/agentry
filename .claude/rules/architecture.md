# Architecture

> **Project-specific architecture lives in `.claude/project/architecture.md`.**
> That file describes the current project's module/folder layout, dependency rules, and framework-specific conventions. Read it alongside this one.

This file covers the universal principles that apply regardless of stack.

---

## Universal Principles

### Layered Architecture

Upper layers depend on lower layers. Never the reverse.

```
Presentation (controllers, routes, UI components)
    ↓
Application (services, use cases, jobs, FormRequests/validators)
    ↓
Domain (models, entities, enums, value objects)
    ↓
Infrastructure (database, queues, external APIs, filesystem)
```

### Allowed Dependencies

| Layer | Can Depend On |
|-------|---------------|
| Controllers / Route handlers | Services, validators, API resources / DTOs, policies |
| Services | Models, other services, domain types, events |
| Models / Entities | Other models (relationships), enums, value objects |
| Jobs / Workers | Receive primitives or IDs only - resolve entities inside the handler |
| Validators / FormRequests | Enums, policies, value objects |
| Middleware | Config, auth context |

### Forbidden Dependencies

| Layer | Must NEVER Depend On |
|-------|---------------------|
| Models / Entities | Services, controllers, jobs, events |
| Services | Controllers, validators, API resources |
| Jobs | Full entity instances in constructor (pass IDs instead) |
| Middleware | Services or models (except auth context) |
| Config files | Services or models (config is data, not behavior) |

### Module / Boundary Rules

- Modules communicate through contracts (interfaces), not through concrete types from another module
- Shared primitives (enums, DTOs, value objects, interfaces) live in a shared/core module
- If module A needs data from module B, B exposes a contract; A depends on the contract

### Where New Code Goes

Always mirror the project's existing structure. Check sibling files for naming, layout, and convention before creating anything new. Specifics live in `project/architecture.md`.

### Database Conventions (Universal)

- Every table has a primary key
- Every mutable table has `created_at` / `updated_at` (or equivalent)
- Use soft deletes for user-generated content
- Foreign keys have explicit `ON DELETE` behavior
- Index every column used in `WHERE`, `JOIN`, or `ORDER BY`
- Migrations are schema-only - no data manipulation in schema migrations
- Never drop columns with user data without explicit approval and verified backfill

### What NOT to Do

- No business logic in controllers, models, or middleware
- No raw SQL where the ORM is sufficient
- No inline validation in controllers - use dedicated request/validator classes
- No raw entity returns from APIs - always wrap in resources / DTOs
- No circular dependencies between modules or services
- No God classes - if a service exceeds ~300 lines, split by responsibility
- No global mutable state
- No direct config env reads outside the config layer
- No cross-module direct entity imports - use contracts
