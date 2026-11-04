# Threat Model — Authentication and Session Surface (Phase 1)

Scope: tenant-realm and platform-realm login, MFA, refresh, password reset, e-mail verification, invitations, the web BFF. Method: STRIDE per asset. "Test" names refer to executed tests in `services/api/tests`; "none" means the mitigation is a design/code review item only.

| Threat | Mitigation in Phase 1 | Evidence |
|---|---|---|
| **Spoofing — credential stuffing / brute force** | Argon2id; per-IP and per-email limits on login (6 / 5 min per email), MFA (6 / 5 min per subject), reset, signup, invitation accept, refresh; MFA for privileged roles | `test_login_is_rate_limited` (memory backend; Redis backend verified by `test_redis_limiter_shared_window`) |
| **Spoofing — account enumeration** | Identical responses for unknown vs wrong password (including a dummy Argon2 verify for timing), signup of existing email, reset of unknown email | `test_login_errors_do_not_reveal…`, `test_signup_response_is_identical…`, `test_password_reset_flow…` |
| **Spoofing — stolen access token** | 15-minute lifetime (platform 10); session row checked on every request, so logout/revoke/disable is immediate | `test_logout_revokes_the_session_immediately`, `test_session_list_and_remote_revocation`, `test_disabled_user_loses_access_on_refresh` |
| **Spoofing — stolen refresh token** | Opaque, hashed at rest, rotated on use; reuse of a used token revokes the whole session | `test_refresh_rotation_and_reuse_detection` |
| **Spoofing — realm confusion** | Separate signing secrets and audiences; separate DB roles; settings reject equal secrets | `test_realms_are_separate`, `test_production_settings_reject_development_secrets` |
| **Spoofing — MFA code replay** | Accepted time step recorded; same/older step rejected; recovery codes single-use | `test_mfa_login_challenge_replay_and_recovery_codes`, `test_recovery_code_works_once` |
| **Tampering — token forgery / alg confusion** | Algorithm pinned to HS256; `iss`, `aud`, `exp`, `iat`, `sub`, `sid` required; token type (`access` vs `mfa_challenge`) checked | `test_realms_are_separate` (challenge token rejected as access token) |
| **Tampering — CSRF (web)** | Tokens in HttpOnly SameSite=Strict cookies; BFF rejects non-GET without custom header or with foreign Origin | Playwright e2e (403 without header; cookie flags asserted) |
| **Repudiation** | `audit_events` / `platform_audit_events` append-only (privileges + trigger); login success/failure, MFA, session revoke, password reset, invitations, denials recorded | `test_audit_tables_are_append_only`, `test_audit_log_records_actions…` |
| **Information disclosure — secrets at rest** | Passwords Argon2id; TOTP secrets Fernet-encrypted; refresh/email/invitation tokens stored as SHA-256 only; audit redacts sensitive keys | `test_passwords_are_stored_as_argon2id`, `test_audit_redacts_sensitive_values` |
| **Information disclosure — cross-tenant** | RLS + composite FKs + 404 semantics | `test_isolation_api.py`, `test_database_rls.py` |
| **Information disclosure — tokens in JS** | Web never exposes tokens to scripts | e2e: `document.cookie` contains no `artify_*` |
| **Denial of service** | Rate limits; bounded request sizes by schema; per-route limits on expensive hashing endpoints | partial — no global request-size limit or load test yet |
| **Elevation of privilege — MFA bypass for privileged roles** | `firm_owner`/`practice_admin` blocked (`mfa_required`) until the session is MFA-verified; platform tokens without MFA only reach enrollment endpoints | `test_privileged_roles_require_mfa_before_org_access`, `test_platform_admin_must_enroll_mfa…` |
| **Elevation — invitation abuse** | Single-use hashed tokens with expiry; existing accounts must authenticate as the invited user; `firm_owner` cannot be invited | `test_invitation_edge_cases`, `test_existing_user_must_authenticate…` |
| **Elevation — platform admin reading tenant data** | No platform policies/grants on business tables | `test_platform_role_cannot_read_tenant_business_data` |

## Residual risks and gaps (not mitigated in Phase 1)
1. No breached-password screening (small denylist only).
2. Client IP is taken from the socket; behind a reverse proxy, trusted-proxy header handling must be configured (Phase 12) or rate limits key on the proxy address.
3. Refresh reuse is strict: a legitimate client retrying a rotation after a network failure will be signed out.
4. No device binding of refresh tokens yet (sessions can carry a `device_id`, but registration flows arrive in Phase 5).
5. CSP allows inline scripts required by this Next.js version; nonce-based CSP is planned.
6. HS256 shared-secret signing: acceptable for one deployable; asymmetric keys/rotation (`kid`) are not implemented.
7. User-level auth events are stored without an organization and are not visible to tenant admins (Phase 3 audit viewer).
8. No account lockout notification e-mails, no "new device" alerts.
9. The in-memory mailer/console mailer is a placeholder: real delivery is not implemented (D-3 pending hosting choice).
