# Phase 1 Test Report

Evidence for what is claimed in [`PHASE_1_COMPLETION_REPORT.md`](PHASE_1_COMPLETION_REPORT.md). Everything in the "Executed" tables was run in this repository's sandbox (PostgreSQL 16, Redis 7, Chromium) on 2026-11-04 after the Phase 1 gap pass; nothing here is copied from documentation. Test data is synthetic (`@example.com` users, invented firm names); no financial data exists.

## 1. Executed results

| Suite | Command | Result |
|---|---|---|
| API + database (real PostgreSQL, database rebuilt from migrations 0001→0003) | `ARTIFY_TEST_REDIS_URL=redis://127.0.0.1:6399/0 make check` (pytest stage) | **92 passed, 0 skipped** (without the Redis variable: 91 passed + 1 skipped — the Redis limiter test) |
| Lint / formatting / module boundaries | ruff, ruff format, eslint, import-linter | clean; 2 contracts kept |
| Static types | mypy `--strict` (34 files), `tsc --noEmit` (3 packages) | clean |
| API contract | `export_openapi.py --check`; generated TS client | up to date (regenerated for the new endpoints) |
| Web unit | vitest | 6 passed |
| Web production build | `next build` | succeeded (14 routes) |
| Browser end-to-end | `E2E_CHROMIUM_PATH=/opt/pw-browsers/chromium make e2e` | **24 passed** (journeys, 18 axe scans, keyboard, error association, switcher, profile + revoked session) |
| Native startup check | `make db-reset DB=artify_dev`, uvicorn on :8077 | `/health` and `/health/ready` → `{"status":"ok"}`, `/docs` 200, protected route without token → 401 |

Earlier results still valid and not re-run in this pass: Docker Compose smoke test (local and CI), pre-commit hooks, dependency audits, RLS benchmark, CI run 15 on GitHub (all 7 jobs green) and CI run 18. See the completion report §2.

## 2. API tests per file (92 test functions)

| File | Tests | Covers |
|---|---:|---|
| `test_auth.py` | 16 | sign-up enumeration safety, unverified e-mail, password policy and Argon2id storage, MFA requirement for privileged roles, TOTP replay and recovery codes, refresh rotation + reuse detection, logout revocation, session list/revoke, password reset, rate limiting, disabled user, platform MFA and roles |
| `test_isolation_api.py` | 8 | cross-tenant 404 + audit, unauthenticated requests, accountant sees only assigned companies, company employee confined, realm separation, every route declares its authorization, membership status changes take effect immediately, last firm owner protected |
| `test_database_rls.py` | 12 | service roles lack BYPASSRLS, empty context sees nothing, org context isolation, composite FK, append-only audit, platform role cannot read tenant data, every table classified + RLS forced, tenant columns present, no float money columns, definer functions, policy planned once per query |
| `test_followups.py` | 12 | role changes (limits, assignment cleanup, firm-owner protection), reactivation limit, idempotent invitations/devices, reconcile CLI, JSON logs, no bound parameters in DB errors, SMTP delivery and failure handling, production settings, auth-provider seam |
| `test_entitlements.py` | 10 | company/user/accountant/device limits through the raw API, **concurrent creation cannot oversubscribe**, override precedence/expiry, plan change without deploy, suspended subscription/organization behaviour, counter reconciliation, unenforceable keys, **policy matrix** |
| `test_plumbing.py` | 11 | idempotency, duplicate code conflict, problem+json, commit-before-response, audit redaction/tenancy, outbox + backoff, worker single-company context, invitation edge cases |
| `test_audit_visibility.py` | 5 | tenant-visible user events, no cross-tenant leak, forged events rejected |
| `test_openapi_breaking.py` | 7 | breaking-change detector |
| `test_ratelimit.py` | 2 | memory and Redis limiters |
| `test_phase1_gap_pass.py` | 9 | org profile update + audit, owner-only update, cross-tenant update 404, org list, company member management and escalation guards, cross-tenant member update 404, role/permission catalog, **expired session rejected (access and refresh)**, **no self-escalation** |

## 3. Coverage of the brief's test requirements (section 14)

