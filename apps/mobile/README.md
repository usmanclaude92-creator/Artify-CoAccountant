# Artify Co-Accountant — mobile (Flutter, Android and iOS)

The phone companion of Artify Co-Accountant: sign in with the existing account, choose an organization and company, scan or pick documents, fill in the company's own fields, and send them. The accounting workbench stays in the Windows app.

* Docs: [`docs/mobile/`](../../docs/mobile/README.md) · Report: [`docs/development/PHASE_11_COMPLETION_REPORT.md`](../../docs/development/PHASE_11_COMPLETION_REPORT.md)
* No secret is compiled in. The server address is a build-time value:
  `flutter run --dart-define=ARTIFY_API_URL=https://your-server/api/v1`
* Checks: `flutter analyze` and `flutter test` (94 tests; no device needed).
* Builds: see `docs/mobile/ANDROID_RELEASE.md` and `docs/mobile/IOS_RELEASE.md`. Signing keys and profiles are never committed.
