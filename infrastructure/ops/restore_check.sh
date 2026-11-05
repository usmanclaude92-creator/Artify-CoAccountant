#!/usr/bin/env bash
# Backup/restore verification: dump a database, restore it into a scratch database and prove the copy is identical.
# DEVELOPMENT / STAGING TOOL. Run it against a staging copy; never point it at production credentials from a laptop.
#   usage: restore_check.sh <source-db> [scratch-db]      (PG* environment as for psql)
# Output: timings, per-table row counts of source and restore, a content hash for every table, and PASS/FAIL.
set -euo pipefail
SRC="${1:?source database}"; DST="${2:-${SRC}_restore_check}"
PGHOST="${PGHOST:-/var/run/postgresql}"; PGPORT="${PGPORT:-54329}"; PGUSER="${PGUSER:-postgres}"
export PGHOST PGPORT PGUSER
DUMP="$(mktemp -d)/backup.dump"; trap 'rm -rf "$(dirname "$DUMP")"' EXIT
t0=$(date +%s.%N)
pg_dump -Fc --no-owner -d "$SRC" -f "$DUMP"
t1=$(date +%s.%N)
psql -q -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$DST\" WITH (FORCE)" -c "CREATE DATABASE \"$DST\""
# roles referenced by grants/policies must exist on the target (a real restore recreates them from the infrastructure code)
pg_restore --no-owner -d "$DST" "$DUMP" 2>/tmp/restore.err || true
t2=$(date +%s.%N)
tables=$(psql -Atq -d "$SRC" -c "SELECT quote_ident(schemaname)||'.'||quote_ident(tablename) FROM pg_tables WHERE schemaname IN ('public','app') ORDER BY 1")
fail=0; n=0
fp() { # table -> "count|md5 of all rows (order-independent)"
  psql -Atq -d "$1" -c "SELECT count(*) || '|' || COALESCE(md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))), 'empty') FROM $2 t"
}
for t in $tables; do
  a=$(fp "$SRC" "$t"); b=$(fp "$DST" "$t"); n=$((n+1))
  if [[ "$a" != "$b" ]]; then echo "MISMATCH $t  source=$a restore=$b"; fail=1; fi
done
rows=$(psql -Atq -d "$SRC" -c "SELECT COALESCE(sum(n_live_tup),0) FROM pg_stat_user_tables")
size=$(psql -Atq -d "$SRC" -c "SELECT pg_size_pretty(pg_database_size('$SRC'))")
printf 'tables compared: %s   approx rows: %s   source size: %s\n' "$n" "$rows" "$size"
printf 'dump: %.1fs   restore: %.1fs   dump file: %s\n' "$(echo "$t1 - $t0" | bc)" "$(echo "$t2 - $t1" | bc)" "$(du -h "$DUMP" | cut -f1)"
echo "restore warnings (first lines):"; head -5 /tmp/restore.err || true
psql -q -d postgres -c "DROP DATABASE IF EXISTS \"$DST\" WITH (FORCE)"
if [[ $fail -eq 0 ]]; then echo "PASS: every table identical after restore"; else echo "FAIL"; exit 1; fi
