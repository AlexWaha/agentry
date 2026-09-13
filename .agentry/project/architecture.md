# Architecture

> **[PROJECT-SPECIFIC - REPLACE ME]**
> This document describes the current project's architecture. When adapting the
> template to a new project, replace the project-specific blocks (the example
> directory trees and module names) with your own architecture decisions. The
> generic layering and dependency rules below are a sensible default for a
> server-side app - keep, adapt, or replace them as needed. The root
> `rules/architecture.md` references this file.

System architecture rules governing project structure, dependency flow, service boundaries, and technical constraints.

---

## Project Structure

> [PROJECT-SPECIFIC - REPLACE ME] - replace this tree with the real top-level
> layout of your project. The example below is for a modular monolith; a flat
> framework app, a monorepo, or a service-per-folder layout would look different.

```
project-root/
├── .claude/                  # AI agent configuration (this template)
├── docs/                     # All documentation (business, technical, etc.)
│
├── {{SRC_DIR}}/              # Application code - [PROJECT-SPECIFIC - REPLACE ME]
│   └── ...                   # modules / packages / layers as the project requires
│
├── config/                   # Framework and custom config
├── database/                 # Migrations, factories, seeders (if applicable)
├── routes/                   # Route definitions (if applicable)
├── tests/                    # Test suites
├── .env.example
├── CHANGELOG.md
└── README.md
```

### Module / Package Structure (example)

> [PROJECT-SPECIFIC - REPLACE ME] - if the project is split into modules or
> packages, document each one's responsibility and internal layout here. The
> example below shows the SHAPE for a modular monolith - swap in your real module
> names and contents.

```
{{SRC_DIR}}/
├── {{MODULE_A}}/             # [PROJECT-SPECIFIC - REPLACE ME] (shared/core: models, enums, contracts, DTOs)
│   ├── app/
│   │   ├── Console/Commands/
│   │   ├── Contracts/
│   │   ├── Enums/
│   │   ├── Http/
│   │   ├── Models/
│   │   ├── Services/
│   │   └── Providers/
│   └── database/
├── {{MODULE_B}}/             # [PROJECT-SPECIFIC - REPLACE ME] (feature domain)
│   └── app/
│       ├── Http/Controllers/
│       ├── Jobs/
│       ├── Models/
│       └── Services/
└── {{MODULE_C}}/             # [PROJECT-SPECIFIC - REPLACE ME] (another feature domain)
```

### Frontend Structure (if applicable)

> [PROJECT-SPECIFIC - REPLACE ME] - delete this section if the project has no
> frontend. The example below shows a shared-package monorepo for web + mobile.

```
frontend/
├── packages/
│   └── shared/               # Shared business logic (hooks, types, API client)
│       └── src/
│           ├── api/          # API client and request functions
│           ├── hooks/        # Shared custom hooks
│           ├── types/        # TypeScript interfaces and types
│           └── utils/        # Pure utility functions
├── web/                      # Web application
│   └── src/
│       ├── components/       # UI components
│       ├── pages/            # Page-level components
│       └── stores/           # State management
└── mobile/                   # Mobile application (if any)
```

## Where Does New Code Go?

> [PROJECT-SPECIFIC - REPLACE ME] - adjust paths to match your real structure.
> The table below assumes a modular layout; simplify for a flat framework app.

### Backend code (primary location for new features)

| Type | Location |
|------|----------|
| New model | `{{SRC_DIR}}/{{Module}}/app/Models/` |
| New controller | `{{SRC_DIR}}/{{Module}}/app/Http/Controllers/` |
| New service | `{{SRC_DIR}}/{{Module}}/app/Services/` |
| New request/validator | `{{SRC_DIR}}/{{Module}}/app/Http/Requests/` |
| New resource/serializer | `{{SRC_DIR}}/{{Module}}/app/Http/Resources/` |
| New job/worker | `{{SRC_DIR}}/{{Module}}/app/Jobs/` |
| New CLI command | `{{SRC_DIR}}/{{Module}}/app/Console/Commands/` |
| New migration | `{{SRC_DIR}}/{{Module}}/database/migrations/` |
| New factory | `{{SRC_DIR}}/{{Module}}/database/factories/` |
| New enum | `{{SRC_DIR}}/{{Module}}/app/Enums/` (or the shared/core module) |
| New contract/interface | the shared/core module's `Contracts/` |

### Frontend (if applicable)

| Type | Location |
|------|----------|
| Shared frontend code | `frontend/packages/shared/src/` |
| Web component | `frontend/web/src/components/` |
| Mobile component | `frontend/mobile/src/components/` |

**Always check sibling files** before creating anything new. Match existing naming, structure, and patterns.

---

## Dependency Rules

Strict layered architecture. Upper layers depend on lower layers, never the reverse.

```
Controllers
    ↓
Services, FormRequests, API Resources
    ↓
Models, Data classes, Enums
    ↓
Database
```

