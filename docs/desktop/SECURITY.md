# Desktop Security

## Controls and their evidence

| Control | How | Evidence |
|---|---|---|
| No plaintext password | exists only as a function argument; field cleared after submit | live + offline tests scan every file written for the password |
| Tokens not in the webview | HTTP only in Rust; commands return data, never tokens | command surface review; UI types contain no token fields |
| Refresh token in the OS vault | Windows Credential Manager via `keyring` (feature `os-vault`) | in-memory vault in tests proves where it is written; **Credential Manager itself not exercised here (no Windows)** |
| No secrets on disk | database, logs, diagnostics checked for token/password strings | `logout_clears_session_and_cache…`, redaction tests |
| Cache encryption | AES-256-GCM, key in vault, AAD user|table|row | raw-file scan, replay and wrong-key tests |
| Cache is not an authority | server confirms every open; 404 purges; older versions ignored | flows tests |
| Per-user data | cache keyed by user; wiped on logout/expiry/revocation; pending ops kept | db + flows tests |
| Least-privilege webview | CSP with `connect-src ipc:` only; capability `core:event:default` only; no fs/shell/http plugins; frozen prototype | `tauri.conf.json`, `capabilities/default.json` |
| Filesystem safety | the UI cannot send a path to anything except `storage_set_root`; validation: absolute, no `..`, writable probe, space, overlap, symlink-escape, reserved names | `storage_and_logs.rs` (15 tests) |
| Settings closed list | unknown keys and bad values rejected; `storage_root` only via the validated command | `settings.rs` test |
| Revoked device | server revokes bound sessions; client cannot rebind | backend + live tests |
| Logs | JSON lines, credential redaction on write and read, size-capped rotation | redaction tests |
| Diagnostics | version/OS/db schema/counters/redacted log tail; written locally; the user sends it themselves; no tokens, company names, workspace content or documents | `diagnostics_export` code and About text |
| Dependencies | `cargo` crates pinned by `Cargo.lock`; `pnpm audit` clean | CI audit; **`cargo audit` not run** |

## Known weaknesses (honest)

- Revocation takes effect at the next request, not instantly.
- An attacker with the user's Windows session can use the Credential Manager like the app does (same as any desktop app).
- The access token and decrypted cache live in process memory.
- `style-src 'unsafe-inline'` is allowed (React style attributes).
- No code signing yet ⇒ Windows SmartScreen warnings; no tamper protection of the installer. Updates are off until signed ([`RELEASE_AND_UPDATE_PROCESS.md`](RELEASE_AND_UPDATE_PROCESS.md)).
- No penetration test, no fuzzing of the problem+json parser beyond the unit tests, no review of the Credential Manager behaviour on shared machines/roaming profiles.
