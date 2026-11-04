# Phase 1 Completion Report — SaaS Foundation, Database and Authentication

Date: 2026-11-04 · Branch: `ccr-f7e172c5-lwnudw` · Decisions applied: D-1, D-3 (dev infra), D-8 (schema), D-9 (owner replied "Accept the recommendations and start Phase 1").

**Verdict: Phase 1 is delivered. Two passes were made:** the first built the foundation; a follow-up pass (see "Follow-up pass" below) closed most of the open items it left. The few items still open are listed in §8. Phase 2 has not been started and must not start until this report is reviewed.

Classification: **IMPLEMENTED** (exists and was verified by something that ran) · **IMPLEMENTED, NOT VERIFIED** (exists, nothing ran it) · **NOT IMPLEMENTED** · **DEFERRED** · **REQUIRES DECISION**.

## Follow-up pass (closing the §8 list from the first report)

Every item that was open at the end of the first pass, with what was done and the evidence. "Evidence" means something that ran.

| # | Open item | Outcome | Evidence |
|---|---|---|---|
| 1 | Run CI on GitHub | **Done**, and it earned its keep: run 1 all green; run 2 caught a gitleaks false positive (fixed); run 14 caught a lockfile mismatch that broke `web` and `e2e` (fixed, `make lockfiles` added). Final result: last line of this report | GitHub Actions runs 41816747468, 41819604051, 41821033425 |
| 1b | Dependency-scan step in CI | **Done** (`audit` job: `pip-audit`, `pnpm audit --prod`, `pnpm audit --audit-level high`) | job green in run 2 |
| 2 | Verify `docker-compose.yml`, write Dockerfiles | **Done.** `services/api/Dockerfile` (non-root, no dev dependencies, 234 MB); compose now has migrate → api, worker, dispatcher. Running the worker for real exposed a **missing `celery[redis]`/`packaging` dependency** (would have crashed in production) — fixed. `compose_smoke.py` proves sign-up → MFA → company → outbox → dispatcher → Redis → Celery worker, the Redis-backed rate limiter, JSON logs, and that no password reaches any log. **MinIO service remains unverified** (no reachable registry image here); it sits behind the `storage` profile | local run, and CI `docker` job green in run 2 |
| 3a | Pre-commit hooks | **Done** (6 local hooks; a planted private key is rejected) | `uvx pre-commit run --all-files` |
| 3b | Module boundaries | **Done** (import-linter: layered + purity contracts; a deliberate violation breaks both) | `make boundaries`, CI |
| 3c | Structured JSON logging | **Done** (correlation ids; database errors no longer expose bound parameters) | `test_json_log_format…`, `test_database_errors_do_not_expose_bound_parameters`, container logs |
| 4a | `AuthProvider` interface | **Done** (protocol + `LocalAuthProvider`; routes depend only on it; proven with a fake provider) | `test_routes_use_the_auth_provider_interface`, mypy conformance |
| 4b | Member role changes | **Done** with safety rules (only firm owners touch the owner role, last owner protected, no self-change, accountant-limit and reactivation guarded, assignments end when leaving accountant/staff roles; promotion demands MFA at once) | `test_followups.py` (4 tests) |
| 5a | Org/company switcher | **Done** (selection in the URL; switching org clears company; company isolation visible: unassigned staff see none) | `switcher.spec.ts` |
| 5b | Dark-mode check | **Done** (rendered and viewed; contrast checked by axe in dark scheme) | screenshots reviewed; `accessibility.spec.ts` |
| 5c | Keyboard / screen-reader audit | **Partly done.** Automated axe (WCAG 2.0–2.2 A/AA tags) on 7 public pages × light/dark, Arabic RTL, and the signed-in dashboard; keyboard order, visible focus, skip link, error association. It **found and I fixed 3 real defects** (links distinguished by colour only; language button name not containing its visible text). **No manual screen-reader or expert audit has been done** — automated tools find only part of real issues | `accessibility.spec.ts` |
| 6 | Native review of Arabic | **Not done — needs a human.** | — |
| 7 | Semantic OpenAPI breaking-change check | **Done** (rules + allow-list + 7 unit tests; run for real against the previously pushed contract: 0 breaking) and wired into CI | `test_openapi_breaking.py`, CI step |
| 8 | User-level auth events invisible to tenants | **Done** with an implementer-chosen rule, **open for your confirmation** (ADR-025) | `test_audit_visibility.py` (5 tests) |
| 9 | Real e-mail adapter | **Partly done.** SMTP adapter tested against a real SMTP server (links, failure handling, no content in logs); delivery is best-effort/in-process; provider undecided (D-3); no real mail provider was ever contacted | `test_smtp_*` |
| 10 | Uncaptured e2e failure | **Not explained.** Likely candidates examined: a leaked server from my own script (fixed), and a TOTP-window test assumption (fixed in the API suite, where it failed once). Traces are now retained on failure; see the repeatability result below | batch result |
| — | Also closed | `reconcile` CLI (counter drift), Idempotency-Key on invitations and devices, forgot/reset-password and accept-invitation pages (e-mailed links now resolve), `bootstrap-dev` and `migrate` commands, Dependabot config | tests / e2e |

