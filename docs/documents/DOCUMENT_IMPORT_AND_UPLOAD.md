# Document Import and Upload

## Protocol (three steps, all restart-safe)

1. **Register** `POST /companies/{c}/documents/uploads` — body: `idempotency_key`, `filename`, `size`, optional `sha256`, `node_id` (or `target_document_id` for a new version), `category_id`, `display_name`, `metadata`, `duplicate_policy` (`block`|`allow_copy`), `filing_rule_id`. The server validates the extension, size against the plan, destination permission (`upload` on the node), category/file-type rules, custom metadata (Phase 4 validation, required fields enforced), duplicate content (when a checksum is announced) and quotas. Repeating the same `idempotency_key` returns the same session (HTTP 200); reusing it for a different file is 409.
2. **Send bytes** `PUT .../uploads/{id}/content` (raw body). The server streams to a temporary file computing SHA-256 and size, then: size and announced checksum must match; `inspect_file` checks the real content; duplicate and quota checks; scanner; store; read back size. A rejected file answers 4xx **and the failure is persisted** (`status` failed/quarantined, `error_code`). A repeat PUT after success is a no-op.
3. **Complete** `POST .../uploads/{id}/complete` — re-verifies the object, creates the document and version 1 (or the next version), metadata values, events, audit. Idempotent.

Also: `GET uploads` / `GET uploads/{id}` (own sessions only), `cancel` (discards received bytes), `retry` (only transient failures: storage/scanner unavailable), import batches (`POST/GET import-batches`).

## Supported formats

PDF, JPG/JPEG, PNG, TIFF/TIF, BMP, HEIC/HEIF, DOC/DOCX, XLS/XLSX, CSV, TXT. Everything else is refused at registration. The client allow-list in the desktop is advisory; the server list is authoritative.

## Validation (backend, never trusted from the client)

| Check | Result |
|---|---|
| Extension not allowed | 422 `file_not_accepted` (`unsupported_type`) |
| Declared size over the plan limit | 402 `entitlement_exceeded` (`limit.file_size_bytes`); over the system ceiling 422 `file_too_large` |
| Bytes ≠ announced size / checksum | `size_mismatch`, `checksum_mismatch` |
| Content does not match the extension | `content_mismatch` |
| Truncated / damaged | `corrupt_file` |
| Executable, script | `executable_content` → **quarantined** |
| Office macros or embedded executables | `macro_content` → quarantined |
| PDF with JavaScript / Launch action | `active_content` → quarantined |
| Decompression bomb, unsafe archive path | `archive_bomb`, `unsafe_archive_entry` → quarantined |
| Identical content already in this company | 409 `duplicate_document` (policy `block`) |
| Scanner verdict infected | `malware_detected` → quarantined |

"Quarantined" means *refused and flagged; nothing was stored*. The upload attempt is audited.

## Desktop behaviour

Files are chosen in a native dialog or dropped on the window; both are handled in Rust, which hashes them and gives the UI opaque ids. The review screen sets destination, category, name and duplicate handling per file (with a **Suggest** button that calls the deterministic filing/naming engines). Items enter a sealed SQLite queue with statuses **Preparing, Waiting, Uploading, Verification pending, Completed, Failed, Cancelled, Quarantined**. See [LOCAL_STORAGE_INTEGRATION](LOCAL_STORAGE_INTEGRATION.md) for restart recovery.

## Limits and honest cuts

No multipart or resumable transfer: a retry sends the file again from the start (the registered session and idempotency key are reused). A PUT holds one database transaction and a row lock on its own session for the duration of the stream (documented trade-off, ADR-055).
