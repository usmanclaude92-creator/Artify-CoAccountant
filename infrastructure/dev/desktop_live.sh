#!/usr/bin/env bash
# Desktop live test: the Rust core (apps/desktop/core) against a REAL API + PostgreSQL. DEVELOPMENT / CI ONLY.
# Requires: uv, cargo, a PostgreSQL superuser via PG* env. Synthetic data only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PGHOST="${PGHOST:-/var/run/postgresql}" PGPORT="${PGPORT:-54329}" PGUSER="${PGUSER:-postgres}"
DB=artify_desktop_live; API_PORT=8200
LOG="${LIVE_LOG_DIR:-$(mktemp -d)}"; mkdir -p "$LOG"
url() { if [[ "$PGHOST" == /* ]]; then echo "postgresql+psycopg://$1:$2@/$DB?host=$PGHOST&port=$PGPORT"; else echo "postgresql+psycopg://$1:$2@$PGHOST:$PGPORT/$DB"; fi; }
"$ROOT/infrastructure/database/reset_dev_db.sh" "$DB" >/dev/null
export ARTIFY_STORAGE_ROOT="$LOG/storage"
export ARTIFY_ENVIRONMENT=local ARTIFY_MAILER_BACKEND=console ARTIFY_RATE_LIMIT_BACKEND=memory
export ARTIFY_DATABASE_URL_APP="$(url artify_app_login dev-only-app)"
export ARTIFY_DATABASE_URL_PLATFORM="$(url artify_platform_login dev-only-platform)"
export ARTIFY_DATABASE_URL_DISPATCHER="$(url artify_dispatcher_login dev-only-dispatcher)"
export ARTIFY_DATABASE_URL_MIGRATE="$(url "$PGUSER" "${PGPASSWORD:-x}")"
[[ -n "${PGPASSWORD:-}" ]] || export ARTIFY_DATABASE_URL_MIGRATE="${ARTIFY_DATABASE_URL_MIGRATE/:x@/@}"
if (exec 3<>"/dev/tcp/127.0.0.1/$API_PORT") 2>/dev/null; then echo "port $API_PORT already in use" >&2; exit 1; fi
setsid bash -c "cd '$ROOT/services/api' && exec uv run uvicorn artify_api.main:app --port $API_PORT" >"$LOG/api.log" 2>&1 & API_PID=$!
trap 'kill -- "-$API_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -fsS "http://localhost:$API_PORT/api/v1/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS "http://localhost:$API_PORT/api/v1/health" >/dev/null
BASE="http://localhost:$API_PORT/api/v1"
uv run --project "$ROOT/services/api" python "$ROOT/apps/desktop/tests/live/seed_live.py" "$BASE" "$LOG/api.log" "$LOG/fixture.json"
cd "$ROOT/apps/desktop" && ARTIFY_LIVE_FIXTURE="$LOG/fixture.json" cargo test -p artify-core --test live_backend -- --ignored --nocapture --test-threads=1
echo "logs: $LOG"
