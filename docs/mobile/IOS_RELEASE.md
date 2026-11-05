# iOS build and release

* Bundle id `com.artify.coaccountant`, deployment target iOS 15, usage strings for camera, photo library and Face ID in `Info.plist`; `ITSAppUsesNonExemptEncryption=false` (standard HTTPS only — confirm with your export-compliance advisor).
* Placeholder app icon (generated) — replace with the official artwork. Launch screen is Flutter's default storyboard.
* **Build (on a Mac with Xcode):** `flutter build ipa --dart-define=ARTIFY_API_URL=https://<server>/api/v1`; open `ios/Runner.xcworkspace` for signing.
* **Signing:** an Apple Developer Program membership, an App ID for the bundle id, a distribution certificate and provisioning profile (kept outside the repository), then upload through Xcode/Transporter to **TestFlight** for internal testing before App Store review. Add the privacy nutrition labels and a privacy policy URL.
* **Status here:** iOS cannot be built or tested on this Linux environment. **No iOS build, simulator or device test was run**; iOS-specific behaviour (Keychain accessibility, camera, Face ID, document picker, HEIC conversion) is unverified. Nothing has been published.
