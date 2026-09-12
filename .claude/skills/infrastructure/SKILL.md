---
name: infrastructure
description: Set up complete Docker-based dev infrastructure - Compose services, Dockerfiles, reverse proxy, runtime tuning, env files, and CI/CD pipeline - then verify every container passes its health check. Use when a task says "set up infrastructure", "add Docker", "configure CI/CD", "add a new service to docker-compose", or when scaffolding a new project's deployment stack.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Infrastructure

Set up the complete development infrastructure: Docker Compose with all services, Dockerfiles, reverse proxy configuration, runtime tuning, environment files, and CI/CD pipeline. Concrete images, versions, ports, and commands live in `.claude/project/stack.md`; this skill uses placeholders (`{{BACKEND_RUNTIME}}`, `{{DEFAULT_DB}}`, `{{CACHE_BACKEND}}`, `{{FRONTEND_RUNTIME}}`, `{{PKG_MANAGER}}`, `{{MIGRATION_TOOL}}`, `{{TEST_CMD}}`, `{{FORMAT_CMD}}`, `{{LINT_CMD}}`) that resolve against the project overlay.

## Steps

1. **Define services.** Pick the subset this project needs from: backend runtime, reverse proxy/web server, primary database, cache/queue, frontend runtime (optional), object storage (optional). Pin explicit version tags (never `latest`), give every service a healthcheck, put them all on a named bridge network. Image choices per role: references/infra-examples.md.
2. **Write `docker-compose.yml`** at the project root: one service per role from Step 1, `depends_on` with `condition: service_healthy`, env vars for ports/credentials with sane local defaults. Full skeleton: references/infra-examples.md.
3. **Write the backend Dockerfile** at `docker/app/Dockerfile` following: base image -> system deps -> runtime extensions/packages -> package manager -> working dir -> non-root user -> expose -> CMD. Per-runtime examples (PHP-FPM, Node, Python, Go): references/infra-examples.md.
4. **Write the reverse proxy config** at `conf/web/default.conf` following: TLS termination -> static files -> request size -> upstream -> timeouts -> security headers -> deny hidden files. Nginx example: references/infra-examples.md.
5. **Tune the runtime.** Set memory limit, upload size, execution timeout, opcode/JIT cache, error reporting, timezone, session security for the chosen runtime. Per-runtime examples (php.ini, Node env, gunicorn config): references/infra-examples.md.
6. **Write `.env.example`** with every variable referenced by `docker-compose.yml` or the runtime config, placeholder values only, never real secrets. Full example: references/infra-examples.md.
7. **Set up the CI/CD pipeline** (`.github/workflows/ci.yml` or equivalent) following: lint -> test -> (optional) frontend -> build image. Services (db, cache) run as CI service containers with healthchecks. Full workflow example: references/infra-examples.md.
8. **Update `.gitignore`** for persistent data/logs, env files, dependency directories, and build artifacts. Full list: references/infra-examples.md.
9. **Document the infrastructure** in `docs/technical/infrastructure.md`: service inventory, getting-started steps, common commands, environment variables, troubleshooting.
10. **Verify everything works.** `docker compose up -d --build`, confirm every service shows "healthy" in `docker compose ps`, run `{{MIGRATION_TOOL}} status` and `{{TEST_CMD}}` inside the app container, then `curl` the health endpoint. On failure, check `docker compose logs <service>` per service.

## Output checklist

- [ ] `docker-compose.yml` - all services defined, pinned tags, healthchecks
- [ ] `docker/app/Dockerfile` - backend runtime with required system deps, non-root user
- [ ] `conf/web/default.conf` - reverse proxy with security headers and timeouts
- [ ] Runtime tuning file in place (memory, upload, timeouts, cache, timezone)
- [ ] `.env.example` - every variable documented with placeholder values only
- [ ] CI pipeline - lint + test jobs, frontend job if applicable
- [ ] `.gitignore` updated for data/logs, dependency and build directories
- [ ] `docs/technical/infrastructure.md` - setup guide and command reference
- [ ] All containers start and pass healthchecks; backend reaches `{{DEFAULT_DB}}` and `{{CACHE_BACKEND}}`; reverse proxy serves the app; health endpoint returns 200

## References (read only when needed)

- [references/infra-examples.md](references/infra-examples.md) - full docker-compose.yml, per-runtime Dockerfiles, nginx config, runtime tuning files, .env.example, .gitignore, and GitHub Actions workflow
- [references/config-examples.md](references/config-examples.md) - condensed config skeletons and a Docker command reference