**Things I caused that you should know about**
- Adding `.github/dependabot.yml` made Dependabot immediately open **5 pull requests** (#1–#5, all major version bumps of GitHub Actions) on your repository. I did not merge or close them; their CI runs were green. Decide whether to merge, close or keep Dependabot.
- A Dependabot `npm_and_yarn` update run failed on GitHub; I did not investigate it (it is Dependabot's job, not CI) — likely pnpm workspace handling, unconfirmed.

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

Local runs are from the sandbox (PostgreSQL 16, Redis 7, Docker 29 with registry mirrors). "CI" means GitHub Actions on a clean runner.

| Check | Command | Result |
|---|---|---|
| API tests (real PostgreSQL 16, database rebuilt from migrations) | `make test-api` with `ARTIFY_TEST_REDIS_URL` set | **83 passed** (run twice at the end). Without the Redis variable: 82 passed + 1 skipped |
| Lint / format / boundaries | `make lint` (ruff, ruff format, import-linter, eslint) | clean; 2 boundary contracts kept; a deliberate violation breaks both |
| Types | `make typecheck` (mypy `--strict`, 34 files; `tsc --noEmit`, 3 packages) | clean |
| OpenAPI | `make openapi-check`; generated client diff; breaking-change check vs the first pushed contract | up to date; 0 breaking |
| Lockfiles | `make lockfiles` (`uv lock --check`, `pnpm install --frozen-lockfile`) | consistent (added after CI caught a mismatch, see §4) |
| Web unit tests / build | `make web-test`, `make web-build` | 3 passed; build succeeded |
| Browser tests | `make e2e` (API + production web build + headless Chromium): 3 journeys, 18 axe scans (7 public pages × light/dark, Arabic RTL, signed-in dashboard in 3 modes), keyboard, error-association, org-switcher | **23 tests; 12 consecutive full runs all passed (12/12)**, plus several earlier green runs |
| Docker stack | `docker compose up` + `infrastructure/dev/compose_smoke.py` | passed locally and in CI: sign-up → MFA → company → outbox → dispatcher → Redis → Celery worker; Redis rate limiter; JSON logs; no password in any log |
| Pre-commit | `uvx pre-commit run --all-files` | 6 hooks pass; planted private key is rejected |
| Dependency audit | `pip-audit` (Python), `pnpm audit` | Python: none. JavaScript production: none. Dev: **3 moderate `minimatch` advisories remain** (lint tooling; patched major is incompatible with ESLint 9). CI fails on high/critical |
| RLS performance baseline | `infrastructure/dev/bench_rls.py` | §5 |
| Docs check | `python3 tools/check_docs.py` | OK (44 required files, 48 markdown files) |
| CI, run 1 (first push) | GitHub Actions | 5/5 jobs green |
| CI, run 2 (follow-up A) | GitHub Actions | 6/7 green; **gitleaks failed** (false positive in a test, fixed) |
| CI, run 14 (follow-up B) | GitHub Actions | 5/7 green; **`web` and `e2e` failed at `pnpm install --frozen-lockfile`** (manifest/lockfile mismatch I introduced; fixed). gitleaks, API, Docker, audit, docs green |
| CI after the lockfile fix | GitHub Actions | see the last line of this report |

**Mutation checks (to prove tests can fail):** (1) widened the `companies` RLS policy → 2 API isolation tests failed; (2) opened the `organizations` policy to everyone → 4 database tests failed; (3) planted import violations → both import-linter contracts broke; (4) planted a private key → the pre-commit hook rejected it. All reverted.

**Not executed:** the MinIO service (no reachable registry image), any load/soak test, any manual screen-reader or expert accessibility audit, a local gitleaks run (binary download blocked by the permission policy; CI runs it), a run against a real mail provider.

## 3. Requirement-by-requirement status (against `PHASE_1.md`)

### Functional scope
| Requirement | Status | Notes |
|---|---|---|
| Sign-up of an Organization (firm / independent), e-mail verification, login, logout, password reset, TOTP MFA, session list | IMPLEMENTED | E-mail is console/memory only — no real delivery |
| Device list | IMPLEMENTED (minimal) | `POST/GET/DELETE /devices`, counted by `limit.devices`; full device management is Phase 5 |
| Organization members and roles; invitations | IMPLEMENTED | Status and role changes with safety rules |
| Platform-admin bootstrap with mandatory MFA | IMPLEMENTED | `artify-api create-platform-admin` |
| Companies: minimal create/read | IMPLEMENTED | Plus update, archive, assignments, company-side invitations |
| Entitlement catalog + service + seeded plan + counters | IMPLEMENTED | 17 keys defined; only 4 are enforceable now (`limit.companies/users/accountants/devices`) |
| Audit events for auth, membership, company events | IMPLEMENTED | User-level security events are visible to each tenant the user belongs to (ADR-025, rule awaiting your confirmation) |
| Web shell: login, MFA, reset/accept-invitation pages, i18n en + ar, light/dark tokens | IMPLEMENTED | Dark mode rendered, viewed, and axe-checked |
| Web shell: org/company switcher | IMPLEMENTED | Selection lives in the URL; verified by `switcher.spec.ts` |
| Arabic scaffolding | IMPLEMENTED, **needs native review** | First-pass translation; RTL layout and number order verified by an automated geometry assertion |

### Technical implementation
| Requirement | Status | Notes |
|---|---|---|
| Pin versions (ADR-019) | IMPLEMENTED | `uv.lock`, `pnpm-lock.yaml`, versions in `TECHNOLOGY_STACK.md` |
| pnpm + uv workspaces, task runner | IMPLEMENTED | `Makefile` |
| Pre-commit hooks | IMPLEMENTED | 6 local hooks |
| Docker Compose (api, worker, postgres, redis, minio) | IMPLEMENTED; **MinIO NOT VERIFIED** | Everything except MinIO is verified locally and in CI; MinIO sits behind the `storage` profile |
| FastAPI factory, settings, error handling, request context | IMPLEMENTED | |
| Structured (JSON) logging | IMPLEMENTED | Correlation ids; text format in local, JSON elsewhere |
| Alembic, UUIDv7 | IMPLEMENTED | Forward-only migrations |
| Idempotency | IMPLEMENTED (key creating routes) | Company, invitation and device creation; not a global middleware |
| Outbox + dispatcher skeleton | IMPLEMENTED | Dispatch/backoff tested with a fake sender |
| Celery wiring with tenant-context propagation | IMPLEMENTED | Context helper tested; real worker verified in the container smoke test |
| Auth module behind an `AuthProvider` interface | IMPLEMENTED | Protocol + `LocalAuthProvider`; routes depend only on it |
| Argon2id, rotating refresh + reuse detection, TOTP, rate limiting, realm separation | IMPLEMENTED | Rate limiting also verified against a real Redis |
| Context resolution, policy engine, permission catalog, role seeding | IMPLEMENTED | |
| RLS framework: roles, helpers, immutability triggers, catalog check | IMPLEMENTED | Catalog check is a pytest, not yet a CI gate that has run |
| `EntitlementService` with locked counters + reconciliation | IMPLEMENTED | Reconciliation exists as a function; no scheduled job |
| OpenAPI → TS client generation | IMPLEMENTED | Drift check plus a semantic breaking-change check with an allow-list |
| `apps/web` route groups, `packages/ui` | IMPLEMENTED | |
| CI (lint, types, boundaries, tests, migrations, isolation, scans, docs, Docker, e2e) | IMPLEMENTED and run on GitHub | 7 jobs; see §2 for the run history |
| Module-boundary enforcement (import-linter) | IMPLEMENTED | 2 contracts, in CI and pre-commit |

### API requirements
Routes follow the architecture documents rather than the loose list in `PHASE_1.md`: there is **no `POST /orgs`** (organizations are created by sign-up); companies are `POST/GET /orgs/{org_id}/companies` and `GET/PATCH /companies/{company_id}` (with `X-Organization-Id`). Every route declares its authorization (enforced by a test); mutating company creation accepts `Idempotency-Key`.

### Security requirements
Implemented and tested: realm-separated tokens, MFA enforcement for privileged roles, session revocation, fail-closed RLS, separate DB roles, rate limiting, security headers (API and web), append-only audit. Not implemented: see `THREAT_MODEL_AUTH.md` residual risks (breached-password check, trusted-proxy handling, nonce CSP, device-bound refresh tokens).

### Acceptance criteria
| # | Criterion | Status |
|---|---|---|
| 1 | `docker compose up` yields a working stack from a clean clone | **Met** (local and CI): `compose_smoke.py` exercises the whole chain. MinIO excluded (see above) |
| 2 | New org can sign up, verify, enable MFA, create a company, invite a second user; audited | **Met via API tests.** In the browser: sign-up, verify, MFA, dashboard. Creating a company and inviting a user have **no UI** (Phase 3) |
| 3 | Isolation suite passes and fails closed on empty context | **Met** (TS-ISO-01, 02, 04, 05, 08, 09, 10 executed; see `TEST_SCENARIOS.md`) |
| 4 | Company beyond `limit.companies` fails via the raw API; concurrency test passes | **Met** (8 parallel creators, one slot left → exactly 1 success) |
| 5 | OpenAPI → TS client generation in CI; breaking-change check active | **Met:** generation, drift check and breaking-change check run in CI |
| 6 | CI is green; report lists every command | **Met once the final run is green** (see the last line); commands are in §2. CI has failed twice on real defects, both fixed |

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

Found in the follow-up pass:

10. Missing `celery[redis]`/`packaging` dependency — the worker crashed in a production-style image; invisible in dev because `pytest` pulled `packaging` in. Fixed; the container smoke test now guards it.
11. A TOTP replay test depended on the wall clock staying in one 30-second window → failed once in four runs. Made deterministic (uses the recorded step).
12. Three real accessibility defects found by axe: links distinguished by colour alone; the language button's accessible name did not contain its visible text; (and a redundant group label that made locators ambiguous). Fixed.
13. gitleaks (CI) flagged a fake key literal in a test → removed (generated at test time) + documented `.gitleaksignore` entry for the historical commit.
14. CI `web` and `e2e` jobs failed: I pinned an exact version in `package.json` after `pnpm add` had written a caret range, so `--frozen-lockfile` failed; local runs used a non-frozen install and hid it. Fixed; `make lockfiles` now checks it.
15. A language switch refetched all dashboard data (effect depended on translated strings) → uses `useEffectEvent`.

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

## 8. Still open after the follow-up pass

1. **Native review of the Arabic strings** — needs a human with accounting vocabulary.
2. **Manual screen-reader / expert accessibility audit** — only automated axe and keyboard checks were done.
3. **MinIO service** unverified (no reachable image); not needed until Phase 6.
4. **Real e-mail provider** (D-3) and **durable** delivery through the outbox (Phase 3). The SMTP adapter is tested only against a local SMTP server.
5. **Branch protection** making the CI checks *required* (a repository setting).
6. **Dependabot**: 5 pull requests it opened (#1–#5) await your decision; one Dependabot `npm_and_yarn` update run failed and was not investigated.
7. **Local gitleaks run** was not possible; CI covers it.
8. **Unexplained single e2e failure** from the first pass: not reproduced in 12 + several further runs; traces are retained if it recurs.
9. Confirm the **audit attribution rule** (ADR-025).
10. Residual security items in `THREAT_MODEL_AUTH.md` (breached-password check, trusted-proxy IP handling, nonce CSP, key rotation, device-bound refresh tokens).

## 9. Risks
- **CI has caught two defects my local runs missed** (a lockfile mismatch and a secret-scanner finding). It is the most valuable safety net here; making its checks *required* (branch protection) is still a repository setting to configure.
- **Security controls are tested on one database cluster**: PostgreSQL 16 as superuser owner. A non-superuser owner role (production) needs `BYPASSRLS` or explicit owner policies for `seed`/bootstrap; documented in ADR-023 but not exercised.
- **HS256 shared secrets, no key rotation (`kid`)**.
- **Strict refresh-reuse policy** may sign out legitimate clients after network retries.
- **Arabic accounting terminology** needs a domain reviewer before any customer sees it.
- **Automated accessibility checks are partial** by nature; a manual audit is still required.
- **Best-effort e-mail** can lose queued messages on a crash until the outbox-based delivery exists.
- **Policy performance** was measured for the `companies` table only; every future company-scoped table must follow ADR-024 and be re-benchmarked at realistic row counts (documents will be orders of magnitude larger).

## 10. Docs check
`python3 tools/check_docs.py` was re-run after this report was written: `OK: 44 required files present; 48 markdown files checked.` (exit 0). It checks structure, links, anchors and ID references only.

## 11. Decisions requiring approval
| ID | Question | Needed by |
|---|---|---|
| D-2 | Billing provider (or confirm manual invoicing first) | Phase 2 |
| — | Confirm ADR-025 (user-level security events visible to each tenant the user belongs to) | Now |
| — | What to do with the 5 Dependabot PRs and whether to enable branch protection | Now |
| D-7, D-5, D-6, D-10, D-4, D-11 | unchanged from `DECISIONS.md` | see roadmap |

## 12. Instructions for beginning Phase 2
Do not begin until this report is approved. Recommended order: (1) confirm the final CI run is green (last line below); (2) decide on the Dependabot PRs and ADR-025; (3) answer D-2; (4) say "Start Phase 2". Phase 2 then builds the Super Admin on the existing platform realm: plan/version/entitlement editing, subscriptions and usage views, support-access grant workflow with tenant approval and the grant-scoped RLS policies (TS-ISO-07), platform audit viewer — see `PHASE_2.md`.
