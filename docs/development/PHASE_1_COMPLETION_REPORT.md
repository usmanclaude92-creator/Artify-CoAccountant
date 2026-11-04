# Phase 1 Completion Report — SaaS Foundation, Database and Authentication

Date: 2026-11-04 · Branch: `ccr-f7e172c5-lwnudw` · Decisions applied: D-1, D-3 (dev infra), D-8 (schema), D-9 (owner replied "Accept the recommendations and start Phase 1").

**Verdict: Phase 1 is substantially delivered but NOT fully complete.** The backend, database isolation, authentication, entitlement enforcement and a minimal web shell are implemented and were verified by executed tests. Several items in `PHASE_1.md` were not done or could not be verified (listed in §3 and §8). Phase 2 has not been started and must not start until this report is reviewed.

Classification: **IMPLEMENTED** (exists and was verified by something that ran) · **IMPLEMENTED, NOT VERIFIED** (exists, nothing ran it) · **NOT IMPLEMENTED** · **DEFERRED** · **REQUIRES DECISION**.

## 1. What was built

| Area | Location | Size |
|---|---|---|
| Database schema, RLS, grants, triggers, definer functions | `services/api/migrations/sql/0001_foundation.sql`, `0002_rls_and_grants.sql` (+ two thin Alembic revisions) | 894 lines SQL |
| API (FastAPI) | `services/api/src/artify_api/` | ~3,800 lines Python |
| API tests (real PostgreSQL) | `services/api/tests/` | 59 tests, ~1,400 lines |
| Generated contract + typed client | `packages/api-client/` (`openapi.json`, `src/schema.d.ts`) | 39 paths, 44 operations |
| Design tokens + primitives | `packages/ui/` | tokens (light/dark), Button, TextField, Alert, Panel |
| Web shell (Next.js 16) | `apps/web/` | sign-up, e-mail verify, login, MFA, dashboard, platform login/console, en + ar (RTL), BFF |
| Tooling | `Makefile`, `.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml`, `infrastructure/{database,dev}/` | |
| Skills | `.claude/skills/` (6) | architecture, multi-tenant-saas, api-development, security-audit, automated-testing, code-review |
| Docs | threat model, ADR-020…024 + D-outcomes, RLS performance rule, test-execution table | |

## 2. Commands actually executed, and their results

| Check | Command | Result |
|---|---|---|
| API tests (real PostgreSQL 16, database rebuilt from migrations) | `make test-api` (with `ARTIFY_TEST_REDIS_URL` set) | **59 passed**. Without the Redis variable: 58 passed, 1 skipped (the Redis limiter test) |
| Lint / format | `make lint` (ruff check, ruff format --check, eslint) | clean |
| Types | `make typecheck` (mypy `--strict` on 31 files; `tsc --noEmit` on 3 packages) | clean |
| OpenAPI contract drift | `make openapi-check`; regenerate client → no diff | up to date |
| Web unit tests | `make web-test` (vitest) | 3 passed |
| Web production build | `make web-build` (Next.js 16.4.5) | succeeded |
| Browser end-to-end | `make e2e` (API + production web build + headless Chromium) | 3 tests × 15 runs after the script fix: 14 consecutive passes preceded by **1 failure whose details I did not capture** (first run right after I fixed the process cleanup); not reproduced in 14 runs |
| Docs check | `python3 tools/check_docs.py` | OK (44 required files, 48 markdown files) |
| RLS performance baseline | `infrastructure/dev/bench_rls.py` (50,000 companies / 100 orgs, one local machine) | §5 |
| Dependency audit, Python | `pip-audit` on the 162 pinned packages | no known vulnerabilities |
| Dependency audit, JavaScript | `pnpm audit` | production: none. Dev toolchain: a critical + 2 moderate Vitest advisories were found and **fixed by upgrading** (vitest 4.1.12, eslint 9.39.5); **3 moderate `minimatch` advisories remain** (lint toolchain only; the patched major is incompatible with ESLint 9) |
| Secret scan | local `grep` for key/token patterns | no matches. The `gitleaks` CI job has **not run** |

