# Phase 6 — Document Management and Storage

**Status: DELIVERED FOR REVIEW** (started on the owner's Phase 6 brief — *Document Management & Storage Engine*). Report: [`PHASE_6_COMPLETION_REPORT.md`](PHASE_6_COMPLETION_REPORT.md); docs: [`../documents/`](../documents/DOCUMENT_MANAGEMENT_ARCHITECTURE.md).

**Where the owner's brief narrowed this plan (not built, by design):** thumbnails/page images, the document workflow engine and stage history, `document_processing_jobs`, pre-signed multipart upload and signed download URLs (replaced by backend-mediated chunked upload and authenticated streaming, ADR-053), an S3/MinIO driver (ADR-054), RLS mirroring of node grants (ADR-058), Intake Inbox and `_Inbox` watched folder, download-policy automation beyond a bounded manual run. Everything below remains the original plan text, kept for traceability.

## Objectives

Implement secure document ingestion, storage, versioning and retrieval on the configurable workspace, with the Desktop document workspace and the basic Processing Monitor.

## Functional scope

- Upload API (resumable, idempotent), object storage with opaque keys, SHA-256 integrity, malware scan and file validation, thumbnails/page images.
- Documents bound to section/folder/category by stable ids; `kind_at_filing`, `filed_path_snapshot`; manual and rule-based filing (propose/auto-file); Intake Inbox handling; refile with history.
- Versioning (original + additional versions); duplicate detection (exact; fuzzy deferred to Phase 8 keys); `duplicate_candidates` queue.
- Custom field values on documents (typed rows), validated against definitions.
- Section-permission enforcement for documents (API + RLS + change feed).
- Desktop: document list/viewer, upload from UI and `_Inbox`, download policies (all/recent/on-demand), local mirror file placement with naming rules, queue execution, sync status UI.
- Control Center: basic Document Processing Monitor (module 7 part 1) — list, status, filters.
- Workflow engine (document stages and history) running with default and custom workflows; transitions without AI.
- Retention policy plumbing (no purge yet).
- Storage quota enforcement (`quota.storage_bytes`, `quota.documents_per_month`).

## Technical implementation

`documents` domain module, `StorageBackend` interface (S3/MinIO), pre-signed multipart, ingest workers (scan, validate, render), outbox events, change-feed records for documents, workflow engine with ADR-009 enforcement, naming-rule engine shared by server and Desktop (single specification with golden tests), PDF/image library licensing decision (PyMuPDF AGPL concern → recorded), `document-storage-and-sync` skill.

## Database changes

Creates `documents`, `document_versions`, `document_storage_locations`, `document_processing_jobs` (ingest stages), `document_field_values` (+ trigger from Phase 4), `duplicate_candidates`, `workflow_instances`, `workflow_history`, `upload_sessions`, `idempotency` usage for uploads; RLS (company + section grants) and immutability triggers.

## API requirements

`POST /companies/{id}/uploads` (+ complete/abort), `GET /companies/{id}/documents` (filters, cursor pagination), `GET …/documents/{id}`, `GET …/documents/{id}/versions/{n}/content` (signed URL), `PATCH …/documents/{id}` (metadata, `If-Match`), `POST …/documents/{id}/refile`, `POST …/documents/{id}/transitions`, `GET …/duplicates`, `POST …/duplicates/{id}/decision`.

## Dependencies

Phases 4 and 5 complete. Spike: OpenPyXL fidelity (feeds Phase 9). Spike: Arabic evaluation set creation (feeds Phase 7).

## Security requirements

`SECURITY_ARCHITECTURE.md` §7 in full: allow-listed types, magic bytes, size limits, malware scan before availability, signed URLs ≤ 5 min, originals immutable and non-deletable by app role, no content in logs, downloads audited.

## Testing requirements

Executable: TS-WS-03, TS-WS-04, TS-WS-10 (stored values), TS-WS-11, TS-ISO-03 (documents), TS-SYNC-02, TS-SYNC-04, TS-SYNC-05 (server side), TS-ENT-06 (storage/doc quotas), TS-SEC-03; load test on upload path; resumability tests; naming-rule golden tests shared with Desktop.

## Acceptance criteria

1. Documents upload resumably, deduplicate by hash, and store with opaque keys; originals cannot be modified/deleted via the app.
2. Renaming/archiving/moving workspace nodes never moves objects or loses documents (TS-WS-03/04 pass at document level).
3. A user without a section permission cannot list, fetch or receive feed records for that section's documents.
4. Desktop mirrors documents into the Company's own folder tree using the Company's naming rule.
5. Hostile-file fixtures are rejected or neutralized.

## Completion checklist

- [ ] TS scenarios above executed and recorded
- [ ] Library licensing ADR
- [ ] Skill `document-storage-and-sync`
- [ ] Spike notes recorded (OpenPyXL, Arabic dataset)
- [ ] `PHASE_6_COMPLETION_REPORT.md`

## Known risks

Large-file reliability on poor networks; scan latency; naming-rule divergence between server and Desktop; storage cost growth; AGPL licensing pitfall.
