# Phase 5 Completion Report — Windows Desktop Foundation

**Status: delivered for review (2026-11-04). Phase 6 has not been started and will not be until the owner approves this phase.**

Evidence conventions: *Executed* means the command was run in this repository's sandbox (Linux, PostgreSQL 16, Redis 7, Chromium, Xvfb) in this session. **The sandbox is Linux: no Windows build, installer, Credential Manager or SmartScreen behaviour was executed.** All data is synthetic. Feature docs: [`../desktop/`](../desktop/DESKTOP_ARCHITECTURE.md).

Phase 5 started on the owner's Phase 5 brief; a separate recorded approval of Phase 4 is not in the repository (PR #6 containing Phase 4 was still open and CI was running when this phase started). **This work is committed on the same branch as Phase 4, so PR #6 now contains both phases.**

## 1. Summary of implementation

A real Tauri 2 desktop application (not a mock-up): Rust core + Tauri shell + React UI, connected to the existing FastAPI backend.

- **Sign-in** with the existing tenant authentication (password, MFA challenge, session restore from the OS credential vault, single-flight refresh, session-expiry handling, sign-out).
- **Organization and company selection**, with search, filters, favorites and recents; every company open is verified by the backend.
- **Dynamic workspace**: the company's real Phase 4 workspace as a hierarchical tree (expand/collapse, search, archived toggle, details, permissions shown, refresh), validated before caching, synchronized by ETag with changes described by stable id.
- **Local storage foundation**: user-chosen root (native folder dialog), validation (writable, free space, overlap, OneDrive warning), Windows-safe stable company folders; nothing is written beyond empty company folders.
- **Sealed SQLite cache** with migrations, corruption recovery, per-user wipe.
- **Device registration** with server-issued ids; **device-bound sessions** (small additive backend change) so revocation ends a session.
- **Settings** (General, Storage, Synchronization, Account, Devices, About), notifications log, diagnostics export, update check that truthfully reports "not configured".
- **Honest scope**: Documents, Processing Queue, Accounting, Reports are labelled "Not available yet" and show no data.

## 2. Desktop architecture

Three layers (details: `DESKTOP_ARCHITECTURE.md`): `artify-core` (platform-neutral Rust: API client, DB, sealing, storage, workspace logic, orchestration) → `src-tauri` (26 allow-listed commands, background sync loop, updater hook) → React UI behind a `Bridge` interface. Tokens never leave Rust; the webview has no network, file-system or shell capability (CSP `connect-src ipc:`; capability `core:event:default` only). ADR-047…051.

## 3. Technology and dependencies

Tauri 2.x, Rust (stable; `reqwest` rustls, `rusqlite` bundled SQLite, `aes-gcm`, `keyring` with `windows-native`, `fs2`, `tokio`), React 19 + TypeScript 5.9 + Vite 7, the shared `@artify/ui` tokens/components and the generated `@artify/api-client` package. Dev/test: `wiremock`, `tempfile`, vitest 4, Testing Library, jsdom, `@tauri-apps/cli`. No Electron; no alternative stack.

## 4. New repository structure

`apps/desktop/{core,src-tauri,src,tests/live}`, `docs/desktop/` (13 documents), `infrastructure/dev/desktop_live.sh`, `.claude/skills/tauri-desktop-development`, CI jobs `desktop` and `desktop-windows`, Make targets `desktop-test`, `desktop-live`, `desktop-build`. Backend touched only in `routers/me.py` (+ tests, OpenAPI/TS client regenerated).

## 5. Authentication implementation

Password → `/auth/login` → (MFA `/auth/mfa/verify`) → access token in memory, refresh token in Windows Credential Manager (written before use). Restore at startup (offline ⇒ stored session kept, retry offered). 401 ⇒ one single-flight refresh ⇒ repeat; refusal ⇒ session ended, user cache wiped, pending operations kept. 22 API-client tests + 5 live tests + UI tests. Enrolment of MFA, password reset and Windows Hello are not in the app.

## 6. Organization and company selection

Single organization goes straight in, several show a picker (also switchable), none shows an explanation. The company list shows name, code, country, currency, status, last opened; search, filters (all/active/favorites/recent), refresh; an offline list is marked as saved. Opening calls the backend; a refused id (also typed or guessed) is purged and changes nothing. Assigned accountants appear in the workspace header where the backend allows. **Company logos are not available (no backend field); favorites are local only.**

## 7. Dynamic workspace integration

Verified with 4-section, 10-section/nested and a third company (offline tests), live against a real API (4 vs 10 sections, nested folder, archived node delivered as archived, an unconfigured company shows nothing), permission-restricted users (only delivered nodes appear), version change (rename detected by id), stale/invalid/other-company answers rejected, offline fallback labelled, revoked access (404 not papered over by the cache). No category, section or folder name exists in the app's code.

## 8. Local storage implementation

`storage.rs`: sanitizer (illegal/control characters, trailing dots/spaces, reserved device names with any extension, length), unique stable company folder names, root validation (absolute, no `..`, not a drive root, writable probe, space, overlap, OneDrive warning), never-overwrite, symlink-escape refusal, path-length guard, "missing drive is not silently recreated". 15 tests. (The tests found a genuine reserved-name bug — `LPT9.log` stayed reserved — which was fixed.) The per-workspace folder tree and file mirror are **not** built.

