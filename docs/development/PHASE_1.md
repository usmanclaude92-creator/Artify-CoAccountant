# Phase 1 — SaaS Foundation, Database and Authentication

**Status: DELIVERED FOR REVIEW (2026-11-04) — partially complete; see [`PHASE_1_COMPLETION_REPORT.md`](PHASE_1_COMPLETION_REPORT.md) for what was implemented, what was verified and what remains.** Phase 2 must not start until this phase is reviewed and approved.

## Objectives

1. Create the monorepo scaffolding and developer experience (one-command local stack, CI).
2. Implement the identity, tenancy and authorization core with PostgreSQL RLS and prove isolation with an automated suite.
3. Implement the centralized entitlement service and usage counters.
4. Establish the API conventions (contract-first, problem+json, idempotency, audit, outbox) that all later phases reuse.
5. Ship a minimal web shell with authentication screens and the i18n/RTL and design-token foundation.

## Functional scope

- Sign-up of an Organization (`accounting_firm` or `independent_accountant`), email verification, login, logout, password reset, TOTP MFA, session/device list.
- Organization members and roles (`firm_owner`, `practice_admin`, `accountant`, `staff`); invitations; platform-admin bootstrap (CLI-seeded) with mandatory MFA.
- Companies: minimal create/read (full management UI is Phase 3) so isolation can be tested end-to-end.
- Entitlement catalog and service with a seeded development plan; counters for companies/users/devices.
- Audit event writing for auth, membership and company events.
- Web shell: login, MFA, org/company switcher skeleton, i18n (English + Arabic scaffolding if D-9 confirms), light/dark tokens.
- **Out of scope:** Super Admin UI, Control Center modules, workspaces, documents.

## Technical implementation

- Select and lock versions (ADR-019); create `pnpm` workspace, `uv` Python workspace, task runner, pre-commit, Docker Compose (api, worker, postgres, redis, minio).
- `services/api`: FastAPI app factory, settings, structured logging, error handling (RFC 9457), request context middleware, SQLAlchemy 2 + Alembic, UUIDv7 helper, idempotency middleware, outbox table + dispatcher skeleton, Celery wiring with tenant-context propagation.
- Auth module behind `AuthProvider` (per D-1): Argon2id, access/refresh tokens, rotation + reuse detection, TOTP, rate limiting, realm separation (platform vs tenant issuers).
- Context resolution + policy engine + permission catalog (code-defined) + role seeding.
- RLS migration framework: DB roles, `app.*` helper functions, per-table policy templates, CI catalog check, immutability trigger helper.
- `EntitlementService` with locked counters and reconciliation job.
- OpenAPI generation, TypeScript client generation into `packages/api-client`, OpenAPI breaking-change check.
- `apps/web`: Next.js app with `(console)` and `(platform)` route groups (only auth screens now), `packages/ui` tokens + first primitives.
- CI (GitHub Actions): lint, type-check, unit + DB + API tests, migration upgrade test, isolation suite, dependency/secret scan, docs check.

## Database changes

Creates: `organizations`, `users`, `auth_credentials`, `mfa_factors`, `sessions`, `devices`, `roles`, `permissions`, `role_permissions`, `organization_memberships`, `invitations`, `companies`, `company_memberships`, `accountant_assignments`, `platform_admins`, `platform_settings`, `entitlement_definitions`, `subscription_plans`, `subscription_plan_versions`, `plan_entitlements`, `subscriptions`, `entitlement_overrides`, `usage_counters`, `usage_records`, `audit_events`, `platform_audit_events`, `outbox_events`, `idempotency_keys`, `currencies`, `support_access_grants` (table only), DB roles and RLS policies for all of the above. See `ENTITY_CATALOG.md` for columns.

## API requirements

`/api/v1/auth/*` (tenant realm), `/api/v1/platform/auth/*` (platform realm), `/api/v1/orgs` (create/get), `/api/v1/orgs/{id}/members|invitations`, `/api/v1/companies` (create/list/get), `/api/v1/me` (memberships, permissions, entitlements summary), `/api/v1/health`. Every route declares a permission (TS-ISO-10); mutating routes accept `Idempotency-Key`.

## Dependencies

Phase 0 approved; decisions D-1, D-3 (dev infra), D-8 (policy schema only), D-9 resolved.

## Security requirements

Implement `SECURITY_ARCHITECTURE.md` §2–4, §6 (rate limiting, headers), §9 (audit); fail-closed RLS; no secrets in repo; separate DB roles; MFA mandatory for privileged roles; dependency and secret scanning in CI.

## Testing requirements

Executable: TS-ISO-01, 02, 04, 05, 08, 09, 10; TS-ENT-01 (companies/users/devices), TS-ENT-02; TS-SEC-01, 02, 04, 06; unit tests for policy engine and entitlement service; migration up/down on clean DB; RLS catalog check; schema lint (no float money; every table classified); performance baseline of RLS with synthetic data.

## Acceptance criteria

1. `docker compose up` yields a working local stack from a clean clone using documented steps.
2. A new Organization can sign up, verify, enable MFA, create a Company, invite a second user; all audited.
3. The isolation suite passes and fails closed on empty context.
4. Creating a Company beyond `limit.companies` fails through the raw API; concurrent test passes.
5. OpenAPI → TS client generation runs in CI; breaking-change check active.
6. CI is green; completion report lists every command run.

## Completion checklist

- [x] Versions pinned and recorded (`uv.lock`, `pnpm-lock.yaml`, `TECHNOLOGY_STACK.md`)
- [x] Migrations reviewed; RLS coverage check green (pytest; also runs in CI)
- [ ] Isolation suite wired as a **required** CI check — it runs on every push, but branch protection requiring it is a repository setting that has not been configured
- [x] Skills created: `co-accountant-architecture`, `multi-tenant-saas`, `api-development`, `security-audit`, `automated-testing`, `code-review`
- [x] Threat-model note for the auth surface (`docs/security/THREAT_MODEL_AUTH.md`)
- [x] `DECISIONS.md` updated (D-1, D-3, D-8, D-9 outcomes; ADR-020…027)
- [x] `PHASE_1_COMPLETION_REPORT.md` written honestly (with a follow-up pass)

## Known risks

RLS performance with large company sets; auth build effort/security (mitigated by D-1 decision and interface); scope creep into Control Center UI; scaffolding churn if versions are pinned late.
