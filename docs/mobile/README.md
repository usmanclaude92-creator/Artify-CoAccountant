# Mobile application (Phase 11)

Status: **delivered for review.** Phase 12 has not been started. Read [the completion report](../development/PHASE_11_COMPLETION_REPORT.md) for what is and is not verified (no Android or iOS binary was built in this environment).

| Document | Content |
| --- | --- |
| [MOBILE_BACKEND_AUDIT.md](MOBILE_BACKEND_AUDIT.md) | what the backend already provides, what was verified, the one extension made |
| [ARCHITECTURE.md](ARCHITECTURE.md) | project structure, layers, state, screens |
| [SCANNER_AND_PDF.md](SCANNER_AND_PDF.md) | edge detection, perspective, clean-up, multi-page PDF, limits |
| [UPLOAD_QUEUE.md](UPLOAD_QUEUE.md) | the offline queue, states, retries, idempotency |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | storage, tokens, biometrics, what the app does not guarantee |
| [NOTIFICATIONS.md](NOTIFICATIONS.md) | what exists, what push needs |
| [ANDROID_RELEASE.md](ANDROID_RELEASE.md), [IOS_RELEASE.md](IOS_RELEASE.md) | build, signing, store preparation |

Decisions: ADR-079 (architecture), ADR-080 (scanner), ADR-081 (queue and honesty about upload state).
