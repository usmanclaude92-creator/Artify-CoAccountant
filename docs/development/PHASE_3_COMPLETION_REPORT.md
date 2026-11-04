# Phase 3 Completion Report — Firm Control Center and Client Management

**Status: delivered for review (2026-11-04). Phase 4 has not been started and will not be until the owner approves this phase.**

Evidence conventions: *Executed* means the command was run in this repository’s sandbox (PostgreSQL 16, Redis 7, Chromium) in this session; nothing is copied from documentation. All data is synthetic. Per-requirement security evidence: [`../firm-control-center/SECURITY_TEST_RESULTS.md`](../firm-control-center/SECURITY_TEST_RESULTS.md). Feature documentation: [`../firm-control-center/OVERVIEW.md`](../firm-control-center/OVERVIEW.md).

Phase 3 started on the owner’s Phase 3 brief; a separate formal approval of Phase 2 was not recorded in the repository.

## 1. What was implemented

Backend (FastAPI, PostgreSQL) and web (Next.js) for the customer-facing console: dashboard with real counts and entitlements; companies (list with search/filter/sort/paging, 4-step atomic creation wizard, detail with profile, accounting settings, assigned accountants, client users, configuration record and history; activate/deactivate/archive/restore); team (directory, role change, suspend/reactivate/remove, professional profile, roles-and-permissions panel, ownership transfer); accountants overview; company–accountant assignments with type, responsibility, start/end dates, active state and history; invitations (create with company access, resend, cancel, accept, decline, expiry, duplicate and rank rules, honest e-mail status); read-only subscription and usage page; organization activity (audit) viewer with filters; organization settings; own profile (name, password, sessions); support-access approval moved into the shell. Four new organization roles and five new permissions.

## 2. Reused from Phases 1 and 2

No second identity, organization, company, subscription or permission model. Reused: users/sessions/MFA/refresh rotation, `organizations`/`companies`/memberships/`accountant_assignments`/`invitations`, the pure policy engine and `Requires(...)` dependency, RLS context and the ADR-024 policy pattern, `EntitlementService` and plan/override/usage tables from Phase 2, the append-only audit writer, idempotency, RFC 9457 errors, outbox, the BFF with CSRF and HttpOnly cookies, the design tokens and UI kit (`packages/ui`, `DataTable`, `ActionDialog`, `Tabs`, …; the kit moved from `components/platform/` to `components/` so both consoles share it), the OpenAPI client generator and breaking-change check, the test fixtures and the Playwright/axe setup.

## 3. New database tables and migrations

Migration `0005_control_center` (forward-only, hand-written SQL wrapped by Alembic): new tables `company_configurations`, `member_profiles`; altered `organizations`, `companies`, `accountant_assignments`, `invitations`; replaced functions `check_assignment_member`, `measure_entitlement`, `auth_invitation_by_hash`, `platform_provision_organization`, new `decline_invitation`; view `platform_org_overview` replaced. Details: [`../database/INITIAL_DATABASE_IMPLEMENTATION.md`](../database/INITIAL_DATABASE_IMPLEMENTATION.md) §9. Both new tables have forced RLS and are covered by the table-classification test. Applied cleanly from migration 0001 on every test run and in `make e2e`.

## 4. New API endpoints (22 new, several changed)

New: `GET /orgs/{org}/dashboard`, `/settings` (GET, PATCH), `/team`, `/accountants`, `/accountants/{user}`, `/assignments`, `/subscription`, `/activity`, `PATCH /orgs/{org}/members/{user}/profile`, `POST /orgs/{org}/ownership-transfer`, `GET /orgs/{org}/companies/overview`, `POST /companies/{id}/status`, `PATCH /companies/{id}/settings`, `GET /companies/{id}/configuration`, `GET /companies/{id}/history`, `PATCH /companies/{id}/assignments/{user}`, `GET /companies/{id}/assignments/history`, `POST /orgs/{org}/invitations/{id}/resend`, `POST /auth/invitations/decline`, `PATCH /me`, `POST /me/password`. Changed: company creation (wizard payload, atomic), `PATCH /companies/{id}` (no longer accepts `status`), invitation create/list/accept, `/roles`, `/me`. Full table with permissions: [`../firm-control-center/API_REFERENCE.md`](../firm-control-center/API_REFERENCE.md).

## 5. Frontend pages and components

Pages under `apps/web/src/app/(console)/(app)/`: `dashboard`, `companies`, `companies/new`, `companies/[id]`, `team`, `accountants`, `assignments`, `invitations`, `subscription`, `activity`, `settings`, `profile`, `support-access`. Components: `components/cc/shell.tsx` (organization context, permission-filtered navigation, organization switcher via `?org=`, MFA notice, responsive drawer), shared `components/kit.tsx`, `lib/tenant.ts`. The public invitation page gained a *Decline* button. Every list keeps filters, sort and paging in the URL; every page has loading, error, empty and permission-denied states.

