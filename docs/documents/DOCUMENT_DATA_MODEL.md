# Document Data Model

Migration `0007_documents.sql`. Every table carries `organization_id` and `company_id` with the composite FK `(organization_id, company_id) → companies`, has RLS **enabled and forced** with the ADR-024 policy, and is reachable only through the application role (no `BYPASSRLS`).

| Table | Purpose | Notes |
|---|---|---|
| `documents` | One row per logical document (stable id) | `node_id` → `workspace_nodes` and `category_id` → `document_categories` via composite FKs (**no cascade**: a node holding documents can only be archived). `status` active/archived/deleted/quarantined, `retention_hold`, `row_version`, `purged_at` (tombstone). Unique active name per node: `documents_unique_name`. |
| `document_versions` | Immutable history | `(document_id, version_no)` unique; `storage_object_id`, `sha256`, `size_bytes`, `scan_status`, `change_reason`, `restored_from_version`. |
| `storage_objects` | Link between database and object store | Opaque `storage_key` (CHECK-constrained shape, unique), `state` pending/stored/verified/missing/purged, `sha256`, `size_bytes`. |
| `document_metadata_values` | Values for Phase 4 custom fields | `(document_id, field_id)` unique, JSONB value (money stays a string). Definitions stay in `custom_field_definitions`. |
| `document_import_batches` | Groups one import | `total_files` 1–1000. |
| `document_upload_operations` | The upload session / queue on the server | Status machine below; `idempotency_key` unique per `(company, user)`; destination, metadata, duplicate policy, declared and received size/hash, error code, expiry. |
| `document_events` | Activity and access trail | **Append-only**: the application role has `SELECT, INSERT` only. |

Money and quotas: sizes are `bigint` bytes; no floating point anywhere.

## Upload session states

`awaiting_upload → uploading → uploaded → completed`, with exits `failed`, `cancelled`, `quarantined`, `expired`. Only `completed` creates a document.

## Document states

`active ⇄ archived`; `active|archived → deleted ⇄ active`; `deleted → purged` (terminal; the row remains as a tombstone with scrubbed names so the activity trail keeps its references). A retention hold blocks purge.

## Indexes

`documents(company_id, node_id, status, created_at desc)`, `documents(company_id, status, created_at desc, id)`, `documents(company_id, content_sha256)`, trigram GIN indexes on lower(`display_name`) and lower(`original_filename`), metadata value index by field, upload ops by `(company, status)` and `(status, expires_at)`.

## RLS

Policies are `organization_id = app.org_id() AND (company_scope = 'all' OR company_id = ANY(visible_company_ids))` for SELECT/INSERT (and UPDATE where allowed). `artify_definer` can read `documents` and `storage_objects` only inside `app.measure_entitlement`, which refuses an organization other than the caller's. Tests: `test_every_table_is_classified_and_protected`, `test_database_isolation_for_document_tables`.
