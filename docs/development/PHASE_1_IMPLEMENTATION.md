# Phase 1 Implementation Overview

How the Phase 1 foundation is built and where to find it. Status per requirement (implemented / tested / open) is in [`PHASE_1_COMPLETION_REPORT.md`](PHASE_1_COMPLETION_REPORT.md); evidence in [`PHASE_1_TEST_REPORT.md`](PHASE_1_TEST_REPORT.md); scope in [`PHASE_1.md`](PHASE_1.md).

## 1. Repository map

| Path | Content |
|---|---|
| `services/api/src/artify_api/` | FastAPI app: `main.py` (factory, middleware, error handlers), `settings.py`, `db.py` (engines, transaction context), `context.py` (authentication, membership resolution, `Requires`/`PlatformRequires`/`Public`), `policy.py` + `permissions.py` (pure authorization), `auth_service.py`, `auth_provider.py`, `security/` (Argon2id, JWT, TOTP, tokens), `entitlements.py`, `audit.py`, `idempotency.py`, `outbox.py`, `worker.py`, `mailer.py`, `ratelimit.py`, `logging_config.py`, `cli.py`, `seed.py`, `routers/` |
| `services/api/migrations/` | Alembic environment and hand-written SQL (`sql/0001…0003`) |
| `services/api/tests/` | 90 API/database tests against a real PostgreSQL |
| `packages/api-client/` | `openapi.json` (generated from the API) and `src/schema.d.ts` (generated TypeScript types) — no hand-copied API types |
| `packages/ui/` | design tokens (`styles.css`, light/dark, RTL-safe logical CSS) and primitives |
| `apps/web/` | Next.js 16 app: `(console)` pages, `(platform)` pages, BFF (`bff/[...path]`, `session/[realm]/[action]`), `i18n/` (English, Arabic), `proxy.ts` route guard; `e2e/` Playwright |
| `infrastructure/` | `database/` (reset + dev roles), `dev/` (e2e runner, compose smoke test, RLS benchmark) |
| `docker-compose.yml`, `services/api/Dockerfile` | local stack |

Layering (enforced by import-linter): routes → services/auth → db; `policy`/`permissions` import no web framework and no database.

## 2. Backend conventions

- **Versioned REST** under `/api/v1`; OpenAPI at `/openapi.json`, Swagger UI at `/docs` (disabled in production).
- **Errors:** RFC 9457 `application/problem+json` with stable `code`, `correlation_id`; unknown paths and unhandled exceptions use the same shape; database errors never echo bound parameters.
- **Transactions:** one per request, committed before the response is sent; RLS context set from validated memberships only.
- **Idempotency:** mutating create/invite/register routes accept `Idempotency-Key` (replay returns the stored response; same key with a different body → 422).
- **Audit:** append-only tenant and platform audit; denials are recorded in their own transaction.
- **Outbox → Celery:** events written transactionally; dispatcher publishes; worker runs with a single-company context (`test_worker_context_is_limited_to_one_company`). Redis is used only for rate limiting and the Celery broker.
- **Logging:** JSON, correlation id on every line, identifiers only (no bodies, tokens or passwords).
- **Entitlements:** `EntitlementService` is the only enforcement point (locked counters, authoritative re-measurement, overrides > plan > default deny); `artify-api reconcile` repairs counter drift.

## 3. API surface (all under `/api/v1`)

Guard column: **public** = no session (rate-limited); **session** = any authenticated tenant user; a permission key = that permission in the claimed organization (and company, for `/companies/...`) per [`ROLE_AND_PERMISSION_MATRIX.md`](../security/ROLE_AND_PERMISSION_MATRIX.md).

