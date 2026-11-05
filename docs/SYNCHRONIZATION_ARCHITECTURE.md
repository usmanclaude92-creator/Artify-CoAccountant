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

## 4. Client status vocabulary

`Pending → Queued → Uploading / Downloading → Processing → Synchronized`, with `Failed`, `Conflict Detected`, `Requires Review` and `Cancelled` as terminal or parked states. Mapping to what exists today:

| Status | Where it exists today |
|---|---|
| Pending / Queued / Uploading / Failed / Cancelled | Desktop import queue (Phase 6) and mobile file-journal queue (Phase 11), each persisted locally with idempotency keys. |
| Processing | Server processing status of the document (Phase 7), fetched on demand. |
| Synchronized | Shown only after `POST …/complete` returns the stored document (ADR-081). |
| Downloading | Desktop on-demand file copy with checksum verification (Phase 6). |
| Conflict Detected / Requires Review | **Not implemented in clients.** The server can now say `stale`/`locked`; no screen consumes it. |

## 5. What is NOT built (honest list)

* Desktop and mobile do not yet call `/sync/changes` or `/sync/check`; they are the next step (H-1 in the audit).
* No `sync_conflicts` table, no resolve UI, no conflict history (H-2).
* No byte-range resumable uploads (H-3); a retry re-sends the whole file safely.
* No tombstone retention or purge job (D-8 values undecided).
* No device wipe/revocation signal to a running client beyond session revocation, which the client discovers on its next request.
* No server push (websocket/SSE); clients poll.
* Selective sync exists only as "which folders you can see"; there is no per-device include/exclude setting.
* The bookkeeping sync report is still client-declared and is labelled as such in the closing validation.

## 6. Offline rules

Whatever a client queued while offline is submitted through the same endpoints as online work. Therefore: an approval needs a user with the permission at submit time; a transaction dated in a closed accounting period is refused (`period_closed`); a document or exception change on a closed business day is refused by the database trigger; an AI result can never be approved by a queued request without a human actor. The client must treat any 403/409/422 on a queued item as `Requires Review`, not retry it blindly.

## 7. Security

The feed adds no new table and no write path. It runs under the caller's RLS context with the same `Requires("workspace.read", company=True)` gate; cross-company and cross-organization requests return 404 (tested with two organizations). A feed page contains ids, revisions and short summaries, so a lost cursor leaks nothing the caller could not list anyway.