**Mutation checks (to prove the tests can fail):** (1) widened the `companies` RLS policy → 2 API isolation tests failed (list endpoints leaked; direct reads were still blocked by the API-layer check, which shows the two layers are independent); (2) opened the `organizations` policy to everyone → 4 database tests failed. Both were reverted.

**Not executed:** `docker compose up` (the environment had no Docker daemon), the GitHub Actions workflow, any run against a real Celery broker, any load/soak test, any test of dark-mode appearance, any screen-reader or keyboard accessibility audit.

## 3. Requirement-by-requirement status (against `PHASE_1.md`)

### Functional scope
| Requirement | Status | Notes |
|---|---|---|
| Sign-up of an Organization (firm / independent), e-mail verification, login, logout, password reset, TOTP MFA, session list | IMPLEMENTED | E-mail is console/memory only — no real delivery |
| Device list | IMPLEMENTED (minimal) | `POST/GET/DELETE /devices`, counted by `limit.devices`; full device management is Phase 5 |
| Organization members and roles; invitations | IMPLEMENTED | Status changes only; **changing a member's role is not implemented** |
| Platform-admin bootstrap with mandatory MFA | IMPLEMENTED | `artify-api create-platform-admin` |
| Companies: minimal create/read | IMPLEMENTED | Plus update, archive, assignments, company-side invitations |
| Entitlement catalog + service + seeded plan + counters | IMPLEMENTED | 17 keys defined; only 4 are enforceable now (`limit.companies/users/accountants/devices`) |
| Audit events for auth, membership, company events | IMPLEMENTED, with a gap | User-level auth events (login, MFA) are stored without an organization and are **not visible to tenant admins** (Phase 3) |
| Web shell: login, MFA, i18n en + ar, light/dark tokens | IMPLEMENTED | Dark mode is defined in tokens but was never rendered or checked |
| Web shell: **org/company switcher skeleton** | NOT IMPLEMENTED | The dashboard shows the first organization only |
| Arabic scaffolding | IMPLEMENTED, **needs native review** | First-pass translation; RTL layout and number order verified by an automated geometry assertion |

### Technical implementation
| Requirement | Status | Notes |
|---|---|---|
| Pin versions (ADR-019) | IMPLEMENTED | `uv.lock`, `pnpm-lock.yaml`, versions in `TECHNOLOGY_STACK.md` |
| pnpm + uv workspaces, task runner | IMPLEMENTED | `Makefile` |
| Pre-commit hooks | NOT IMPLEMENTED | |
| Docker Compose (api, worker, postgres, redis, minio) | IMPLEMENTED, **NOT VERIFIED** | Infra services only; no API/worker Dockerfile; never started |
| FastAPI factory, settings, error handling, request context | IMPLEMENTED | |
| Structured (JSON) logging | NOT IMPLEMENTED | Standard logging only; correlation id is in responses and error logs |
| Alembic, UUIDv7 | IMPLEMENTED | Forward-only migrations |
| Idempotency | IMPLEMENTED (partial) | Helper applied to company creation only; not yet a global middleware |
| Outbox + dispatcher skeleton | IMPLEMENTED | Dispatch/backoff tested with a fake sender |
| Celery wiring with tenant-context propagation | IMPLEMENTED, **partly verified** | Context helper tested; no real broker/worker run |
| Auth module behind an `AuthProvider` interface | PARTIAL | `auth_service.py` implements D-1, but a formal interface was **not** extracted |
| Argon2id, rotating refresh + reuse detection, TOTP, rate limiting, realm separation | IMPLEMENTED | Rate limiting also verified against a real Redis |
| Context resolution, policy engine, permission catalog, role seeding | IMPLEMENTED | |
| RLS framework: roles, helpers, immutability triggers, catalog check | IMPLEMENTED | Catalog check is a pytest, not yet a CI gate that has run |
| `EntitlementService` with locked counters + reconciliation | IMPLEMENTED | Reconciliation exists as a function; no scheduled job |
| OpenAPI → TS client generation | IMPLEMENTED | Drift check only; **no semantic breaking-change check** |
| `apps/web` route groups, `packages/ui` | IMPLEMENTED | |
| CI (lint, types, tests, migrations, isolation, scans, docs) | WRITTEN, **NOT RUN** | `.github/workflows/ci.yml`; has no dependency-scan step |
| Module-boundary enforcement (import-linter) | NOT IMPLEMENTED | Flagged as an open risk in Phase 0 |

