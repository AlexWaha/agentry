# Infrastructure

Docker-based development environment, CI/CD pipelines, deployment strategy, and environment management. Stack-specific versions and service names live in `project/stack.md` - this file defines the universal shape.

---

## Docker Compose (Local Development)

All services run in Docker containers. The development environment must be reproducible across all machines with a single command.

### Service Shape

Every project will have some subset of these roles. Exact images, ports, and versions are recorded in `project/stack.md`.

| Role | Placeholder | Typical Image |
|------|-------------|---------------|
| Backend runtime | `{{BACKEND_RUNTIME}}` | `php:8.4-fpm`, `node:22-slim`, `python:3.12-slim`, `ruby:3.3`, `golang:1.23`, `eclipse-temurin:21` |
| Web server / reverse proxy | `nginx` or `traefik` | `nginx:1.27`, `traefik:v3` |
| Primary database | `{{DEFAULT_DB}}` | `postgres:16`, `mysql:8`, `mariadb:11` |
| Cache / queue | `{{CACHE_BACKEND}}` | `redis:7`, `memcached:1.6`, `valkey:8` |
| Frontend runtime (if separate) | `{{FRONTEND_RUNTIME}}` | `node:22-slim`, `bun:1` |
| Object storage (optional) | `minio` | `minio/minio:latest-stable` |

### Docker Compose Skeleton

Generic shape - adapt to the stack chosen in `project/stack.md`.

```yaml
services:
  # {{BACKEND_RUNTIME}} - application runtime
  # app:
  #   build:
  #     context: ./docker/app
  #     dockerfile: Dockerfile
  #   container_name: app
  #   volumes:
  #     - ./src:/app
  #   depends_on:
  #     db:    { condition: service_healthy }
  #     cache: { condition: service_healthy }
  #   networks: [app-network]

  # Reverse proxy / web server
  # web:
  #   image: nginx:1.27
  #   ports: ["8080:80"]
  #   volumes:
  #     - ./conf/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
  #   depends_on: [app]
  #   networks: [app-network]

  # {{DEFAULT_DB}} - primary database
  db:
    image: postgres:16          # or: mysql:8, mariadb:11
    container_name: db
    ports: ["5432:5432"]        # or: "3306:3306" for MySQL
    environment:
      POSTGRES_DB: ${DB_DATABASE:-app}
      POSTGRES_USER: ${DB_USERNAME:-app}
      POSTGRES_PASSWORD: ${DB_PASSWORD:-secret}
    volumes:
      - ./data/db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "${DB_USERNAME:-app}"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks: [app-network]

  # {{CACHE_BACKEND}} - cache / queue / session store
  cache:
    image: redis:7
    container_name: cache
    ports: ["6379:6379"]
    volumes:
      - ./data/cache:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks: [app-network]

  # {{FRONTEND_RUNTIME}} - optional, only if frontend has a separate dev server
  # frontend:
  #   build: { context: ./docker/frontend, dockerfile: Dockerfile }
  #   ports: ["5173:5173"]
  #   volumes: [./frontend:/app]
  #   working_dir: /app
  #   command: npm run dev -- --host
  #   networks: [app-network]

networks:
  app-network:
    driver: bridge
```

### Docker Image Rules

- **Always use explicit version tags**: `postgres:16`, `redis:7`, `nginx:1.27`, `node:22-slim` - never `latest`
- **Never** use `latest` for anything: it breaks reproducibility between machines and over time
- Minimize image layers - combine related `RUN` commands into one
- Clean up package manager cache in the same layer as install (`apt-get clean && rm -rf /var/lib/apt/lists/*`, `apk --no-cache`, `rm -rf ~/.cache/pip`)
- Run as a non-root user in every image (`USER app` / `USER node` / `USER nobody`)
- Pin sub-dependencies where stability matters (e.g. Composer version in multi-stage PHP builds)

### `[EXAMPLE - Backend Dockerfile (PHP-FPM)]`

Adapt to your language's base image - the structure (system deps → runtime → app user → expose → cmd) stays the same.

