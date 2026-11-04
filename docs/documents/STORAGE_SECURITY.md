# Storage Security

Read with [`../security/SECURITY_ARCHITECTURE.md`](../security/SECURITY_ARCHITECTURE.md) §7. Each statement below says how it is enforced; the executed tests are listed in [SECURITY_TEST_RESULTS](SECURITY_TEST_RESULTS.md).

## Tenant isolation

* Company-scoped tables carry `(organization_id, company_id)` with composite FKs and **forced RLS**; the application role has no `BYPASSRLS`. A document id from another company or organization answers **404** (never 403), including on content, versions, events, move targets and uploads.
* Folder permissions (Phase 4 allow/deny, hidden nodes) are enforced **in the API on every operation**; they are *not* mirrored in RLS (a documented limitation: a bug in a handler could bypass folder-level restrictions within a company the user can already reach, but not company or organization boundaries).
* The platform realm has **no access** to documents: platform roles hold no document permission; support grants (Phase 2) show metadata aggregates only. Nothing in the platform console lists file names.

## Files

| Threat | Control |
|---|---|
| Executable / script disguised as a document | extension allow-list **and** content detection (a real Windows PE header, ELF, Mach-O/Java class, `#!` scripts; web/script markup in text files) ⇒ refused; MIME type is derived from content, the client's type is ignored |
| Wrong extension (e.g. `.pdf` that is a PNG) | detected type must match the extension family, else refused |
| Corrupt / truncated files | PDF needs `%%EOF`, JPEG/PNG end markers, Office files must be valid ZIP/OLE containers, text/CSV must not contain NUL |
| Active content | PDFs with `/JavaScript`, `/JS`, `/Launch`, `/RichMedia`, `/EmbeddedFile` and Office files with macros are **quarantined**: the session ends as `quarantined` (code `quarantined`), **nothing is stored as a document**, and the staged bytes are removed. There is no admin review queue yet |
| Malware | optional ClamAV (`malware_scan = clamd`): scan before availability; an infected file ⇒ session `quarantined` (code `malware_detected`), nothing stored; **scanner down ⇒ fail closed** (`scan_unavailable`, retryable, staged data kept). **Default is `off`** (versions are labelled `not_scanned`) — the clamd path is tested against a fake clamd server only, not a real ClamAV |
| Zip bombs / oversize | size ceiling (central and plan), declared size enforced exactly, no decompression is ever done by the API |
| Path traversal, weird names | storage keys are opaque (`o/<hex>/<hex>/<hex>`, strict regex) and never contain names; `original_filename` is reduced to its last segment, NFC-normalised, control characters refused, and used only for display and extension; `display_name` has a database `CHECK` against `/ \ : * ? " < > |` |
| Wrong content on resume/replay | server-owned offset, exact declared size, optional SHA-256, `409 offset_mismatch`; completion verifies before storing |
| Overwriting stored data | `commit_stage` refuses an existing key; version rows are immutable (trigger); no `DELETE` grant |
| Content sniffing / XSS via download | `X-Content-Type-Options: nosniff`, `Content-Disposition: attachment` for everything except PDF/images when `inline=true`; the app serves under the API origin with the global CSP; HTML/SVG are not allowed types |
| Public exposure | the storage root is a private directory; no static route, **no signed or public URLs** |
| Enumeration | random v4 UUIDs; not-found and not-allowed look the same for out-of-scope objects |
| Abuse | per-user open-session cap, session TTL (24 h, then `upload_expired` and staging cleaned), `Idempotency-Key` required |

## Deletion and retention

* **Remove** (soft delete) needs the `delete` action on the folder and a reason (≤ 500 chars); it is reversible (`undelete`).
* **Permanent delete (purge)** needs the organization-level permission `documents.purge` (owner and administrator only), the document must be *removed* first, **no legal hold**, the **retention period** (`document_purge_min_days`, default 30) must have passed, and the user must **type the document's name**. It deletes the binaries, sets `purged_at` on every version, keeps a tombstone (row, version rows without content, events) and writes an audit event. Failure of any guard changes nothing.
* **Legal hold** (`documents.purge`) blocks purge; every change is audited.

## Audit and privacy

Every create/version/rename/move/metadata/archive/remove/restore/purge/hold and every download or preview is a `document_events` row; business-significant actions also write the organization audit log. The document modules contain **no logging calls**, so file names and contents cannot reach application logs; error messages returned to clients never include stored content. A rejected or quarantined upload has no document and therefore no `document_events` row; its upload session (status, code, size, timestamps) is the record. Desktop logs and diagnostics bundles are redacted (Phase 5) and contain no paths or file names from the queue.

## Desktop

* The webview has no file-system, shell or HTTP capability; it can only call the allow-listed commands. **It never supplies a path**: files are chosen by the native dialog or drag-and-drop in Rust and referred to by an opaque token; the file to open is chosen by the core.
* The queue and mirror tables live in the **sealed** SQLite cache (AES-GCM with associated data bound to table/user), the file path and names inside sealed JSON.
* Downloads land in a temp folder in the app's data directory, are verified by SHA-256, then moved with a no-overwrite rule; paths below the storage root are validated segment by segment.
* Tokens remain in Rust (Phase 5 rule). The PDF/image preview uses `blob:` URLs; CSP allows `img-src 'self' data: blob:` and nothing else new.

## Known gaps (also in the completion report)

No S3/MinIO driver and no encryption-at-rest implemented by the application (relies on the volume); malware scanning off by default and unverified against a real ClamAV; folder permissions not in RLS; quarantined content is rejected and discarded, not retained for review; no per-company quota; Windows-only behaviours (Explorer open, drag-and-drop, WebView2 PDF viewer) are not verified on a real Windows desktop in this environment.
