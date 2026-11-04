# Synchronization Foundation

Phase 5 synchronizes **workspace configuration only**. Documents, uploads and offline capture belong to Phase 6+ and are not implemented.

## What exists

- **Connectivity** — every API call updates an online/offline indicator (top bar, Synchronization page). A background task, while a company is open, revalidates its workspace every 60 s with an ETag request (cheap when nothing changed) and emits `workspace-updated`, `sync-error` or `session-expired` events.
- **Per-company sync state** — last success, last attempt, last error code, last version (`sync_state`). The last *success* time is never overwritten by a failure.
- **Manual refresh** — Workspace page and Synchronization page.
- **Safe cache updates** — only a *well-formed, same-company, not-older* configuration replaces the cache; changes are described by stable id; failures are recorded and surfaced (notification + last error), never swallowed.
- **Pending-operation metadata** — table `pending_ops` with a persistent state machine skeleton (`pending → in_progress → done|failed`, attempts, `next_retry_at`, sealed payload), counters shown on the Synchronization page (all zero today because nothing enqueues yet). A cache wipe (logout, expiry, revoked device) **keeps** pending operations.
- **Device identity** — server-issued device id stored locally (see [`DEVICE_REGISTRATION.md`](DEVICE_REGISTRATION.md)).

## Detection of a Control Center change

Control Center renames/moves/archives ⇒ `config_version` increments ⇒ the ETag changes ⇒ within 60 s (or on Refresh) the desktop fetches it, validates it, replaces the cache, records "Workspace updated: …", reloads the open tree and keeps the selection by id. Verified live against a real API (rename of one section detected as "1 renamed" with the same id).

## Not built (designed in the Phase 0 sync architecture)

Change-feed consumption with cursors and snapshot resync (the backend feed does not exist), upload/download queues, resumable uploads, conflict records, background file sync, local mirror renames-as-moves, `_Inbox` watching, D-7 refile requests, offline approvals policy UI, remote-lock. These attach to the existing tables and events without a rewrite.

## Failure behaviour

| Situation | Result |
|---|---|
| Offline | indicator "Offline", saved copy shown and labelled, retry on next tick/click |
| Server error 5xx | same, with the error code in "Last error" |
| Invalid workspace received | rejected, previous cache kept, notification, `invalid_workspace` |
| Older version received | ignored, `stale_response` |
| 404/403 for the company | **not** answered from the cache (access removed) |
| Session ended | event ⇒ back to sign-in, cache wiped, pending operations kept |
