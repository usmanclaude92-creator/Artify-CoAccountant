# Document API Reference

Authoritative contract: the generated OpenAPI (`services/api/openapi.json`, client in `packages/api-client`). This page explains behaviour. All company routes are `/api/v1/companies/{company_id}/…`, require a signed-in tenant session with `X-Organization-Id`, the company-level permission `workspace.read`, and then the **Phase 4 node action** named below on the folder concerned. Out-of-scope or hidden objects → `404`; visible but forbidden → `403`. Errors use `application/problem+json` with a stable `code`.

## Uploads

| Method & path | Node action | Notes |
|---|---|---|
| `GET /documents/limits` | — | effective `max_file_size`, `batch_max_files`, `allowed_extensions`, `chunk_max_bytes`, `max_concurrent` |
| `POST /document-batches` | — | `{source, label, file_count, device_id}` → batch id (limited by `limit.import_batch_files`) |
| `POST /documents/suggest` | `upload` (to be offered) | filing + naming suggestions, applicable fields |
| `POST /documents/check-duplicate` | — | `{sha256}` → `{exists, document\|null}` |
| `POST /uploads` | `upload` | header `Idempotency-Key` required; body `UploadIn`; `201` (or `200` on replay) |
| `GET /uploads` | — / `configure` | own sessions (`mine=true`, default); `mine=false` needs `configure` |
| `GET /uploads/{id}` | — | state for resuming |
| `PATCH /uploads/{id}` | `upload` | change destination / name / values / duplicate policy while open |
| `PUT /uploads/{id}/content?offset=N` | — | raw bytes; `409 offset_mismatch {received_bytes}`; `410 upload_expired`; `422 too_much_data/bad_chunk` |
| `POST /uploads/{id}/complete` | `upload` | `200` document + version; `409 size_mismatch` (no state change); failures as problem+json with the session state committed |
| `POST /uploads/{id}/cancel`, `/retry` | — | |

Error codes to expect on complete: `invalid_file`, `quarantined`, `malware_detected`, `scan_unavailable` (503), `checksum_mismatch`, `duplicate_detected` (409, `document` only if visible), `target_unavailable` (409), `invalid_metadata` (422, `problems[]`), `entitlement_exceeded` (402, `key/limit/current/requested`), `subscription_restricted` (403), `storage_error` (retryable).

## Documents

| Method & path | Node action | Notes |
|---|---|---|
| `GET /documents` | `view` | filters in [DOCUMENT_SEARCH](DOCUMENT_SEARCH.md); `{items,total,limit,offset}`; each item has `can{view,download,edit,move,archive,delete,purge}` |
| `GET /documents/counts` | `view` | `{by_node:{id:{active,archived}}, totals}` for visible folders |
| `GET /documents/storage` | `configure` (company level) | company usage, by type, recent upload health |
| `GET /documents/{id}` | `view` | detail + `field_values`, `path[]`, `versions[]` |
| `PATCH /documents/{id}` | `upload` | `{display_name, field_values, row_version}`; `409 document_changed`, `409 name_taken` |
| `POST /documents/{id}/move` | `upload` on the source and the target folder | `{node_id, category_id, row_version}` |
| `POST /documents/{id}/archive`, `/restore` | `upload` | (the role key `documents.contribute` is what grants `upload`) |
| `POST /documents/{id}/delete` | `delete` | `{reason ≥3 chars}` soft delete |
| `POST /documents/{id}/undelete` | `delete` | |
| `POST /documents/{id}/purge` | org permission `documents.purge` | `{confirm: <exact display name>}`; `409 legal_hold / retention_period / bad_state`, `422 confirmation_mismatch` |
| `POST /documents/{id}/legal-hold` | org permission `documents.purge` | `{hold}` |
| `GET /documents/{id}/versions` | `view` | |
| `POST /documents/{id}/versions/{n}/restore` | `upload` | `{reason}`; pointer move |
| `GET /documents/{id}/events` | `view` | `{items,total}` newest first |
| `GET /documents/{id}/content` | `view` | `?version=N`, `?inline=true` (PDF/images), `Range`; headers `X-Content-SHA256`, `X-Document-Version`, `nosniff`; audited |

## Organization level

| Method & path | Permission | Notes |
|---|---|---|
| `GET /orgs/{org_id}/storage` | `entitlements.read` | used/limit/available bytes, documents this month, per-company table, accounting-model text |
| `POST /orgs/{org_id}/storage/reconcile` | `documents.purge` | `{dry_run}` default `true`; audited |

## Permissions added in Phase 6

`documents.contribute` (company scope: upload, rename, move, archive, add versions where the folder allows; held by firm owner, administrator, practice manager, accountant, assistant accountant, document operator) and `documents.purge` (organization scope: permanent delete, legal hold, reconcile; firm owner and administrator). Read-only users and staff can view but not upload. In the workspace engine `documents.contribute` maps to the node action `upload`.

## Idempotency and concurrency

Uploads: `Idempotency-Key` per company+user (replay returns the same session). Documents: `row_version` for optimistic concurrency on `PATCH`/`move`. All state-changing calls are audited.

## Breaking-change policy

The OpenAPI breaking-change check (`make openapi-breaking`) ran against the previous phase's schema; Phase 6 only **adds** routes, schemas and permissions.
