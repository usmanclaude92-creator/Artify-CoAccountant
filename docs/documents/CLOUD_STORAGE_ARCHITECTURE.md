# Cloud Storage Architecture

## Interface

```python
class ObjectStorage(Protocol):
    def new_key(org_id, company_id) -> str          # opaque key, see below
    def stage_append(stage_ref, offset, data) -> int  # append at an exact offset (server-authoritative)
    def stage_size(stage_ref) -> int
    def stage_open(stage_ref) -> BinaryIO
    def stage_discard(stage_ref) -> None
    def commit_stage(stage_ref, key) -> None        # never overwrites an existing key
    def exists(key) / size(key) / read(key, start, end)  # ranged read
    def delete(key) -> None
    def list_keys() / list_stages()                 # for reconciliation
```

Implemented: **`FilesystemStorage`** (`storage_backend = "filesystem"`, `storage_root`, default `./var/storage`). **Not implemented: an S3/MinIO driver.** Selecting any other backend is rejected by settings validation. This is an honest gap: the filesystem driver is correct for a single API node with a persistent volume; a multi-node deployment needs shared storage or the future object-store driver.

## Keys

Storage keys are **opaque and never derived from names or folders**: `o/<organization uuid hex>/<company uuid hex>/<version uuid hex>` (matched by a strict regex before any path is built; anything else raises `StorageError` — path traversal and absolute paths are impossible by construction). The database column additionally has a `CHECK` on the shape. File names, folder names and tenant names never appear in a key. Staging files use a separate 32-hex reference.

## Durability and consistency

* `commit_stage` renames the staging file into place and never overwrites; the key is unique in the database.
* Finalisation order: commit object → insert rows (nested transaction) → if the insert fails, **delete the object**. A crash between the two leaves an *orphan object*, which `reconcile` finds.
* Versions are immutable; purging removes the object and sets `purged_at`.

## Reconciliation

`POST /orgs/{org_id}/storage/reconcile` (permission `documents.purge`; `GET /orgs/{org_id}/storage` shows usage) and `artify-api reconcile-storage` compare one organization's objects with its version rows:

* **orphans** — objects with no version row, older than a grace period (60 min; an in-flight store is never touched);
* **missing** — version rows whose object is gone (reported, never "fixed" silently);
* **stale staging** — staging files of finished/expired sessions.

`dry_run` reports only; otherwise orphans and stale staging are removed. Each run is audited (`storage.reconciled`).

## Operating requirements (not provided by this repository)

* A **persistent, encrypted volume** that is never served by a web server and is excluded from public routes, with **backups and restore tests**. None of this is configured here; the compose file uses a local volume for development only.
* Clock and disk-space monitoring. Writes fail closed with `storage_error` (retryable) when the disk is full.
* A decision on the production object store (D-3/hosting) and an S3/MinIO driver if multi-node is required.

## Downloads

`GET /documents/{id}/content` streams through the API (HTTP `Range` supported, `Content-Length`, `Accept-Ranges`, `X-Content-Type-Options: nosniff`, `X-Content-SHA256`, `X-Document-Version`). `?inline=true` is honoured only for PDF and images; every other type is always an attachment. Each access writes a `downloaded` (or `previewed`) event. Infected versions cannot be downloaded. There are **no signed or public URLs**.
