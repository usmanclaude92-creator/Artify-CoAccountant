# Phase 10 Completion Report — Daily Bookkeeping & Closing

Status: **delivered for review.** Phase 11 has not been started. Everything below was verified only as stated in section 11; nothing has been used with real customer data, run on Windows, or exercised by more than one real user at a time.

Legend used throughout: **[Full]** fully implemented · **[Partial]** partially implemented · **[None]** not implemented · **[Pass]** tested and passed · **[Fail]** tested and failed · **[Untested]** not tested.

## 1. Summary of implemented functionality
| Area | Status |
| --- | --- |
| Daily workbench (overview, findings, document queue, transactions, exceptions, daily report) with real data only | **[Full]** API · **[Partial]** desktop (see §5, §13) |
| Tasks: nine statuses, assignment history, comments, audit history, review, no self-approval | **[Full]** API + DB · **[Partial]** desktop (create, start, complete, review; no assignment picker, no comment/history view) |
| Company-specific daily checklists (copy default templates, add, rename, reorder, mandatory, role-assign, automatic rules, archive, several lists) | **[Full]** API · **[Partial]** desktop (copy default, tick; the rest is API only) |
| Deterministic daily validation reusing Phase 8 data, with exact reasons and the four results | **[Full]** |
| Six-stage closing, six statuses, send-back (rejection), approve-and-close with independence, cancel | **[Full]** API · **[Partial]** desktop (all actions exist; direct navigation only to transactions) |
| Immutable, hash-checked closing snapshots; reopening keeps the original and links a new closing | **[Full]** |
| Closed-day protection in the database and the application | **[Full]** |
| Overrides with permission, explanation and audit; integrity checks cannot be overridden | **[Full]** |
| Per-company policy: independence, who may prepare/review/approve/reopen/override, severities, override length, time zone | **[Full]** API · **[None]** UI (no policy screen) |
| Daily Excel reports through the Phase 9 engine, linked to the closing/snapshot | **[Full]** API · **[Partial]** desktop (template choice, create, repeat confirmation, list; no preview or download inside this screen) |
| Cash and bank daily summary (not a reconciliation) | **[Partial]** (per account opening/receipts/payments/closing; no transfers split, no "unclassified") |
| Period-end readiness overview | **[Partial]** (closings, open errors/transactions, unexported, unprocessed documents, missing source documents; no "open accounting periods" list beyond the status) |
| Firm Control Center visibility | **[Partial]** (one read-only overview page; see §6) |
| Notifications | **[Partial]** — events are queued in the existing outbox; **no delivery exists for any event** (§12) |

## 2. Existing functionality reused from Phases 0–9
Tenancy/RLS/roles/permissions and audit (Phases 1–3); `workspace.load_access` folder permissions (Phase 4) for the document queue; document, version, scan status and processing-job tables (Phases 6–7); transactions, lines, journals, tax codes, duplicate matches, stored validation, periods and `require_open_period` (Phase 8); `excel.create_export`, `regenerate_export`, repeat acknowledgement, idempotency and template/mapping versioning (Phase 9); the transactional outbox; the desktop allow-list/bridge/mock pattern and `@artify/ui`; the Control Center shell and `DataTable`. No second accounting, Excel, OCR or sync engine was written. `accounting_txn.approve` gained one call (`assert_day_open`).

## 3. New database models and migration (0012)
`bk_policies`, `bk_checklists`, `bk_checklist_items`, `bk_checklist_completions`, `bk_tasks`, `bk_task_assignments`, `bk_task_comments`, `bk_closing_sessions`, `bk_validation_runs`, `bk_exceptions`, `bk_closing_approvals`, `bk_closing_snapshots`, `bk_closing_exports`, `bk_sync_reports`, `bk_audit_events`. Composite company FKs, forced RLS, no `BYPASSRLS`, append-only tables without UPDATE/DELETE grants, plus triggers for the session state machine, the task graph, closed days, transaction approval on a closed day, snapshot hashing and immutability. Details: [bookkeeping/DATABASE.md](../bookkeeping/DATABASE.md). Forward-only; applied from scratch on every test run.

## 4. New API endpoints
46 routes under `/api/v1/companies/{company_id}/bookkeeping` plus the organization overview `GET /api/v1/bookkeeping/overview` (policy, checklists, tasks, day views, sync declaration, exceptions, closings and their steps, snapshots, closing exports, readiness). List: [bookkeeping/API.md](../bookkeeping/API.md). OpenAPI and the TypeScript client were regenerated; `openapi-check` passes.

## 5. Desktop UI screens and workflows
"Daily Bookkeeping" page in the existing shell and design system: **Today** (overview incl. approved-not-exported, findings, document queue, transactions), **Tasks**, **Checklists**, **Closing** (six-stage guide, exceptions with acknowledge/override and "Open transaction", validate, summary, submit, approve and close, send back, reopen, cancel, history, daily Excel report), **Daily report** (approved totals, journal totals, cash and bank), **Period readiness**. The Rust core exposes one allow-listed `bk_call`. Not built in the UI: policy editor, checklist editing beyond copy/tick, task assignment/comments/history, selectors for financial year/period/preparer/reviewer (the page has company and date), export preview and download from this page (use "Excel & Reports"), navigation from an exception to a document or task (only to a transaction).

