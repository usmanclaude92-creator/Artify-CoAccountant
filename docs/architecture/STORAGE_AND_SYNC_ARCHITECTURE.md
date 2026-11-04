# Storage and Synchronization Architecture

The cloud is the central source of truth (ADR-015). Devices hold caches, queues and a local mirror; they never own data.

## 1. Cloud storage

- **Backend:** S3-compatible object storage behind a `StorageBackend` interface (local development uses MinIO).
- **Object key (ADR-011):** `t/{organization_id}/c/{company_id}/d/{document_id}/v/{version_no}/{sha256}`. Keys never contain customer-chosen names, so workspace renames/moves never touch objects.
- **Immutability:** bucket versioning (and object lock / retention where the provider supports it) protects originals; the application role has no delete permission on `originals/` — deletion happens only through the audited retention/erasure job with a separate role.
- **Integrity:** SHA-256 recorded at upload (client-computed and server-verified); mismatch rejects the upload.
- **Metadata** (`document_versions`, `document_storage_locations`): bucket, key, size, content type (sniffed server-side), hash, encryption reference, malware-scan status.
- **Access:** clients receive short-lived pre-signed URLs **only after** the policy engine authorizes the specific document; URLs are scoped to one object, one method, ≤ 5 minutes.
- **Encryption:** TLS in transit; server-side encryption at rest with provider KMS (per-tenant keys are decision D-4; `encryption_key_ref` column reserved).
- **Upload validation:** allow-list of content types (PDF, JPEG, PNG, HEIC/WebP converted, XLSX/CSV for bank exports), size limits from entitlements, magic-byte verification, malware scan before the document becomes available to workers, PDF active-content stripping/neutralization for rendering.
- **Derived files** (thumbnails, page images, searchable PDFs, Excel exports) live under separate prefixes and can be regenerated; originals cannot.

## 2. Document versioning

A `documents` row is the logical document; each `document_versions` row is an immutable file. Version 1 is the original. Replacements (e.g. a better scan, a rotated copy) add versions; the extraction and review state records which version it was based on. Nothing overwrites a version. Duplicate uploads (same SHA-256 in the Company) are detected before storage and surfaced as `duplicate_candidates` instead of creating silent copies.

## 3. Local Windows storage and the local mirror