## 6. Organization and company management

Implemented and tested: organization settings with validation and audit; company creation wizard, edit, independent accounting settings (currency, financial year, basis, VAT with number consistency rule, free-text jurisdiction — no jurisdiction rules hard-coded), lifecycle with reversible archive, subscription-limit-aware restore, company history, configuration-ownership record. Details: [`COMPANY_MANAGEMENT`](../firm-control-center/COMPANY_MANAGEMENT.md), [`ORGANIZATION_MANAGEMENT`](../firm-control-center/ORGANIZATION_MANAGEMENT.md).

## 7. Team and accountant management

Implemented and tested: invitations (rank rule, duplicates, expiry, resend, cancel, decline, accept with company assignment), member role/status changes with immediate effect, removal, professional profile separate from the authorization role, accountant list with workload, assignments (many-to-many, responsibility, windows, history), ownership transfer. Independent accountants use the same organization model (a one-person organization). Details: [`TEAM_AND_ACCOUNTANT_MANAGEMENT`](../firm-control-center/TEAM_AND_ACCOUNTANT_MANAGEMENT.md).

## 8. Role and permission implementation

Roles: owner (`firm_owner`), administrator (`practice_admin`), `practice_manager`, `accountant`, `assistant_accountant`, `document_operator`, `read_only`, legacy `staff`, and three company roles. 22 tenant-realm permission keys (five new) with organization and company scopes, rank rule, MFA requirement for owner/administrator/manager, no self-elevation, last-owner protection through ownership transfer, assignment-window visibility. Generated tables: [`ROLE_AND_PERMISSION_MODEL`](../firm-control-center/ROLE_AND_PERMISSION_MODEL.md). **Not implemented: organization-defined (custom) roles and a screen to configure permissions** — the brief allowed this only “where the existing architecture supports it”; see ADR-038.

## 9. Subscription and entitlement integration

Read-only view driven by the Phase 2 data: plan, version, status, period, trial end, price string, every configured limit with live counts for the four enforceable keys (`limit.companies`, `limit.users`, `limit.accountants`, `limit.devices`), flags, attention messages and next step. Non-enforceable limits (workspace sections, custom fields, templates, documents, AI, storage) are listed as *Not metered / Not enforced yet*, never zero. Customers have no write route. Limit enforcement (402) happens only in `EntitlementService`; archive frees and restore re-checks the company slot; accountant seats now count accountant, assistant accountant and staff. No upgrade, payment or invoice flow exists — the page says “Contact Artify support”.

## 10. Security controls

Server-side authorization on every route (`Requires`), RLS on both new tables, 404 for out-of-scope objects, per-request re-evaluation of membership/role/assignment window, rank rule, ownership transfer, MFA gate, honest e-mail/notification reporting, append-only audit of privileged changes, no customer path to platform data. Results of the ten required checks and the mutation checks are in `SECURITY_TEST_RESULTS.md`.

## 11–12. Automated tests executed and results

| Suite | Command | Result |
|---|---|---|
| API + database (real PostgreSQL, database rebuilt from migrations 0001→0005) | `ARTIFY_TEST_REDIS_URL=redis://127.0.0.1:6399/0 uv run pytest services/api/tests` (also the pytest stage of `make check`, which exited 0 as a whole) | **207 passed, 0 failed, 0 skipped** (Phase 2 reported 162; the 45 additional cases are the new tests below and parametrized cases) |
| New Phase 3 API/DB tests | `test_p3_companies.py` (10), `test_p3_team.py` (14 functions, 22 cases), `test_p3_org.py` (10), `test_p3_catalog.py` (2), 1 new function in `test_database_rls.py` | all passed |
| Lint, format, boundaries | ruff check, ruff format --check (77 files), eslint, import-linter | clean; 2 contracts kept |
| Static types | mypy `--strict` (47 files), `tsc --noEmit` | clean |
| API contract | `make openapi-check`; `make openapi-breaking BASE=907687f` | up to date; **0 breaking** (but see known issue 1) |
| Web unit / build | vitest; `next build` | 6 passed; succeeded |
| Browser end-to-end | `E2E_CHROMIUM_PATH=/opt/pw-browsers/chromium make e2e` | **31 passed**, 0 failed (final full run after the last code change; includes the Control Center journey with company wizard, lifecycle, invitation cancel/accept/decline, assignment and a least-privilege accountant) |
| Accessibility | axe-core (WCAG 2.0–2.2 A/AA) in `accessibility.spec.ts`: every one of the 12 Control Center pages in light and dark, the roles panel, public pages, Arabic public pages | 0 violations |
| Dependency audit | `make audit` | no known vulnerabilities (Python and JavaScript) |
| Phase 1 and 2 regression | all Phase 1/2 API tests (inside the 207) and Super Admin e2e specs | passed |

