---
name: flutter-mobile-development
description: Use when changing the Artify mobile app in apps/mobile (Phase 11: Flutter/Dart, lib/core, lib/features/{auth,orgs,workspace,scanner,upload,documents,notifications,settings}, test/). Covers server-authoritative design, secure storage, dynamic workspace, the scanner pipeline, the upload queue and what is not verified on devices.
---

# Flutter mobile app

Read `docs/mobile/README.md` and ADR-079..081 first. SDK in this repo's sandbox: Flutter 3.47 at `/root/sdk/flutter` (not part of the repo); run `flutter analyze` and `flutter test` from `apps/mobile`.

## Rules that must not break
1. **The server decides.** No folder, category, field, role, plan or limit is named in code. Company/organization ids come from the server's lists or from the queue item; 403/404 mean "no access". Do not cache one company's structure under another (cache key = company id; `WorkspaceRepository` rejects a mismatched company).
2. **No secrets, no tokens outside `SecureStore`.** Only the server address is configuration (`--dart-define`). Never log request bodies, tokens or document content. Notification text stays generic.
3. **Uploaded means confirmed.** Use `UploadService`/`UploadQueue`; never mark `completed` without the server's `complete` answer; keep the idempotency key with the item; never delete the local copy before confirmation.
4. **Dynamic forms:** render fields from `WsField` by type; validate with `MetadataValidator`; unknown types are left to the server.
5. **Scanner:** originals are immutable; crop/rotation/look are settings; image code is pure Dart and tested on synthetic photos; detection failure must fall back to manual corners, never to a guess.
6. **Platform capabilities behind interfaces** (`ConnectivityService`, `BiometricService`, `FileImportService`, `PushRegistrar`). The biometric lock protects an existing session; it never signs anyone in.
7. **Widget tests:** file I/O needs real time (`tester.runAsync` and the `settle` helper in `test/screens_test.dart`); use the fake backend in `test/support/`. Synthetic documents only.

## Not verified (say so)
No Android or iOS binary was built; camera, Keystore/Keychain, Face ID, pickers and background behaviour are untested on devices; push is not implemented.