```dockerfile
FROM php:8.4-fpm

RUN apt-get update && apt-get install -y \
    git curl libzip-dev libpng-dev libjpeg-dev unzip \
    && docker-php-ext-install pdo pdo_mysql zip gd bcmath pcntl \
    && pecl install redis && docker-php-ext-enable redis \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
WORKDIR /app

RUN groupadd -g 1000 app && useradd -u 1000 -g app -m app
USER app

EXPOSE 9000
CMD ["php-fpm"]
```

### `[EXAMPLE - Frontend Dockerfile (Node LTS)]`

```dockerfile
FROM node:22-slim
WORKDIR /app
RUN npm install -g npm@latest
USER node
EXPOSE 5173
```

For other runtimes the pattern is identical: pick an explicit version tag, install system deps, copy in dependency manifests, install, drop to a non-root user, expose, set CMD.

---

## Running Commands

Commands that talk to the framework's CLI (migrations, tests, linters, code generators) run **natively on the host** where practical - host runtime matches the container runtime version exactly, and IDE tooling works without proxying through docker-exec.

Container commands (starting services, tailing logs, orchestration) run against the Docker daemon.

Exact commands for this project are in `project/stack.md`. The universal shape:

```bash
# Framework CLI (host-native)
<framework> migrate
<framework> generate <type> <name>
<framework> test
<framework> format

# Package manager (host-native)
<pkg-manager> install
<pkg-manager> add <package>

# Host git
git status
git commit -m "message"

# Docker orchestration
docker compose up -d
docker compose down
docker compose logs -f app
```

---

## Configuration Files

### Reverse Proxy (Nginx / Traefik)

Every stack needs a reverse proxy in front of the app for TLS termination, static file serving, and request size limits. Example below is Nginx fronting a PHP-FPM app - adapt upstream and path handling to your runtime.

```nginx
server {
    listen 80;
    server_name localhost;
    root /app/public;
    index index.php index.html;

    client_max_body_size 20M;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ \.php$ {
        fastcgi_pass app:9000;
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include fastcgi_params;
    }

    location ~ /\.ht { deny all; }
}
```

For a Node / Python / Ruby / Go app the upstream becomes `proxy_pass http://app:PORT;` - the rest (request size, static files, SSL) is the same.

### Runtime Tuning

Every runtime has its own tuning file. Example below is `php.ini` - but the concerns are universal: memory limit, upload size, execution timeout, opcode cache, error reporting.

```ini
[PHP]
memory_limit = 256M
upload_max_filesize = 20M
post_max_size = 25M
max_execution_time = 60
display_errors = On
error_reporting = E_ALL

[opcache]
opcache.enable = 1
opcache.memory_consumption = 128
opcache.max_accelerated_files = 10000
opcache.validate_timestamps = 1
opcache.revalidate_freq = 0
```

For Node: `--max-old-space-size`, cluster workers. Python: `gunicorn` worker count, `WEB_CONCURRENCY`. Ruby: `puma` workers/threads. Go: `GOMAXPROCS`. Record in `project/stack.md`.

---

## Environment Variables

### Local Development

Use `.env` (or equivalent: `.env.local`, `application.yml` overrides, `direnv`) at the application root. Specific variable names and defaults live in `project/stack.md`.

```env
APP_NAME={{PROJECT_NAME}}
APP_ENV=local
APP_DEBUG=true
APP_URL=http://localhost:8080

DB_HOST=127.0.0.1
DB_PORT=5432
DB_DATABASE={{PROJECT_DB_NAME}}
DB_USERNAME={{PROJECT_DB_USER}}
DB_PASSWORD=secret

CACHE_URL=redis://cache:6379
QUEUE_URL=redis://cache:6379
```

### Staging / Production

- Managed via a **secrets manager** - GitHub Actions Secrets, AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, Doppler, 1Password - never in VCS
- `.env` is **never** committed
- `.env.example` **is** committed, kept up to date with every required variable (placeholder values only)
- Sensitive values are injected at deployment / container start, not baked into images

---

## CI/CD

### `[EXAMPLE - GitHub Actions]`

The pipeline below is GitHub Actions, but the exact same stages apply on GitLab CI, CircleCI, Buildkite, Jenkins, or Drone - rename the YAML keys, the jobs and tools stay identical.

