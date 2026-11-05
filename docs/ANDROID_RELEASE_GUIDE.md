# Android Release Guide

Preparation for an Android release of `apps/mobile`. **Nothing has been built, run on a device or submitted to Google Play.** The Android SDK cannot be installed in the development environment (its download host is blocked), so not even a debug APK exists. Detailed configuration facts are in [mobile/ANDROID_RELEASE](mobile/ANDROID_RELEASE.md); this guide is the release procedure and gate.

## 1. Prepare

* Replace the generated placeholder icon (`assets/branding/icon.png`) and splash with the official artwork; regenerate launcher icons.
* Version: bump `version:` in `pubspec.yaml` (`name+buildNumber`); the build number must increase for every upload.
* Application id `com.artify.coaccountant` — decide whether this is the final id **before** the first upload; it cannot change afterwards.
* Create the upload keystore on a secure machine; keep it and its passwords outside the repository and in at least two protected backups. Copy `android/key.properties.example` to the untracked `android/key.properties`. Enrol in Play App Signing.

## 2. Build

```
flutter pub get
flutter analyze && flutter test
flutter build appbundle --release --dart-define=ARTIFY_API_URL=https://<api-host>/api/v1
```

Without `key.properties` a release build refuses to sign with the debug key. Record the SHA-256 of the bundle.

## 3. Device test matrix (to be executed)

Android 6 (API 23) minimum and current; low-RAM device; a phone with a poor camera and one with a good one. Sign in (with MFA), switch company, camera permission denied then granted, scan a multi-page document under poor light, import from gallery/files, kill the app during upload and resume, airplane mode, session expiry, biometric lock on and off, rotate screen, dark mode, large font size, TalkBack basics, Arabic text in file names.

## 4. Play Console prerequisites

Developer account (one-time fee, identity verification), privacy-policy URL, data-safety form (account data, user-chosen files and photos, camera, documents, no ads, no data sale), content rating, target API level currently required by Google, internal testing track first, then closed testing. Review times are outside our control.

## 5. Known limitations to state in the listing/notes

No push notifications, no certificate pinning, no root detection, no offline login, scanning quality unproven on real cameras.

## 6. Release gate

Release build installed from the bundle on the device matrix with no blocker; signing verified (`apksigner verify` on the generated APKs from the bundle); privacy and data-safety text approved by the owner/legal (D-11). **Do not say the app is published or in review until the console says so.**
