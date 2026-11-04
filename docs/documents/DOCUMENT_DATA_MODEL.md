# Document Data Model

Migration `0007_documents` (`services/api/migrations/sql/0007_documents.sql`, wrapper `versions/0007_documents.py`). Every table carries `organization_id` and `company_id` with the composite foreign key `(organization_id, company_id) → companies`, **forced row-level security** (ADR-024 InitPlan pattern) and grants for the `artify_app` role only. The application role has no `BYPASSRLS`. **There is no `DELETE` grant** on `documents`, `document_versions`, `document_events` or `document_duplicate_candidates`.

## Tables

| Table | Purpose | Notes |
|---|---|---|
| `documents` | stable identity of a business document | `node_id` → `workspace_nodes`, `category_id` → `document_categories`, **both `ON DELETE RESTRICT`** (a node that holds documents can only be archived). `status`: `active` · `archived` · `deleted` · `purged`. `display_name` constrained (no `/ \ : * ? " < > |`, no control characters, 1–255). `legal_hold`, `row_version` (optimistic concurrency), `kind_at_filing` and `filed_path_snapshot` (snapshots; **never used as references**), `filing_rule_id` |
| `document_versions` | every binary ever stored | `size_bytes > 0`, `sha256` (64 hex), `mime_type` derived from content, `storage_driver`, `storage_key` (opaque, unique), `scan_status` (`not_scanned` · `clean` · `infected` · `pending`), `purged_at`. **Trigger `document_versions_guard`**: only `scan_status` and `purged_at` may change; no deletes; a purged version stays purged |
| `document_field_values` | typed custom-field values | one row per (document, field); `value` jsonb as normalised by the Phase 4 validator; `value_text` / `value_number numeric(28,6)` / `value_date` for filtering. Money is never a float |
| `document_upload_sessions` | resumable, idempotent upload state | `declared_size`, `declared_sha256`, `received_bytes`, `status` (`open` · `uploading` · `verifying` · `completed` · `failed` · `cancelled` · `expired` · `quarantined`), `staging_ref`, `duplicate_policy` (`block` · `allow_copy`), `idempotency_key` (unique per company+user), `expires_at`, `result_document_id` / `result_version_id`, `error_code` |
| `document_import_batches` | group of files imported together | `source`, optional `device_id` and `label` |
| `document_events` | append-only activity and access log | `event_type` ∈ `uploaded, version_created, version_restored, renamed, moved, metadata_changed, archived, restored, deleted, undeleted, purged, downloaded, previewed, legal_hold_changed, duplicate_kept, quarantined`; `actor_type` user/system; **update/delete forbidden by trigger** |
| `document_duplicate_candidates` | a user's decision to keep an identical file separately | `decision = 'kept_separate'`; immutable; the queue Phase 8 extends |

Also in this migration: `pg_trgm` and a trigram index on `lower(display_name)`; `app.measure_entitlement` replaced to measure `quota.storage_bytes` and `quota.documents_per_month`; a backfill of the usage counters for existing organizations; a trigger that seeds the counters for new organizations.

## Status model

```
active ──archive──▶ archived ──restore──▶ active
  │                    │
  └────── delete ──────┴──▶ deleted ──undelete──▶ (previous state)
                              │
                              └─ purge (permission + no legal hold + retention + typed confirmation) ─▶ purged
```

`purged` removes the *binaries* and sets `purged_at`; the document row, version rows (without content), the events and the audit trail remain as a tombstone, so history stays explainable.

## Why not store the bytes in PostgreSQL

Backups, replication and RLS scans stay small and fast; large objects belong in storage with their own durability and encryption. The cost is two stores that must agree: the finalise step stores the object and the rows in one flow (object deleted again if the database part fails), and `reconcile` detects drift ([CLOUD_STORAGE_ARCHITECTURE](CLOUD_STORAGE_ARCHITECTURE.md)).

## Relationship to the workspace

A document's folder and category are ids from Phase 4. Renaming or moving a folder does not touch documents. Archiving a folder with documents is refused unless the impact is confirmed; the Phase 4 impact summary now counts documents. Naming rules (Phase 4) produce *suggested* names; the stored `display_name` is whatever the user accepted.
