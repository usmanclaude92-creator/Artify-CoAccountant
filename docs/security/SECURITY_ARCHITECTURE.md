# Security Architecture

Design for Phase 1 onward. Statements here are requirements and designs, not claims of implemented controls. Nothing in Phase 0 is security-tested because nothing is implemented.

## 1. Principles

1. Authorize on the server for every request; clients are untrusted.
2. Two independent isolation boundaries: tenant and company (ADR-002), each enforced by backend authorization **and** PostgreSQL RLS (ADR-003).
3. Least privilege everywhere: roles, DB roles, service accounts, signed URLs.
4. Fail closed: missing context → no data.
5. Platform operators have no implicit access to customer financial data (ADR-013).
6. Everything sensitive is auditable; audit data is append-only.
7. No production credentials or real customer documents in development, CI, or demos.

## 2. Authentication

- Realms: **tenant** and **platform** (separate token audiences, issuers/keys, cookie scopes and hostnames). A token from one realm is rejected by the other.
- Passwords: Argon2id with parameters tuned to the hardware; breached-password and length checks; no composition rules theatre; rate-limited and lockout-resistant (progressive delays, not hard lockout to avoid DoS).
- MFA: TOTP at launch; WebAuthn/passkeys later. **Mandatory** for platform admins, `firm_owner`, `practice_admin`; strongly encouraged and policy-enforceable per Organization for others.
- Sessions: short-lived access tokens (≈15 min) + rotating refresh tokens (opaque, stored hashed, device-bound, reuse detection revokes the family). Desktop/Mobile store tokens in the OS credential vault/Keychain/Keystore. Web uses `HttpOnly`, `Secure`, `SameSite` cookies with CSRF protection for cookie-authenticated mutating requests.
- Account recovery: email-based single-use, short-lived tokens; recovery never bypasses MFA for privileged roles without a verified secondary path; recovery events notify the account and are audited. Platform-admin recovery is manual with dual control.
- Invitations: single-use, expiring, hashed tokens; role and target fixed at issue time.
- Decision D-1 (build vs adopt IdP) pending; the `AuthProvider` interface isolates the choice.

## 3. Authorization

- **Permission catalog** defined in code (`resource.action`, e.g. `documents.approve`, `workspace.manage`, `excel_templates.publish`); roles are data composed of permissions (system roles + optional custom roles).
- **Policy engine** (single module): decision = role permissions ∩ scope (organization / assigned Companies / own Company) ∩ section permission ∩ resource state (e.g. approved ⇒ immutable) ∩ entitlement/subscription status. Returns allow/deny with reason code for audit.
- **Object-level authorization** on every resource fetch by id: a resource outside the caller's scope returns **404**, not 403, to avoid existence disclosure.
- **Client-supplied IDs** (`organization_id`, `company_id`, `section_id`, document ids in bodies/paths) are validated against resolved memberships and re-verified by RLS and composite FKs.
- **Separation of duties** configurable (submitter ≠ approver).
- **Service-to-service/worker:** workers act with a system actor identity, constrained by the same RLS context; system actors cannot perform approvals (ADR-009).

## 4. Tenant and company isolation

See [`../database/ISOLATION_AND_RLS.md`](../database/ISOLATION_AND_RLS.md). Summary: composite FKs, RLS with fail-closed context, non-owner DB role, platform role limited to platform metadata, support grants for exceptions, isolation test suite as a release gate, object-storage key prefixing plus authorization-gated signed URLs.

## 5. Support access (explicit, restricted, audited)

Lifecycle: request (platform admin; reason + scope + duration) → approval (tenant `firm_owner`; break-glass = two Platform Admins + tenant notification) → time-limited activation (default ≤ 4 hours, platform-setting cap) → every access recorded in `audit_events` (tenant-visible) and `platform_audit_events` with the grant id → auto-expiry/revocation. Access levels: `metadata` (configuration and counts, no document content) and `read` (document content). **No write access via support grants** in v1. Impersonation of users is not supported.

## 6. API and application protections

- Input validation with Pydantic; strict schemas; request size limits; pagination caps.
- Output encoding in UIs; Content-Security-Policy, HSTS, `X-Content-Type-Options`, `Referrer-Policy`, frame protections; secure cookie flags.
- CORS allow-list per surface; no wildcard with credentials.
- **Rate limiting** (Redis): per IP, per user, per tenant, stricter on auth, invitation, recovery, upload-initiate and AI-trigger endpoints; separate limits per realm.
- **Idempotency keys** for retried mutating calls; replay-safe uploads.
- Errors do not reveal internals; stack traces never returned; correlation id returned.
- SSRF protection for any outbound fetch (provider webhooks, URL imports): allow-lists, no internal ranges.
- Dependency, container image and secret scanning in CI (Phase 1); SBOM generation (Phase 12).