### Allowed Dependencies

| Layer | Can Depend On |
|-------|---------------|
| Controllers | Services, FormRequests, API Resources, Policies |
| Services | Models, other Services, Data classes, Events |
| Models | Other Models (relationships), Enums |
| Jobs | Receive only primitives or model IDs - resolve models inside `handle()` |
| Events | Models (via constructor), primitives |
| Listeners | Services, Models |
| Policies | Models, Enums |
| Data classes | Other Data classes, Enums |
| Middleware | Config values, Auth facade |

### Forbidden Dependencies

| Layer | Must NEVER Depend On |
|-------|---------------------|
| Models | Services, Controllers, Jobs, Events |
| Services | Controllers, FormRequests, API Resources |
| Jobs | Full model instances in constructor (pass IDs instead) |
| Middleware | Services, Models (except the authenticated user) |
| Config files | Services, Models |

### Module Boundary Rules

> Adjust to your project. Applies only if the project is split into modules.

- Controllers in Module X should **not** directly use Models from Module Y - go through a shared contract (interface)
- Cross-module dependencies go through service interfaces defined in the shared/core module
- Shared enums, DTOs, and base models live in the shared/core module
- If Module A needs data from Module B, Module B exposes a contract, and Module A depends on that contract

---

## Request Lifecycle

### API Request

```
HTTP Request
  → Route matching
    → Global middleware (rate limit, CORS, JSON response)
      → Route middleware (auth)
        → Request validation + authorization
          → Controller method
            → Service (business logic)
              → Model (data access)
            → API Resource (response formatting)
          → JSON Response
```

### Job / Worker Execution

```
Service dispatches Job with primitive data (IDs, strings)
  → Queue picks up Job
    → Job::handle() resolves models from IDs
      → Service performs business logic
        → Events dispatched for side effects
```

### Event Flow

```
Service performs action
  → Event dispatched (synchronous or via queue)
    → Listener(s) handle side effects
      → Notification sent
      → Cache invalidated
      → Analytics tracked
```

---

## Module Conventions (if applicable)

> [PROJECT-SPECIFIC - REPLACE ME] - keep only if the project uses a module system
> (e.g. a modular monolith). Describe how modules communicate and stay independent.

### Module Communication

- Modules communicate through contracts (interfaces) in the shared/core module
- Cross-module dependencies go through service interfaces, not direct model imports
- Shared enums, DTOs, and base models live in the shared/core module

### Module Independence

- Each module has its own migrations, factories, routes, config
- Module-specific models stay in their module's `app/Models/`
- Shared models live in the shared/core module

---

## Database Conventions

> [PROJECT-SPECIFIC - REPLACE ME] - adjust to your actual database engine and
> conventions. The defaults below are sensible for a relational DB.

- All tables use an auto-incrementing primary key
- `created_at`, `updated_at` timestamps on all tables
- Soft deletes (`deleted_at`) on content tables that need recovery
- Foreign keys with appropriate `ON DELETE` behavior
- Indexes on all columns used in `WHERE`, `JOIN`, `ORDER BY`
- JSON columns for flexible settings and metadata where the engine supports them

### Migration Rules

- **Migrations are schema-only** - no data manipulation in migrations (no INSERT, UPDATE, DELETE). Use a dedicated data-migration mechanism for one-time data operations.
- **NEVER drop columns with user data** without explicit approval and a verified backfill. Always: add new column, backfill, verify, THEN drop old column in a separate migration.
- **Before any destructive migration** (drop column, drop table, change column type) - verify existing data will not be lost.
- **Migrations must not delete user records** - ever.
- One migration per change (do not combine unrelated schema changes)
- Migrations must be reversible (implement the down path)
- Never modify a migration that has been pushed - create a new one
- Use descriptive names: `create_<table>_table`, `add_<column>_to_<table>_table`

### Seed Data Storage

Reference data with a finite, known dataset (countries, currencies, languages, timezones) should be seeded via a data migration, a JSON fixture, or a small inline seeder - in that order of preference. Avoid PHP/code array files masquerading as "data fixtures": if it is code it goes in a migration; if it is data it goes in a structured fixture (JSON/YAML).

---

## What NOT to Do

- **No business logic** in controllers, models, or middleware
- **No raw DB facade / raw SQL** when the ORM/query builder suffices
- **No inline validation** in controllers - always use request/validator classes
- **No raw model returns** from API endpoints - always use API Resources/serializers
- **No new base folders** in the source root without team approval
- **No direct file system access** from API controllers - use the service layer
- **No circular dependencies** between services or modules
- **No God classes** - if a service exceeds ~300 lines, split by responsibility
- **No global state** - no singletons for business logic, no static mutable state
- **No reading env vars outside config files** - always go through config
- **No cross-module direct model imports** - use shared contracts
