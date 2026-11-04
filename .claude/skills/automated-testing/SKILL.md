---
name: automated-testing
description: Use when writing, running or reporting tests in the Artify repo — API/database tests against real PostgreSQL, web unit tests, Playwright end-to-end, the RLS benchmark and the docs check. Covers commands, fixtures, synthetic-data rules and honest reporting.
---

# Testing

## Commands (all local commands were executed in Phase 1)
| What | Command |
|---|---|
| Everything except browser e2e | `make check` |
| API tests (real PostgreSQL, rebuilds `artify_test`) | `make test-api` (`ARTIFY_TEST_SKIP_RESET=1` skips the rebuild for fast reruns) |
| Redis limiter test | set `ARTIFY_TEST_REDIS_URL=redis://localhost:6379/0`; otherwise it is **skipped**, report it as skipped |
| Browser e2e (API + built web + Chromium) | `make e2e` (set `E2E_CHROMIUM_PATH` if Chromium is pre-installed) |
| RLS performance baseline | `uv run --project services/api python infrastructure/dev/bench_rls.py <db>` |
| Docs consistency | `make check-docs` |

## Rules
- RLS and entitlement behaviour must be tested against real PostgreSQL; never mock the database for these.
- Synthetic data only: fabricated users/companies, `@example.com`. No real documents or credentials.
- Fixtures live in `services/api/tests/conftest.py` (`new_owner`, `invite_and_accept`, `make_company`, `set_plan_limit`). The autouse fixture resets the in-memory rate limiter and mailer per test.
- TOTP: a code can be used once per 30 s step; use `totp_now(secret, 1)` for the next step after enrollment.
- When changing a security control, add a **mutation check**: weaken the control temporarily and confirm a test fails (done for RLS in Phase 1 — see the completion report).
- Scenario IDs (`TS-*`) are defined in `docs/development/TEST_SCENARIOS.md`; reference them in test docstrings.

## Reporting
State exactly which commands ran and their results. A skipped test is not a passed test. Docs describing a test do not count as running it.