```yaml
name: PR Checks

on:
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Set up {{BACKEND_RUNTIME}}
      # - uses: shivammathur/setup-php@v2
      #   with: { php-version: '8.4' }
      # - uses: actions/setup-node@v4
      #   with: { node-version: '22' }
      # - uses: actions/setup-python@v5
      #   with: { python-version: '3.12' }
      - run: <pkg-manager> install --frozen-lockfile
      - run: <formatter> --check

  test:
    runs-on: ubuntu-latest
    services:
      db:
        image: postgres:16             # or mysql:8
        env:
          POSTGRES_DB: testing
          POSTGRES_USER: testing
          POSTGRES_PASSWORD: testing
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U testing"
          --health-interval 10s --health-timeout 5s --health-retries 5
      cache:
        image: redis:7
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - run: <pkg-manager> install --frozen-lockfile
      - run: cp .env.ci .env
      - run: <framework> migrate --no-interaction
      - run: <framework> test
```

### Branch / Tag Pipelines

- **Push to `main`** → build, push Docker images, deploy to staging
- **Release tag / manual dispatch** → deploy to production
- All deploys require green CI on the commit being shipped

```yaml
# Staging deploy
on:
  push:
    branches: [main]

# Production deploy
on:
  release:
    types: [published]
  workflow_dispatch:
```

---

## Environments

| Environment | Purpose | Database | Deployment |
|-------------|---------|----------|------------|
| `local` | Developer workstations | Docker DB | `docker compose up -d` |
| `testing` | CI test runs | CI service container | Automated on PR |
| `staging` | Pre-production verification | Managed DB | Auto-deploy on `main` push |
| `production` | Live users | Managed DB | Manual release / release tag |

### Environment Parity

Keep all environments as similar as possible:
- Same runtime major+minor version across local / CI / staging / production
- Same database engine and major version
- Same cache backend and major version
- Same OS family (Linux) for the container base
- Same reverse proxy configuration, adapted for TLS in staging / production

Exact versions for this project: see `project/stack.md`.

---

## Volume Management

```
project/
├── data/
│   ├── db/          # Database data (gitignored)
│   └── cache/       # Cache / queue data (gitignored)
├── logs/
│   ├── web/         # Reverse proxy logs (gitignored)
│   └── app/         # Application logs (gitignored)
```

All data and log directories are in `.gitignore`. Docker creates them automatically via the volume mounts.

---

## Health Checks

Every service must have a health check. The compose file uses `healthcheck:` blocks (see skeleton above) and the application exposes a dedicated endpoint.

Universal health check expectations:

- Database: vendor's ping command (`pg_isready`, `mysqladmin ping`, `redis-cli ping`)
- Cache: `PING` or equivalent
- Web server: HTTP GET on the health endpoint returning 200
- Application: a dedicated `/health` (or `/healthz`) endpoint that verifies the process is up and dependencies are reachable

### Generic health endpoint (pseudocode)

```pseudo
GET /health
→ 200 { status: "ok", timestamp: now(), checks: { db: "ok", cache: "ok" } }
→ 503 { status: "degraded", ...details }
```

### `[EXAMPLE - Laravel]`

```php
Route::get('/api/health', function () {
    return response()->json([
        'status'    => 'ok',
        'timestamp' => now()->toISOString(),
    ]);
})->name('api.health');
```

Equivalents: Express `app.get('/health', ...)`, FastAPI `@app.get('/health')`, Rails `get '/health', to: ...`, Spring `@GetMapping("/health")`. Wire your orchestrator (Kubernetes liveness/readiness, ECS healthcheck, Docker Swarm) to this endpoint.

---

## Backup Strategy

- **Database** - automated daily backups in staging / production; retention policy documented; restore tested at least quarterly
- **Object / media storage** - versioning enabled on the bucket; lifecycle policy for old versions
- **Configuration** - all config in VCS (except secrets); secrets manager has its own backup cadence
- **Recovery** - a written runbook lives in `docs/technical/`; tested end-to-end at least once per quarter; RTO and RPO targets documented

**Backups that have never been restored are not backups.** The first real test of a backup must not be during an incident.
