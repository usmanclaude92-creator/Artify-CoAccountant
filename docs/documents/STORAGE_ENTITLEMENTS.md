# Storage Entitlements and Quotas

All limits are enforced **only by the backend `EntitlementService`** (`entitlements.py`). No limit constant exists in the Desktop, the web app or the document code; clients read the effective limits from `GET /companies/{id}/documents/limits`.

## Keys

| Key | Kind | Meaning |
|---|---|---|
| `quota.storage_bytes` | metered | bytes of every **non-purged version** of every document in the organization |
| `quota.documents_per_month` | metered | documents **created** in the current UTC month (new documents only; a new *version* is not a new document) |
| `limit.max_file_size_bytes` | hard | largest single file; **a plan can only lower the central ceiling** `max_upload_bytes` (default 200 MiB), never raise it |
| `limit.import_batch_files` | hard | files per batch; a plan can only lower `import_batch_max_files` (default 100) |

Plan values and per-organization overrides come from Phase 2 (plans, subscriptions, overrides). `app.measure_entitlement` (migration 0007) measures the two metered keys in the database under the counter lock; counters are backfilled for existing organizations and seeded for new ones.

## Accounting model

| Event | Effect on storage usage |
|---|---|
| Upload | + size (new document or new version) |
| Archive / restore | none |
| Remove (soft delete) | **none** — the bytes are still stored and recoverable |
| Restore an earlier version | none (pointer move) |
| Identical file uploaded as a separate document (`allow_copy`) | + size again (stored and counted separately) |
| **Permanent delete (purge)** | − the sizes of all versions of that document |

This is stated to users: the Control Center shows the model next to the numbers.

## Where it is checked

1. **Reserve** (`POST /uploads`): `guard_amount(quota.storage_bytes, declared size)` — a clear `402 entitlement_exceeded` (with key, limit, current, requested) **before a single byte is sent**.
2. **Complete**: checked again under the counter lock with the real size (and `quota.documents_per_month` for a new document), because other uploads may have happened in between. On failure the staged data is deleted and nothing is stored.
3. Operations that do not add bytes (rename, move, archive, restore, remove, version pointer) are never blocked by quotas.

A suspended or missing subscription answers `403 subscription_restricted` for anything that stores new data; reading existing documents is not affected by quotas.

## Shrinking a plan, over-quota organizations

**Nothing is ever deleted because of a quota.** If a plan is lowered below current usage, existing documents stay readable, downloadable, movable and removable; only *increases* are refused until usage drops (purging frees space). The Desktop explains: "Your plan's storage or document limit is reached… nothing was deleted."

## Visibility

* Control Center → *Subscription*: used / limit / available bytes, documents this month, source of the limit, per-company stored bytes and failed uploads in the last 7 days, and the accounting model text (`GET /orgs/{id}/storage`, permission `entitlements.read`).
* Control Center → company → *Documents* tab: that company's counts, storage, by-type breakdown, recent upload failures.
* Platform admins see no documents and no file names — see [STORAGE_SECURITY](STORAGE_SECURITY.md).

## Not done in this phase

Per-company storage quotas (the platform per-company storage column in Super Admin is still *not metered*; only the organization total is), billing for overage, usage-based notifications, soft warnings at a percentage of the quota.