## 6. Firm Control Center changes
New page **Daily Closing** (`bookkeeping.monitor`): per company for a chosen day — closing status, open errors, open and overdue tasks, recent days with activity not closed. Status and counts only. **Not built:** closing history browser, task-completion summaries, policy configuration, report/export status. Policy is configurable through the API only.

## 7. Daily validation rules
31 checks with exact codes and stable ordering ([bookkeeping/VALIDATION_ENGINE.md](../bookkeeping/VALIDATION_ENGINE.md)): future day, accounting not set up, closed/missing accounting period, unbalanced journal, approved without journal, tax header ≠ lines, blocking Phase 8 issues, awaiting approval, draft, duplicates, inactive tax code, inactive account, tax configuration missing, unverified tax code, no source document, infected/quarantined/unprocessed/failed/manual/awaiting-review documents, declared sync conflicts/failures/pending/not reported (Phase 12 adds `SYNC_SERVER_CONFLICTS`, counted by the server — 32 checks in total), mandatory checklist item open, blocking/overdue tasks, empty day. Integrity checks (`FUTURE_DATE`, `TXN_UNBALANCED`, `TXN_NO_JOURNAL`, `DOC_INFECTED`, `PERIOD_CLOSED`, `PERIOD_MISSING`) can be neither lowered nor overridden; every other severity is company policy. Results: Passed / Passed with warnings / Awaiting review / Failed. **No country tax rule is assumed**; the Phase 8 Oman pack stays unverified and a day using an unverified tax code is flagged.

## 8. Daily closing workflow
Prepare → validate → review exceptions → closing summary → submit → approve and close; statuses Draft, In progress, Ready for review, Closed, Reopened, Cancelled; "rejection" is *send back with a note* (there is no separate Rejected status). Critical failures block submission and closing; closing validates again in the same transaction; the submitter cannot close when independence is required (default), enforced by `bk_core` and a CHECK. Reopen needs permission, the company's role restriction, a reason and an open period; the original closing and snapshot stay, a linked new closing starts. Closing a day inside a closed accounting period, or where a required period does not exist, is refused. Details: [bookkeeping/CLOSING_WORKFLOW.md](../bookkeeping/CLOSING_WORKFLOW.md).

## 9. Excel integration
`POST …/closings/{id}/exports` calls the Phase 9 `create_export` with the day as range; approved data by default; unapproved needs `excel.export_unapproved` and an explicit request; repeat exports need acknowledgement and a reason; the same idempotency key returns the first export; regenerate creates a new reasoned record; template version and mapping number appear in the closing's export list; exports link to the closing and, when closed, to its snapshot. A template of company A cannot be used from company B. **Not done here:** export preview inside the closing screen, a closing-report workbook template, failed-export notification, and a Phase 10 test of a *failed* export (Phase 9 tests cover failure recording).

## 10. Security and permission implementation
11 new permissions (`bookkeeping.*`) mapped to the existing system roles; every route declares one; out-of-scope company/organization → 404; roles holding `bookkeeping.read` also hold `accounting.read` (test-enforced); per-company role restrictions for prepare/review/approve/reopen/override; no client-supplied company, actor or status is trusted; approve-and-close/reopen/override/submit/send-back are structurally user-only (CHECK on the trail); exceptions store counts and accounting object ids only, never document ids/names; the document queue uses folder permissions; outbox payloads carry ids and dates, no amounts; platform admins gain nothing (all tables are tenant RLS; no new platform path). The desktop core validates ids, dates, template keys and query keys and forwards bodies unchanged. ADR-076..078.

