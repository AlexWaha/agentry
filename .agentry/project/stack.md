# Stack

> **[PROJECT-SPECIFIC - REPLACE ME]**
> Concrete stack, commands, and tooling for the current project. Replace every
> value below when adapting the template to a new project. The example rows show
> the expected shape - swap them for your real stack.

## Tech stack

> Replace with the actual layers your project uses.

| Layer | Technology | Status |
|-------|-----------|--------|
| Backend | `{{BACKEND_STACK}}` | ACTIVE |
| Database | `{{DEFAULT_DB}}` | ACTIVE |
| Cache/Queue | [PROJECT-SPECIFIC - REPLACE ME] | ACTIVE |
| Auth | `{{AUTH_METHOD}}` | ACTIVE |
| Testing | `{{TEST_FRAMEWORK}}` | ACTIVE |
| Code Style | `{{FORMATTER}}` | ACTIVE |
| Frontend Web | [PROJECT-SPECIFIC - REPLACE ME] | ACTIVE |
| Frontend Mobile | [PROJECT-SPECIFIC - REPLACE ME] | PLANNED |
| Infrastructure | `{{CONTAINER_TOOL}}`, CI/CD (`{{CI_TOOL}}`) | ACTIVE |

## Placeholder values for CLAUDE.md

Fill in every row with the detected/decided value for this project, then copy
these into the `{{...}}` placeholders across `.claude/` (including
`.claude/CLAUDE.md`). This table is the canonical source of truth for all tokens.

| Placeholder | Value |
|-------------|-------|
| `{{PROJECT_NAME}}` | [PROJECT-SPECIFIC - REPLACE ME] |
| `{{PRODUCT_DESCRIPTION}}` | [PROJECT-SPECIFIC - REPLACE ME] |
| `{{STACK}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. Laravel 12 + React 19) |
| `{{LANG}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. PHP 8.4 / TypeScript) |
| `{{FRAMEWORK}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. Laravel 12) |
| `{{TEST_CMD}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. `composer test`, `npm test`, `pytest`) |
| `{{FORMAT_CMD}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. `composer format`, `npm run format`) |
| `{{LINT_CMD}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. `composer format-lint`, `npm run lint`) |
| `{{BUILD_CMD}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. `npm run build`) |
| `{{DEV_CMD}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. `npm run dev`, `php artisan serve`) |
| `{{MAIN_BRANCH}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. `main`) |
| `{{DEFAULT_DB}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. PostgreSQL 16, MySQL 8) |
| `{{AUTH_METHOD}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. Laravel Sanctum, JWT, OAuth2) |
| `{{BACKEND_STACK}}` | [PROJECT-SPECIFIC - REPLACE ME] |
| `{{TEST_FRAMEWORK}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. Pest 4, Jest, pytest) |
| `{{FORMATTER}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. Laravel Pint, Prettier, Black) |
| `{{CONTAINER_TOOL}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. Docker Compose) |
| `{{CI_TOOL}}` | [PROJECT-SPECIFIC - REPLACE ME] (e.g. GitHub Actions) |

## Commands

> Replace these example rows with the real commands for this project. Keep the
> canonical four (test/format/lint/build) plus any project-specific scripts.

| Command | Description |
|---|---|
| `{{TEST_CMD}}` | Run the test suite |
| `{{FORMAT_CMD}}` | Auto-fix code style |
| `{{LINT_CMD}}` | Check code style without modifying files |
| `{{BUILD_CMD}}` | Build the project |
| `{{DEV_CMD}}` | Start the dev server |
| [PROJECT-SPECIFIC - REPLACE ME] | Any project-specific CLI command (data import, codegen, etc.) |

## API routes

> [PROJECT-SPECIFIC - REPLACE ME] - list the real endpoints once they exist.

| Method | URI | Auth | Description |
|---|---|---|---|
| GET | `/api/health` | No | Health check |
| GET | `/api/v1/{{RESOURCE}}` | `{{AUTH_METHOD}}` | [PROJECT-SPECIFIC - REPLACE ME] |

## Environment Variables

> [PROJECT-SPECIFIC - REPLACE ME] - list the real env vars this project needs.
> Keep secrets out of git; document the variable name and value shape only.

| Variable | Description | Default |
|---|---|---|
| [PROJECT-SPECIFIC - REPLACE ME] | Purpose of the variable | - |

## Project structure

> [PROJECT-SPECIFIC - REPLACE ME] - sketch the real top-level layout. Example:

```
src/                # or app/, Modules/, packages/ - whatever this project uses
  ...               # [PROJECT-SPECIFIC - REPLACE ME]
```

Detailed file/module layout lives in `project/architecture.md`.
