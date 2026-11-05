# Security and privacy

* **Credentials:** access and refresh tokens and the device id live only in `flutter_secure_storage` (Android Keystore-backed encryption, iOS Keychain `first_unlock_this_device`). Passwords are never stored. Test-enforced: after sign-in nothing resembling a token is in shared preferences.
* **No secrets in the app:** the only configuration is the server address (a build-time `--dart-define`). No database, storage, AI-provider or administrator credentials exist in the code or in the build.
* **Authorization:** every company call is authorised by the server; 403/404 answer "no access" without revealing existence; the app shows only organizations/companies the server returns for the signed-in user; the company in the URL is the one the item was created for.
* **Transport:** HTTPS is required in release (Android `usesCleartextTraffic=false`, iOS App Transport Security default). **Certificate pinning is not implemented** (decision recorded: operational risk of pin rotation outweighs the benefit before the production domain exists).
* **Session:** refresh once on 401 (single-flight); failure ends the session, clears tokens and shows the "session ended" page; queued documents are kept and continue after sign-in. Sign-out calls the server and wipes tokens, the queue, cached workspace and scans from the phone.
* **Biometric lock:** protects the *existing* session after the app has been in the background (30 s); it cannot sign anyone in and does not replace server authentication.
* **Local documents:** in the app's private directory until verified, then removed; previews are cached in a cache directory that "Clear cache" and sign-out remove.
* **Logging:** the app writes no logs of requests, bodies or document content; `ApiError` carries only status, a code and the server's message.
* **Notification text:** generic only; no amounts, names or content.
* **What this does not guarantee:** protection against a rooted/jailbroken device, malware with root, screen capture or screenshots (no `FLAG_SECURE` policy is set), forensic recovery from a lost unlocked phone, or compromise of the OS keystore. Jailbreak/root detection is not implemented. A lost phone is handled by revoking the device (ends its session) and by the fact that tokens are short-lived.