## 11. Automated tests executed and their actual results
Executed in this sandbox (PostgreSQL 16, Redis, Tesseract installed locally):
* `tests/test_p10_core.py` (pure rules): 27 tests — **[Pass]**.
* `tests/test_p10_bookkeeping.py` (API + database): 46 tests — **[Pass]**: policy, checklists, tasks, validation ladder, tax/sync/document/period/account checks, exceptions, full closing workflow, independence in app and DB, stale validation, late-change re-validation, snapshots (immutable, tamper-evident), closed-day protection (application and database), reopening and linked closings, role restrictions, Excel export/idempotency/repeat/regenerate/company-specific templates, cash and bank, workbench, folder permissions, readiness, organization overview, cross-organization and cross-company isolation, RLS with the real app role, append-only tables, audit and outbox, time zones.
* Full API suite (`pytest`, all phases, database rebuilt from migrations): **689 passed, 1 skipped, 0 failed** (13 min) — **[Pass]**. The skipped test is the pre-existing environment-dependent one.
* Browser end-to-end (`infrastructure/dev/e2e.sh`, real API + production web build + Chromium): **33 passed** — **[Pass]**, including the axe accessibility audit of every Control Center page (the new Daily Closing page among them) and the Daily Closing assertions added to `documents.spec.ts`.
* Desktop: `vitest` 100 tests (11 new) **[Pass]**; `tsc --noEmit`, `eslint` **[Pass]**; `cargo test -p artify-core` (112 tests incl. 3 new in `tests/bk.rs`; 6 live-backend tests ignored by design) **[Pass]**; `cargo fmt`, `cargo clippy -D warnings` on the core **[Pass]**.
* Web: `vitest` 6 **[Pass]**; `tsc`, `eslint`, `next build` **[Pass]** (the `/bookkeeping` route is built).
* Static: `ruff`, `ruff format`, `mypy`, `lint-imports`, `openapi-check`, `check_docs.py` **[Pass]**.
* Mutation review: ten deliberate defects (independence ignored, integrity checks lowerable, closed-day check removed, role restriction ignored, acknowledgement hiding a critical, self-approval of tasks, server-date grouping, reopen without reason, future day allowed, unverified snapshot hash, sync "clean" without a report) were each caught by a test.
* **[Untested]:** the Tauri shell crate (`src-tauri`, including the new 20-line `bk_commands.rs`) does not compile here because the GTK system libraries are missing; it mirrors `acct_commands.rs`.  The desktop UI has never run on Windows. No multi-user or concurrent-load test of closing was run (single-statement row locks are used; a double-close attempt is refused by status). Notification delivery, large data volumes, real sync clients and real Excel were not tested.

## 12. Known issues
* **Notifications are not delivered.** The outbox events (`bookkeeping.task.assigned`, `…review_requested`, `bookkeeping.closing.submitted|sent_back|closed|reopened`) are queued, but the existing worker only acknowledges events (Phase 1 skeleton). There are no emitted events for overdue tasks, critical exceptions or failed Excel exports, and no company-level notification preferences.
* Synchronisation is a **declaration by the client** (`POST /sync-reports`); the desktop UI does not send one yet, so `SYNC_NOT_REPORTED` (information) is what a real day shows today.
* Validation loads the day's facts with several queries per call (also on every closing view for the stale check); not benchmarked for large days.
* `TXN_NO_SOURCE_DOCUMENT` treats a fixed list of Artify transaction types as "normally has a document"; companies can lower its severity but not change the list.
* "Rejected" closings are modelled as send-back, not a status. "Company administrators" (company role) cannot configure the policy; the permission is held by firm owner and practice manager.
* The test helpers call `ruff format` over `tests/`, which once reformatted two unrelated test files; they were restored (no unrelated change is in the diff).

## 13. Incomplete functionality
Not built: policy and checklist editing UI; task assignment/comment/history UI; workbench selectors for financial year, period, preparer and reviewer; per-document/task navigation from exceptions; export preview/download inside the closing screen; closing history browser and task summaries in the Control Center; company notification preferences; transfers and "unclassified" lines in the cash/bank summary; automatic bulk operations; digest emails; a statutory VAT figure (only per-type tax totals); any bank reconciliation, VAT return, third-party posting or period locking (out of scope by design).

## 14. Manual configuration or setup requirements
* Apply migration 0012 (`alembic upgrade head`). New permissions appear after `artify-api seed` (reference data sync).
* A company needs accounting set up (Phase 8) and accounting periods for the dates it will close (when `require_open_period` is on).
* Each company copies a default checklist (or builds its own) and may set its time zone, independence and role restrictions with `PUT …/policy`; defaults are independent approval on, no role restriction, UTC/organization time zone.
* Excel reports need an active template with an active mapping (Phase 9).
* The hosted demo (Vercel + Supabase) has **not** been redeployed with Phase 10; it still serves the earlier branch.

## 15. Dependencies to resolve before Phase 11
Phase 11 (mobile) does not depend on daily closing, but: (1) the sync engine (Phase 12) is required before sync state can stop being a declaration; (2) a notification delivery handler (push/email) is needed before mobile notifications mean anything; (3) the Tauri shell must be compiled and the Windows UI exercised on Windows; (4) the owner should review the default validation severities and the system-role permission table; (5) the Phase 8 Oman tax pack is still unverified. None blocks starting Phase 11 work that uses only the existing upload/workspace APIs.

## 16. Existing design and functionality preserved
No theme, colour, sidebar structure or component style was changed: one navigation entry ("Daily Bookkeeping" / "Daily Closing") and screens built from `@artify/ui` and the existing CSS classes were added. Earlier phases are unchanged except: `permissions.py` (new keys), `main.py` (router), `accounting_txn.approve` (one closed-day check), desktop `types.ts`/`mock.ts`/`tauri.ts`/`shell.tsx`/`app.tsx` (additive), `acct_*` triggers (one new trigger on `acct_transactions` that only fires for approval on a closed day). All earlier tests still pass (full suite, see §11).

## Readiness for Phase 11
**Ready to proceed, with the exceptions stated above.** The daily closing backend is complete and well-tested as a *service*; the Windows interface covers the daily path but not configuration, and notifications are not delivered. Phase 11 (mobile) can start because it reuses authentication, workspace, upload and document APIs that Phase 10 did not change; it should not assume daily-closing features on mobile (out of scope for that phase).