## 9. SQLite schema and migrations

Migration 1: settings, accounts, organizations, companies, workspace_cache, sync_state, device, local_dirs, pending_ops, events (+4 indexes). Forward-only migrations in transactions, newer-database refusal, corruption quarantine + rebuild, AES-256-GCM sealed payloads bound to user+table+row. 13 tests incl. raw-file scan, replay and wrong-key. See `SQLITE_DATABASE.md`.

## 10. API integration

Single gateway with timeouts, GET-only retries with backoff, single-flight refresh, ETag, problem+json mapping, malformed-response rejection, redacted structured logs. Backend contract additions: `POST /devices/{id}/session`, `DeviceOut.registered_at/last_seen_at`, binding in `POST /devices` (0 breaking OpenAPI changes). `API_INTEGRATION.md`.

## 11. Synchronization foundation

Connectivity indicator, 60-second background ETag revalidation of the open company, per-company sync state, manual refresh, safe cache replacement, change descriptions by id, notifications, pending-operation skeleton (survives a wipe). **Not built:** change-feed consumption (no backend feed), upload/download queues, conflict records, file sync, `_Inbox` watcher, D-7 handling.

## 12. Device registration foundation

Server-issued id; heartbeat/bind on every start; revoked ⇒ session ended and cache wiped; list and revoke in Settings; `limit.devices` surfaced as a plain message with no local record. Effective at the next request (no push); the all-users device list in the Control Center is not built. `DEVICE_REGISTRATION.md`.

## 13. Update architecture

Versioning, a Tauri-updater-based check command that reports "not configured" until `ARTIFY_UPDATER_PUBKEY` + `ARTIFY_UPDATER_ENDPOINT` are compiled in, channel setting, documented manual steps (key generation, manifest host, signing, workflow). **Automatic updates are not claimed to work; download/install is not implemented; no key or certificate is in the repository.** `RELEASE_AND_UPDATE_PROCESS.md`.

## 14. Windows build and installation results

| Item | Result |
|---|---|
| Linux release build of the Tauri app (`pnpm tauri build --no-bundle`) | **executed, succeeded** (11 MB binary, 4m30s) |
| The real app started under Xvfb with a Secret Service keyring and driven by synthetic input against a real seeded API: sign-in (accountant and owner with MFA), organization/company lists, workspace trees (4 vs 10 sections, nested), device registration, Settings, native folder dialog opened, error paths | **executed (manual, with screenshots)** — a smoke run, not an automated test; the folder-dialog selection was not completed |
| Post-run scan of the app's data folder: log is JSON lines; the password appears in no file; company names/emails not readable in `artify.db` or its WAL | executed |
| `pnpm tauri build --bundles nsis` (Windows installer), MSI, install/upgrade/uninstall, Windows Credential Manager, WebView2, SmartScreen | **NOT EXECUTED — no Windows machine here.** Configured in `tauri.conf.json`; CI job `desktop-windows` is added as `continue-on-error` and has not run |
| Code signing, installer branding, real app icon | **not done** — placeholder teal "A" icon; unsigned |

## 15. Automated test results (all executed in this session)

| Suite | Command | Result |
|---|---|---|
| Rust core, offline (wiremock + tempdirs) | `cargo test -p artify-core` | **78 passed** (api_client 22, flows 21, storage_and_logs 15, db_and_seal 13, workspace_model 6, settings 1) |
| Rust core against a real API + PostgreSQL | `infrastructure/dev/desktop_live.sh` (`make desktop-live`) | **5 passed** (owner+MFA workspaces and remote change; accountant confined to one company; wrong credentials; restore/logout; device revocation) |
| Desktop UI (vitest, mock bridge) | `pnpm --filter @artify/desktop test` | **50 passed** |
| Desktop lint, types, build | eslint, `tsc --noEmit`, `vite build`; `cargo clippy -D warnings`, `cargo fmt --check`, `cargo check -p artify-desktop` | clean |
| Backend (real PostgreSQL) | `uv run pytest services/api/tests` | **306 passed** (303 + 3 new device tests); one Phase 1 test updated because revoking the device a session is bound to now ends that session |
| Backend static | ruff check/format, mypy `--strict`, import-linter, OpenAPI check, breaking-change check | clean; **0 breaking** |
| Web regression | web vitest, `pnpm -r typecheck`, eslint; browser e2e (`infrastructure/dev/e2e.sh`) | 6 passed; clean; **32 passed** (Phase 1–4 flows incl. Control Center and Workspace Builder) |
| Dependency audit | `make audit` | no known vulnerabilities (Python, JavaScript). **`cargo audit` not run.** |
| Docs | `python3 tools/check_docs.py` | passed |

Not executed: Docker Compose smoke, pre-commit hooks, GitHub CI for this change, any test on Windows, any test of the Credential Manager implementation (`OsVault` was exercised only through the Linux Secret Service during the manual smoke run).

## 16. Security test results

