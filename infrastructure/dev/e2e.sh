#!/usr/bin/env bash
# Browser end-to-end run: real PostgreSQL + API (uvicorn) + production Next.js build + Playwright.
# DEVELOPMENT / CI ONLY. Requires: uv, pnpm, a PostgreSQL superuser via PG* env, Chromium.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PGHOST="${PGHOST:-/var/run/postgresql}" PGPORT="${PGPORT:-54329}" PGUSER="${PGUSER:-postgres}"
DB=artify_e2e; API_PORT=8100; WEB_PORT=3100
LOG="${E2E_LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG"
url() { # user pass
  if [[ "$PGHOST" == /* ]]; then echo "postgresql+psycopg://$1:$2@/$DB?host=$PGHOST&port=$PGPORT"
  else echo "postgresql+psycopg://$1:$2@$PGHOST:$PGPORT/$DB"; fi; }
"$ROOT/infrastructure/database/reset_dev_db.sh" "$DB" >/dev/null
export ARTIFY_ENVIRONMENT=local ARTIFY_MAILER_BACKEND=console ARTIFY_RATE_LIMIT_BACKEND=memory
export ARTIFY_DATABASE_URL_APP="$(url artify_app_login dev-only-app)"
export ARTIFY_DATABASE_URL_PLATFORM="$(url artify_platform_login dev-only-platform)"
export ARTIFY_DATABASE_URL_DISPATCHER="$(url artify_dispatcher_login dev-only-dispatcher)"
export ARTIFY_DATABASE_URL_MIGRATE="$(url "$PGUSER" "${PGPASSWORD:-x}")"
[[ -n "${PGPASSWORD:-}" ]] || export ARTIFY_DATABASE_URL_MIGRATE="${ARTIFY_DATABASE_URL_MIGRATE/:x@/@}"
export E2E_PLATFORM_EMAIL="platform-e2e@example.com" ARTIFY_BOOTSTRAP_ADMIN_PASSWORD="correct horse battery staple"
uv run --project "$ROOT/services/api" artify-api create-platform-admin --email "$E2E_PLATFORM_EMAIL" --name "E2E Admin" >/dev/null
# Refuse to run against a stale server left over from an earlier run (it would serve another database).
for port in "$API_PORT" "$WEB_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then echo "port $port already in use; stop the old process" >&2; exit 1; fi
done
# Each server runs in its own process group so cleanup kills the whole tree, not just a wrapper.
setsid bash -c "cd '$ROOT/services/api' && exec uv run uvicorn artify_api.main:app --port $API_PORT" >"$LOG/api.log" 2>&1 & API_PID=$!
cleanup() { for pid in "${WEB_PID:-}" "$API_PID"; do [[ -n "$pid" ]] && kill -- "-$pid" 2>/dev/null || true; done; }
trap cleanup EXIT
for _ in $(seq 1 60); do curl -fsS "http://localhost:$API_PORT/api/v1/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS "http://localhost:$API_PORT/api/v1/health" >/dev/null
cd "$ROOT/apps/web"
ARTIFY_API_URL="http://localhost:$API_PORT/api/v1" pnpm build >"$LOG/web-build.log" 2>&1
ARTIFY_API_URL="http://localhost:$API_PORT/api/v1" setsid ./node_modules/.bin/next start -p "$WEB_PORT" >"$LOG/web.log" 2>&1 & WEB_PID=$!
for _ in $(seq 1 60); do curl -fsS "http://localhost:$WEB_PORT/login" >/dev/null 2>&1 && break; sleep 0.5; done
E2E_API_LOG="$LOG/api.log" E2E_API_URL="http://localhost:$API_PORT/api/v1" E2E_BASE_URL="http://localhost:$WEB_PORT" pnpm exec playwright test "$@"
echo "logs: $LOG"
