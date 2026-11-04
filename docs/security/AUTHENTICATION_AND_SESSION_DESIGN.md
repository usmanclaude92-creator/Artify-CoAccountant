# Authentication and Session Design (as implemented in Phase 1)

Decision: in-house authentication behind an `AuthProvider` interface (D-1; web sessions ADR-021; pre-login lookups ADR-022). Threats and residual risks: [`THREAT_MODEL_AUTH.md`](THREAT_MODEL_AUTH.md). Authorization: [`ROLE_AND_PERMISSION_MATRIX.md`](ROLE_AND_PERMISSION_MATRIX.md).

## 1. Two realms

| | Tenant realm | Platform realm |
|---|---|---|
| Principals | `users` (firm staff, client users) | `platform_admins` (Artify staff) |
| Routes | `/api/v1/auth/*` and everything under `/orgs`, `/companies`, `/me` | `/api/v1/platform/*` (never reachable with a tenant token and vice versa) |
| Signing secret / audience | own | **different** (settings reject equal secrets) |
| Database role | `artify_app` | `artify_platform` (no grants or policies on tenant business tables) |
| Access / refresh lifetime | 15 min / 30 days | 10 min / 24 h |
| MFA | mandatory for `firm_owner` and `practice_admin` | mandatory for every platform admin |
| Creation | sign-up or invitation | CLI only (`artify-api create-platform-admin`; `bootstrap-dev` creates development credentials and refuses to run outside development) |
| Web sign-in page | `/login` | `/platform/login` (not deployed on the public console surface: `APP_SURFACE=console` returns 404) |

## 2. Credentials

- Passwords: **Argon2id** (argon2-cffi defaults, RFC 9106 low-memory profile); minimum length from settings, maximum 256, a small denylist of common passwords and "equals e-mail" rejected. Verification does constant work even for unknown accounts (dummy hash). No breached-password lookup yet (open).
- TOTP (RFC 6238): secret encrypted at rest with Fernet; accepted time step recorded (`last_used_step`) so a code cannot be replayed; 10 single-use recovery codes (stored hashed).
- Opaque tokens (refresh, e-mail verification, password reset, invitation) are random, shown once, and stored only as SHA-256 digests.

## 3. Flows

1. **Sign-up** `POST /auth/signup` creates the user, an Organization (`accounting_firm` or `independent_accountant`), the `firm_owner` membership and a trial subscription in one transaction (via `SECURITY DEFINER` functions where RLS requires it). Response is identical whether or not the e-mail is already registered. Verification link is sent through the mailer.
2. **Verify e-mail** `POST /auth/verify-email`; login is refused until verified.
3. **Login** `POST /auth/login` → either tokens, or `mfa_required` with a 5-minute signed challenge token (`mfa_enrollment_required` when a privileged role has no factor yet). `POST /auth/mfa/verify` completes it.
4. **MFA enrollment** `POST /auth/mfa/enroll` + `/mfa/confirm` (returns recovery codes once and an MFA-verified access token).
5. **Refresh** `POST /auth/refresh` rotates the refresh token. Presenting an already-used token revokes the **whole session** (reuse detection).
6. **Logout** `POST /auth/logout` revokes the session immediately.
7. **Session list / remote revoke** `GET /auth/sessions`, `DELETE /auth/sessions/{id}`.
8. **Password reset** `POST /auth/password-reset/request` (always 202-style identical response) and `/confirm`; a successful reset revokes the user's sessions.
9. **Invitations** `POST /orgs/{id}/invitations`, `POST /companies/{id}/invitations`; `POST /auth/invitations/accept` (new users set a password; existing users must be authenticated as the invited identity).
10. **Disabled users** lose access at the next request: the session row **and** the user status are checked on every authenticated call.

## 4. Access token and per-request authorization

HS256 JWT with pinned algorithm; required claims `iss`, `aud`, `exp`, `iat`, `sub`, `sid` and token type (`access` vs `mfa_challenge`). On every request the server (a) verifies the token, (b) checks the session row is not revoked or expired, (c) loads the caller's memberships for the **claimed** organization (`X-Organization-Id` header or path), (d) sets the PostgreSQL transaction context (`app.user_id`, `app.org_id`, `app.company_scope`, `app.company_ids`) for RLS, (e) authorizes the declared permission. A claimed organization with no membership gives **404** and an unattributed audit event.

## 5. Browser sessions (web BFF)

- The browser never receives tokens in JSON. The Next.js server (BFF) stores them in **HttpOnly, SameSite=Strict, `Secure` (in production) cookies** and attaches them to API calls; nothing is placed in `localStorage`/`sessionStorage`.
- Non-GET BFF requests need the custom header `x-artify-csrf: 1` and a same-origin `Origin`; otherwise **403**.
- On a 401 the BFF rotates the refresh token once and retries; if that fails the cookies are cleared and the UI returns to `/login` (e2e test: revoked session → redirected to sign-in, stale cookies get 401).
- `proxy.ts` redirects unauthenticated page requests before rendering; the API remains the authority.
- If no backend is configured (`ARTIFY_API_URL` unset in production) the BFF answers 503 `api_unavailable` and the UI explains it.

## 6. Rate limits (per key, fixed window)

Login (per IP and per e-mail), MFA verify (per subject), signup, password reset, invitation accept and refresh are limited; the backend is in-memory for local use and **Redis** in staging/production (settings refuse the in-memory limiter there). IP is the socket address until trusted-proxy handling is configured in deployment (open, Phase 12).

## 7. Audit

Login success/failure, MFA, session revocation, password reset, invitation creation/acceptance/revocation, membership and role changes, company changes and authorization denials are written to the append-only `audit_events` (tenant-visible rules in ADR-025) or `platform_audit_events`. Passwords, tokens and secrets are redacted from audit summaries and never logged.

## 8. Not implemented / deferred

| Item | Status |
|---|---|
| Breached-password screening | NOT IMPLEMENTED |
| Real mail provider; durable (outbox) mail delivery | DEFERRED (D-3, Phase 3); SMTP adapter exists, best-effort |
| Key rotation (`kid`), asymmetric signing | NOT IMPLEMENTED |
| Device-bound refresh tokens (desktop/mobile) | DEFERRED (Phase 5) |
| "New device" and lockout notification e-mails | NOT IMPLEMENTED |
| External identity provider / SSO (SAML, OIDC) | DEFERRED (the `AuthProvider` seam exists) |
| Nonce-based CSP | NOT IMPLEMENTED |
| Creating an **additional** organization for an already-registered user | NOT IMPLEMENTED (organizations are created at sign-up; a person joins others by invitation) |
