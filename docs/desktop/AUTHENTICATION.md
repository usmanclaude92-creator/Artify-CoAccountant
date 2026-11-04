# Authentication

The desktop app uses the **existing Phase 1 tenant authentication**; it adds no authentication of its own. Endpoints: `POST /auth/login`, `POST /auth/mfa/verify`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me`.

## Flow

1. **Sign in** — email + password go to `auth_login`; Rust calls `/auth/login`. The password exists only as a function argument and is never stored or logged; the UI clears the field after submit.
2. **Two-factor** — owners, administrators and practice managers must use MFA. `mfa_required` returns a short-lived signed challenge; the UI asks for the authenticator code and calls `auth_mfa`. An account that has not enrolled yet gets a clear instruction to enrol in the Control Center first (enrolment is not built into the desktop app).
3. **Session** — the access token lives in memory in Rust. The rotating refresh token is written to the **Windows Credential Manager** (`keyring`, service `ArtifyCoAccountant`) *before* the new access token is used, so a crash cannot lose the session.
4. **Restore** — at startup `auth_restore` presents the stored refresh token. Ended/expired/revoked ⇒ the stored token is deleted and the sign-in screen appears. Offline/server down ⇒ the token is **kept** and the app offers "Try again" (it never signs the user out because of a network problem).
5. **Refresh** — any request answered 401 triggers one single-flight refresh (concurrent requests share one rotation) and a repeat of the request. A refresh refused with 401/403 ends the session: tokens are cleared, the user's cache is wiped (pending operations are kept), the UI returns to sign-in with an explanation.
6. **Sign out** — best-effort `/auth/logout`, then tokens removed from memory and the vault and the user's cache wiped.

## What is stored where

| Item | Location |
|---|---|
| Password | nowhere |
| Access token | process memory only |
| Refresh token | Windows Credential Manager only |
| Cache encryption key | Windows Credential Manager only |
| Profile, organizations, companies, workspace | SQLite, AES-256-GCM sealed per user + row |
| Settings (theme…) | SQLite, plain (preferences, no secrets) |

Tests prove the refresh token, access token and password appear in no database or log file ([`SECURITY.md`](SECURITY.md)).

## Unauthorized and permission errors

401 → refresh once → session ended if it fails. 403 is shown as "not permitted" and never confused with an expired session. 404 for a company means "no access or does not exist" (the backend does not reveal which).

## Backend support added for Phase 5

Sessions are now **device-bound** (see [`DEVICE_REGISTRATION.md`](DEVICE_REGISTRATION.md)), so revoking a device ends its session and blocks its refresh token.

## Not built

Biometric/Windows Hello unlock, offline unlock without the server, SSO, password reset in the app (use the Control Center), MFA enrolment in the app.
