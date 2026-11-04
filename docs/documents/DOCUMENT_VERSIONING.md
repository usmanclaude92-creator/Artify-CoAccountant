# Document Versioning

## Model

A **document** is a stable identity (id, folder, name, metadata). A **version** is one immutable binary (`document_versions`). `documents.current_version_id` / `current_version_number` point at the version everyone sees by default.

* The first upload creates document + version 1.
* **Adding a version**: reserve an upload with `document_id` of an existing, *active* document. The user needs the `upload` action on the document's folder. On completion version *n+1* is stored and the current pointer moves; an event `version_created` records who, when and the optional `change_reason`.
* Versions are **never edited or deleted**: a database trigger rejects any change to the binary identity of a version and any `DELETE`; only the scan result and the purge marker may change. The application role cannot bypass it.
* **Restore version** (`POST /documents/{id}/versions/{n}/restore`, body `reason`): moves the pointer back to version *n*. **No bytes are copied and nothing is overwritten**; the later versions remain. Event `version_restored`. Requires the `upload` action on the folder and an active document.
* `GET /documents/{id}/versions` lists all versions (number, file name, type, size, SHA-256, scan status, who and when, reason, whether it is current, whether it was purged). `GET …/content?version=N` streams a specific version (previewed/downloaded events record the version id).

## What is versioned and what is not

| Changes… | …creates a version? |
|---|---|
| The file's bytes | yes |
| Display name, folder, category, custom field values | no — they are fields of the document; `renamed`, `moved`, `metadata_changed` events carry before/after in `detail` |
| Archive / remove / restore | no — `status` and events |

Optimistic concurrency: `row_version` on the document; `PATCH`/`move` accept the version the client saw and answer `409 document_changed` with the current one if someone else edited first.

## Storage and quota

Every non-purged version counts toward `quota.storage_bytes`, including old versions of archived or removed documents. Adding a version is an *increase*: refused with 402 when the plan would be exceeded. Restoring a version does not change usage.

## Purge and versions

Purging a document deletes all of its binaries (`purged_at` set on every version) but keeps the version rows without content, the events and an audit entry ([STORAGE_SECURITY](STORAGE_SECURITY.md)). A purged version cannot come back.

## Desktop

*Versions* tab: list, **View** (preview that version), **Make current** (the restore call). Uploading a new version from the UI has no button yet; the queue and API support it (`QueueItem.document_id`).

## Deliberately not here

No diff, no merge, no compare, no automatic versioning by file name, no approval workflow (Phase 8+). A restored version is an operational choice, not an accounting approval.
