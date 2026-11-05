# Windows Release Guide

How a Windows build of the desktop app is meant to be produced and checked. **No installer has been built, signed or run in any Phase.** The Tauri shell crate cannot even be compiled in the Linux environment used so far (GTK/WebKit libraries missing); only the pure Rust core (`apps/desktop/core`) and the React UI are tested here. Existing background: [desktop/WINDOWS_BUILD_AND_INSTALLATION](desktop/WINDOWS_BUILD_AND_INSTALLATION.md), [desktop/RELEASE_AND_UPDATE_PROCESS](desktop/RELEASE_AND_UPDATE_PROCESS.md), [desktop/TAURI_CONFIGURATION](desktop/TAURI_CONFIGURATION.md).

## 1. Build machine

Windows 10/11 x64, Rust stable (MSVC toolchain), Visual Studio Build Tools (C++), Node 20+ and pnpm (versions pinned in the repository), WebView2 runtime. Build from a clean checkout of a release tag.

## 2. Steps

1. Set `ARTIFY_API_URL` for the target environment (an HTTPS URL; an unconfigured release build disables sign-in on purpose). 2. `pnpm install --frozen-lockfile`. 3. Run the desktop tests (`make desktop-test`) on Windows. 4. In `apps/desktop`: `pnpm tauri build --bundles nsis` (add `,msi` for an MSI) → installer under `apps\desktop\target\release\bundle\nsis\` (see the build document). 5. Sign the installer and the executable with the company's code-signing certificate (§4). 6. Compute SHA-256 of each artifact; record it in the release notes. 7. Run the clean-machine tests (§3). 8. Publish to the chosen channel.

## 3. Clean-machine test list (to be executed; none has been)

Fresh Windows 10 and 11 VMs, standard (non-admin) user: install; first start; sign in with and without MFA; register device; pick root folder (local, OneDrive, removable); mirror a workspace; import a file via the queue; go offline mid-upload and recover; open each viewer; sign out and confirm the cache is wiped but the queue stays; uninstall (user data left unless chosen); upgrade over a previous version without data loss; second Windows user on the same machine; high-DPI and dark mode; Arabic file names.

## 4. Signing and SmartScreen

Needs an OV or EV code-signing certificate (purchase and private-key custody are owner decisions). An unsigned installer triggers SmartScreen warnings and may be quarantined. **Do not describe a build as signed unless the signature was applied and `signtool verify /pa` passed.** Status: no certificate, no signature.

## 5. Updates

The update channel design (signed update manifests, staged rollout, minimum supported version) is described in the release document; no updater endpoint or signing key exists. Until it does, updates are manual installer downloads and the server's API versioning policy must keep older desktop clients working.

## 6. Release gate

Windows tests of the Rust shell and an end-to-end run against staging pass; clean-machine list passed; installer signed and verified; SBOM and dependency scans attached; release notes list known limitations ([COMMERCIAL_RELEASE_CHECKLIST](COMMERCIAL_RELEASE_CHECKLIST.md)).
