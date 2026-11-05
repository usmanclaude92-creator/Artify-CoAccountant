# Phase 6 Completion Report — Document Management & Storage Engine

**Status: delivered for review. Phase 7 has not been started.** Evidence conventions: *Executed* = run in this sandbox (Linux, PostgreSQL 16, Chromium) or in the PR's CI; *Not verified* = could not be run here.

## 1. Summary
One document engine for cloud, Windows app and Control Center: private object storage with opaque keys, a three-step verified upload protocol, versions, duplicates, search, safe deletion tiers, quotas, a persistent desktop upload queue, a local folder mirror, viewers, and a Control Center storage page.

## 2. Architecture
See [DOCUMENT_MANAGEMENT_ARCHITECTURE](../documents/DOCUMENT_MANAGEMENT_ARCHITECTURE.md). Decisions: ADR-053 (storage), 054 (authorisation), 055 (upload protocol), 056 (accounting), 057 (local mirror).

## 3. Database
Migration `0007_documents.sql`: `documents`, `document_versions`, `storage_objects`, `document_metadata_values`, `document_import_batches`, `document_upload_operations`, `document_events` (append-only). Composite FKs, forced RLS, `measure_entitlement` extended. Desktop SQLite migration 2: `upload_queue`, `mirror_nodes`, `mirror_files`. See [DOCUMENT_DATA_MODEL](../documents/DOCUMENT_DATA_MODEL.md).

## 4. Storage
`LocalBackend` (dev/tests) and `S3Backend` (boto3). Staging/production refuse local. Executed against moto; **not verified against real S3/MinIO**.

## 5. Import and upload
Register → stream → complete; idempotent; persisted failures; quarantine of dangerous content; cancel/retry; batches. [DOCUMENT_IMPORT_AND_UPLOAD](../documents/DOCUMENT_IMPORT_AND_UPLOAD.md).

## 6. Formats and validation
PDF, JPG/JPEG, PNG, TIFF, BMP, HEIC, DOC/DOCX, XLS/XLSX, CSV, TXT, each accepted in tests; 14 dangerous/damaged cases + zip bomb refused. Extension, magic bytes, structure, size, empty, macros, scripts checked server-side.

## 7. Workspace integration
Documents reference node ids; node-level allow/deny/restricted mode govern every operation; folder rename/move/archive never touch documents or bytes; nodes with documents cannot be deleted; impact counts read documents.

## 8. Operations, versions, metadata, duplicates, search, naming/filing
Rename, move, edit metadata, duplicate, archive/restore, soft delete/recover, permanent delete (confirm + hold), versions with restore-as-new-version, custom metadata with Phase 4 validation, same-company SHA-256 duplicate policy, filtered/sorted/paginated search, deterministic suggest endpoint.

## 9. Desktop
Native file dialog and Rust-handled drag-and-drop, hashing, review table (destination/category/name/duplicate policy/suggest), sealed persistent queue (Preparing, Waiting, Uploading, Verification pending, Completed, Failed, Cancelled, Quarantined), restart recovery, All/Archived Documents screens with tree, list, details, versions, activity; image viewer (zoom, fit, rotate, fullscreen, print) and PDF via the WebView2 built-in viewer.

## 10. Local mirror
Id-keyed folder tree, rename/move in place, never delete/overwrite, conflict reporting, unavailable-drive handling, verified downloads. [LOCAL_STORAGE_INTEGRATION](../documents/LOCAL_STORAGE_INTEGRATION.md).

## 11. Security
See [STORAGE_SECURITY](../documents/STORAGE_SECURITY.md) and [SECURITY_TEST_RESULTS](../documents/SECURITY_TEST_RESULTS.md). **Malware scanning is not active by default** and the ClamAV client was tested only against a protocol fake.

## 12. Entitlements
`limit.file_size_bytes`, `quota.storage_bytes`, `quota.documents_per_month`; limits refuse new uploads only. [STORAGE_ENTITLEMENTS](../documents/STORAGE_ENTITLEMENTS.md). Phase 2/3 tests that asserted storage was "not metered" were updated deliberately.

## 13. Control Center
"Documents & Storage" page (usage vs plan, per-company storage, uploads last 7 days, recent failures, read-only storage check) backed by `/storage/overview` and `/storage/reconcile`; real database figures only.

## 14. API
[DOCUMENT_API_REFERENCE](../documents/DOCUMENT_API_REFERENCE.md); OpenAPI regenerated, 0 breaking changes.

## 15. Tests executed
| Suite | Result |
|---|---|
| Phase 6 backend (`test_p6_documents.py`) | 115 passed |
| Full API suite | 393 tests run, 0 failed, 1 skipped (no failures; the run was started before the final formatting-only fix) |
| Desktop Rust core | all passed incl. 20 new document tests (`documents.rs`) |
| Desktop UI (vitest) | 59 passed |
| Desktop ↔ real API (`desktop_live.sh`) | 6 passed (incl. import/duplicate/mismatch/permissions flow) |
| ruff / mypy / import-linter, tsc, eslint, cargo clippy | clean |
| Web unit tests, production build | passed |
| CI on the PR | first Phase 6 push failed on `ruff format` (test file) and one Windows path-separator assertion in `desktop-windows`; both fixed in the follow-up commit |
| Browser e2e (`documents.spec.ts`) | written; see section 17 |

## 16. Partial / not implemented (honest list)
No multipart/resumable upload (a retry resends the file); a streaming upload holds a DB transaction for its duration. No thumbnails; no preview for HEIC, TIFF or Office files (Open/download instead). PDF viewing/printing depends on WebView2. No bidirectional sync or Explorer-change detection (D-7 open); no bulk offline download. No automatic retention schedule or purge job (hold + soft-delete tiers only). Search is by name/metadata, not document content. Duplicate detection is exact SHA-256 only. `source` is always `desktop` until Mobile exists. Control Center page has no drill-down.

## 17. Not verified here
Real S3/MinIO; real ClamAV; Windows-only behaviour (installer, WebView2 PDF viewer, Explorer open, native drag-and-drop) — Windows CI ran the Rust core tests only; load/performance at scale. Browser e2e: see final status in the PR.

## 18. Running and testing
`make db-reset && make test-api`; `pytest services/api/tests/test_p6_documents.py`; `make desktop-test`; `make desktop-live`; `make e2e`; `python3 tools/check_docs.py`. Configure `ARTIFY_STORAGE_BACKEND=s3` plus bucket settings for real deployments ([CLOUD_STORAGE_ARCHITECTURE](../documents/CLOUD_STORAGE_ARCHITECTURE.md)).

## 19. Next
Owner review of Phase 6. Phase 7 starts only after approval ([PHASE_7_INTEGRATION](../documents/PHASE_7_INTEGRATION.md)).
