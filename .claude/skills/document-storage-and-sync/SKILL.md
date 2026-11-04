---
name: document-storage-and-sync
description: Use when changing document upload, storage, versions, search, duplicates, quotas or the Desktop import queue and local mirror in the Artify repo (services/api documents*.py, storage.py, file_validation.py, scanner.py, routers/documents.py; apps/desktop core documents/imports/mirror). Covers the upload protocol, opaque keys, immutable versions, storage accounting, folder permissions, and how to test each layer.
---

# Document storage and sync

Read first: `docs/documents/DOCUMENT_MANAGEMENT_ARCHITECTURE.md`, then the topic doc in `docs/documents/`.

## Rules that must not break
1. **Bytes never go into PostgreSQL.** Rows hold metadata and an opaque `storage_key` (`o/<org hex>/<company hex>/<version hex>`, strict regex in `storage.py`). Never build a key or path from a name, folder or tenant name.
2. **Validate before storing.** Upload = reserve → `PUT` chunks at the server's offset → complete (size, SHA-256, `inspect_file`, scanner, duplicate, quotas) → commit object + rows in one flow (delete the object if rows fail). No signed or public URLs; downloads are authenticated, audited streams.
3. **Versions are immutable** (DB trigger). Change = new version; "restore version" = move `current_version_*`. Purge deletes binaries and leaves a tombstone. There is no `DELETE` grant on documents, versions or events.
4. **The workspace decides placement.** Documents reference node/category ids (`ON DELETE RESTRICT`). No folder or category name in code; processing keys off `kind_at_filing`. Out-of-scope or hidden → 404.
5. **Folder permissions are enforced in handlers** (`env.acc.require/can/visible`, search query filters `d.node_id = ANY(:vis)`); RLS only isolates organization/company. Any new listing, count or aggregate must apply the visibility filter.
6. **Quotas only via `entitlements.guard_amount`** (at reserve and under lock at completion). Archive/remove free nothing; purge frees. Plans can only lower the central size/batch ceilings. Shrinking a plan never deletes.
7. **Never invent metadata.** Missing stays missing; naming/filing are *suggestions* from configured rules; the original file name is the fallback.
8. **Desktop:** the webview never supplies a path (tokens from `Staging`); document operations go through the closed `DocOp::parse` set; local files are placed by node id, verified by SHA-256, never overwritten; an upload is "stored" only after the server confirmed it; pending uploads are never discarded silently.

## Adding a feature
- New table → follow `multi-tenant-saas` (composite FKs, forced RLS, grants, no DELETE for immutable data) and extend `test_tenant_columns`-style checks.
- New route → `routers/documents.py` with the right node action; add the audit/event row; run `make client`; extend `test_p6_documents.py` (two-company, permission, quota cases) and `test_p6_security.py`.
- New file type → `file_validation.TYPES` + content check + sample in `tests/samples.py` + `test_p6_validation.py`; update `docs/documents/STORAGE_SECURITY.md`.
- New Desktop operation → add a `DocOp` variant + validation + `core/tests/documents.rs` case, then a thin command in `src-tauri`, then bridge types/mock/UI and a UI test. Update the command count in docs/ADR.

## Tests
`services/api/tests/test_p6_*.py` (real PostgreSQL), `apps/desktop/core/tests/{documents,mirror}.rs` (wiremock), `infrastructure/dev/desktop_live.sh` (core against a real API), `apps/desktop/src/documents.test.tsx`, `apps/web/e2e/documents.spec.ts`. Use synthetic files from `tests/samples.py`; never real documents.
