# Cloud Storage Architecture

## Interface

`StorageBackend`: `put_file`, `stat`, `open_range`, `delete`, `list_objects`. Selected by `ARTIFY_STORAGE_BACKEND` (`local` | `s3`). Staging and production **refuse** `local` (settings validator) and require `ARTIFY_STORAGE_S3_BUCKET`.

| Setting | Meaning |
|---|---|
| `storage_s3_bucket`, `storage_s3_region`, `storage_s3_endpoint_url` | Bucket and optional S3-compatible endpoint (MinIO, R2…) |
| `storage_s3_access_key_id`, `storage_s3_secret_access_key` | Optional; the SDK credential chain is used when unset |
| `storage_s3_sse` | Server-side encryption (`AES256` or `aws:kms`) |
| `storage_tmp_dir` | Upload staging directory |
| `max_file_size_ceiling_bytes` (200 MB) | System ceiling above any plan |
| `upload_session_ttl_seconds` (24 h) | Session lifetime |

## Keys and privacy

`t/{organization_id}/c/{company_id}/o/{object_id}` — opaque, independent of names and folders, CHECK-constrained in the database and validated again by every backend (path traversal, wrong shapes → `StorageError`). Objects are private; there are **no public URLs and no pre-signed links**. Downloads are streamed by the API after authorisation, with `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff` and Range support. Only PDF, PNG, JPEG and BMP may be served inline; everything else is forced to download as `application/octet-stream`.

## Integrity

SHA-256 and size are computed on arrival, stored in `storage_objects`, re-read (size) after `put_file`, re-checked at `complete`, and announced on download (`X-Document-Sha256`) so the desktop refuses a damaged download. The database row and the object are not one transaction, so:

* the object is written **first**; a failure before the row commits leaves an *orphan object* (no row);
* a failure after leaves nothing visible to the user;
* **reconciliation** (`POST /storage/reconcile`, dry run by default) finds rows whose object is missing (marked `missing` on repair, reported honestly on download as 503), objects without a live row older than `min_age_seconds` (deleted only on repair; young objects are never touched), and expired sessions (content discarded).

## Deletion tiers

archive (reversible) → soft delete (reversible, bytes kept) → permanent delete (`DELETE …?confirm=true`, needs `documents.purge`, only from deleted items, blocked by retention hold). Purge removes every version's object, marks rows `purged`, scrubs names/metadata and keeps the trail. A backend delete failure leaves a *purged* row plus a leftover object which reconciliation removes.

## Verified against

`LocalBackend` and `S3Backend` through **moto** (`test_s3_backend_behaves_like_the_local_backend_under_moto`, `test_full_upload_download_and_purge_against_s3`). **Not verified against a real S3/MinIO endpoint** in this phase.
