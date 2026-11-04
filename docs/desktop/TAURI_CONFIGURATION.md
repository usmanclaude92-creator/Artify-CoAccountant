# Tauri Configuration

File: `apps/desktop/src-tauri/tauri.conf.json`. Tauri 2.x, identifier `com.artify.coaccountant`, product name "Artify Co-Accountant".

## Window

One window, 1360×860, minimum 980×640, resizable, centered. `withGlobalTauri` is **false**: the UI imports `@tauri-apps/api` as a module; there is no global `window.__TAURI__` object.

## Content Security Policy

`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src blob:; font-src 'self'; connect-src ipc: http://ipc.localhost; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. Phase 6 added `blob:` for images and frames only, so the in-app viewer can show a verified preview that the Rust side handed over as bytes (a `blob:` URL created in the page; no remote content is allowed). The webview can only talk to the Rust side; it has no network permission toward the API or anywhere else. `'unsafe-inline'` for styles is needed for React `style` attributes; scripts are never inline. `freezePrototype` is on.

## Capabilities (`capabilities/default.json`)

Only `core:event:default` (receive events). There is **no** file-system, shell, HTTP, opener or dialog permission for the webview. The folder dialog is opened by Rust inside the `storage_pick` command; the chosen path is validated in Rust before use.

## Commands (the complete allow-list)

`app_info`, `auth_login`, `auth_mfa`, `auth_restore`, `auth_logout`, `organizations`, `companies`, `company_select`, `company_favorite`, `company_assignments`, `workspace_get`, `workspace_refresh`, `settings_get`, `settings_set`, `storage_pick`, `storage_set_root`, `storage_status`, `company_dir`, `device_ensure`, `devices_list`, `device_revoke`, `sync_status`, `notifications_list`, `notifications_read`, `diagnostics_export`, `update_check`. Only the three storage commands and `company_dir` involve paths; `settings_set` refuses the `storage_root` key and every key outside a closed list.

## Bundle

Targets `nsis` and `msi`; NSIS `installMode: currentUser` (no administrator rights needed). Publisher/copyright metadata set; icon set generated from a **placeholder** teal "A" (real brand artwork to be supplied). `certificateThumbprint` is `null`: **installers are unsigned** until a certificate is configured (see [`WINDOWS_BUILD_AND_INSTALLATION.md`](WINDOWS_BUILD_AND_INSTALLATION.md)). `createUpdaterArtifacts` is `false` for the same reason.

## Build-time configuration (environment, nothing secret)

| Variable | Purpose |
|---|---|
| `ARTIFY_API_URL` | API base, e.g. `https://api.example.com/api/v1`. Read at run time first, then the value baked at build time. Release builds without it refuse to sign in (no guessing); debug builds fall back to `http://localhost:8000/api/v1`. Non-HTTPS is accepted only for localhost/127.0.0.1. |
| `ARTIFY_BUILD_ID` | build identifier shown in About |
| `ARTIFY_UPDATER_PUBKEY`, `ARTIFY_UPDATER_ENDPOINT` | enable the update check (see [`RELEASE_AND_UPDATE_PROCESS.md`](RELEASE_AND_UPDATE_PROCESS.md)); empty ⇒ "not configured" |

## Plugins

`tauri-plugin-dialog` (native folder picker, used only from Rust) and the `tauri-plugin-updater` crate (used through `UpdaterExt` only when configured). No fs, shell, http, process or opener plugin.
