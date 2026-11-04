---
name: document-storage-and-sync
description: Use when changing document upload/import, storage backends, document versions, duplicate detection, search, storage quotas, the scanner interface, the desktop upload queue, viewers or the local folder mirror in the Artify repo (Phase 6 code in services/api documents.py, storage.py, file_safety.py, scanner.py, routers/documents.py; apps/desktop core queue.rs, docs.rs, mirror.rs). Covers invariants, the three-step upload protocol, how authorisation reuses the workspace engine, and what is and is not verified.
---

# Document storage and sync

Read `docs/documents/DOCUMENT_MANAGEMENT_ARCHITECTURE.md` and ADR-053..057 first.

## Rules that must not break
1. Documents are filed by **workspace node id**; never branch on category/folder names. Moving or renaming folders must not touch documents or storage.
2. Bytes live only in private object storage behind `StorageBackend`; keys are opaque `t/{org}/c/{company}/o/{id}`. No public or pre-signed URLs; the API streams.
3. A document exists only after `complete` verified the object. Persist failures on the upload session (return 4xx without raising, so the transaction commits).
4. Authorisation is two-stage: `Requires("documents.*", company=True)` then `doc_access(ctx)` + `acc.require(action, node)`. Hidden = 404. Add new node-level behaviour to `_BASE_ACTIONS`, not a parallel model.
5. Originals are immutable; corrections are new versions (restore = new version sharing the object).
6. Quotas/limits only through `EntitlementService` (`guard_quota`, `max_file_size`); they refuse new uploads, never delete data.
7. Never claim malware scanning unless a scanner is configured; report `not_scanned`.
8. Desktop: the webview never supplies a file path; ids are validated as UUIDs; query parameters whitelisted; the queue is sealed and user-scoped; "Completed" only after the server confirmed.

## Adding a file type or check
Edit `file_safety.py` (pure; no DB/web imports — enforced by import-linter) and add a case to `test_every_supported_format…` or `test_dangerous_or_damaged_content…`.

## Adding a document operation
Service function in `documents.py` (SQL + audit via `log_event(..., also_audit=True)`), route in `routers/documents.py`, test in `test_p6_documents.py` covering: other org (404), other company, hidden node, role without permission, workspace deny, activity event. Then `make client`.

## Tests
`pytest services/api/tests/test_p6_documents.py` (needs PostgreSQL; moto for S3), `cargo test -p artify-core --test documents`, `pnpm --filter @artify/desktop test`, `make desktop-live` for the real-API flow.

## Not verified here
Real S3/MinIO, real ClamAV, Windows behaviour (WebView2 PDF viewer, Explorer open, native drag-and-drop, installer).
