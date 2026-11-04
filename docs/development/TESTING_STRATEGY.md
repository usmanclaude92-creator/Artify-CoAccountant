# Testing Strategy

Phase 0 executes no application tests because no application code exists. The only automated check currently in the repository is `tools/check_docs.py` (documentation consistency; see below). This document defines the strategy later phases implement.

## 1. Test data policy

Synthetic data only: generated documents (PDF/image renderings of fabricated invoices/statements in English and Arabic), fabricated companies and people. No production credentials, no real customer documents, no scraped data. Any consented real sample requires written approval, de-identification, and storage outside the repository.

## 2. Layers

| Layer | Scope | Tools (planned) |
|---|---|---|
| Unit | Pure logic: Decimal math, VAT, validators, mapping language, naming rules, path sanitization | pytest, hypothesis (property tests for rounding and balance) |
| Database | Migrations, constraints, triggers, RLS policies | pytest + ephemeral PostgreSQL; `pg_policies` coverage check |
| API | Endpoint contracts, authorization matrix, entitlement errors | pytest + httpx; OpenAPI diff |
| Authorization matrix | Every route × every role × inside/outside scope | Generated from the OpenAPI route table; fails when a route has no declared permission |
| Integration | Worker pipelines, storage, outbox, sync protocol | docker-compose stack (postgres, redis, minio) |
| Contract | TS and Dart clients generated from OpenAPI compile and pass smoke tests | CI |
| Frontend | Component tests, accessibility (axe), visual regression | Vitest/Testing Library, Playwright |
| E2E | Critical journeys across web + API | Playwright |
| Desktop | Sync queue, mirror, watcher, long paths, offline replay | Tauri test harness + Windows CI runners |
| Mobile | Config-driven category rendering, upload queue, scanning flow | flutter test, integration tests |
| Security | Isolation suite, file-parser fuzzing, dependency/secret scanning, pen test | Phase 1 (isolation), Phase 12 (pen test) |
| Performance | RLS overhead, large Company trees, bulk imports, export of large entry sets | k6/locust + synthetic data |
| AI evaluation | Extraction accuracy per kind and language | Evaluation harness (Phase 7) |

## 3. Release gates

A phase is complete only when its acceptance criteria are met **by executed tests**, the isolation suite passes, migrations apply on a clean DB and an upgrade path, linters/type checks are clean, and the phase completion report lists exactly what was run (commands, results). Documentation of a test is never reported as a passed test.

## 4. Named scenario sets

Scenario definitions are in [`TEST_SCENARIOS.md`](TEST_SCENARIOS.md): `TS-ISO-*` isolation, `TS-WS-*` workspace, `TS-ACC-*` accounting, `TS-XLS-*` Excel, `TS-ENT-*` entitlements, `TS-SYNC-*` sync, `TS-SEC-*` security. Each phase document lists which scenario IDs it must make executable.

## 5. Docs check (exists now)

`python3 tools/check_docs.py` verifies: required Phase 0 files exist; relative Markdown links and anchors resolve; every `PHASE_N.md` contains the eleven mandatory sections; scenario IDs referenced in documents exist in `TEST_SCENARIOS.md`; no banned float-money patterns appear in design SQL snippets. It checks structure and links only — it cannot verify that the architecture is *correct*.
