# Local Setup Guide

Everything here uses synthetic development data only. Nothing connects to a production system; no real credentials are needed or allowed in the repository (`.env` is git-ignored; CI runs gitleaks).

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.13 via `uv` | `uv sync` creates the virtualenv |
| Node.js / pnpm | 22+ / 10 | lockfile is authoritative (`pnpm install --frozen-lockfile`) |
| PostgreSQL | 16 | needs a superuser (or owner with `BYPASSRLS`) for migrations only; the application connects with non-privileged roles |
| Redis | 7 | optional locally (the in-memory rate limiter is used by default); required for staging/production and for the Redis limiter test |
| Docker | 24+ | optional; runs the whole stack in containers |

## 2. Path A — Docker Compose (nearest to a clean-clone experience)

```bash
docker compose up --build        # PostgreSQL, Redis, migrate, API :8000, Celery worker, outbox dispatcher
curl http://localhost:8000/api/v1/health         # {"status":"ok"} -- liveness
curl http://localhost:8000/api/v1/health/ready   # readiness: checks the database
# API docs: http://localhost:8000/docs   (OpenAPI JSON: /openapi.json)
```

`make compose-smoke` builds, starts, exercises sign-up → MFA → company → outbox → worker, and tears down (also run in CI). Registry-mirror overrides: `POSTGRES_IMAGE`, `REDIS_IMAGE`, `PYTHON_IMAGE`. The optional `--profile storage` (MinIO) is **not verified**.

The web app is not part of the Compose file; run it as in §3.4 pointing at `http://localhost:8000/api/v1`.

## 3. Path B — native development

### 3.1 Install

```bash
make install            # uv sync + pnpm install --frozen-lockfile
make hooks              # optional: git pre-commit hooks (ruff, import-linter, docs check, eslint, private-key check)
```

### 3.2 Database

The `Makefile` defaults to a local cluster: `PGHOST=/var/run/postgresql PGPORT=54329 PGUSER=postgres`. Override for your machine, e.g. `make db-reset PGHOST=localhost PGPORT=5432 PGPASSWORD=...`.

```bash
make db-reset           # drop/recreate DB artify_dev: Alembic migrations -> dev login roles -> seed
```

`dev_roles.sql` creates four **development-only** login roles with throwaway passwords (`artify_app_login`, `artify_platform_login`, `artify_dispatcher_login`; the owner credential is used only by migrate/seed). `bootstrap-dev` refuses to run unless `ARTIFY_ENVIRONMENT` is `local` or `ci`.

### 3.3 Configuration and API

```bash
cp .env.example .env     # defaults are development-only and are rejected in staging/production
uv run --project services/api uvicorn artify_api.main:app --reload --port 8000
```

In `local` mode the mailer prints "e-mails" (verification, reset and invitation links) to the API console, so the full sign-up flow works without a mail server. Production-like settings (`ARTIFY_ENVIRONMENT=staging|production`) require real JWT secrets, a Fernet key, the Redis rate limiter and a real mailer; startup fails otherwise.

There are **no pre-seeded user accounts**. Create a test firm through the UI or API sign-up (use `@example.com` addresses), copy the verification link from the API console, then sign in. Firm owners must enroll TOTP on first sign-in (any authenticator app).

Platform administrator (Super Admin foundation; no UI beyond a metadata page):

```bash
uv run --project services/api artify-api create-platform-admin --email admin@example.com --name "Dev Admin"
```

### 3.4 Web

```bash
cd apps/web
ARTIFY_API_URL=http://localhost:8000/api/v1 pnpm dev      # http://localhost:3000  (/signup, /login, /dashboard)
# Platform sign-in: http://localhost:3000/platform/login  (APP_SURFACE=all locally)
```

`ARTIFY_API_URL` is server-side only. If it is unset in production the site shows "not connected to a backend".

## 4. Tests and quality gates

| Command | Runs |
|---|---|
| `make check` | lockfile check, ruff, import-linter, eslint, mypy `--strict`, tsc, OpenAPI drift, **API tests against real PostgreSQL**, docs check, web unit tests, production web build |
| `make test-api` | API tests only (rebuilds database `artify_test` from the migrations) |
| `ARTIFY_TEST_REDIS_URL=redis://127.0.0.1:6379/0 make test-api` | also runs the Redis rate-limiter test |
| `make e2e` | API + production web build + headless Chromium: journeys, axe accessibility scans, keyboard, switcher, session expiry |
| `make client` | regenerate `packages/api-client/openapi.json` and the TypeScript types after any API change |
| `make openapi-breaking BASE=<git ref>` | fail on breaking contract changes |
| `make audit` | dependency vulnerability scan (Python and JavaScript) |
| `make db-reset DB=name` | rebuild any database |

If Playwright reports a missing browser, point it at an installed Chromium: `E2E_CHROMIUM_PATH=/path/to/chromium make e2e`.

## 5. Resetting

`make db-reset` is destructive and development-only. Never point the `PG*` variables or `ARTIFY_DATABASE_URL_*` at a shared or production database.

## 6. Troubleshooting

- *Startup error about secrets/Fernet:* you are in a non-local environment with default secrets; set real values or `ARTIFY_ENVIRONMENT=local`.
- *`permission denied` / empty results from the API role:* expected when no organization context is set — row-level security fails closed.
- *Migrations are forward-only:* there is no downgrade; recreate the development database with `make db-reset`.

## 7. Super Admin (Phase 2)

1. Create the first platform administrator (operations CLI; development only password source):
   `ARTIFY_BOOTSTRAP_ADMIN_PASSWORD='a-long-dev-password' uv run --project services/api artify-api create-platform-admin --email you@example.com --name "You" --role platform_owner`
2. Open `http://localhost:3000/platform/login`, sign in, enrol an authenticator app (mandatory). Further administrators are invited from **Platform Administrators**; in local mode the invitation link is printed in the API console.
3. A customer firm owner approves Artify support requests at `/support-access`.
4. Settings that change behaviour without a deploy (default plan, trial days, support grant cap, invoice prefix) are under **Platform Settings**.
5. In sandboxes where PostgreSQL stops with the session, restart it before `make db-reset` / tests (`pg_ctl -D <data dir> -o "-p 54329 -k /var/run/postgresql" start`). If Playwright reports a missing browser: `E2E_CHROMIUM_PATH=/path/to/chromium make e2e`.
