# Document Management Architecture (Phase 6)

One central document engine serves the Windows app, the Control Center and (later) Mobile. The cloud API owns documents; the desktop never decides what is allowed and never keeps the only copy.

## Components

| Piece | Where | Job |
|---|---|---|
| Document service | `services/api/src/artify_api/documents.py` | Upload sessions, documents, versions, lifecycle, search, suggestions, storage usage, reconciliation |
| HTTP API | `routers/documents.py` | Thin, typed routes; every route needs a `documents.*` permission on the company |
| File safety | `file_safety.py` (pure) | Extension allow-list, magic-byte/structure checks, executable/macro/PDF-script refusal, file-name sanitising |
| Storage backends | `storage.py` | `StorageBackend` interface: `LocalBackend` (dev/tests) and `S3Backend` (S3-compatible, tested with moto) |
| Scanner interface | `scanner.py` | `NoopScanner` (default, honest "not scanned") and a ClamAV `clamd` INSTREAM client |
| Entitlements | `entitlements.py` | `limit.file_size_bytes`, `quota.storage_bytes`, `quota.documents_per_month` enforced by `EntitlementService` |
| Desktop core | `apps/desktop/core/src/{queue,docs,mirror}.rs` | Staging, persistent upload queue, streaming transfer, previews, local folder mirror |
| Desktop UI | `apps/desktop/src/screens/documents.tsx` | All Documents, Import Documents, Upload Queue, Archived Documents, viewer |
| Control Center | `apps/web/.../documents/page.tsx` | Storage, upload activity, failures, storage check |

## Principles

1. **Workspace-driven.** A document is filed in a workspace node *by id*. No category, section or folder name exists in code (invariant 3). Renaming, moving or archiving a folder never touches a document or its stored bytes.
2. **Metadata in PostgreSQL, bytes in private object storage.** Keys are opaque (`t/{org}/c/{company}/o/{object_id}`): no file or folder names, so a rename never needs a storage operation. No public URLs; the API streams to authorised callers (ADR-053).
3. **Two-stage authorisation.** Stage 1: route permission (`documents.read|upload|manage|delete|purge`, `storage.manage`) plus company scope (out of scope → 404). Stage 2: the Phase 4 allow/deny engine on the document's node, so workspace grants and restricted mode govern documents exactly like folders (ADR-054). Hidden documents answer 404, visible-but-forbidden answer 403.
4. **Originals are immutable.** A correction is a new version; restoring an old version adds a new version that points at the same stored object. Nothing is overwritten.
5. **No success before confirmation.** A document exists only after `complete` verified the stored object (ADR-055).
6. **Limits never delete.** Exceeding a quota blocks *new* uploads; existing documents stay readable, downloadable, archivable and purgeable.
7. **AI is out of scope.** Suggestions of destination and name are deterministic rules (Phase 4 engines); nothing here classifies, extracts or approves.

## Data flow (import)

```
Desktop: pick/drop (Rust) -> stage + SHA-256 -> user reviews -> SQLite queue (sealed)
  -> POST uploads (idempotency key)      server: session, entitlement + permission checks
  -> PUT uploads/{id}/content (stream)   server: hash, size, magic bytes, scan, store, verify
  -> POST uploads/{id}/complete          server: verify object, create document + version v1
  -> queue item 'completed'
```

See [DOCUMENT_IMPORT_AND_UPLOAD](DOCUMENT_IMPORT_AND_UPLOAD.md) and [CLOUD_STORAGE_ARCHITECTURE](CLOUD_STORAGE_ARCHITECTURE.md).

## What is deliberately not here

OCR, AI classification/extraction, accounting entries, VAT, reconciliation, Excel, Mobile, full offline sync, multipart/resumable transfer and thumbnails. See the partial list in the [completion report](../development/PHASE_6_COMPLETION_REPORT.md).
