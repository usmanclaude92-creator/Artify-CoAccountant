# Synchronization Architecture

One authoritative backend. Desktop and mobile are clients with local copies; neither owns a competing database of record. This document describes what **exists** after Phase 12 and, separately, what is still missing. Code: `services/api/src/artify_api/sync.py`, `routers/sync.py`. Tests: `tests/test_p12_sync.py`, `tests/test_p12_chain.py`.

## 1. Principles

1. The server decides. A client never decides that something is saved, approved, locked or "synchronized"; it shows that only after the server said so (ADR-081, extended by ADR-082).
2. Stable ids everywhere: documents, transactions, closings, exports and workspace nodes are addressed by uuid; display names are never keys.
3. Time is the database clock. The change feed orders by a server timestamp; a device clock is never trusted (tested).
4. Reads are permission-filtered exactly like the normal endpoints; the feed cannot be used to learn about records the caller could not open (hidden folders, other companies, other organizations).
5. Writes keep using the existing endpoints with `Idempotency-Key` and `base_row_version`. The feed is read-only.
6. Offline work never bypasses approvals, period locks or closed days: those checks run on the server when queued work is submitted (see §6).

## 2. Change feed — `GET /companies/{company_id}/sync/changes`

| Parameter | Meaning |
|---|---|
| `cursor` | Opaque keyset checkpoint returned by the previous page (`next_cursor`). Omit for the beginning. A malformed cursor is `422 invalid_cursor`. |
| `limit` | 1–500, default 200. |
| `kind` | Repeatable filter: `workspace`, `document`, `transaction`, `closing`, `export`. |
| `overlap_seconds` | 0–600. Re-reads a short window before the cursor so a row that committed late (long transaction) is not missed; the client de-duplicates by `(kind, id, revision)`. |

Each item: `kind`, `id`, `revision` (the row version, or the workspace version), `status`, `changed_at` (server time) and a small `data` object of summary fields — never the document body. The page also carries `next_cursor`, `has_more`, `server_time` and `kinds` (the kinds this caller is allowed to see). For `workspace` the id is the company id and only the latest workspace revision is reported.

The cursor is `(timestamp, kind, id)`; ordering is total, so paging never skips or repeats a row with an identical timestamp. Authorization is per kind (`NEEDS` in `sync.py`): a role without `accounting.read` sees no transactions, without `bookkeeping.read` no closings, without `excel.read` no exports; documents additionally respect folder visibility.

## 3. Preflight — `POST /companies/{company_id}/sync/check`

Up to 200 `{kind, id, base_revision?}` items. The answer lists, per item, `kind`, `id`, `state`, `current_revision` and a human-readable `reason`:

| State | Meaning | Client action |
|---|---|---|
| `current` | The server copy has the revision the client holds (or the client sent none). | Proceed. |
| `stale` | The server revision is newer. | Download first; if local edits exist this is a **conflict** ([CONFLICT_RESOLUTION_GUIDE](CONFLICT_RESOLUTION_GUIDE.md)). |
| `locked` | Cannot be changed: transaction approved/posted/reversed, its business day closed, its accounting period closed; closing closed/reopened/cancelled; exports always. | Do not upload; show the reason; use the correction workflow. |
| `missing` | Not found **or not visible to this caller** (indistinguishable on purpose). | Remove from the queue; ask the user. |

## 4. Conflicts — `/sync/conflicts`, `/sync/status`

A client that could not write an item reports it (`POST /sync/conflicts` with kind, id, its `client_id`, the revision it started from and a small local summary). **The server verifies the claim** with the same check as §3: a current copy is `422 no_conflict`, an invisible item is 404, a stale or locked one is stored in the immutable `sync_conflicts` table (one open conflict per item and device; reporting again is idempotent). `GET /sync/conflicts`, `GET /sync/conflicts/{id}` list and read them, limited to what the caller may still see. `GET /sync/status` returns the server's own count of open conflicts. `POST /sync/conflicts/{id}/resolve` records the decision (`keep_server`, `apply_local`, `merged`, `discarded`) with the resolver, time and note, and writes an audit event; a resolved conflict can never change. Resolving **does not write the record**: `apply_local`/`merged` return `next_base_revision` and the client sends its change through the normal endpoint on top of it. Rules per data type and who may decide: [CONFLICT_RESOLUTION_GUIDE](CONFLICT_RESOLUTION_GUIDE.md).

