# Phase 7 Integration Points

Phase 7 (document processing: OCR, classification suggestions) can start from these contracts without changing Phase 6:

* **Input**: a `document_versions` row (id, `storage_object_id`, `sha256`, `mime_type`) — stream the bytes with `StorageBackend.open_range` or the content endpoint. Versions are immutable, so a result can always name the exact version it analysed.
* **Hook**: `complete_upload` is the single place a new version becomes visible; it writes `document.created` / `document.version_added` events (and an outbox event can be added there in the same transaction).
* **Document kind**: derived from `documents.category_id → document_categories.document_kind`; processing must key off the kind, never names (invariant 3).
* **Results**: store suggestions/candidates in new tables referencing `(organization_id, company_id, document_id, version_no)`; AI output is never approval (invariant 6) and missing data is `NULL` plus a flag (invariant 7).
* **Status**: documents already carry `status = quarantined` (unused in Phase 6) for processing rejects; `scan_status` shows the pattern for per-version processing state.
* **Quota**: AI units are `quota.ai_units_per_month` (defined, not yet metered).
* **Desktop**: the queue/statuses and the details panel have room for a processing state; nothing is faked today.
