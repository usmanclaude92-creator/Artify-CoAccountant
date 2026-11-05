---
name: daily-bookkeeping-closing
description: Use when changing daily bookkeeping, tasks, company checklists, daily validation, the closing workflow, closing snapshots, reopening, daily reports or the period-readiness overview in the Artify repo (Phase 10: services/api bk_core.py, bookkeeping.py, bookkeeping_closing.py, bk_templates_data.py, routers/bookkeeping.py, migration 0012; apps/desktop src/screens/bookkeeping.tsx, core/src/bk.rs; Control Center /bookkeeping). Covers the business-day rule, severity policy, independence, immutable snapshots, closed-day protection and what is not verified.
---

# Daily bookkeeping and closing

Read `docs/bookkeeping/README.md` and ADR-076..078 first.

## Rules that must not break
1. **A business day is a calendar day in the company's time zone** (`bookkeeping.company_tz`: policy → organization → UTC). Never use the server's date for "today", for future checks or for grouping documents.
2. **Validation is pure and deterministic** (`bk_core.validate_day`). Add a check as: a `CODES` entry (default severity, `hard` only for integrity), facts loaded in `bookkeeping.build_facts`, an issue in `validate_day`, a test in `test_p10_core.py` and a row in `docs/bookkeeping/VALIDATION_ENGINE.md`. Never "repair" data and never assume a country tax rule: compare what the company configured with what was recorded.
3. **Integrity checks cannot be lowered or overridden** (`HARD_CODES`). Everything else is company policy. Severity overrides are validated by `validate_severity_overrides`.
4. **Closing is a human decision.** Approve-and-close, reopen, override, submit and send-back need `actor_type = user` (CHECK on `bk_closing_approvals`). The submitter cannot close when `independence_required` (checked in `bk_core.check_close` and by a CHECK on the row). A critical failure blocks closing; closing re-validates in the same transaction.
5. **Snapshots and trails are append-only.** `bk_closing_snapshots`, `bk_closing_approvals`, `bk_validation_runs`, `bk_audit_events` use `app.forbid_mutation`; the snapshot hash is computed by the database. Reopening keeps the original and creates a linked new closing; never edit a closed closing.
6. **A closed day is protected in the database** (exceptions, completions, approving a transaction dated on it). The application checks first (`assert_day_open`, code `day_closed`); the trigger is the backstop. Reversals of approved records stay possible.
7. **Exceptions store counts and ids of accounting objects only, never document ids or names** (they are read by people who may not see every folder); the document queue uses Phase 4 node permissions. `bookkeeping.read` is only for roles that also hold `accounting.read`.
8. **Sync state is a declaration** (`bk_sync_reports`), shown as declared. Do not present it as proof before the sync engine exists.
9. **Checklists are copied, never linked**; automatic items are rule keys evaluated from data, never titles. Archive, do not delete.
10. **Daily Excel reports reuse Phase 9** (`excel.create_export`): do not write a second export path.
11. **Money** is `Decimal`/`NUMERIC`, strings in JSON. No amounts in notifications (outbox payloads carry ids and dates).
12. **Every mutation** writes `bookkeeping.event` (domain history + audit log) in the same transaction.

## Tests to extend
`test_p10_core.py` (pure), `test_p10_bookkeeping.py` (API + DB: workflow, validation, closing, snapshot, closed day, reopen, Excel, isolation, RLS, audit/outbox, time zones), `apps/desktop/core/tests/bk.rs`, `apps/desktop/src/bookkeeping.test.tsx`, `apps/web/e2e/documents.spec.ts` (Control Center page).

## Not verified (say so)
The desktop UI has not run on Windows; real multi-day business use, large volumes, real sync clients and delivery of notifications are untested; the Oman tax pack stays unverified.
