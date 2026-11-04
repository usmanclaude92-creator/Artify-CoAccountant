# Phase 11 — Mobile Application

**Status: NOT STARTED.** Requires decision D-10 (offline scope).

## Objectives

Deliver the Flutter Android and iOS application for secure document collection and submission driven entirely by each Company's own workspace configuration.

## Functional scope

- Secure login with MFA; device registration; biometric re-unlock (convenience only).
- Company selection limited to Companies where the user has upload permission.
- Dynamic category/folder selection from `upload-config` — **no hard-coded categories**; required custom fields rendered from definitions (all types).
- Camera capture, multi-page scanning into one document, edge detection, perspective correction, enhancement, quality warnings.
- Uploads for any configured category (bank statements, receivables, payables, expenses, or whatever the Company defined), resumable with progress, retry queue, offline capture (per D-10).
- Sync status screen, document history (own submissions), notifications inbox and push.
- Handling of stale configuration: structured `409` → guided re-selection; archived targets never lost.
- Accessibility and localization (English/Arabic RTL if confirmed).

## Technical implementation

Flutter app using a generated Dart API client; state management and local persistence (encrypted SQLite) for queue and cached config (ETag revalidation); scanning plugin chosen by the Phase 5 spike ADR; secure token storage (Keychain/Keystore); push notifications via platform services; on-device image enhancement; `flutter-mobile-development` skill; store distribution setup (privacy declarations, review requirements).

## Database changes

Server: `devices` platform values for mobile, push token storage, notification delivery records; no new workspace/document tables.

## API requirements

Consumes: auth, `GET …/workspace/upload-config`, uploads, `GET …/documents?mine=true`, `GET /me/notifications`, device registration, push token registration endpoints (new).

## Dependencies

Phase 4 (config API), Phase 6 (upload API), Phase 5 spike ADR, D-10.

## Security requirements

Tokens in Keychain/Keystore; certificate validation (pinning decision recorded); no document data persisted outside the app sandbox beyond the queue; screenshots/secure-flag policy decided; jailbreak/root advisory signals; remote revoke honored; logs free of document data.

## Testing requirements

Executable: TS-SYNC-02 (mobile client), TS-SYNC-05, TS-WS-02 (mobile config for 4 vs 10 sections), widget and integration tests on emulators/devices, scanning quality tests with synthetic captures, offline queue tests, accessibility audit.

## Acceptance criteria

1. Two Companies with different sections show different categories in the app with no app update.
2. Multi-page scan becomes one uploaded document; interrupted uploads resume.
3. Archiving a target while a submission is queued does not lose the document.
4. Users see only Companies and categories they are permitted to use.

## Completion checklist

- [ ] Store listing/compliance items prepared
- [ ] Skill `flutter-mobile-development`
- [ ] `PHASE_11_COMPLETION_REPORT.md`

## Known risks

Scanner plugin quality/licensing; iOS/Android platform review requirements; background upload constraints (especially iOS); camera hardware diversity; push delivery reliability.