| Requirement | Evidence | Status |
|---|---|---|
| Successful login; invalid credentials | `test_auth.py` login tests; e2e journeys and wrong-credentials test | IMPLEMENTED AND TESTED |
| Expired sessions | `test_expired_session_is_rejected_for_access_and_refresh` (session `expires_at` in the past → 401 on access and refresh); access-token `exp` is checked by the JWT decoder (covered indirectly, no dedicated clock-manipulation test) | IMPLEMENTED AND TESTED (session); token `exp` BY DESIGN ONLY |
| Logout and session revocation; unauthorized access | `test_logout_revokes_the_session_immediately`, `test_session_list_and_remote_revocation`, `test_unauthenticated_requests_are_rejected`; e2e revoked-session test | IMPLEMENTED AND TESTED |
| Org A cannot access Org B's data | `test_cross_tenant_access_is_denied_with_404_and_audited`, `test_org_context_only_sees_own_org…`, new cross-tenant update tests | IMPLEMENTED AND TESTED |
| User cannot access an unassigned company; manipulated company IDs rejected | `test_accountant_sees_only_assigned_companies`, `test_company_employee_is_confined_to_own_company`, 404 probes with foreign ids | IMPLEMENTED AND TESTED |
| Users cannot assign themselves privileged roles; unauthorized membership changes rejected | `test_users_cannot_escalate_their_own_privileges`, `test_firm_owner_role_is_protected`, company-member guard test | IMPLEMENTED AND TESTED |
| Owner / accountant / company employee / Super Admin permissions | `test_policy_matrix`, `test_accountant_sees_only…`, `test_company_employee_is_confined…`, `test_platform_roles_are_enforced`, `test_platform_role_cannot_read_tenant_business_data` | IMPLEMENTED AND TESTED |
| Required relationships, FKs, unique constraints | `test_composite_fk_blocks_cross_company_reference`, duplicate-code conflict, invitation edge cases | IMPLEMENTED AND TESTED (unique constraints on memberships/assignments only exercised indirectly) |
| Migration up from a clean database | rebuilt on every test session; `make db-reset` | IMPLEMENTED AND TESTED |
| Migration **rollback** | none — migrations are forward-only by decision (ADR-020) | NOT IMPLEMENTED (deliberate deviation) |
| Frontend: login flow, protected routes, session expiry, org/company selection | e2e `smoke` (sign-up→verify→MFA→dashboard→re-login; anonymous `/dashboard` → `/login`; `/platform` → `/platform/login`), `switcher` (two organizations, company selection), revoked session → `/login` | IMPLEMENTED AND TESTED |
| Frontend component/unit tests | only 6 vitest cases (error mapping, font stacks) | PARTIAL — behaviour is tested through Playwright, not component tests |

## 4. Mutation checks (tests can fail)

| Mutation | Result |
|---|---|
| Disabled the company-role escalation guard (`_guard_company_role_grant` returns immediately) | `test_company_member_management_and_escalation_guards` failed; reverted |
| Earlier phase: widened `companies` RLS policy; opened `organizations` policy; planted import violations and a private key | isolation, database, import-linter and pre-commit checks failed as intended; reverted |

## 5. Defects found while writing these tests

- The first draft of the new escalation test passed for the wrong reason: an invited `practice_admin` is refused (403, `mfa_required`) until MFA is completed, so the "cannot promote" assertions were satisfied by the MFA gate, not by the role rule. The test now enrolls MFA first and asserts both the refusal before MFA and the specific rule outcomes (422 for self role change, 403 for granting `firm_owner`).
- An accountant assigning themselves to a company outside their scope returns **404**, not 403 (no existence leak); the test documents this intended behaviour.
- A missing-browser failure in `make e2e` was environmental (Playwright 1.64 expected a Chromium build not installed here); solved with `E2E_CHROMIUM_PATH`, not by changing code.

## 6. Not tested / known gaps

Load or soak behaviour; manual screen-reader and expert accessibility audit; real mail provider; MinIO; non-superuser migration owner; access-token `exp` with a manipulated clock; browser matrix beyond Chromium; mobile viewport screenshots (responsive layout is verified only by axe at default size and by the owner's phone screenshot of one page).

## 7. Correction (found in Phase 2)

`test_every_route_declares_its_authorization` in `test_isolation_api.py` inspected zero routes with the pinned FastAPI (`app.routes` returns wrapper objects) and therefore passed without checking anything. Fixed in Phase 2 (see `PHASE_2_TEST_REPORT.md` §4, item 1). Any claim in §3 that depends on it was true by inspection of the code but untested until then.
