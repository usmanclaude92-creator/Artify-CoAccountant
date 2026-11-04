# Document Import and Upload

## Protocol (server)

All routes live under `/companies/{company_id}`; the caller needs `workspace.read` on the company and, per folder, the Phase 4 node action `upload` (granted by the role key `documents.contribute`, or by explicit node grants). A hidden folder answers 404, a visible but forbidden one 403.

1. **Reserve** — `POST /uploads` with `filename`, `size`, optional `sha256`, `node_id`, optional `category_id`, `document_id` (adds a **version** to that document), `display_name`, `field_values`, `duplicate_policy` (`block` default, or `allow_copy`), `batch_id`, `source`, `device_id`. Header **`Idempotency-Key`** (8–128 chars) is required; repeating the call returns the same session (`200`, not `201`). Checks: folder permission, file extension allowed, size ≤ effective maximum, per-user open-session cap, **storage quota** (an increase that would exceed the plan is refused with 402 before any byte is sent), metadata valid against the configured fields.
2. **Send** — `PUT /uploads/{id}/content?offset=N`, body = raw bytes (1 byte … `upload_chunk_max_bytes`, default 8 MiB). **The server owns the offset**: a wrong offset answers `409 offset_mismatch` with `received_bytes`, the client continues from there. `GET /uploads/{id}` returns the state for resuming after a crash. Data is appended to a *staging* file; nothing is a document yet.
3. **Complete** — `POST /uploads/{id}/complete`. In order: size must equal the declared size (a partial upload gets `409 size_mismatch` and **changes nothing**, so it can continue); SHA-256 must equal the declared one if given; **content inspection**; optional **malware scan**; **duplicate check**; **quotas**; then the object is committed to storage and the `documents` / `document_versions` / events rows are written in a nested transaction (the object is deleted again if the database part fails). The response carries the new document id.
4. **Cancel / retry** — `POST …/cancel` releases the staging data; `POST …/retry` re-opens a failed session whose cause is retryable.
5. Failures are returned as `problem+json` **and the session state is committed** (so the next call sees `failed` / `quarantined`). Codes that keep the staged data: `storage_error`, `scan_unavailable`, `duplicate_detected`, `target_unavailable`, `size_mismatch`.

`PATCH /uploads/{id}` changes the destination/metadata of an open session; `GET /uploads` lists sessions (your own, or everyone's with `configure` on the company). `POST /document-batches` creates a batch. `GET /documents/limits` returns the effective limits (file size, batch size, allowed extensions, chunk size) so clients never hard-code them.

## What is checked on the content

See [STORAGE_SECURITY](STORAGE_SECURITY.md). Summary: allow-listed extensions only; the first/last bytes must match the claimed type (PDF `%PDF-` + `%%EOF`, JPEG, PNG, TIFF, BMP, HEIC, Office containers, text/CSV without NUL); executables and scripts are refused; PDFs with JavaScript or launch/embedded-file actions are **quarantined** (not stored). Empty files are refused.

## Desktop import (what the user sees)

* **Choose files…** (native dialog) or **drag them onto the window**. The Rust side remembers each path behind an opaque token; the UI only ever sees *token, name, size*.
* Per file: destination folder (only folders the server says the user may upload to), category, document name (blank = keep the file name), configured **detail fields** for that folder/category, **Suggest folder and name** (runs the company's filing/naming rules on the server), and a **duplicate policy**.
* **Add to the upload queue.** The core re-checks extension, emptiness and size against the server's limits, hashes the file (streaming), stores a sealed queue row, and the background loop (every 10 s, up to 3 uploads in parallel) uploads it.
* The original file on the PC is **never moved, changed or deleted**.

## Upload queue (Desktop)

States: *Waiting → Preparing → Uploading → Verifying → Stored in the cloud*, or *Failed*, *Cancelled*, *Blocked*. A document is shown as stored **only after the server confirmed it**. Offline or server errors keep the entry and retry with back-off (30 s, 2 min, 10 min, 30 min, then hourly; up to 6 automatic attempts); an app restart puts interrupted uploads back in the queue and resumes at the server's offset. Duplicates wait for the user ("Add it anyway" / "Do not add it"). Errors are explained in plain language and never include file contents or tokens.

## Adding a version

Queue a file with `document_id` set (API: `document_id` on reserve). The new bytes become version *n+1* and the current pointer moves; version *n* stays exactly as it was. The Desktop UI exposes viewing, making an earlier version current, and activity; uploading a new version from the UI is available through the API and the core (`QueueItem.document_id`) but has no dedicated button in this release.

## Batch limits

`limit.import_batch_files` (plan) can lower the central `import_batch_max_files` (default 100). The Desktop refuses a larger selection with a clear message; the server enforces it too.
