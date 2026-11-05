# Daily validation engine

`services/api/src/artify_api/bk_core.py` is pure: it takes a `DayFacts` object and a severity policy and returns issues. `bookkeeping.build_facts` loads the facts from the database (reusing Phase 8 data: transactions, lines, journals, tax codes, duplicate matches, stored transaction validation; Phase 6/7 documents, versions and processing jobs; declared sync state; tasks; checklists). The same facts always give the same issues in the same order.

## Checks
| Code | Default | Meaning | Policy can change severity? |
| --- | --- | --- | --- |
| `FUTURE_DATE` | critical | the day has not ended in the company's time zone | no |
| `ACCOUNTING_NOT_SET_UP` | error | no accounting configuration | yes |
| `TXN_UNBALANCED` | critical | an approved journal's debit ≠ credit (recomputed from its lines) | no |
| `TXN_NO_JOURNAL` | critical | an approved transaction has no journal | no |
| `TXN_TAX_MISMATCH` | error | header tax ≠ sum of line tax (beyond the company's tolerance) | yes |
| `PERIOD_CLOSED` | critical | the accounting period containing the day is closed (a day inside a locked period cannot be closed; creating a closing there is refused too) | no |
| `PERIOD_MISSING` | critical | no accounting period covers the day and the company's accounting configuration requires one | no |
| `ACCOUNT_INACTIVE` | error | a transaction line or journal line uses an inactive account | yes |
| `TXN_NO_SOURCE_DOCUMENT` | warning | a transaction of a type that normally rests on a document (invoices, credit notes, receipts, payments, expenses) has no linked source document | yes |
| `TXN_BLOCKING_VALIDATION` | error | a not-yet-approved transaction still has Phase 8 blocking issues | yes |
| `TXN_AWAITING_APPROVAL` | error | waiting for review or approval | yes |
| `TXN_DRAFT_OPEN` | warning | still a draft | yes |
| `TXN_DUPLICATE_EXACT` / `_POSSIBLE` | error / warning | duplicate match nobody decided | yes |
| `TAX_CODE_INACTIVE` | error | a line uses an inactive tax code | yes |
| `TAX_CONFIG_MISSING` | warning | tax recorded but the company has no tax registration or jurisdiction | yes |
| `TAX_CODE_UNVERIFIED` | warning | tax recorded with a code nobody has verified (one issue per code) | yes |
| `DOC_INFECTED` | critical | malware scan failed | no |
| `DOC_QUARANTINED` | error | quarantined documents | yes |
| `DOC_UNPROCESSED` / `DOC_PROCESSING_FAILED` / `DOC_NEEDS_MANUAL` / `DOC_AWAITING_REVIEW` | warning / error / error / warning | processing state of the day's documents | yes |
| `SYNC_CONFLICTS` / `SYNC_FAILED` / `SYNC_PENDING` | error / error / warning | **declared by a device**; shown as such | yes |
| `SYNC_NOT_REPORTED` | info | no device reported for this day | yes |
| `CHECKLIST_MANDATORY_OPEN` | error | a mandatory checklist item is not done | yes |
| `TASK_BLOCKING_OPEN` / `TASK_OVERDUE` | error / warning | tasks | yes |
| `DAY_EMPTY` | info | no activity at all | yes |

No Oman or other country rule exists in this module. A tax check can only say "recorded but not configured", "inactive", "unverified" or "header ≠ lines".

## Result ladder
| Result | When |
| --- | --- |
| **Failed** | an open critical issue |
| **Awaiting review** | no critical, but an open error |
| **Passed with warnings** | open warnings, or any error acknowledged / critical overridden |
| **Passed** | nothing but information |

The result shown in the workbench, stored with a validation run and used at submit/close is always computed by `bk_core.evaluate` from the issues and the current exception decisions.

## Exceptions
Each issue has a fingerprint `code:subject_type:subject_id`. `bk_exceptions` has one row per fingerprint per company and day. Validation reconciles: new → open; present again → keeps a person's decision unless the severity got worse; gone → resolved by the system. A person may **acknowledge** a warning or error (error: written explanation of at least the company's minimum, default 10 characters; permission `bookkeeping.close.review`) or **override** a critical issue that is not an integrity check (permission `bookkeeping.close.override`, explanation required). Integrity checks (`FUTURE_DATE`, `TXN_UNBALANCED`, `TXN_NO_JOURNAL`, `DOC_INFECTED`, `PERIOD_CLOSED`, `PERIOD_MISSING`) can be neither acknowledged nor overridden. Decisions are recorded on the exception, in the closing trail and in the audit log. Exceptions never contain document ids or names.

## Stale validation
A run stores a digest of what it looked at. If the day changes afterwards, the closing shows `stale` and submission is refused until the day is validated again. Closing re-validates in the same transaction regardless.
