# Notifications

**Implemented (while the app is open):** a notices list built from real records — this phone's upload results (verified, failed, quarantined) and the company's document events (processing failed/completed/needs review, rejected, quarantined, workspace updated) from `GET …/documents/events` — with unread state and two preference switches. Wording is generic; no amounts, parties or content.

**Added in Phase 12:** the person's own server notifications (task assigned, review requested, closing sent back or closed — generic text only) appear in the list and can be marked read; an "E-mail about tasks and closings" switch controls the server's e-mail. See [../NOTIFICATION_DELIVERY.md](../NOTIFICATION_DELIVERY.md).

**Not implemented:** push delivery. No provider (FCM/APNs) is selected or configured, and the backend has no push-token endpoint (the outbox worker now creates notification rows and e-mails but does not push). The app has a `PushRegistrar` interface (`NoPushRegistrar` today) and says plainly in the UI that push is not available.

**To add push later:** choose a provider (decision D-5/ops), add `POST /devices/{id}/push-token` and a delivery handler on the outbox events, implement `PushRegistrar` with `firebase_messaging` (Android) / APNs (iOS), add the platform entitlements, and keep payloads to an id plus a generic title so nothing sensitive reaches the lock screen.