| Area | Endpoints | Guard |
|---|---|---|
| Health | `GET /health`, `GET /health/ready` | public |
| Sign-up, verification, login | `POST /auth/signup`, `/auth/verify-email`, `/auth/login`, `/auth/mfa/verify`, `/auth/refresh`, `/auth/password-reset/request`, `/auth/password-reset/confirm`, `/auth/invitations/accept` | public |
| Session management | `POST /auth/logout`, `/auth/mfa/enroll`, `/auth/mfa/confirm`; `GET /auth/sessions`; `DELETE /auth/sessions/{id}` | session |
| Current user | `GET /me` (profile, memberships, permissions for `X-Organization-Id`), `GET /roles`, `GET /permissions` | session |
| Devices | `GET/POST /devices`, `DELETE /devices/{id}` | `devices.manage_own` |
| Organizations | `GET /orgs` | session (own memberships only) |
| | `GET /orgs/{id}` / `PATCH /orgs/{id}` | `organization.read` / `organization.update` |
| | `GET /orgs/{id}/members`, `PATCH /orgs/{id}/members/{user}` | `members.read` / `members.manage` |
| | `GET/POST /orgs/{id}/invitations`, `DELETE …/{invitation}` | `invitations.manage` |
| | `GET /orgs/{id}/entitlements`, `GET /orgs/{id}/audit-events` | `entitlements.read` / `audit.read` |
| Companies | `GET/POST /orgs/{id}/companies` | `companies.list` / `companies.create` |
| | `GET/PATCH /companies/{id}` (archive = `PATCH {"status":"archived"}`) | `companies.read` / `companies.update` |
| | `GET /companies/{id}/members`, `PATCH …/members/{user}`, `POST …/invitations` | `company_members.read` / `company_members.manage` |
| | `GET/POST /companies/{id}/assignments`, `DELETE …/{user}` | `assignments.read` / `assignments.manage` |
| Platform (separate realm) | `POST /platform/auth/login`, `/mfa/verify`, `/refresh` (public); `/logout`, `/mfa/enroll`, `/mfa/confirm` (platform session); `GET /platform/me`, `GET /platform/organizations` (metadata only) | `platform.*` |

Not provided by design: generic "create a second organization" for an existing user; a hard-delete of companies; direct add of an existing user to a company without an invitation.

## 4. Web (Next.js)

| Route | Purpose |
|---|---|
| `/signup`, `/login`, `/verify-email`, `/forgot-password`, `/reset-password`, `/accept-invitation` | public auth flows against the real backend |
| `/security/mfa` | TOTP enrollment + one-time recovery codes |
| `/dashboard` | profile, organization switcher, company selector (selection lives in the URL), role display, company list/details, entitlement usage, empty/loading/error states, language switch, sign-out |
| `/platform`, `/platform/login` | Super Admin foundation: separate sign-in, mandatory MFA, metadata-only organization list (absent on the public deployment) |

Responsive, light/dark tokens, English + Arabic with RTL logical CSS. No fake statistics or customers. Tokens never reach page JavaScript (see `AUTHENTICATION_AND_SESSION_DESIGN.md`).

## 5. Deviations from Phase 0 / from the brief, and why

| Deviation | Reason |
|---|---|
| SQLAlchemy **Core** + hand-written SQL migrations instead of ORM models; migrations forward-only | RLS policies, triggers and `SECURITY DEFINER` functions are the substance of the schema; every statement stays visible to review (DECISIONS ADR-020) |
| Permission keys are dotted (`members.manage`) rather than the brief's snake_case examples; document/accounting/workspace/subscription permissions are **not** in the catalog yet | a permission is added together with the resource it protects (matrix §5) |
| Company roles: `company_owner`, `company_admin`, `company_employee` (the brief's "Company Owner/Administrator/Employee") | added `company_admin` in the gap pass |
| "Create organization" only through sign-up | an organization is created together with its first owner and trial subscription; additional organizations for an existing user are not built |
| Registration is public sign-up of a *firm*; client users and staff join by invitation | matches the approved tenant model |
| Mail: console (local) and SMTP adapter; no provider | D-3 undecided |
| Rate limiter in-memory locally, Redis required in staging/production | avoids a mandatory Redis in development while failing closed in production |

## 6. Phase 1 gap pass (this iteration)

Added after re-checking the brief against the delivered code: `PATCH /orgs/{id}`, `GET /orgs`, `PATCH /companies/{id}/members/{user}`, `GET /roles`, `GET /permissions`, the `company_admin` role with company-role escalation guards, a profile panel on the dashboard, an e2e test for revoked sessions, and the seven documents the brief lists. Tests: `tests/test_phase1_gap_pass.py` (7), e2e `profile is shown, and a revoked session sends the user back to sign-in`.