Not executed in this session: Docker Compose smoke test, `make hooks` pre-commit run, the RLS benchmark, GitHub CI for this change (it can only run after the push; CI for the previous commit was not re-checked).

## 13. Known issues (nothing hidden)

1. **`PATCH /companies/{id}` no longer accepts `status`** (ADR-037). The breaking-change detector does not see removed request fields, so it reports 0 breaking. Any client sending `status` there must use `POST /companies/{id}/status`.
2. **Signed-in screens are English-only.** The Phase 1 dashboard had Arabic strings; the new pages do not (ADR-040, deviates from D-9). Public pages are still bilingual.
3. **Two behavioural mutations survived** and one RLS mutation went undetected until a test was added (details and fixes in `SECURITY_TEST_RESULTS.md`): the member-role rank rule has no behavioural test because it is unreachable with today’s catalog (guarded by a catalog test); the other two are now caught.
4. `invitations.company_ids` is an array without a database foreign key (validated by the API at invitation and acceptance).
5. Ownership transfer and other sensitive actions have no second-person approval.
6. No optimistic-concurrency check on company/organization edits (`row_version` exists but is not compared); the last save wins.
7. “Last activity” on the team list is the member’s latest audit event, not their last sign-in.
8. Removing a member does not delete their user account; they can be re-invited.
9. Test-isolation fragility found and fixed: the outbox test now drains the queue first (see security results).
10. Responsive layout is exercised only by axe at the default viewport; no phone-size or screen-reader review was done. No penetration test.

## 14. Features not completed

- Custom/organization-defined roles and a permission-configuration screen.
- Notification framework: preferences are stored; nothing is sent (`notifications_sent: false`).
- Audit **export** (the viewer exists with filters; export from `PHASE_3.md` is not built).
- Organization logo (no storage architecture yet) and security-related organization settings.
- Per-assignment permission definition in the wizard: step 3 sets assignment type and responsibility; the member’s permissions come from their organization role.
- Accountant detail page (the API endpoint exists; the list shows each accountant’s assigned companies).
- Editing one’s own professional profile from My Profile (the API allows it; the UI offers it only to roles with `member_profiles.manage`, on the Team page).
- Arabic/RTL for signed-in pages (known issue 2); the project skill for the Control Center (`PROJECT_SKILLS_PLAN.md` timing unchanged).
- Workspace builder and everything in the brief’s out-of-scope list (intentionally not started).

## 15. Manual configuration still required

- **SMTP** (`ARTIFY_MAILER_BACKEND=smtp` plus host settings) for invitation e-mail; without it invitations are created and the UI says nothing was sent. Local development prints the link in the API console.
- Plans and limits are configured in the Super Admin (Phase 2); nothing to configure in the Control Center.
- Production secrets, Redis rate limiter, Fernet key and hosting remain Phase 12 items. The Vercel preview deployment from Phase 1 was **not** redeployed for this phase.

## 16. Exact steps to run and test

```bash
make install && make hooks                       # once
make db-reset                                    # artify_dev: migrations 0001->0005 + seed
uv run --project services/api uvicorn artify_api.main:app --port 8000      # API
cd apps/web && ARTIFY_API_URL=http://localhost:8000/api/v1 pnpm dev        # http://localhost:3000
# sign up at /signup (use @example.com), copy the verification link from the API console, sign in, enrol TOTP
make check                                       # lint, types, OpenAPI drift, API tests, docs check, web tests, build
ARTIFY_TEST_REDIS_URL=redis://127.0.0.1:6379/0 make test-api     # also runs the Redis limiter test
E2E_CHROMIUM_PATH=/path/to/chromium make e2e     # Playwright + axe, includes controlcenter.spec.ts
make client                                      # after any API change
make openapi-breaking BASE=<ref>
```

Sandbox note: PostgreSQL and Redis stop when the sandbox restarts; restart them before tests (see `LOCAL_SETUP_GUIDE.md` §7–8).

## 17. Readiness for Phase 4

Ready, with the conditions in [`PHASE_4_INTEGRATION.md`](../firm-control-center/PHASE_4_INTEGRATION.md): every company already has a `company_configurations` owner record (backfilled), company-scoped RLS and permission patterns are in place and tested, no default structure exists anywhere, and the subscription page already lists the workspace-related limits as not yet metered. Before Phase 4 starts the owner should decide: (a) whether the Control Center must regain Arabic/RTL first, (b) whether custom roles are needed before workspace permissions, (c) whether a second approver for sensitive actions is required, and (d) the SMTP provider.
