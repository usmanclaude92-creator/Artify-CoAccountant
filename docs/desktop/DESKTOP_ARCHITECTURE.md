# Desktop Architecture (Phase 5)

Status: foundation implemented. Read first: [`../architecture/APPLICATION_ARCHITECTURE.md`](../architecture/APPLICATION_ARCHITECTURE.md) §3 and §5, [`../architecture/STORAGE_AND_SYNC_ARCHITECTURE.md`](../architecture/STORAGE_AND_SYNC_ARCHITECTURE.md). What the app does today is listed in [`../development/PHASE_5_COMPLETION_REPORT.md`](../development/PHASE_5_COMPLETION_REPORT.md).

## Principles

1. **The cloud is the source of truth.** The app holds a cache and local metadata. The cache never decides authorization; every company open, workspace read and device action is confirmed by the backend.
2. **Tokens never reach the webview.** The Rust side performs all HTTP, keeps the access token in memory and the rotating refresh token in the OS credential vault.
3. **A narrow command surface.** The UI can call 26 named commands and nothing else (no file-system, shell, HTTP or opener permission is granted to the webview).
4. **Workspace is data.** Nothing in the app names a section, folder or category; the tree is whatever the server delivers for that user and company.
5. **Honest scope.** Areas that are not built (Documents, Processing Queue, Accounting, Reports) say so and show no sample data.

## Layout

```
apps/desktop/
  Cargo.toml                workspace: core, src-tauri
  core/                     artify-core (Rust, platform-neutral, fully testable without a window)
    src/api.rs              the only HTTP gateway: timeouts, retries, token refresh, problem+json mapping
    src/desktop.rs          application service: sign-in, org/company, workspace sync, storage, device
    src/db.rs  seal.rs      SQLite + migrations + AES-256-GCM sealed cache
    src/vault.rs            SecretStore trait: Windows Credential Manager (feature os-vault) / in-memory (tests)
    src/storage.rs          Windows-safe names, root validation, per-company folders
    src/workspace.rs        parse/validate the Phase 4 workspace, diff by stable id
    src/settings.rs logs.rs error.rs
    tests/                  78 offline tests + 5 live tests (real API)
  src-tauri/                Tauri 2 shell: 26 commands, background sync loop, updater hook, bundle config
  src/                      React + TypeScript UI (Vite); bridge/ isolates Tauri; screens/; state/
  tests/live/seed_live.py   seeds a real API for the live test
```

`artify-core` has no Tauri dependency, so `cargo test -p artify-core` runs on any CI image. The shell (`src-tauri`) is thin: it builds the state, registers commands and starts the sync loop.

## Data flow

```
UI (React) ──invoke(command, ids)──▶ Tauri command ──▶ Desktop service ──▶ ApiClient ──HTTPS──▶ FastAPI (source of truth)
                                           │                 │
                                           │                 └──▶ SQLite (sealed cache, settings, sync/device/queue metadata)
                                           └──▶ storage (validated paths) / vault (OS credential store)
UI ◀── events: workspace-updated · session-expired · sync-error
```

## UI

Shared design tokens and components from `packages/ui` (`@artify/ui`), light/dark following the system or the user's choice, a persistent sidebar with the ten areas, a top bar with organization → company context and a connection indicator. The UI is English only for now (ADR-040 applies; the `language` setting exists but only English is offered).

## Navigation areas

| Area | State |
|---|---|
| Dashboard | real: identity, organization, number of authorized companies, open company |
| My Companies | real: list, search, filters, favorites, recents, open (server-verified) |
| Workspace | real: the company's own tree, search, details, refresh, version tracking |
| Documents · Processing Queue · Accounting · Reports | placeholders labelled "Not available yet"; no data |
| Synchronization | real: connection, last sync, saved version, last error, queue counters, manual sync |
| Notifications | real: local event log (workspace changes, sync problems, offline, revoked device) |
| Settings | real: General, Storage, Synchronization, Account, Devices, About |

## Browser preview

`VITE_BRIDGE=mock pnpm --filter @artify/desktop dev` serves the UI in a normal browser with an in-memory mock bridge and a permanent "DEMO DATA" banner. It is selected only when `import.meta.env.DEV`; the production bundle contains neither the mock nor its data (checked).
