# Financial Integrity (Phase 8)

| Concern | How it is enforced |
|---|---|
| Decimal precision | `Decimal` in code (`D()` refuses float), `NUMERIC(24,6)` in the database, strings in JSON; a test asserts there is no `real`/`double precision`/`money` column in any `acct_*` table |
| Balanced journals | service validation (both currencies) **and** a database trigger on approval |
| Approved records immutable | triggers refuse UPDATE/DELETE of approved transactions, journals and lines; corrections are reversals/amendments |
| One journal per transaction | `UNIQUE (transaction_id)`; approving twice is a 409 |
| One live transaction per extraction (and statement row) | partial unique index |
| Person approves | CHECKs + trigger; AI/system cannot (`actor_type`) |
| Separation of duties | policy + trigger |
| Period control | validation + reversal check; dates never moved |
| No cross-company references | composite FKs `(organization_id, company_id, id)` on every reference + RLS forced on all tables |
| Source traceability | transaction → document, extraction, bank row; a document with approved accounting records cannot be permanently deleted |
| Audit | `acct_approvals` (append-only), `audit_events` for configuration and workflow, `acct_period_events`, rule versions |
| Quotas | `EntitlementService` only; reaching a limit blocks **new** records, never changes existing ones |

## Honest limits
* Reports are company-level aggregates behind `accounting.reports.read`; they are not narrowed by workspace section.
* Posting, ledger-of-record duties, closing entries and external system writes do not exist yet.
* No penetration test; the trigger tests use a superuser connection to prove the database refuses changes even if the application did not.
