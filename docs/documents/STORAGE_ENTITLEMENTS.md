# Storage Entitlements

All limits are data (plans, overrides) enforced only by `EntitlementService` (invariant 9). No limit constants exist in code; only the system *ceiling* `max_file_size_ceiling_bytes` (a safety valve above any plan).

| Key | Kind | Enforced when | Over the limit |
|---|---|---|---|
| `limit.file_size_bytes` | per file | Registration (declared size) | 402 `entitlement_exceeded` |
| `quota.storage_bytes` | organization | Registration, again when bytes arrive, duplicate | 402 |
| `quota.documents_per_month` | organization | Registration and completion of a **new** document (new versions do not count) | 402 |

## Accounting model

*Stored bytes = sum of `size_bytes` of every storage object in state `stored` or `verified`.* Archived and soft-deleted documents **count**; permanent deletion frees space; a restored version shares an object and costs nothing; duplicate documents are independent copies and each counts (no cross-document de-duplication). The measurement runs in `app.measure_entitlement` (security definer, bound to the caller's organization) and is serialised per organization with the usage-counter lock, so concurrent uploads cannot oversubscribe. `usage_counters` is refreshed after each change; a missing counter is read as 0 (nothing stored yet).

## Behaviour at the limit

Existing documents are never deleted or hidden: read, download, rename, archive, delete and purge keep working. A suspended or missing subscription blocks uploads (403 `subscription_restricted`) but not reading. The Control Center shows usage; the desktop shows the server's message verbatim.

Tests: `test_file_size_limit_comes_from_the_plan…`, `test_storage_quota_refuses_new_uploads_but_never_hides…`, `test_documents_per_month_quota`, `test_quota_is_enforced_at_the_content_step_too`, `test_suspended_subscription_blocks_uploads_but_not_reading`.
