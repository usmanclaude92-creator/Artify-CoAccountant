# Phase 7 Integration (OCR and AI Document Intelligence)

Phase 6 stores and organises files and does **nothing else** with their content. This page lists the seams Phase 7 should use, and what it must not change. Do not start Phase 7 before the owner approves Phase 6.

## What Phase 7 can rely on

| Need | Provided by Phase 6 |
|---|---|
| A trigger when a new binary exists | the outbox event **`document.version_created`** (payload: `document_id`, `version_id`, `version`, `sha256`) written in the same transaction as the version row; delivery uses the existing outbox dispatcher |
| An immutable input | `document_versions` (hash, size, MIME from content, opaque key). Pipelines are keyed `(document_version_id, stage, processor_version)`; re-running never changes the original |
| The Artify meaning of a document | `documents.kind_at_filing` (from the category → document-kind mapping) — processing selects its extraction schema from this, **never from a folder or category name** |
| Reading the bytes | `ObjectStorage.read(key)` inside the API/worker, or the authenticated `GET …/content?version=N` for a client; no public URLs |
| Who may see what | the same access engine (`workspace.load_access`) — a processor acting for a user must pass the same checks; background workers need an explicit system actor design |
| Provenance for results | `document_events` (`actor_type = 'system'` is already allowed) and the audit log |
| Per-company custom fields | `document_field_values` stores **user-confirmed** values |
| Duplicate/near-duplicate queue | `document_duplicate_candidates` (decisions) — Phase 8 extends it; Phase 7 may add *fuzzy* candidates |
| Quotas | `EntitlementService`; AI units (`quota.ai_units_per_month`) are a new metric, not a storage one |

## Rules Phase 7 must keep

1. **AI never approves or finalises.** Extracted values go to new suggestion tables (`ai_suggestions`, `extracted_data` as in `PHASE_7.md`) with confidence and provenance; they are **never written into `document_field_values` or the document row automatically**. A user (actor `user`) accepts a suggestion, which is then an audited edit.
2. **Do not invent missing data.** Absent stays absent (`NULL` + flag).
3. **Do not weaken validation.** The upload path rejects/quarantines before storage; OCR must not become a second ingest route that skips content inspection and quotas.
4. **Quarantined or infected files are not stored**, so there is nothing to process; if a review queue for quarantine is added later it needs its own access rules.
5. **Provider data policy** (D-5): sending document bytes to an external service is outbound disclosure; it needs the organization's `ai_processing_policy` and must never include other tenants' data. Nothing in Phase 6 sends bytes anywhere.
6. **Money is `Decimal`/`NUMERIC`/strings.**

## Gaps Phase 7 will hit (known, not fixed here)

* **No processing status** on `documents` yet and no `document_processing_jobs` table (the older `PHASE_6.md` plan listed one; Phase 6 as briefed did not build processing). Phase 7 creates them.
* **No thumbnails / page images** are generated; the Desktop viewer renders images and uses the web view's PDF viewer. Phase 7 needs rasterisation with an acceptable licence (the PyMuPDF AGPL question in `PHASE_6.md` is still open).
* **No S3 driver**: workers on other nodes cannot read a local-filesystem store. Either keep workers on the API node, mount shared storage, or implement the driver first.
* **Folder permissions are not in RLS**; a worker using the application role must apply `load_access` itself.
* **Search is metadata only** (name, file name, text field values); there is no content index.
* **Mobile** and **Intake Inbox** upload sources exist as enum values (`mobile`, `inbox`) but no client or route is built for them.

## Desktop

The viewer, document details and upload queue are where review UI will plug in. The Rust command surface is closed (`DocOp::parse` lists the operations); adding processing endpoints means adding variants there, with tests.
