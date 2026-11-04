# Local SQLite Database

File `artify.db` in the app-data folder (`%APPDATA%\com.artify.coaccountant`), SQLite 3 via `rusqlite` (bundled), WAL, foreign keys on.

## Schema (migration 1; migration 2 adds the Phase 6 tables below)

| Table | Purpose |
|---|---|
| `schema_migrations` | applied versions |
| `settings(user_id, key, value)` | preferences (closed key list, validated values) |
| `accounts(user_id)` · `organizations` · `companies` · `workspace_cache` | cached identity, organizations, authorized companies (+favorite, last opened), workspace (+version, etag) — **payloads sealed** |
| `sync_state(user_id, company_id)` | last success/attempt, last error, last version |
| `device(user_id, org_id)` | this installation's server-issued device id, status, timestamps |
| `local_dirs(user_id, company_id, dir_name)` | stable local folder name per company |
| `pending_ops` | operation queue skeleton: state `pending/in_progress/failed`, attempts, `next_retry_at`, sealed payload |
| `events` | notification log (bounded to ~200 per user) |
| `imports` (migration 2) | the **upload queue**: one row per file — company, destination node and category by id, status (`waiting · preparing · uploading · verifying · completed · failed · cancelled · quarantined`), bytes sent, attempts, next retry, server session id, idempotency key, duplicate policy, result document id, error code/message; the source path, file name, display name, field values and change reason are in a **sealed** JSON column |
| `local_files` (migration 2) | the **local mirror**: document id → version id, node id, relative path under the company folder, SHA-256, size |

Indexes: `companies_by_org`, `companies_recent`, `pending_ops_due`, `events_recent`.

## Migrations

Forward-only list in `db.rs::MIGRATIONS`, each in a transaction (a failing migration changes nothing — tested). A database from a **newer** app is refused untouched (`database_too_new`, no downgrade). Upgrade keeps data (tested with an added column and table).

## Corruption

On open, `PRAGMA quick_check`; a damaged or non-SQLite file is renamed to `artify.corrupt-<timestamp>` (with -wal/-shm), a fresh database is created and the user is told in About. This is safe because the database is only a cache; pending operations in a destroyed file are lost — a limitation, documented.

## Encryption (ADR-047)

Sensitive payloads are sealed field-by-field with **AES-256-GCM**; the 256-bit key is generated on first run and stored only in the Windows Credential Manager. Associated data = `user|table|row`, so a blob cannot be replayed into another user's or company's row (tested), and a different key cannot read the cache (tested). Raw-file scan tests show company names, workspace content, email addresses and queued payloads are not present in the database file. What is **not** encrypted: setting values, ids, statuses, timestamps, company statuses, sync error codes, notification messages. SQLCipher was rejected because it complicates the Windows build (OpenSSL) and still needs a key store.

## Authority

The cache is never an authorization source. After logout/expiry/revocation the user's cache is wiped; `pending_ops` survive a wipe so unsent work is never silently destroyed.