### API requirements
Routes follow the architecture documents rather than the loose list in `PHASE_1.md`: there is **no `POST /orgs`** (organizations are created by sign-up); companies are `POST/GET /orgs/{org_id}/companies` and `GET/PATCH /companies/{company_id}` (with `X-Organization-Id`). Every route declares its authorization (enforced by a test); mutating company creation accepts `Idempotency-Key`.

### Security requirements
Implemented and tested: realm-separated tokens, MFA enforcement for privileged roles, session revocation, fail-closed RLS, separate DB roles, rate limiting, security headers (API and web), append-only audit. Not implemented: see `THREAT_MODEL_AUTH.md` residual risks (breached-password check, trusted-proxy handling, nonce CSP, device-bound refresh tokens).

### Acceptance criteria
| # | Criterion | Status |
|---|---|---|
| 1 | `docker compose up` yields a working stack from a clean clone | **NOT VERIFIED.** The equivalent flow with locally installed PostgreSQL/Redis was run (`make db-reset`, `make check`, `make e2e`) |
| 2 | New org can sign up, verify, enable MFA, create a company, invite a second user; audited | **Met via API tests.** In the browser: sign-up, verify, MFA, dashboard. Creating a company and inviting a user have **no UI** (Phase 3) |
| 3 | Isolation suite passes and fails closed on empty context | **Met** (TS-ISO-01, 02, 04, 05, 08, 09, 10 executed; see `TEST_SCENARIOS.md`) |
| 4 | Company beyond `limit.companies` fails via the raw API; concurrency test passes | **Met** (8 parallel creators, one slot left → exactly 1 success) |
| 5 | OpenAPI → TS client generation in CI; breaking-change check active | **Not met:** generation and drift check run locally; CI never ran; no breaking-change check |
| 6 | CI is green; report lists every command | **Not met** for CI (never ran). Commands are listed in §2 |

## 4. Defects the verification found (and what was done)
1. Schema `CHECK` on `permissions.scope` rejected the `member` scope → caught by the first seed run; schema fixed.
2. Malformed development Fernet key → caught by the first MFA call; settings now validate the key at startup.
3. Test-client address was not a valid IP for the `inet` audit column → request metadata now keeps only well-formed IPs.
4. psycopg treated `%` in SQL files as placeholders → migration runner escapes it.
5. Untyped `NULL` parameters in a refresh query → explicit casts.
6. **RLS performance defect:** scoped listing was 76× slower than firm-wide (65 ms vs 0.85 ms) because policies re-parsed the company list per row → policies now inline a once-per-query pattern (1.2 ms); guarded by a plan-shape test (ADR-024).
7. **E2E harness leaked servers** (cleanup killed the wrapper, not the child), so one run silently hit a stale API → script now uses process groups and refuses to start if ports are busy.
8. User-level auth events invisible to tenant admins → **not fixed**, recorded as a known limitation.
9. Dev-toolchain vulnerabilities (Vitest critical) → fixed by upgrade.

## 5. RLS performance baseline (indicative, one machine, one run each)

| Scenario (50,000 companies, 100 orgs) | Median query time |
|---|---|
| No RLS, explicit `WHERE` | 0.76 ms |
| RLS, firm-wide scope (500 rows) | 0.82 ms |
| RLS, 300 assigned companies (300 rows) | 1.19 ms |
| RLS, point lookup by id | 0.28 ms |

Before the fix the 300-company case measured 65 ms. This is a baseline, not a performance guarantee; it covers one table with one access pattern.

