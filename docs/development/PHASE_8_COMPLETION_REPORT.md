# Phase 8 Completion Report — Accounting Intelligence Engine

Status: **delivered for review.** Phase 9 has not been started. Nothing here has been run on Windows, reviewed by a tax professional, or connected to any external accounting system.

## 1. What was implemented (and exercised through the API against PostgreSQL)
* **Schema** (migration 0009): 18 `acct_*` tables (configuration, accounts, role mappings, periods + events, tax codes/rates, exchange rates, contacts, rules, approval policy, counters, transactions + lines, journals + lines, approvals, duplicate matches). Composite company FKs, forced RLS, no float columns, immutability and balance triggers.
* **Pure core** `acct_core.py`: decimal arithmetic/rounding, tax calculation, journal generation and validation for every supported type, rule resolution with conflict detection, contact matching, duplicate levels.
* **Services**: company accounting profile and approval policy; chart of accounts with hierarchy checks and role mappings; periods (generate, close, reopen with reason); tax codes with effective-dated rates and recorded verification; exchange rates; customers/suppliers with match suggestions; versioned rules; transactions from **approved** extractions (invoices, credit notes, receipts, bank-statement rows) or by hand; automatic validation; duplicate detection with evidence; submit/review/approve/reject/correct/revise/reverse/amend; journals; read-only reports.
* **API**: 67 routes under `/companies/{id}/accounting/…`, each declaring its permission. New permissions `accounting.*` (10), entitlements for transactions, journals, rules, multi-currency, advanced approval, advanced accounting. OpenAPI regenerated, 0 breaking changes.
* **Windows app**: an Accounting screen (transactions by status, trial balance) and a transaction review screen (source page, details, customer/supplier confirmation, editable lines, proposed journal with totals and a text balance indicator, journal editing, checks with plain-language severity, duplicate evidence, calculation details, submit/approve/reject/request correction/reopen/reverse, history); "Prepare accounting transaction" on approved extractions. The Rust layer is an allow-listed passthrough.
* **Docs**: `docs/accounting/*` (10 files), ADR-063…068, skill `accounting-engine`.

## 2. Transaction types that work (journal balances in tests)
Sales invoice/credit note, customer receipt/refund, purchase invoice/credit note, supplier payment/refund, cash/bank/employee/general expense, bank deposit/withdrawal/charges/transfer, journal entry, opening balance, adjustment, accrual, prepayment, depreciation (manual lines), reversal entry (created only by reversing). From documents: sales/purchase invoices (tested end to end through the real Phase 7 pipeline) and bank statement rows (tested). Credit notes, receipts, payments and expenses are tested through hand-made drafts, not through document extraction.

## 3. Tax scenarios tested
Exclusive and inclusive calculation, quantity/price/discount, multiple rates, zero-rated, exempt, out-of-scope, reverse charge (journal), credit-note mirroring, line vs document rounding with half-up/half-even, effective-dated rate change by date, no rate in force (blocking), a second jurisdiction (15%) by configuration only, invalid configuration (rate on exempt, >100%, overlap, wrong direction), missing tax code on a registered company (warning), printed-vs-calculated tax mismatch with acknowledgement. **Not** verified: the Oman starter pack against the law; no VAT return; no import-specific logic beyond a rate-bearing "import" code.

## 4. Configurable rules
Account (line/control/settlement), tax code, narration template and dimension rules with conditions, priority, effective dates, versions, validation, conflict flagging, per-company limit. No scripting.

## 5. Double entry, approvals, multi-currency
See [DOUBLE_ENTRY_JOURNALS](../accounting/DOUBLE_ENTRY_JOURNALS.md), [APPROVAL_WORKFLOW](../accounting/APPROVAL_WORKFLOW.md), [MULTI_CURRENCY](../accounting/MULTI_CURRENCY.md). Rates are entered or configured, never invented; base-currency rounding differences go to a rounding account; the rate, source and date are stored for reproducibility.

## 6. Not implemented / incomplete (honest list)
* Posting, closing/retained-earnings entries, VAT returns, bank reconciliation, Excel (out of scope), writes to external systems.
* Cash-basis accounting (the API accepts accrual only), FX gain/loss and revaluation, **allocation of receipts/payments to invoices** (no ageing or open items).
* **No configuration screens**: chart, tax, rules, periods, rates and policy are managed through the API only (no Control Center or desktop UI yet). The desktop covers review and approval.
* No AI-assisted suggestions (deterministic rules only); no idempotency keys on create routes (duplicates are prevented by unique source keys and duplicate detection).
* Reports are company-level aggregates, not narrowed by workspace section; no report export; the ledger is paged but untested at large volumes.
* Bank-statement drafts are created up to 300 per call; counter accounts for bank rows must be chosen or ruled.
* The desktop UI is tested with the mock bridge and component tests, not in a real Windows window; Arabic/RTL was not specifically reviewed beyond `dir="auto"` on text inputs.

## 7. Tests (executed)
* API: `test_p8_core.py` (39, pure, including 300 seeded random documents that must balance) and `test_p8_accounting.py` (52: chart, tax, extraction-to-transaction, rules, duplicates, FX, periods, approvals incl. two-level and limits, reversal/amendment, database trigger refusals, decimal precision, reports, tenancy, restricted documents, entitlements). Full API suite run on PostgreSQL after the last backend change: no failures.
* Desktop: 2 Rust passthrough tests; 13 new UI tests; lint/types pass (77 UI tests total).
* Pre-existing tests (RLS classification, entitlement catalogue, permissions) pass with the new tables, permissions and entitlements.

## 8. Security notes and risks
Per-company queries filter `company_id` explicitly (RLS alone would show an all-company user other companies' rows). Approved records are protected in the database as well as the service. Risks: accounting logic is only as good as the company's configuration; tax codes may be wrong until verified; no penetration test; rounding conventions of other software may differ (documented, configurable); performance on large ledgers unmeasured.

## 9. Run and test
`make db-reset` · `make test-api` (or `pytest services/api/tests/test_p8_*.py`) · `make client` · `make desktop-test` · `make e2e`. Set up a company: `POST /companies/{id}/accounting/config`, then `/templates/chart`, `/templates/tax-pack`, `/periods/generate` (all `accounting.configure` / `accounting.periods.manage`).
