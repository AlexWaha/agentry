# Infrastructure Config Examples

Reference examples for container orchestration, Dockerfiles, and CI/CD
pipelines. All snippets are `[EXAMPLE - <tool>]` - adapt to the project's
actual stack per `.agentry/project/stack.md`.

## Docker Compose Service Definition

[EXAMPLE - Docker Compose - adapt to `{{CONTAINER_TOOL}}`]

```yaml
services:
  app:
    build:
      context: .
      dockerfile: docker/app/Dockerfile
    volumes:
      - .:/var/www/html
    depends_on:
      db:
        condition: service_healthy
      cache:
        condition: service_healthy
    environment:
      - APP_ENV=local
    networks:
      - app-network

  db:
    image: <pinned-image>:<version>
    volumes:
      - db-data:/var/lib/db
    environment:
      DATABASE_NAME: ${DB_NAME}
      DATABASE_USER: ${DB_USER}
      DATABASE_PASSWORD: ${DB_PASSWORD}
    healthcheck:
      test: ["CMD", "<db-ping-command>"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - app-network
```

Typical services (adjust to the stack defined in `project/stack.md`): **app**
(runtime for `{{LANG}}` / `{{FRAMEWORK}}`), **proxy** (nginx, Caddy, Traefik),
**db** (`{{DEFAULT_DB}}`), **cache** (`{{CACHE_BACKEND}}`), **frontend**
(Node.js build), **worker** (queue processor, if the stack has queues),
**scheduler** (cron runner, if the stack has scheduled tasks).

## Dockerfile Skeleton

[EXAMPLE - generic Dockerfile - adapt to your stack]

```dockerfile
FROM <runtime>:<version> AS base
# Install system dependencies
# Install language-specific dependencies
# Configure runtime

FROM base AS development
# Install dev tools (debugger, test runners)

FROM base AS production
# Copy only production files
# Optimize / compile / precompile as needed
# Set proper permissions
# Run as non-root user
```

Standards: multi-stage builds (builder + production), specific version tags
(never `latest`), non-root user, health check instructions, `.dockerignore`,
dependency install before code copy so the dep layer caches.

## CI/CD Pipeline

[EXAMPLE - GitHub Actions - adapt to `{{CI_TOOL}}` (GitLab CI, CircleCI, Jenkins, etc.)]

```yaml
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  lint:
    # Run formatter check + language linter
  test:
    # Run backend tests ({{TEST_CMD}}) and frontend tests
    # DB and cache as services
  security:
    # Dependency vulnerability scan
    # Forbidden pattern check (debug functions, direct env access outside config, hardcoded secrets)
  build:
    # Build container images
    # Tag with git SHA
  deploy-staging:
    # Deploy to staging on push to main
    # Run migrations
    # Health check verification
```

Required CI checks for every PR:

1. **Formatter** - `{{LINT_CMD}}` (check-only)
2. **Backend tests** - `{{TEST_CMD}}`
3. **Frontend lint + tests** - when frontend exists
4. **Forbidden pattern scan** - no debug dumps, no direct env access outside config, no hardcoded secrets, no hardcoded fallback locale strings
5. **Dependency audit** - language-native dep audit command (composer audit / npm audit / pip-audit / cargo audit / etc.)

## Environment Layout

```
Environments:
├── local      -> containers on developer machine, local .env file, development mode
├── staging    -> Deployed on push to main, staging secrets
└── production -> Manual deploy trigger, production secrets
```

- Local: env file with development defaults
- Staging/Production: secrets via CI secrets store or a dedicated secrets manager
- Never commit env files with secrets to git (enforce via `.gitignore`)
- Provide `.env.example` with all required variables (names + placeholder values, no real secrets)

## Command Reference

```bash
# Containers
{{CONTAINER_TOOL}} up -d
{{CONTAINER_TOOL}} build --no-cache
{{CONTAINER_TOOL}} logs -f app
{{CONTAINER_TOOL}} ps

# Migrations / framework CLI - see project/stack.md
# Tests / formatter - see project/stack.md
{{TEST_CMD}}
{{FORMAT_CMD}}
{{LINT_CMD}}
```