## 6. Dependencies installed
Python (locked in `uv.lock`): fastapi, uvicorn, pydantic, pydantic-settings, sqlalchemy, alembic, psycopg, argon2-cffi, pyjwt, pyotp, cryptography, redis, celery, uuid-utils; dev: pytest, httpx, ruff, mypy. JavaScript (locked in `pnpm-lock.yaml`): next, react, react-dom, tailwindcss, @tailwindcss/postcss, server-only, openapi-fetch, openapi-typescript, typescript, eslint, eslint-config-next, vitest, @playwright/test and type packages. System: PostgreSQL 16 and Redis 7 were already present in the sandbox and were used as-is. `pip-audit` was run via `uvx` and is not a project dependency.

## 7. Files
92 tracked files at the time of writing (Phase 0 contributed 43). New in Phase 1: the API service, migrations, tests, generated client, UI/web packages, tooling, six skills, `THREAT_MODEL_AUTH.md`, this report. Modified: README, CLAUDE.md, DECISIONS, TECHNOLOGY_STACK, ISOLATION_AND_RLS, ENTITY_CATALOG, TEST_SCENARIOS, PROJECT_SKILLS_PLAN, roadmap, PHASE_1, docs index, `tools/check_docs.py`, `.gitignore`.

## 8. Not done / outstanding for Phase 1 (do these or consciously defer)
1. Run CI on GitHub and fix whatever it finds (workflow unrun); add a dependency-scan step.
2. Verify `docker-compose.yml`; write API/worker Dockerfiles.
3. Pre-commit hooks; import-linter module boundaries; JSON structured logging.
4. Extract a real `AuthProvider` interface; implement member role changes.
5. Org/company switcher in the web shell; dark-mode visual check; keyboard and screen-reader audit.
6. Native review of Arabic strings.
7. Semantic OpenAPI breaking-change check.
8. Make user-level auth events visible to tenant admins (decide attribution rule).
9. Real e-mail delivery adapter (needs D-3 hosting decision).
10. Investigate the single uncaptured e2e failure if it recurs (traces are now retained on failure).

## 9. Risks
- **Unrun CI** is the largest process risk: nothing yet proves the project builds from a clean clone on a neutral machine.
- **Security controls are tested on one database cluster**: PostgreSQL 16 as superuser owner. A non-superuser owner role (production) needs `BYPASSRLS` or explicit owner policies for `seed`/bootstrap; documented in ADR-023 but not exercised.
- **HS256 shared secrets, no key rotation (`kid`)**.
- **Strict refresh-reuse policy** may sign out legitimate clients after network retries.
- **Arabic accounting terminology** needs a domain reviewer before any customer sees it.
- **Policy performance** was measured for the `companies` table only; every future company-scoped table must follow ADR-024 and be re-benchmarked at realistic row counts (documents will be orders of magnitude larger).

## 10. Docs check
`python3 tools/check_docs.py` was re-run after this report was written: `OK: 44 required files present; 48 markdown files checked.` (exit 0). It checks structure, links, anchors and ID references only.

## 11. Decisions requiring approval
| ID | Question | Needed by |
|---|---|---|
| D-2 | Billing provider (or confirm manual invoicing first) | Phase 2 |
| — | Org-level audit attribution of user-level auth events (see §8.8) | Phase 3 |
| — | Whether Phase 2 may begin while §8 items 1–3 (CI, compose, hooks) are still open, or they should be closed first (recommended: close CI first) | Now |
| D-7, D-5, D-6, D-10, D-4, D-11 | unchanged from `DECISIONS.md` | see roadmap |

## 12. Instructions for beginning Phase 2
Do not begin until this report is approved. Recommended order: (1) push and let CI run, fix findings; (2) approve or amend §8; (3) answer D-2; (4) say "Start Phase 2". Phase 2 then builds the Super Admin on the existing platform realm: plan/version/entitlement editing, subscriptions and usage views, support-access grant workflow with tenant approval and the grant-scoped RLS policies (TS-ISO-07), platform audit viewer — see `PHASE_2.md`.