### 3.1 Layout
Default root per device: `%USERPROFILE%\Artify\{organization-slug}\{company-slug}\` (configurable per device and per Company via `local_file_mappings`/device settings). Below it the tree mirrors the Company's **own** workspace:

```
…\{company-slug}\
   _Inbox\                   ← watched drop folder → uploads to the Intake Inbox
   {Section display name}\
       {Folder}\{Subfolder}\
           {document file name per the Company's naming rule}
```

- Company A with 4 sections and Company B with 10 produce different trees from different configurations; one Company's structure never alters another's local tree.
- Folder and file names are generated from `display_name` and the Company's **document naming rule** (tokens: date, vendor, document number, category key, custom field values, short document id). Names are sanitized for Windows (reserved names such as `CON`, illegal characters, trailing dots/spaces, 260-character path limit with long-path support enabled or truncation + id suffix). Collisions get a stable short-id suffix.
- The authoritative mapping from document to local path is stored on the device and server (`local_file_mappings`: device, document version, relative path, local hash, state) so renames are *moves*, not delete+download.

### 3.2 Directionality
- **Cloud → local:** the Desktop applies the change feed: new/renamed/moved/archived workspace nodes become directory operations; new document versions are downloaded (policy: all, recent N days, or on demand/"online-only" placeholders for large Companies).
- **Local → cloud:** only through supported ingestion: the Desktop app UI, the `_Inbox` drop folder, and configured watch folders. Structural changes are made only in the Control Center, never by renaming folders in Explorer.
- **Direct Explorer edits (decision D-7):** a moved/renamed file is detected by the watcher and reported as a **refile request** (not auto-applied) subject to permission; deletions never propagate to the cloud; modified content of an original is treated as a *new candidate version*, not an overwrite.

### 3.3 Offline operation
Permitted offline: capture/import into the upload queue, view cached documents, edit review drafts. Not permitted offline: approvals, exports, and anything needing authoritative period/entitlement/permission checks. When connectivity returns, queued operations are replayed through idempotent endpoints; the server revalidates permission, workspace version, entitlement and duplicates at that time.

## 4. Synchronization protocol

### 4.1 Pull (change feed)
Every Company has a monotonically increasing `change_seq`. `company_change_log(organization_id, company_id, seq, entity_type, entity_id, op, version, payload_ref, created_at)` is written in the same transaction as the change. Clients call `GET /companies/{id}/changes?after={cursor}&limit=n` and apply changes in order; the response includes `next_cursor` and `has_more`. If a cursor is older than the retention window, the server answers `410 cursor_expired` and the client performs a snapshot resync (workspace tree + document index). The feed is permission-trimmed per user (a section the user cannot view produces no records for them).

### 4.2 Push (uploads and edits)
1. `POST /companies/{id}/uploads` (with `Idempotency-Key`, `workspace_version`, target section/folder/category, declared size, SHA-256) → returns upload id and pre-signed multipart URLs (resumable).
2. Client uploads parts; `POST …/uploads/{id}/complete` verifies hash, size and type, creates the `documents`/`document_versions` rows, enqueues processing through the outbox.
3. Metadata edits use optimistic concurrency (`If-Match: version`).

### 4.3 Queues and retries
Upload/download queues persist in SQLite on the device: state machine `pending → in_progress → done | failed(retry_at, attempts)`, exponential backoff with jitter, per-item error codes, user-visible status. Idempotency keys make replay safe.

### 4.4 Conflicts
Original files are immutable, so the dominant conflict classes are metadata and structure:

| Conflict | Detection | Resolution |
|---|---|---|
| Metadata edited on two devices | `version` mismatch on `If-Match` | server rejects; client shows both values; user chooses; recorded in `sync_conflicts` |
| Target archived/changed offline | `workspace_version` check | structured `409`; user re-selects target; never silently re-filed |
| Same file captured twice | SHA-256 / `duplicate_candidates` | surfaced to user; no silent drop |
| Local file changed vs cloud version | local hash ≠ mapping hash | local copy preserved as `… (local edit)` candidate version; user decides |
| Folder moved locally (Explorer) | watcher | refile request (D-7) |

`sync_conflicts` stores both sides and the resolution; nothing is lost without an explicit user decision.

## 5. Device registration and control

- `devices`: registered at login (device name, platform, app version, public key fingerprint, last seen, status). Count limited by `limit.devices`.
- Sessions are device-bound; revoking a device invalidates its refresh tokens and flags the local cache for wipe on next contact (best effort; plus remote-lock semantics in the Control Center Devices module).
- `sync_devices` tracks per-device, per-Company sync cursor and status for monitoring.

## 6. Access control in sync

Documents and feed records are served only to principals authorized for that Company and section. The local mirror contains only what the signed-in user may see; switching users on a shared Windows machine uses separate encrypted caches and separate roots.

## 7. Data lifecycle

Retention policies (decision D-8) are per Organization/Company/kind configuration. Expiry never hard-deletes silently: it creates a purge candidate list for review, then a controlled, audited purge job with tombstones in the change feed so devices remove local copies. Legal hold prevents purge.


## Phase 6 implementation status

The object store, upload protocol, versioning and local mirror described here are implemented as of Phase 6 with these refinements: object keys are opaque (`t/{org}/c/{company}/o/{object_id}`, ADR-053), uploads are streamed through the API, there are no pre-signed URLs, multipart/resumable transfer and thumbnails are deferred, and the local folder is an additive derived mirror (ADR-057). See [documents/](../documents/DOCUMENT_MANAGEMENT_ARCHITECTURE.md).
