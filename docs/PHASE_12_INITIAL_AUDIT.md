# Phase 12 Initial Audit

Written at the start of Phase 12, before the Phase 12 code, from three sources: the nine earlier phase reports, a read of the code paths named below, and the test suites that were **executed** in this environment (listed in §2). It is a ranking of what stands between the current system and a commercial release. It is not a penetration test and not a claim that the system is secure.

Severity scale: **Critical** = could expose or corrupt customer data, or breaks a non-negotiable invariant; **High** = blocks a pilot or the brief's acceptance criteria; **Medium** = should be fixed before general availability; **Low** = polish.

## 1. Result in one paragraph

No Critical defect was found in the backend, the database isolation layer or the dependency scans. The High items are all of one kind: **things that were never built or never run against real infrastructure** (clients are not wired to a server change feed, no resumable byte-range uploads, no per-tenant encryption, no external penetration test, no restore in a production-like environment, no Windows/Android/iOS binary ever built here). Nothing in this list was "fixed" by documentation; where Phase 12 added code it says so in the right-hand column.

## 2. What was executed (this environment, synthetic data only)

| Suite | Result | Notes |
|---|---|---|
| API (pytest, PostgreSQL 16 rebuilt from migrations) | 697 passed, 2 skipped, 0 failed at the Phase 12 head ([completion report §18](development/PHASE_12_COMPLETION_REPORT.md)) | At the start of Phase 12 (Phase 11 head) the last full run was 689 passed / 1 skipped (Phase 10); Phase 11 and 12 added tests. |
| Browser end-to-end (Playwright/Chromium, web + API) | 33 passed (Phase 10 run) | Not re-run for Phase 12: no web UI changed. |
| Desktop UI (Vitest) / Rust core (cargo) | 100 / 112 passed (Phase 10 run) | Tauri shell crate cannot be compiled here (GTK libraries missing). |
| Web unit tests | 6 passed | |
| Mobile (Flutter `flutter test`) | 94 passed (Phase 11 run) | No device or emulator available. |
| Python dependency scan (`pip-audit`) / JS scan (`pnpm audit --prod`) | no known vulnerabilities (Phase 10/11 runs) | Scans a snapshot of public advisories; says nothing about unknown ones. |
| Rust dependency scan (`cargo audit`) | **not run** — tool not installable here | |

## 3. Findings

### Critical

None found. Evidence: forced RLS with a non-`BYPASSRLS` application role, the two-organization isolation tests, composite tenant foreign keys and immutability triggers are exercised by the API suite on every run. This is evidence for the cases tested, not a proof.

### High

| # | Finding | Status after Phase 12 |
|---|---|---|
| H-1 | **Clients do not consume a server change feed.** Desktop and mobile refresh by polling individual lists; sync state shown in Daily Bookkeeping is *declared by the client* (`bk_sync_reports`). There was no way to ask "what changed since my checkpoint" or "is my copy still current". | **Partly fixed.** `GET /companies/{id}/sync/changes` and `POST /companies/{id}/sync/check` added and tested ([SYNCHRONIZATION_ARCHITECTURE](SYNCHRONIZATION_ARCHITECTURE.md)). The desktop and mobile apps are **not yet wired to them**. |
| H-2 | **No conflict resolution UI or conflict history.** Optimistic concurrency exists (`base_row_version` → HTTP 409 on documents, accounting, bookkeeping) and the new preflight reports `stale`, but there is no `sync_conflicts` table, no resolve screen and no audited resolution record. | **Open.** Documented in [CONFLICT_RESOLUTION_GUIDE](CONFLICT_RESOLUTION_GUIDE.md). |
| H-3 | **Uploads are not resumable at byte level.** The three-step protocol and idempotency keys make a retry safe, but an interrupted large file restarts from zero. | **Open.** |
| H-4 | **Per-tenant encryption keys (D-4) not implemented.** Storage relies on platform-managed encryption of the database volume and object store. A database or cloud administrator with infrastructure access can read data. | **Open, stated honestly** in [SECURITY_TEST_REPORT §6](SECURITY_TEST_REPORT.md). |
| H-5 | **No independent penetration test.** | **Open — prerequisite for any external pilot.** |
| H-6 | **Backups never restored outside a laptop-sized database.** | **Partly addressed.** `infrastructure/ops/restore_check.sh` added and run (121 tables identical after restore, 18 MB). No PITR, no production-scale or encrypted-backup test. See [BACKUP_AND_DISASTER_RECOVERY](BACKUP_AND_DISASTER_RECOVERY.md). |
| H-7 | **No Windows installer, Android package or iOS archive has ever been built or run on a device.** | **Open.** Release guides written, none of the steps executed. |
| H-8 | **Notifications are queued but never delivered** (the outbox worker only acknowledges events); no push on mobile. | **Open.** |
| H-9 | **No production infrastructure or hosting decision (D-3).** Only the Vercel + Supabase demo exists and it is not a production design. | **Open.** |

### Medium

| # | Finding | Status |
|---|---|---|
| M-1 | Audit log rows are protected by triggers but not hash-chained or anchored externally; a database owner can still rewrite history. | Open |
| M-2 | Rate limiting exists (Redis or in-memory) but its limits were never tuned under load. | Open |
| M-3 | No measured performance baseline existed. | **Fixed (baseline only):** `tests/test_p12_perf.py`, results in the completion report. Single process, one machine, 150 documents / 60 transactions — not a capacity statement. |
| M-4 | No restore-test script or documented RPO/RTO. | Script added; RPO/RTO are targets only. |
| M-5 | Billing has manual records only; no gateway (D-2), no renewal scheduler. | Open — nothing is simulated. |
| M-6 | The Control Center is English-only (ADR-040); Arabic strings need native review. | Open |
| M-7 | Platform-admin alert scan covers at most 500 organizations. | Open |
| M-8 | Monitoring is the existing `/platform/health`, request counters and JSON logs; no dashboards, no alert delivery, no tracing. | Documented in [PRODUCTION_DEPLOYMENT_GUIDE §7](PRODUCTION_DEPLOYMENT_GUIDE.md) |
| M-9 | Mobile has no certificate pinning, no jailbreak/root detection. | Open |
| M-10 | OCR verified only on synthetic documents; AI provider only against a stub; Oman tax pack unverified by a tax professional. | Open |

### Low

Placeholder app icon; `cargo-audit` unavailable; hosted demo not redeployed since Phase 10; documentation duplication between `docs/<area>/` and phase reports.

## 4. Decisions needed from the owner

D-3 (production hosting and data residency), D-4 (tenant keys), D-8 (retention values), D-11 (legal documents), D-2 (billing provider), signing certificate purchase (Windows), Apple/Google developer accounts. None can be decided by the code.