## 7. File and document security

Allow-listed types, magic-byte validation, size limits from entitlements, malware scanning before availability, PDF/Office active-content neutralization, image decompression-bomb limits, ZIP/XLSX bomb and XXE protection (defusedxml), no macro execution, content-type pinned on download with `Content-Disposition` and `nosniff`, signed URLs ≤ 5 min and single-object scoped, originals immutable (bucket versioning/object lock where supported), SHA-256 integrity.

## 8. Encryption and secrets

- In transit: TLS 1.2+ everywhere (internal hops included in production); HSTS.
- At rest: database and object storage encryption with provider KMS; backups encrypted. Per-tenant envelope keys are decision D-4 (hooks reserved: `encryption_key_ref`).
- Application-level encryption for especially sensitive fields (MFA secrets, bank account numbers) using a managed key hierarchy.
- Secrets: secret manager in staging/production, short-lived credentials where possible, rotation procedures documented in Phase 1; `.env` never committed (`.gitignore` in Phase 0 already excludes it); CI secret scanning.
- Desktop local cache: OS-protected storage; tokens only in the OS vault; cache wiped on device revoke (best effort) and on logout when configured.

## 9. Audit logging

- `audit_events` (tenant/company) and `platform_audit_events`: append-only (immutability triggers, no `UPDATE/DELETE` grants), partitioned, retention ≥ longest financial retention.
- Recorded: authentication events, permission/role/assignment changes, workspace change sets, template applications, entitlement changes, approvals and rejections, exports, downloads of documents, support access, device registration/revocation, failed authorization attempts, data erasure.
- Records `actor_type` (`user`/`system`/`ai`/`platform_admin`), IP, user agent, correlation id, and before/after summaries **with sensitive values redacted**.
- Tamper evidence: hash chaining per tenant partition and periodic external anchoring are Phase 12 hardening items.
- Viewing: Control Center Audit module for tenants (own data); Super Admin for platform events; security alerts on anomalies (impossible travel, mass download, repeated denials).

## 10. Device management

Device registration at login, per-device sessions, remote revoke, optional device approval by an admin, `limit.devices` enforcement, minimum app-version enforcement (force upgrade for security fixes), root/jailbreak signals are advisory in v1.

## 11. Subscription entitlement enforcement

Security-relevant because it prevents resource abuse: see [`../architecture/SUBSCRIPTION_AND_ENTITLEMENTS.md`](../architecture/SUBSCRIPTION_AND_ENTITLEMENTS.md). Backend-only enforcement, transactional counters, structured denials, rate limits as a second guard.

## 12. Backup and disaster recovery

Targets (to be confirmed in Phase 12 against real hosting): RPO ≤ 15 minutes for PostgreSQL via continuous archiving/PITR; RPO ≤ 24 hours for derived data; object storage with versioning and cross-region (or cross-zone, per D-3) replication; RTO ≤ 4 hours for the core API. Quarterly restore drills including RLS verification; per-tenant restore/export tooling; backups encrypted, access-controlled, retained per policy; DR runbooks versioned in `docs/security/`. These are **targets**, not achieved metrics.

## 13. Data retention and privacy

Retention is configurable policy (D-8), applied by an audited job; legal hold; export and erasure workflows (see `DATABASE_DESIGN.md` §5); data-processing agreement and sub-processor list before any cloud AI use (D-5); data minimization in logs and AI requests; regional residency per D-3.

## 14. Secure development lifecycle

Threat model per phase (STRIDE on new surfaces), security review checklist in PRs (`code-review`/`security-audit` project skills), dependency policy, mandatory tests for authorization on every endpoint (a test iterates the OpenAPI route table and fails if a route lacks a declared permission), fuzz/negative tests for file parsers, third-party penetration test before commercial release (Phase 12).

## 15. Top risks and mitigations

| Risk | Mitigation |
|---|---|
| Cross-company leak via a missed filter | RLS + composite FKs + route-table authorization test |
| Compromised firm owner account | Mandatory MFA, device approvals, anomaly alerts, audit |
| Malicious file upload | Validation, scanning, sandboxed processing, neutralization |
| Prompt injection via document text | No tool surface for AI, schema-constrained outputs, human review (see AI doc) |
| Insider at Artify | No default data access, grants with tenant approval, audit visible to tenant, DB break-glass controls |
| Token theft from desktop | OS vault, short access tokens, refresh rotation + reuse detection, device revoke |
| Formula injection in Excel exports | Text-prefix guard on untrusted cell content |
| Over-limit abuse / DoS | Entitlements + rate limits + queue quotas |
