# Windows Build and Installation

> **Status: the installer has NOT been built or run in this repository's environment** (a Linux sandbox). What was verified there: the Rust core tests, the Tauri shell **compiles** for Linux (`cargo check`, and a release build), the UI builds. The Windows installer path below is configured but unproven until a Windows machine or the `desktop-windows` CI job succeeds.

## Prerequisites (Windows 10/11 x64)

Rust (stable, MSVC toolchain) with "Visual Studio Build Tools – Desktop development with C++", Node.js 22 + pnpm 10, WebView2 runtime (preinstalled on Windows 11; the installer bootstraps it on Windows 10), and for MSI the WiX tools which Tauri downloads. NSIS is downloaded by Tauri.

## Development build

```powershell
pnpm install --frozen-lockfile
$env:ARTIFY_API_URL = "http://localhost:8000/api/v1"      # or your server
cd apps\desktop
pnpm tauri dev
```

Browser-only UI preview (no Rust): `$env:VITE_BRIDGE = "mock"; pnpm dev` (demo data, clearly bannered).

## Production build

```powershell
$env:ARTIFY_API_URL = "https://api.your-domain.example/api/v1"   # baked in; required for a usable release
$env:ARTIFY_BUILD_ID = (git rev-parse --short HEAD)
cd apps\desktop
pnpm tauri build --bundles nsis        # add ",msi" for an MSI
```

Output: `apps\desktop\target\release\bundle\nsis\Artify Co-Accountant_0.1.0_x64-setup.exe`. Per-user install (no administrator rights), Start-menu entry, uninstaller. Version comes from `tauri.conf.json`/`Cargo.toml` (`0.1.0`); bump both together (and `package.json`).

## Signing (required before real distribution)

Not configured. Obtain a Windows code-signing certificate (OV/EV, or Azure Trusted Signing), then set `bundle.windows.certificateThumbprint` (or a `signCommand`) and `timestampUrl` and sign in CI using secrets stored **outside** the repository. Unsigned builds trigger SmartScreen warnings and must not be distributed as production-ready.

## Install, upgrade, remove

Run the setup `.exe`; the app installs under `%LOCALAPPDATA%\Artify Co-Accountant`. User data lives in `%APPDATA%\com.artify.coaccountant` (database, logs, diagnostics) and the Credential Manager entry `ArtifyCoAccountant`; **uninstalling does not remove user data**, which is deliberate (re-install keeps the cache; a signed-out cache is already wiped).

## CI

`.github/workflows/ci.yml`: job `desktop` (Linux: core tests, live test against a real API, UI lint/types/tests/build, shell compile check) and `desktop-windows` (builds an **unsigned** NSIS installer artifact; `continue-on-error` until first verified).

## Checklist before calling it production-ready

☐ Windows build succeeds on a clean machine ☐ install/launch/sign-in/uninstall tested on Windows 10 and 11 ☐ Credential Manager storage verified ☐ certificate and signing ☐ real icons and installer branding ☐ `ARTIFY_API_URL` production value ☐ update channel (below) ☐ AV/SmartScreen reputation plan.
