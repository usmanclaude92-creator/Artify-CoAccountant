# Commercial Release Checklist

Status of every prerequisite for putting the product in front of paying customers, as of Phase 12. **✔ = evidence exists in the repository or a recorded test; ✘ = not done; ◐ = partly done.** Nothing is ticked on the strength of documentation alone.

## A. Software quality

| Item | Status | Evidence / gap |
|---|---|---|
| API automated tests pass on PostgreSQL | ✔ | [completion report §18](development/PHASE_12_COMPLETION_REPORT.md) |
| Browser end-to-end suite | ◐ | 33 passed at Phase 10; not re-run for Phase 12 (no web change) |
| Desktop UI and Rust core tests | ◐ | pass on Linux; Tauri shell not compiled; nothing run on Windows |
| Mobile tests | ◐ | 94 pass in the Flutter test harness; no device |
| Load/soak test | ✘ | only a single-process latency baseline |
| Accessibility regression | ◐ | axe checks in the web e2e suite; desktop/mobile not audited |
| Arabic / RTL in the Control Center | ✘ | ADR-040 |

## B. Security and privacy

| Item | Status |
|---|---|
| Independent penetration test | ✘ |
| Dependency scans clean | ◐ (Python/JS clean at last run; Rust not scanned) |
| Per-tenant encryption decision (D-4) | ✘ |
| Audit log tamper-evidence (hash chain) | ◐ built and tested for tenant and platform logs, with anchor export/check; the scheduled export to an external store is an operator task, not done |
| Secrets rotation and incident-response drills | ✘ |
| Privacy policy, terms, DPA, sub-processor list (D-11) | ✘ |
| Retention and erasure values (D-8) and purge workflow | ✘ |

## C. Operations

| Item | Status |
|---|---|
| Hosting and residency decision (D-3) | ✘ |
| Production and staging environments | ✘ |
| Backup restore tested | ◐ small-database scripted restore only |
| PITR, DR drill with measured RPO/RTO | ✘ |
| Monitoring, alerting, status page | ◐ `/metrics` + example alert rules built; no scraper, alert delivery or status page exist |
| Migration rehearsal on production-size data | ✘ |
| Support runbooks exercised | ✘ |

## D. Product and commercial

| Item | Status |
|---|---|
| Billing provider (D-2), invoices, renewals | ✘ (manual records only; nothing simulated) |
| Subscription enforcement | ✔ server-side entitlements; ◐ no renewal scheduler |
| Notifications delivered (e-mail/push) | ◐ in-app + e-mail built (stub mailer only); push ✘ |
| Sync clients on the change feed, conflict resolution | ◐ server, desktop, phone and Control Center page built and tested (simulated servers; browser for empty states); open conflicts feed Daily Closing validation; automatic preflight only for desktop new-version uploads |
| Resumable uploads | ◐ built and tested; needs a shared staging volume across replicas; never run over a real network |
| Tax pack verified by a professional | ✘ |
| OCR/AI quality on real documents | ✘ (synthetic only; AI provider only against a stub) |
| Onboarding guide | ✔ [CUSTOMER_ONBOARDING_GUIDE](CUSTOMER_ONBOARDING_GUIDE.md) |

## E. Distribution

| Item | Status |
|---|---|
| Windows installer built, signed, clean-machine tested | ✘ |
| Android bundle built, signed, device tested; Play listing | ✘ |
| iOS archive, TestFlight, App Review | ✘ |
| App-store billing rules for firm subscriptions understood | ✘ |

## Readiness classification

* **A — Development Complete: NO.** Phases 0–11 are delivered for review. Phase 12's *development* tasks are only partly done: the sync change feed and preflight, conflict recording and resolution, resumable uploads, notification delivery (in-app and e-mail), the audit hash chain, client wiring on desktop and phone, a restore check, a latency baseline, the consistency-chain test and the documentation exist; push notifications, tenant key management, the Control Center conflicts page, external anchoring of the audit head, and every release build do not (see sections A–E).
* **B — Ready for Staging: no.** Needs a staging environment (D-3), migrations rehearsed there, the clients wired to the change feed, and at least the Windows and Android builds produced.
* **C — Production Deployment Ready: no.**
* **D — Commercial Launch Ready: no.** Do not describe the product as launch-ready until sections B, C and E are ✔.

The accurate one-line description is: **a substantial, well-tested backend and three client codebases whose Phase 12 integration and release work is unfinished.**
