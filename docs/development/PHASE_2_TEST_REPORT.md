# Phase 2 Test Report

Everything under "Executed" was run in this repository's sandbox (PostgreSQL 16, Redis 7, Chromium) on 2026-11-04 from the final state of the code. Test data is synthetic (`@example.com` users, invented organization names); no real customer or payment data exists. Phase 1 tests were **not** relaxed: they run as the regression suite (two were changed, see §4).

## 1. Executed results

| Suite | Command | Result |
|---|---|---|
| API + database (real PostgreSQL, database rebuilt from migrations 0001→0004) | `ARTIFY_TEST_REDIS_URL=… make check` (pytest stage) | **162 passed, 0 failed, 0 skipped** = 92 Phase 1 regression + 70 new cases (60 new test functions; one is parametrized into 12 role/endpoint cases) |
| Lint, format, import boundaries | ruff, ruff format, eslint, import-linter | clean; 2 contracts kept |
| Static types | mypy `--strict` (45 files), `tsc --noEmit` (3 packages) | clean |
| API contract | `export_openapi.py --check`; TypeScript client regenerated; `make openapi-breaking BASE=HEAD` | up to date; **1 breaking change, allow-listed with its reason** (`GET /platform/organizations` list → paginated envelope) |
| Web unit | vitest | 6 passed |
| Web production build | `next build` | succeeded; 34 routes including the 15 Super Admin modules |
| Browser end-to-end | `E2E_CHROMIUM_PATH=/opt/pw-browsers/chromium make e2e` | **29 passed** = 24 Phase 1 + 5 new (3 Super Admin journeys, axe on 15 pages × light and dark) |
| Docs | `python3 tools/check_docs.py` | see completion report §15 |
| Startup | `make db-reset DB=artify_dev`, uvicorn, `/health`, `/health/ready` | ok (Phase 1 evidence; unchanged startup path) |

**Not re-run in this phase:** Docker Compose smoke test, pre-commit hooks, dependency audits, RLS benchmark (Phase 1 evidence stands but Phase 2 did not re-verify it), GitHub CI for the Phase 2 commits (see the completion report for its state).

## 2. New API tests by file

| File | Functions (cases) | Covers |
|---|---:|---|
| `test_p2_authz.py` | 8 (19) | role catalog is least-privilege; customer tokens (owner, accountant, company employee) get 401 on Super Admin; platform token useless on customer routes; unenrolled administrators blocked; **12 role/endpoint denials, each audited as `authz.denied`**; each role's profile and permissions; allowed paths per role; **static check that every platform route declares a permission whose type matches its HTTP verb** |
| `test_p2_orgs.py` | 9 | list/filter/sort/paginate/search (incl. wildcard-literal input), independent-accountant filter, detail without business data, company overview metadata-only, **provisioning → owner accepts → MFA → signs in**, provisioning validation and permissions, suspend/reactivate (reason + typed slug; customer read-only while suspended; history; audit), append-only history, profile update, permission-aware global search |
| `test_p2_plans_subs.py` | 9 | plan validation and money-as-string; version immutability enforced by the database; compare; lifecycle; **plan assigned in Super Admin → customer API enforces the new limit (402) with no deploy**; downgrade below usage needs acknowledgement and preserves data; extend/suspend/reactivate/status matrix and customer restrictions; append-only history; overrides (precedence, expiry, audit); permission gating |
| `test_p2_billing.py` | 6 | nothing exists unless an administrator recorded it; invoice → payment → refund → adjustment lifecycle and exact balances; void rules; money validation; append-only payments/adjustments; **every billing mutation refused for roles without `billing.manage`** |
| `test_p2_admins.py` | 8 | invitation flow (single-use, MFA mandatory, Argon2id, no secrets); invitation rules; **only the owner manages administrators**; no self-escalation, no role above your own, typed confirmation; owner demotion/suspension; **suspension revokes sessions** (checked on session rows); activity history; password hash never exposed |
| `test_p2_support.py` | 7 | full lifecycle with customer approval and **double audit**; **grant scoping enforced by RLS as the platform DB role** (members/users only for the granted organization; stranger sees nothing; expiry evaluated by the database; tenant business tables stay closed); holder-only use and audited misuse; customer authorization rules (accountant 403, other tenant 404, cannot approve more than requested, stale request); sensitive level needs explicit confirmation; request validation and duplicates; suspended requester loses access |
| `test_p2_ops.py` | 12 | dashboard equals database counts and marks unmeasured items unavailable; section permissions; revenue only from recorded payments; alerts from real conditions; **usage accuracy, tenant isolation and equality with the customer's own entitlement endpoint**; usage history from real records; health (real checks, no overclaiming, stuck-queue detection); audit viewer filters/pagination/detail/append-only (platform role and schema owner); no secrets in audit; activity feed; settings validation, audit and effect on new sign-ups and invoice numbers |

