---
name: security-audit
description: Use when reviewing or changing authentication, sessions, MFA, authorization, RLS, file handling, secrets, CORS/CSP, audit logging or anything on the platform realm in the Artify repo, and before closing a phase. Provides a checklist tied to the code and tests that currently exist.
---

# Security audit checklist

Authoritative design: `docs/security/SECURITY_ARCHITECTURE.md`; auth threat model: `docs/security/THREAT_MODEL_AUTH.md`.

## For every change
- [ ] New route declares authorization (`Public`/`Requires`/`PlatformRequires`) — and `Public` has a justification string.
- [ ] No tenant/company id is trusted from the client without membership validation; objects outside scope → 404.
- [ ] New tables: RLS enabled+forced, least-privilege grants, composite FK (see `multi-tenant-saas` skill). Service roles have no BYPASSRLS (`test_service_roles_cannot_bypass_rls`).
- [ ] Platform realm cannot reach tenant business data (`test_platform_role_cannot_read_tenant_business_data`).
- [ ] State changes write `audit_events`; no secrets/tokens/passwords in audit summaries, logs or error bodies.
- [ ] Inputs validated by Pydantic; SQL only via bound parameters (the f-string SQL in the repo interpolates code-defined column names only — keep it that way; ruff `S608` is allow-listed for that reason).
- [ ] Rate limits on any new unauthenticated or credential-bearing endpoint (`ratelimit.enforce`).
- [ ] Responses to unauthenticated callers do not reveal account existence (compare signup/login/reset responses).

## Auth specifics implemented in Phase 1
Argon2id; separate JWT secret+audience per realm; refresh rotation with reuse detection (family revoked); TOTP with replay protection and encrypted secrets; MFA mandatory for platform admins, `firm_owner`, `practice_admin`; sessions revocable; password reset revokes sessions. Web: tokens only in HttpOnly SameSite=Strict cookies behind a BFF; custom-header CSRF check.

## Known gaps (do not claim otherwise)
Breached-password check, trusted-proxy IP handling, nonce-based CSP, audit hash-chaining, WebAuthn, per-tenant encryption keys (D-4), user-level auth events not visible to tenant admins. Tracked in `docs/development/PHASE_1_COMPLETION_REPORT.md`.
