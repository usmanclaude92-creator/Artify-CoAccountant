# Architecture

Flutter 3 / Dart 3, feature-based. `provider`-free: small `ChangeNotifier` controllers owned by one `AppServices` object, created in `main.dart` (and by a test harness with fakes).

```
apps/mobile/lib
  core/        config (build-time server address), network (ApiClient with single-flight refresh, ApiError), storage
               (SecureStore → Keystore/Keychain; TokenStore), platform (connectivity, biometrics, file import, push interface),
               theme (Artify tokens, light/dark), widgets
  features/
    auth/        repository, controller (state machine), screens (welcome, login, MFA, forgot/reset, expired, lock)
    orgs/        organization/company selection and switching
    workspace/   upload-config models, ETag cache, tree, metadata validation, browse screens
    scanner/     detection, perspective, clean-up, session, PDF, camera/crop/review/preview screens
    upload/      queue (on disk), service (three-step protocol), runner (connectivity/back-off), form, queue screen
    documents/   history, detail, rename, preview
    notifications/  notices from the queue and document events; preferences
    dashboard/   home    settings/ settings, privacy, biometric, storage
```

* **The server decides.** The app sends the active organization in `X-Organization-Id` and company ids in paths; it never trusts its own selection, and 403/404 are shown as "no access". There is no company or folder name anywhere in the code.
* **Screens implemented:** welcome, sign in (password toggle), second factor, forgot/reset password, session expired, lock, organization list, company list (search, switch), home (quick actions, queue counts, drafts, recent), folder browser (nested), scanner (live camera, flash, camera switch, gallery), crop/look editor, scan review (reorder/rotate/retake/delete/add/quality/mode), PDF preview, send form (category and dynamic fields of all ten types), upload queue, documents (paged, search, mine/all), document detail (status, versions count, preview of pictures, rename), notices and preferences, settings (profile, switch, biometric, storage, privacy, help, about, sign out).
* **Responsive:** single-column phone layout; tested at 320×568, 412×915 and 800×1280 with no overflow. Portrait is the primary orientation; there is no tablet-specific two-pane layout.
