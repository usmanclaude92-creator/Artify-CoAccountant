---
name: tauri-desktop-development
description: Use when changing the Windows desktop app in apps/desktop (Rust core in core/, Tauri shell in src-tauri/, React UI in src/). Covers the narrow command surface, where tokens and secrets may live, the sealed SQLite cache, Windows path rules, workspace sync by stable id, device-bound sessions, and how to test each layer.
---

# Tauri desktop development

Read first: `docs/desktop/DESKTOP_ARCHITECTURE.md`, `SECURITY.md`.

## Rules that must not break
1. **Tokens never reach the webview.** HTTP happens only in `core/src/api.rs`. Refresh token → OS vault only; access token → memory only; password → never stored or logged. Nothing credential-like in SQLite, settings, logs, diagnostics or UI types.
2. **Narrow command surface.** A new capability = a new named command in `src-tauri/src/commands.rs`, registered in `lib.rs`, mirrored in `src/bridge/tauri.ts` and `types.ts`. Do not add fs/shell/http/opener plugins or permissions. Only storage commands take paths, and they validate in `core/src/storage.rs`.
3. **Server is authoritative.** Open-company, workspace, device actions are confirmed by the backend; the cache never authorizes. A 404/403 is not answered from the cache. Older versions never overwrite newer cache; invalid/other-company data is never cached.
4. **Sealed cache.** New cached payloads go through `Db` with `Sealer` (AAD = user|table|row). Cache is per user and is wiped on logout/expiry/revocation; `pending_ops` survive a wipe.
5. **Stable ids.** Diff and select by node id, never by name. No hard-coded categories/sections/folders anywhere (UI or Rust).
6. **Windows paths.** Any name that becomes a path segment goes through `sanitize_segment`; reserved device names apply with any extension; never overwrite; stay inside the root; keep paths short.
7. **Honest UI.** Unbuilt areas say "Not available yet" and show no data. Mock data exists only behind `VITE_BRIDGE=mock` in dev and in tests.
8. **No secrets in the repo.** No updater private key, certificate or password; the updater stays "not configured" until the public key + endpoint are compiled in.

## Testing layers
- `cargo test -p artify-core` — offline (wiremock for the API; tempdirs for storage/db). Add a test for every new failure path.
- `make desktop-live` — the core against a REAL API + PostgreSQL (seeded by `tests/live/seed_live.py`).
- `pnpm --filter @artify/desktop test` — UI with the mock bridge (`createMockBridge` options for scenarios).
- `cargo clippy -p artify-core --all-targets -- -D warnings` and `cargo fmt --all --check` (CI).
- Windows installer/Credential Manager behaviour can only be verified on Windows (`desktop-windows` job, `docs/desktop/WINDOWS_BUILD_AND_INSTALLATION.md`).

## Backend contract
Device registration/binding endpoints (`POST /devices`, `POST /devices/{id}/session`), workspace `GET …/workspace` with ETag, `upload-config` for later phases. Regenerate the TS client (`make client`) after API changes.
