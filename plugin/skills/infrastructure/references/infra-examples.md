# Infrastructure Examples

Full config examples referenced by the `infrastructure` skill. All snippets are illustrative - adapt image names, ports, and volume paths to `.agentry/project/stack.md`.

## Service Roles

| Role | Placeholder | Typical Image |
|------|-------------|----------------|
| Backend runtime | `{{BACKEND_RUNTIME}}` | `php:8.4-fpm`, `node:22-slim`, `python:3.12-slim`, `ruby:3.3`, `golang:1.23`, `eclipse-temurin:21` |
| Reverse proxy / web server | `{{WEB_SERVER}}` | `nginx:1.27`, `traefik:v3`, `caddy:2` |
| Primary database | `{{DEFAULT_DB}}` | `postgres:16`, `mysql:8`, `mariadb:11` |
| Cache / queue | `{{CACHE_BACKEND}}` | `redis:7`, `memcached:1.6`, `valkey:8` |
| Frontend runtime (optional) | `{{FRONTEND_RUNTIME}}` | `node:22-slim`, `bun:1` |
| Object storage (optional) | `minio` | `minio/minio:latest-stable` |

## docker-compose.yml

Location: `<project-root>/docker-compose.yml`.

```yaml
services:
  # Backend runtime - {{BACKEND_RUNTIME}}
  app:
    build:
      context: .
      dockerfile: docker/app/Dockerfile
    container_name: app
    restart: unless-stopped
    volumes:
      - ./:/app
    depends_on:
      db:    { condition: service_healthy }
      cache: { condition: service_healthy }
    environment:
      APP_ENV: ${APP_ENV:-local}
      DB_HOST: db
      DB_PORT: ${DB_PORT_INTERNAL:-5432}
      DB_DATABASE: ${DB_DATABASE:-app}
      DB_USERNAME: ${DB_USERNAME:-app}
      DB_PASSWORD: ${DB_PASSWORD:-secret}
      CACHE_URL: redis://cache:6379
    networks: [app-network]

  # Reverse proxy / web server - {{WEB_SERVER}}
  web:
    image: nginx:1.27                       # or traefik:v3 / caddy:2
    container_name: web
    restart: unless-stopped
    ports:
      - "${WEB_PORT:-8080}:80"
    volumes:
      - ./:/app
      - ./conf/web/default.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on: [app]
    networks: [app-network]

  # Primary database - {{DEFAULT_DB}}
  db:
    image: postgres:16                      # or mysql:8 / mariadb:11
    container_name: db
    restart: unless-stopped
    ports:
      - "${DB_PORT:-5432}:5432"
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

  # Cache / queue - {{CACHE_BACKEND}}
  cache:
    image: redis:7                          # or memcached:1.6 / valkey:8
    container_name: cache
    restart: unless-stopped
    ports:
      - "${CACHE_PORT:-6379}:6379"
    volumes:
      - ./data/cache:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks: [app-network]

  # Frontend runtime (optional) - {{FRONTEND_RUNTIME}}
  # frontend:
  #   image: node:22-slim
  #   container_name: frontend
  #   working_dir: /app
  #   ports: ["${FRONTEND_PORT:-5173}:5173"]
  #   volumes: [./frontend:/app]
  #   command: sh -c "{{PKG_MANAGER}} install && {{PKG_MANAGER}} run dev -- --host 0.0.0.0"
  #   networks: [app-network]

networks:
  app-network:
    driver: bridge
```

## Backend Dockerfile

Location: `<project-root>/docker/app/Dockerfile`. Pattern (same shape for any runtime): base image -> system deps -> runtime extensions/packages -> package manager -> working dir -> non-root user -> expose -> CMD.

`[EXAMPLE - PHP-FPM]`

