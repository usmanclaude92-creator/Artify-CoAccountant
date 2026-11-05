# Closing workflow

## Stages and statuses
1 prepare · 2 validate · 3 review exceptions · 4 closing summary · 5 submit for review · 6 approve and close.
Statuses: `draft` → `in_progress` → `ready_for_review` → `closed`; a reviewer may send `ready_for_review` back to `in_progress` (stage 3, with a note); `closed` → `reopened` (history); `draft`/`in_progress`/`ready_for_review` → `cancelled`. The transition graph is enforced by `bk_core` and by the `app.bk_guard_session` trigger. Only one live closing (draft, in progress, ready, closed) can exist per company and day (partial unique index).

## Rules
* Starting: the day must not be in the future in the company's time zone; the closing records the accounting period containing the day (informational).
* Submit: needs a validation run, not stale, result not `failed` and not `awaiting_review`. Records submitter and copies the company's independence setting onto the row.
* Approve and close (`bookkeeping.close.approve` and the company's role restriction): refused unless the day, **validated again now**, is not failed/awaiting review; refused when the closer is the submitter and independence is required (also a DB CHECK). Stores the snapshot, final result, closer and time.
* Critical failures block: no endpoint closes a day with an open critical issue; overriding is allowed only for non-integrity critical issues with permission and explanation.
* Cancel needs a reason; a closed day is reopened, not cancelled.

## Snapshots
One per closed closing: validation result and issues, every exception with its decision, transaction counts by status and approved totals by type and currency (strings), documents counts, cash and bank movement, checklist state, task counts, declared sync state, the policy in force, preparer and submitter. `sha256` is computed by the database from the stored payload; UPDATE/DELETE are forbidden even for the owner role; `GET …/snapshot` re-computes the hash (`verified`).

## Reopening
Permission `bookkeeping.close.reopen` plus the company's role restriction, a reason of at least 10 characters, and an open accounting period. The original closing becomes `reopened` (reason, person, time; nothing else may change) and keeps its snapshot, exports and trail. A new closing (`in_progress`, stage 2, `reopened_from_id` set) is created. Closing it creates snapshot n+1 linked to the previous one. `GET /days/{date}/snapshots` lists the whole history.

## Closed-day protection
While a day is closed: new/changed exceptions and checklist ticks for that day are refused (DB trigger), sync reports are refused, and a transaction dated on that day cannot be approved (application check `day_closed`, DB trigger as backstop). Reversals of approved records stay possible and are dated on their own day.

## Daily reports (Excel)
`POST …/closings/{id}/exports` calls the Phase 9 `create_export` with the day as the date range: approved data only unless the caller has `excel.export_unapproved` and asks; the export records template version and mapping; exporting the same transactions again with the same template needs `acknowledge_repeat` and a reason; the same idempotency key returns the first export; `…/regenerate` makes a new reasoned export and never replaces the first. `bk_closing_exports` links them to the closing and, if closed, to its snapshot.

## Cash and bank
Per cash/bank account: opening (journals before the day), receipts, payments, closing, in base currency, from approved/posted/reversed journals. Unapproved cash/bank transactions are counted separately. **It is not a bank reconciliation**; nothing is compared with a statement (the response says so and `reconciled` is always `false`).

## Period readiness
`GET …/readiness?period_id=` (or a date range up to 93 days): lists days that have activity (transactions or documents) or a started closing and are not closed, open errors, open transactions and days that have not happened. It does not lock the period, prepare a VAT return or approve anything.