## 5. Resumable uploads

`PATCH /companies/{id}/documents/uploads/{op}/chunk?offset=N` (raw body) appends a chunk; `GET …/uploads/{op}` returns `received_bytes` and `chunk_max_bytes`. A wrong offset answers `409 offset_mismatch` with the offset the server really holds; an optional `X-Chunk-SHA256` checks one chunk. The last chunk triggers the same hashing, type, size, malware and quota checks as a one-shot upload, and nothing is a document before `complete`. The database column `received_bytes` is the authority; the bytes wait in a per-session staging file (`storage_tmp_dir`), which in a multi-replica deployment **must be a shared volume** — otherwise a resume on another replica finds no file, the server says `received_bytes = 0`, and the client restarts from zero (safe, slower). Desktop and phone use chunks for files of 8 MiB and over; smaller files use the single request.

## 6. Client status vocabulary and what the clients do

`Pending → Uploading → Synchronized`, with `Failed` and `Conflict Detected`. Both clients fold the same inputs in one pure function, and **"Synchronized" appears only when**: nothing is waiting or failed in the local queue, the change feed was read to its end without error, and the server's own count of open conflicts is zero. If the server cannot be asked the answer is "Pending — the server could not confirm", never "Synchronized".

| Client | What exists |
|---|---|
| Desktop (`core/src/sync.rs`, Synchronization page) | feed pull with a transactional checkpoint (SQLite `sync_feed`, `sync_items`), restart on a refused cursor, status fold, conflict list and resolve through a fixed allow-listed command, chunked upload with resume. Unit/integration tests; UI tests with the mock bridge. |
| Phone (`features/sync`, Settings → Synchronization) | the same, with a file checkpoint per company, plus server notifications and the e-mail preference. |
| Control Center | none of the new screens (the endpoints exist). |

## 7. What is NOT built (honest list)

* Conflicts are *reported by the client when its own write is refused or its preflight says stale/locked*; the apps do not yet call `/sync/check` automatically before every queued change, so detection depends on those call sites being added feature by feature.
* No merge editor: "apply my change on the new version" only records the decision and gives the new base revision; the user re-applies the edit.
* The Control Center has no conflicts page; the Daily Closing validation still uses device-declared sync counts (the server count is available at `/sync/status` but not yet part of validation).
* No tombstone retention or purge job (D-8 values undecided); no server push (clients poll); no per-device selective sync setting.
* Resumable staging is not shared across replicas unless the operator provides a shared volume (§5); no background upload while an app is closed.
* The Tauri shell, installers and mobile apps were not built or run on a device.

## 8. Offline rules

Whatever a client queued while offline is submitted through the same endpoints as online work. Therefore: an approval needs a user with the permission at submit time; a transaction dated in a closed accounting period is refused (`period_closed`); a document or exception change on a closed business day is refused by the database trigger; an AI result can never be approved by a queued request without a human actor. The client must treat any 403/409/422 on a queued item as `Requires Review`, not retry it blindly.

## 9. Security

The change feed and the preflight add no table and no write path. The conflict routes add one company-scoped table (`sync_conflicts`, forced RLS, composite foreign key to the company, update/delete refused after resolution by a trigger). Every route runs under the caller's RLS context with the same `Requires("workspace.read", company=True)` gate; cross-company and cross-organization requests return 404, and a conflict about a folder the caller can no longer see disappears from their list (tested with two organizations and a restricted workspace). A feed page contains ids, revisions and short summaries, so a lost cursor leaks nothing the caller could not list anyway.
