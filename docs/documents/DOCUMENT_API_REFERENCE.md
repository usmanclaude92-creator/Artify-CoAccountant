# Document API Reference

All paths are under `/api/v1`. Auth: bearer token + `X-Organization-Id`. Errors are RFC 9457 problem JSON with a stable `code`. The generated contract is `packages/api-client/openapi.json`.

## Imports

| Method & path | Permission | Purpose |
|---|---|---|
| `POST /companies/{c}/documents/import-batches` | `documents.upload` | Create a batch |
| `GET /companies/{c}/documents/import-batches/{id}` | `documents.upload` | Batch status with per-status counts |
| `POST /companies/{c}/documents/uploads` | `documents.upload` | Register (201) / replay (200) |
| `GET /companies/{c}/documents/uploads` (`status`, `batch_id`) | `documents.upload` | Own sessions |
| `GET /companies/{c}/documents/uploads/{op}` | `documents.upload` | One session |
| `PUT …/uploads/{op}/content` | `documents.upload` | Stream bytes |
| `POST …/uploads/{op}/complete` | `documents.upload` | Verify and create |
| `POST …/uploads/{op}/cancel`, `…/retry` | `documents.upload` | Cancel / reopen transient failure |

## Documents

| Method & path | Permission | Purpose |
|---|---|---|
| `GET /companies/{c}/documents` | `documents.read` | Search/filter/sort/paginate ([DOCUMENT_SEARCH](DOCUMENT_SEARCH.md)) |
| `GET …/documents/counts` | `documents.read` | Per-node counts |
| `POST …/documents/duplicates/check` | `documents.read` | Same-company checksum lookup |
| `POST …/documents/suggest` | `documents.upload` | Advisory filing + naming |
| `GET …/documents/events` | `documents.read` | Company activity feed (visible documents only) |
| `GET …/documents/{id}` | `documents.read` | Details: versions, metadata, duplicates, location, allowed actions |
| `PATCH …/documents/{id}` | `documents.manage` | Rename, category, metadata (`base_row_version`) |
| `POST …/documents/{id}/move` | `documents.manage` | Re-file (database only) |
| `POST …/documents/{id}/archive` / `restore` | `documents.manage` | Reversible |
| `POST …/documents/{id}/delete` / `undelete` | `documents.delete` | Soft delete / recover |
| `POST …/documents/{id}/hold` | `documents.purge` | Retention hold on/off |
| `DELETE …/documents/{id}?confirm=true` | `documents.purge` | Permanent deletion |
| `POST …/documents/{id}/duplicate` | `documents.upload` | Independent copy |
| `GET …/documents/{id}/versions`, `POST …/versions/{n}/restore` | read / upload | History |
| `GET …/documents/{id}/events` | `documents.read` | Activity |
| `GET …/documents/{id}/content?version=&disposition=` | `documents.read` | Streamed bytes, Range supported |

## Organization

| Method & path | Permission | Purpose |
|---|---|---|
| `GET /storage/overview` | `storage.manage` (all-company scope) | Usage, plan limits, backend, scanner, per-company breakdown, failures |
| `POST /storage/reconcile` | `storage.manage` | Dry run by default; `repair=true` to fix |

## Notable error codes

`file_not_accepted` (`reason`), `duplicate_document` (`duplicates`), `entitlement_exceeded` (`key`, `limit`, `current`), `subscription_restricted`, `version_conflict`, `retention_hold`, `storage_unavailable` (503), `storage_verification_failed` (503), `scan_unavailable` (503). Upload failures from `PUT`/`complete` carry the persisted `operation` object.