```dockerfile
FROM php:8.4-fpm

RUN apt-get update && apt-get install -y \
    git curl libzip-dev libpng-dev libjpeg-dev unzip icu-devtools libicu-dev \
    && docker-php-ext-install -j$(nproc) pdo pdo_mysql zip gd bcmath pcntl intl opcache \
    && pecl install redis && docker-php-ext-enable redis \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
WORKDIR /app

RUN groupadd -g 1000 app && useradd -u 1000 -g app -m app
USER app

EXPOSE 9000
CMD ["php-fpm"]
```

`[EXAMPLE - Node LTS]`

```dockerfile
FROM node:22-slim
WORKDIR /app
RUN groupadd -g 1000 app && useradd -u 1000 -g app -m app
USER app
EXPOSE 3000
CMD ["node", "dist/server.js"]
```

`[EXAMPLE - Python slim]`

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m app
USER app
EXPOSE 8000
CMD ["gunicorn", "app.wsgi:application", "--bind", "0.0.0.0:8000"]
```

`[EXAMPLE - Go]`

```dockerfile
FROM golang:1.23 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /bin/app ./cmd/server

FROM gcr.io/distroless/static
COPY --from=build /bin/app /app
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/app"]
```

## Reverse Proxy Configuration

Location: `<project-root>/conf/web/default.conf`. Pattern: TLS termination -> static files -> request size -> upstream -> timeouts -> security headers -> deny hidden files.

`[EXAMPLE - Nginx fronting any HTTP upstream]`

```nginx
server {
    listen 80;
    server_name localhost;
    root /app/public;               # adapt to runtime: public/, dist/, static/, etc.
    index index.html;

    client_max_body_size 20M;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript;
    gzip_min_length 1000;

    # For PHP-FPM upstream
    location ~ \.php$ {
        fastcgi_pass app:9000;
        fastcgi_param SCRIPT_FILENAME $realpath_root$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_read_timeout 60s;
    }

    # For HTTP upstream (Node / Python / Ruby / Go)
    # location / {
    #     proxy_pass http://app:3000;
    #     proxy_http_version 1.1;
    #     proxy_set_header Host $host;
    #     proxy_set_header X-Real-IP $remote_addr;
    #     proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    #     proxy_set_header X-Forwarded-Proto $scheme;
    #     proxy_read_timeout 60s;
    # }

    location ~ /\.(?!well-known).* { deny all; }
    location ~* \.(env|log|sql)$    { deny all; }

    location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2|ttf|eot)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
```

## Runtime Tuning

Universal categories: memory limit, upload size, execution timeout, opcode/JIT cache, error reporting, timezone, session security. Location (stack-specific): `<project-root>/conf/<runtime>/<config-file>`.

`[EXAMPLE - php.ini]`

```ini
memory_limit = 256M
max_execution_time = 60
upload_max_filesize = 20M
post_max_size = 25M

display_errors = Off
log_errors = On
error_reporting = E_ALL
date.timezone = UTC

opcache.enable = 1
opcache.memory_consumption = 128
opcache.interned_strings_buffer = 16
opcache.max_accelerated_files = 10000
opcache.validate_timestamps = 1

session.cookie_httponly = 1
session.cookie_secure = 1
session.use_strict_mode = 1
```

`[EXAMPLE - Node process tuning]`

```
NODE_ENV=production
NODE_OPTIONS=--max-old-space-size=512
UV_THREADPOOL_SIZE=8
```

`[EXAMPLE - Python gunicorn]`

```
# gunicorn.conf.py
workers = 3
threads = 2
timeout = 60
keepalive = 5
worker_class = "gthread"
```

## .env.example

Location: `<project-root>/.env.example`. Commit placeholder values only, never real secrets. Every variable referenced by `docker-compose.yml` or the runtime config must appear here.

```bash
# Application
APP_ENV=local
APP_DEBUG=true
APP_URL=http://localhost:8080

# Ports
WEB_PORT=8080
DB_PORT=5432
CACHE_PORT=6379
FRONTEND_PORT=5173

