#!/usr/bin/env bash
# DEVELOPMENT / CI ONLY: drop and recreate a database, migrate, create login roles, seed.
# usage: reset_dev_db.sh <dbname>   (expects ARTIFY_PG_ADMIN_URL-style env or local defaults below)
set -euo pipefail
DB="${1:?database name}"
PGHOST="${PGHOST:-/var/run/postgresql}"; PGPORT="${PGPORT:-54329}"; PGUSER="${PGUSER:-postgres}"
export PGHOST PGPORT PGUSER
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
psql -q -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" -c "CREATE DATABASE \"$DB\""
export ARTIFY_DATABASE_URL_MIGRATE="postgresql+psycopg://${PGUSER}@/${DB}?host=${PGHOST}&port=${PGPORT}"
uv run --project "$ROOT/services/api" alembic -c "$ROOT/services/api/alembic.ini" upgrade head
psql -q -v ON_ERROR_STOP=1 -d "$DB" -f "$HERE/dev_roles.sql"
uv run --project "$ROOT/services/api" artify-api seed
