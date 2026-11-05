# Android build and release

* Application id `com.artify.coaccountant`, minSdk 23 (secure storage needs API 23), permissions: internet, network state, camera (optional feature), biometric. No storage permission: files and photos come from the system pickers. Backups and cleartext traffic are off.
* Placeholder launcher icon (`assets/branding/icon.png`, generated) — replace with the official artwork before any store submission. Launch screen is Flutter's default.
* **Build:** install the Flutter SDK and Android SDK, then `flutter build apk --dart-define=ARTIFY_API_URL=https://<server>/api/v1` (APK) or `flutter build appbundle …` (AAB for Google Play).
* **Signing (never committed):** create an upload keystore, copy `android/key.properties.example` to `android/key.properties` and fill it in. `android/app/build.gradle.kts` uses it when present; **without it a release build is signed with the debug key and must not be distributed.** Google Play App Signing is recommended.
* **Store prerequisites:** a Google Play developer account, a privacy policy URL, the data-safety form (camera, files/photos chosen by the user, account data, documents), content rating, and a test track.
* **Status here:** the Android SDK is not installed in the build environment and cannot be downloaded through its network policy, so **no APK/AAB was built**. Nothing has been published.
