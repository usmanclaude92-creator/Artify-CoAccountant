# Release and Update Process

## State today

The update check is wired but **disabled**: with no compiled-in public key and endpoint, Settings → About → "Check for updates" truthfully reports "Automatic updates are not configured in this build". Automatic updates are **not claimed to work**; the signing keys, update server and release pipeline do not exist yet and the updater has never run.

## What exists

- Version management: `tauri.conf.json`, `src-tauri/Cargo.toml` (workspace version) and `package.json` carry the app version shown in About and sent as `app_version` at device registration.
- Update check command `update_check` using the Tauri updater (`UpdaterExt`) once `ARTIFY_UPDATER_PUBKEY` and `ARTIFY_UPDATER_ENDPOINT` are compiled in; reports available version or "latest". The download/install step is intentionally **not** implemented until it can be tested end-to-end.
- Release channels: setting `update_channel` (`stable`/`beta`) is stored; it will select the endpoint path.

## Manual configuration still required (nothing here is in the repository)

1. Generate an updater key pair with `pnpm tauri signer generate`; keep the **private key and password in a secret store / CI secrets only**; compile the **public** key in via `ARTIFY_UPDATER_PUBKEY`.
2. Host a static update manifest (`latest.json` per channel) over HTTPS; set `ARTIFY_UPDATER_ENDPOINT`.
3. Set `bundle.createUpdaterArtifacts` to `true` and sign the installer (Windows code signing, separate from the updater signature).
4. Add a release workflow that builds on Windows, signs both, uploads artifacts and the manifest.
5. Implement and test download → verify → install → restart, a failed-update rollback message, and a minimum-supported-version policy (needs a backend field).
6. Decide rollout policy (staged channels, kill switch).

## Release checklist (when infrastructure exists)

Bump versions → changelog → run `make desktop-test desktop-live` → Windows CI build → sign → test install and upgrade-over-previous on clean Windows 10/11 → publish to beta → promote to stable.