Per `docs/desktop/SECURITY.md`: no plaintext password or token on disk (offline test scans every file; manual run scan); tokens not exposed to the UI; cache sealed and not replayable across users/companies; no cross-company data (other-company answers rejected, forbidden ids refused live, nothing of an unassigned company reached the cache); no unauthorized filesystem access (only validated storage commands take paths; traversal, symlink escape, overlap, reserved names tested); revoked device cannot refresh or rebind (backend + live tests); wrong credentials produce no session; unknown settings keys rejected. Not done: penetration test, fuzzing, Credential Manager review, `cargo audit`.

## 17. Known issues

1. **No Windows verification** (see §14). The installer path, Credential Manager storage and WebView2 behaviour are unproven; `desktop-windows` CI may fail on first run.
2. **Installer unsigned, placeholder icon, updates disabled** (ADR-051).
3. **PR #6 now carries Phase 4 + Phase 5** (same branch).
4. The app registers devices with platform `windows` regardless of host OS (it is a Windows app; the Linux smoke build did too).
5. **Not built from `PHASE_5.md`:** local mirror and renames-as-moves, `_Inbox` watcher, change-feed with cursors/resync, Control Center device list, minimum-version enforcement, offline-mode approvals UI, role-based shell driven by `/me/capabilities` (the endpoint does not exist), `Scanner-plugin spike ADR`, moderated usability sessions, installer verification on a clean machine.
6. Revocation takes effect at the next request; the access token and decrypted cache live in process memory; pending operations in a destroyed (corrupt) database are lost.
7. English only; Arabic/RTL and "start with Windows" are not offered (the settings are prepared/validated only where noted); company logos unavailable; favorites are not shared across devices.
8. A database written by a newer app version is refused (no downgrade) — users must update first.
9. The release-build smoke run showed a startup that depends on a working OS credential store; without one the app refuses to start rather than store secrets elsewhere (by design; message is currently only in the log/console).
10. Test hygiene: the live test relies on the API accepting the TOTP of the next 30-second step; the UI tests use a mock bridge (they prove the UI, not the Rust/UI wiring — the manual smoke run and the typed bridge cover that).

## 18. Missing external configuration

Windows build machine/CI runner with MSVC toolchain; code-signing certificate and signing in CI; real brand icon and installer artwork; `ARTIFY_API_URL` production value; updater key pair (`pnpm tauri signer generate`, private key outside the repo), HTTPS update host and manifest, release workflow; SMTP for invitations (unchanged from Phase 3); Windows 10/11 test devices; policy decisions D-7 (Explorer edits) and the offline scope.

## 19. Exact instructions to run the application

```bash
make install                                   # lockfile-exact dependencies
make db-reset                                  # backend database (migrations 0001->0006 + seed)
uv run --project services/api uvicorn artify_api.main:app --port 8000
# browser preview with DEMO data (no Rust, no server):
VITE_BRIDGE=mock pnpm --filter @artify/desktop dev        # http://localhost:1420
# the real desktop app (needs a credential store: Windows Credential Manager, or a Secret Service on Linux):
ARTIFY_API_URL=http://localhost:8000/api/v1 pnpm --filter @artify/desktop tauri dev
# tests
make desktop-test            # Rust core + UI, no server
make desktop-live            # Rust core against a real API
```

Sign up at the web Control Center (`/signup`), verify, enrol MFA (owners/administrators/managers), create a company and a workspace, invite an accountant — then sign in to the desktop app with those credentials.

## 20. Exact instructions to build the Windows installer

On Windows 10/11 x64 with Rust (MSVC), Node 22 + pnpm 10:

```powershell
pnpm install --frozen-lockfile
$env:ARTIFY_API_URL = "https://<your-api>/api/v1"; $env:ARTIFY_BUILD_ID = (git rev-parse --short HEAD)
cd apps\desktop
pnpm tauri build --bundles nsis
# -> target\release\bundle\nsis\Artify Co-Accountant_0.1.0_x64-setup.exe   (UNSIGNED)
```

Signing and updates: `docs/desktop/WINDOWS_BUILD_AND_INSTALLATION.md` and `RELEASE_AND_UPDATE_PROCESS.md`. **This procedure has not been run.**

## 21. Readiness for Phase 6

**Ready for the document-management work that sits on this foundation:** per-user sealed cache and pending-operation table, device-bound sessions, the storage root and stable company folders, workspace delivery by stable id with version/ETag (the `upload-config` contract is already served by the backend), an API client with safe retry rules (writes never retried) and idempotency-friendly structure, redacted diagnostics.

**Before or during Phase 6:** verify the installer on Windows; obtain a certificate; build the change-feed on the backend or accept polling; implement the folder mirror (derive the tree from node ids; renames as moves; `_Inbox` watcher; Windows long paths); define the upload queue state machine on `pending_ops` with `Idempotency-Key`; decide D-7 and the offline scope; add Windows-runner tests for locked files and OneDrive folders.

**Owner decisions that would help:** whether to merge Phase 4 before Phase 5 builds on it (single PR), whether the desktop must offer MFA enrolment and Arabic/RTL before the first pilot, the code-signing route, and whether a backend change-feed should precede document sync.