Browser (`superadmin.spec.ts`): create a plan in the UI → assign it → the customer's API enforces exactly that limit → suspend with typed confirmation → audit trail shows the reason; invite a support administrator → accept → reduced menu → deep links and direct BFF calls refused (403) and recorded; a signed-in customer is turned away from `/platform`, a support request needs approval in the customer console, then members become visible, then the customer ends access; **axe (WCAG 2.0–2.2 A/AA) on 15 Super Admin pages in light and dark**.

## 3. Mutation checks (tests can fail)

| Mutation | Outcome |
|---|---|
| Widened the grant-scoped RLS policy on `organization_memberships` to `USING (true)` | `test_grant_scoping_is_enforced…` and Phase 1 `test_platform_role_cannot_read_tenant_business_data` failed; reverted |
| Dropped the pinned-version trigger on `plan_entitlements` | `test_versions_are_immutable_once_used…` failed; reverted |
| Lowered `record_payment`'s permission to `billing.read` | **first attempt passed** — my patch hit the wrong endpoint, and exposed that the permission test covered only some billing mutations. The test now covers all six; both a correctly aimed mutation of `record_payment` and one of `void_invoice` fail it; reverted |
| Gave `suspend_organization` a read permission | `…matches_its_verb` failed; reverted |
| Removed the permission declaration from `reactivate_organization` | `test_every_route_declares_its_authorization` and the verb test failed; reverted |

## 4. Defects the verification found (and what was done)

| # | Finding | Fix |
|---|---|---|
| 1 | **Phase 1's `test_every_route_declares_its_authorization` (TS-ISO-10) was vacuous.** `app.routes` yields wrapper objects in the pinned FastAPI, so the test inspected **zero** routes and always passed. Any undeclared route would have gone unnoticed | routes are now iterated from the mounted routers (116 routes) with a lower bound of 100, so it cannot pass by inspecting nothing; it passes on the real surface |
| 2 | Suspending an administrator revoked **zero** sessions: an `UPDATE … WHERE` needs rows visible through a SELECT policy and the platform role could see only its own sessions. The earlier "401 after suspension" was true only because a disabled account fails the status check | `platform_sessions_read` policy (platform sessions only); the test now counts live session rows |
| 3 | Settings accepted JSON `true` as `1` (pydantic coercion) | strict integer/string types |
| 4 | Dashboard raised on `datetime.utcnow()` (deprecated; tests treat warnings as errors) | timezone-aware datetimes |
| 5 | The platform invitation page was unreachable: the edge gate redirected `/platform/accept-invitation` to sign-in | the page is public alongside `/platform/login` |
| 6 | Danger buttons failed WCAG contrast in dark mode (axe) | `--color-on-danger` token |
| 7 | `outstanding` was `"0"` for draft/void invoices while every other amount had three decimals | typed `numeric(18,3)` throughout |
| 8 | Phase 1 `test_plan_change_takes_effect_without_deploy` edited a plan version in place — now (correctly) forbidden by the immutability trigger | rewritten to publish a new version and move the subscription, as Super Admin does |
| 9 | Phase 1 `test_platform_admin_must_enroll_mfa_then_sees_metadata_only` and `…cannot_read_tenant_business_data` asserted the old organization-list shape / "permission denied" on tables the platform role may now read through grants | updated to assert the stronger property (no business fields; zero rows without a grant; `password_hash` unreadable) |
| 10 | Content overflowed the viewport on wide tables | grid columns use `minmax(0, 1fr)` |
| 11 | Environment: Playwright 1.64 expects a Chromium build that is not installed in the sandbox; PostgreSQL must be restarted after a sandbox restart | `E2E_CHROMIUM_PATH`; documented in the setup guide |

## 5. Not tested / known gaps

- No component-level unit tests for the new React components (behaviour is exercised through Playwright); vitest still holds only 6 tests.
- No manual screen-reader or expert accessibility audit (axe finds only a subset); keyboard-only operation of the dialogs and the mobile navigation drawer is not covered by a dedicated test; no test at phone width.
- Search/alerts popovers are exercised by one journey only.
- Redis-down behaviour is visible on the health page (the e2e environment has no Redis) but is not asserted.
- No load or performance test of the Super Admin queries; the usage scan is capped at 500 organizations by design.
- Real e-mail delivery of invitations and support requests is untested (console/memory mailers only).
- Scheduling behaviours (trial expiry, renewals) do not exist, so they are not tested.
- Dual control, IP allow-listing and step-up authentication do not exist.