# Database
DB_CONNECTION={{DEFAULT_DB}}
DB_HOST=db
DB_PORT=5432
DB_DATABASE=app
DB_USERNAME=app
DB_PASSWORD=secret

# Cache / queue
CACHE_URL=redis://cache:6379
QUEUE_URL=redis://cache:6379

# External services (placeholders only)
MAIL_HOST=
MAIL_PORT=
MAIL_USERNAME=
MAIL_PASSWORD=

# Third-party APIs
PAYMENT_PROVIDER_KEY=
PAYMENT_PROVIDER_SECRET=
```

## CI/CD Pipeline

Location (GitHub Actions): `.github/workflows/ci.yml`. Same shape applies on GitLab CI, CircleCI, Buildkite, Drone - rename the YAML keys, the jobs stay identical. Pipeline shape: lint -> test -> (optional) frontend -> build image.

`[EXAMPLE - GitHub Actions]`

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  lint:
    name: Lint & Format
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Setup {{BACKEND_RUNTIME}} - uncomment the relevant block:
      # - uses: shivammathur/setup-php@v2
      #   with: { php-version: '8.4', tools: composer:v2 }
      # - uses: actions/setup-node@v4
      #   with: { node-version: '22', cache: '{{PKG_MANAGER}}' }
      # - uses: actions/setup-python@v5
      #   with: { python-version: '3.12' }
      # - uses: actions/setup-go@v5
      #   with: { go-version: '1.23' }
      - run: {{PKG_MANAGER}} install --frozen-lockfile
      - run: {{FORMAT_CMD}}
      - run: {{LINT_CMD}}

  test:
    name: Tests
    runs-on: ubuntu-latest
    services:
      db:
        image: postgres:16                  # or mysql:8
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
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s --health-timeout 5s --health-retries 5
    steps:
      - uses: actions/checkout@v4
      # Setup runtime (same block as above)
      - run: {{PKG_MANAGER}} install --frozen-lockfile
      - run: cp .env.ci .env
      - run: {{MIGRATION_TOOL}} migrate --no-interaction
      - run: {{TEST_CMD}}
        env:
          DB_HOST: 127.0.0.1
          DB_PORT: 5432
          DB_DATABASE: testing
          DB_USERNAME: testing
          DB_PASSWORD: testing
          CACHE_URL: redis://127.0.0.1:6379

  frontend:
    name: Frontend (optional)
    runs-on: ubuntu-latest
    if: hashFiles('frontend/**') != ''
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: '{{PKG_MANAGER}}'
          cache-dependency-path: frontend/package-lock.json
      - working-directory: frontend
        run: {{PKG_MANAGER}} ci
      - working-directory: frontend
        run: {{PKG_MANAGER}} run lint
      - working-directory: frontend
        run: {{PKG_MANAGER}} run type-check
      - working-directory: frontend
        run: {{PKG_MANAGER}} run test
```

## .gitignore

```
# Docker persistent data
data/
logs/

# Environment
.env
.env.local

# Dependencies (language-specific)
node_modules/
vendor/
__pycache__/
.venv/
target/
bin/
obj/

# Build artifacts
dist/
build/
.next/
.nuxt/
```

## docs/technical/infrastructure.md skeleton

Cover: service inventory (what runs, what port, what image, what version); getting-started steps; common commands (`docker exec` shortcuts for `{{MIGRATION_TOOL}}`, `{{TEST_CMD}}`, `{{FORMAT_CMD}}`, `{{PKG_MANAGER}}`); environment variables; troubleshooting.

```markdown
## Getting Started

1. Clone the repository
2. Copy environment file: `cp .env.example .env`
3. Start containers: `docker compose up -d --build`
4. Install dependencies: `{{PKG_MANAGER}} install`
5. Run migrations: `{{MIGRATION_TOOL}} migrate`
6. Access the application: http://localhost:${WEB_PORT}
```
