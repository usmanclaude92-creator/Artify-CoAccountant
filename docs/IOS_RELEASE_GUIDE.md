# iOS Release Guide

Preparation for an iOS release of `apps/mobile`. **Nothing has been built or run**: there is no macOS/Xcode in the development environment, no Apple Developer account is attached, and no device or simulator test has occurred. Configuration facts: [mobile/IOS_RELEASE](mobile/IOS_RELEASE.md).

## 1. Prerequisites

Mac with current Xcode and CocoaPods; Flutter SDK; Apple Developer Program membership (organization account recommended; D-U-N-S verification can take weeks); an App Store Connect app record with the bundle id `com.artify.coaccountant` (decide it is final first); distribution certificate and provisioning profile (automatic signing in Xcode is acceptable for the first builds).

## 2. Prepare

Official icon set and launch screen replacing the placeholder; `Info.plist` usage strings reviewed (camera, photo library, Face ID — wording must say exactly why); version and build number in `pubspec.yaml`; privacy manifest (`PrivacyInfo.xcprivacy`) listing required-reason APIs used by the dependencies; App Privacy answers matching the real behaviour (account data, user content, no tracking).

## 3. Build

```
flutter pub get && flutter analyze && flutter test
flutter build ipa --release --dart-define=ARTIFY_API_URL=https://<api-host>/api/v1
```

Upload with Xcode Organizer or `xcrun altool`/Transporter to TestFlight.

## 4. Device test matrix (to be executed)

Two recent iOS versions and the minimum supported; iPhone with notch and without; Face ID and passcode fallback; camera permission denied/granted; scan in poor light; import from Files/Photos; background and kill during upload; airplane mode; Dynamic Type; VoiceOver basics; Arabic text.

## 5. App Review

Reviewers need a working demo account against a staging server with synthetic data and a note explaining MFA; the app must not require a paid subscription outside Apple's rules — **how a firm's subscription is sold (in-app purchase vs. external billing for business services) is an open commercial/legal question (D-2, D-11) that must be answered before submission.**

## 6. Known limitations

No push notifications, no certificate pinning, no jailbreak detection, scanner quality unproven on real cameras.

## 7. Release gate

TestFlight build run through the matrix with no blocker; privacy answers approved; review notes and demo account ready. **Do not claim the app is on the App Store until it is.**
