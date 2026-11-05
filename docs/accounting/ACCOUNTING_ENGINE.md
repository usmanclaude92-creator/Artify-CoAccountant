# Accounting Engine (Phase 8)

Turns an **accountant-approved** Phase 7 extraction (or a hand-made draft) into a validated double-entry journal that a person reviews and approves. It is a foundation for later phases (Excel, reporting, integrations); it is **not** a posting engine, a VAT-return generator or a bank-reconciliation tool.

## Layers (each has one job)
| Layer | Module | Decides |
|---|---|---|
| Interpretation | `accounting_txn.create_from_extraction` | which transaction type a document is, from the classification the accountant confirmed |
| Pure core | `acct_core.py` (no I/O, no clock, no framework) | **every number**: decimals and rounding, tax, journal generation and validation, rule resolution, contact matching, duplicate levels |
| Configuration | `accounting.py` | the company's own profile, chart, roles, periods, tax codes/rates, exchange rates, contacts, rules |
| Workflow | `accounting_txn.py` | draft → review → approval, reversal/amendment, duplicates, journal storage |
| Reporting | `accounting_reports.py` | read-only queries over approved journals |
| API | `routers/accounting.py` (67 routes) | request validation and permissions |

AI never calculates, approves or posts. Phase 8 suggestions (accounts, tax codes, narration, contact matches) are **deterministic rules over company configuration**; no AI provider is called. The entitlement flag `feature.ai_accounting_suggestions` is reserved and unused.

## Flow
```
Document → OCR → classification → extraction → accountant approves extraction (Phase 7)
   → POST /accounting/transactions/from-extraction/{id}   (refused unless the extraction is approved)
   → draft: lines, tax, accounts, journal, validation, duplicate check (all by the server)
   → accountant edits/confirms the customer or supplier, accounts, tax codes (every edit is re-validated)
   → submit (blocking issues refuse) → [review] → approve (person + permission + policy + no blocking issue)
   → approved, immutable journal with a number; ready for later posting/export
Traceability: document → OCR result → approved extraction → transaction → journal lines → approval trail
```

## What is stored
18 `acct_*` tables (migration 0009), all with `organization_id` + `company_id`, composite FKs and forced RLS: configuration, accounts, role mappings, periods (+events), tax codes and rates, exchange rates, contacts, rules (versioned), approval policy, counters, transactions and lines, journals and lines, approvals (append-only), duplicate matches. See [DOUBLE_ENTRY_JOURNALS.md](DOUBLE_ENTRY_JOURNALS.md) and [FINANCIAL_INTEGRITY.md](FINANCIAL_INTEGRITY.md).

## Permissions (company scope)
`accounting.read`, `.configure`, `.contacts.manage`, `.prepare`, `.review`, `.approve`, `.approve_high`, `.reverse`, `.periods.manage`, `.reports.read`. Firm owners and practice administrators hold all; practice managers all; accountants everything except `approve_high`, `reverse` and `periods.manage`; assistants prepare only; read-only users read.

## Entitlements (no limits in code)
`quota.accounting_transactions_per_month`, `quota.journals_per_month` (new records refused when reached; existing data untouched), `limit.accounting_rules_per_company`, flags `feature.multi_currency`, `feature.advanced_approval`, `feature.advanced_accounting` (rules, reverse-charge/import treatments).

## Supported transaction types
Sales invoice/credit note, customer receipt/refund, purchase invoice/credit note, supplier payment/refund, cash/bank/employee/general expense, bank deposit/withdrawal/charges/transfer, journal entry, opening balance, adjustment, accrual, prepayment, depreciation, and reversal entries. Manual types need the accountant to choose every account: the engine validates but never invents them. Payments and receipts are **not allocated to invoices** (no open-item matching or ageing).

## Known limitations
See [the completion report](../development/PHASE_8_COMPLETION_REPORT.md): cash-basis accounting, FX revaluation, retained-earnings/closing entries, invoice allocation, VAT returns, posting and Excel are not implemented; the Oman tax pack is unverified; desktop screens cover review and approval, not configuration.
