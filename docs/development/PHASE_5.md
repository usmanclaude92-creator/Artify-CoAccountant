# Phase 5 — Windows Desktop Foundation

**Status: NOT STARTED.**

## Objectives

Stand up the Tauri + React + TypeScript Windows application with secure login, device registration, workspace configuration sync and the local mirror skeleton — without document processing yet.

## Functional scope

- Installer and auto-update channel; sign-in (tenant realm), MFA, Organization/Company selection by permission.
- Device registration, `limit.devices` enforcement, device list in Control Center (module 15, Desktop part).
- Pull of workspace configuration (`GET …/workspace`) and change-feed consumption for workspace records.
- Local mirror: root selection per Company, derived folder tree, renames/moves as directory operations, Windows path sanitization and long-path handling, collision suffixes.
- SQLite cache (config versions, queue state), upload/download queue skeleton with persistent state machine.
- `_Inbox` watched folder detection (files queued, upload endpoint wired in Phase 6).
- Role-based shell: accountant vs client-side navigation driven by server capabilities.
- Offline mode shell: cached config, queue persistence, blocked-action messaging.
- Decision D-7 implemented as decided (default: Explorer moves → refile requests).

## Technical implementation

Tauri with a narrow allow-listed command surface; Rust modules for credential vault (Windows Credential Manager/DPAPI), file watching, path sanitizer, queue persistence; React UI using `packages/ui` + generated `api-client`; feed consumer with cursor persistence and snapshot resync; encrypted cache decision (SQLCipher vs OS-level) finalized and recorded as an ADR; `tauri-desktop-development` skill; CI on Windows runners; code signing plan.

## Database changes

Server: `devices` extensions, `sync_devices`, `local_file_mappings`, `sync_conflicts` (structure), `company_change_log` consumer-side fields (no new document tables). Local: SQLite schema + migrations for cache/queue.

## API requirements

`POST /devices` (register), `GET /companies/{id}/changes`, `GET …/workspace`, `POST /devices/{id}/heartbeat`, `DELETE /devices/{id}` (revoke), `GET /me/capabilities`.

## Dependencies

Phase 4 (workspace model and feed), Phase 1 auth (device-bound sessions). Spike: Flutter scanner plugin evaluation runs in parallel (result recorded as ADR).

## Security requirements

Tokens only in the OS vault; no arbitrary filesystem/shell exposure from the webview; signed installers/updates; cache keyed per user; revoke → refresh fails and cache wipe flagged; minimum-version enforcement; per-user separate roots on shared PCs.

## Testing requirements

Executable: TS-SYNC-01, TS-SYNC-03, TS-SYNC-06 (queue behavior), TS-SYNC-07 (revocation), path sanitizer property tests (reserved names, Unicode, 260-char limit), watcher tests on Windows CI (locked files, AV-like locks, OneDrive-redirected folders), Rust unit tests, UI component tests.

## Acceptance criteria

1. Two Companies with different workspaces render different local folder trees on one device, under separate roots.
2. A workspace rename in the Control Center results in a folder rename (move) on the device without re-download.
3. Offline start works with cached configuration; approvals are blocked offline.
4. A revoked device cannot refresh its session.

## Completion checklist

- [ ] Installer + update path verified on a clean Windows machine
- [ ] ADR for cache encryption
- [ ] Skill `tauri-desktop-development`
- [ ] Scanner-plugin spike ADR
- [ ] `PHASE_5_COMPLETION_REPORT.md`

## Known risks

Windows path/AV/OneDrive edge cases; Tauri plugin maturity; code-signing procurement lead time; cache-encryption trade-offs; user expectations of Explorer-as-editor (D-7).
