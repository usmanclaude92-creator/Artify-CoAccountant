# Phase 12 Initial Audit

Written at the start of Phase 12, before the Phase 12 code, from three sources: the nine earlier phase reports, a read of the code paths named below, and the test suites that were **executed** in this environment (listed in §2). It is a ranking of what stands between the current system and a commercial release. It is not a penetration test and not a claim that the system is secure.

Severity scale: **Critical** = could expose or corrupt customer data, or breaks a non-negotiable invariant; **High** = blocks a pilot or the brief's acceptance criteria; **Medium** = should be fixed before general availability; **Low** = polish.

## 1. Result in one paragraph

No Critical defect was found in the backend, the database isolation layer or the dependency scans. The High items are all of one kind: **things that were never built or never run against real infrastructure**. Since this audit was written the client wiring, conflict resolution, resumable uploads, notification delivery and audit chain were built (statuses below); still open: no per-tenant encryption, no external penetration test, no restore in a production-like environment, no Windows/Android/iOS binary ever built here. Nothing in this list was "fixed" by documentation; where Phase 12 added code it says so in the right-hand column.

## 2. What was executed (this environment, synthetic data only)

| Suite | Result | Notes |
|---|---|---|
| API (pytest, PostgreSQL 16 rebuilt from migrations) | 727 passed, 2 skipped, 0 failed at the Phase 12 head (after the third pass) ([completion report §18](development/PHASE_12_COMPLETION_REPORT.md)) | At the start of Phase 12 (Phase 11 head) the last full run was 689 passed / 1 skipped (Phase 10); Phase 11 and 12 added tests. |
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
| H-1 | **Clients did not consume a server change feed.** Sync state in Daily Bookkeeping is still *declared by the client* (`bk_sync_reports`). | **Mostly fixed.** Feed and preflight on the server; desktop and phone pull the feed with a checkpoint and show a status that says "Synchronized" only when the server confirmed it ([SYNCHRONIZATION_ARCHITECTURE](SYNCHRONIZATION_ARCHITECTURE.md) §6). Remaining: the closing validation still uses device-declared counts; the apps do not preflight every queued change automatically. |
| H-2 | **No conflict resolution or conflict history.** | **Mostly fixed.** `sync_conflicts` (migration 0013), verified reporting, audited immutable resolution, `sync.resolve` permission, lists and decisions on desktop and phone ([CONFLICT_RESOLUTION_GUIDE](CONFLICT_RESOLUTION_GUIDE.md)). Remaining: no Control Center page, no merge editor, reporting is wired only where call sites exist. |
| H-3 | **Uploads were not resumable at byte level.** | **Fixed (tested with simulated servers and a real PostgreSQL).** Chunked, offset-checked uploads with `received_bytes`; desktop and phone resume. Remaining: staging must be a shared volume across API replicas; no background upload while the app is closed. |
| H-4 | **Per-tenant encryption keys (D-4) not implemented.** Storage relies on platform-managed encryption of the database volume and object store. A database or cloud administrator with infrastructure access can read data. | **Open, stated honestly** in [SECURITY_TEST_REPORT §6](SECURITY_TEST_REPORT.md). |
| H-5 | **No independent penetration test.** | **Open — prerequisite for any external pilot.** |
| H-6 | **Backups never restored outside a laptop-sized database.** | **Partly addressed.** `infrastructure/ops/restore_check.sh` added and run (121 tables identical after restore, 18 MB). No PITR, no production-scale or encrypted-backup test. See [BACKUP_AND_DISASTER_RECOVERY](BACKUP_AND_DISASTER_RECOVERY.md). |
| H-7 | **No Windows installer, Android package or iOS archive has ever been built or run on a device.** | **Open.** Release guides written, none of the steps executed. |
| H-8 | **Notifications were queued but never delivered.** | **Partly fixed.** In-app notifications and e-mail are delivered by the worker ([NOTIFICATION_DELIVERY](NOTIFICATION_DELIVERY.md)); e-mail verified only against a stub mailer. **Push to phones is not implemented.** |
| H-9 | **No production infrastructure or hosting decision (D-3).** Only the Vercel + Supabase demo exists and it is not a production design. | **Open.** |

### Medium

| # | Finding | Status |
|---|---|---|
| M-1 | Audit log rows were trigger-protected but not hash-chained. | **Fixed in code.** Tenant and platform chains, verify endpoints, anchor export/check ([AUDIT_LOG_INTEGRITY](AUDIT_LOG_INTEGRITY.md)). Remaining: the operator must schedule the export to an external store. |
| M-2 | Rate limiting exists (Redis or in-memory) but its limits were never tuned under load. | Open |
| M-3 | No measured performance baseline existed. | **Fixed (baseline only):** `tests/test_p12_perf.py`, results in the completion report. Single process, one machine, 150 documents / 60 transactions — not a capacity statement. |
| M-4 | No restore-test script or documented RPO/RTO. | Script added; RPO/RTO are targets only. |
| M-5 | Billing has manual records only; no gateway (D-2), no renewal scheduler. | Open — nothing is simulated. |
| M-6 | The Control Center is English-only (ADR-040); Arabic strings need native review. | Open |
| M-7 | Platform-admin alert scan covers at most 500 organizations. | Open |
| M-8 | Monitoring was the existing `/platform/health`, request counters and JSON logs. | **Partly fixed.** Token-guarded Prometheus-format `/metrics` and example alert rules; no scraper, dashboards, tracing or alert delivery exist ([PRODUCTION_DEPLOYMENT_GUIDE §7](PRODUCTION_DEPLOYMENT_GUIDE.md)). |
| M-9 | Mobile has no certificate pinning, no jailbreak/root detection. | Open |
| M-10 | OCR verified only on synthetic documents; AI provider only against a stub; Oman tax pack unverified by a tax professional. | Open |

### Low

Placeholder app icon; `cargo-audit` unavailable; hosted demo not redeployed since Phase 10; documentation duplication between `docs/<area>/` and phase reports.

## 4. Decisions needed from the owner

D-3 (production hosting and data residency), D-4 (tenant keys), D-8 (retention values), D-11 (legal documents), D-2 (billing provider), signing certificate purchase (Windows), Apple/Google developer accounts. None can be decided by the code.
