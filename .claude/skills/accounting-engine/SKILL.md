---
name: accounting-engine
description: Use when changing accounting code in the Artify repo (Phase 8: services/api acct_core.py, accounting.py, accounting_txn.py, accounting_reports.py, routers/accounting.py, migration 0009; apps/desktop src/screens/accounting.tsx, core/src/acct.rs). Covers the invariants for money, journals, tax, approvals, periods, company isolation and what is not verified.
---

# Accounting engine

Read `docs/accounting/ACCOUNTING_ENGINE.md` and ADR-063..068 first.

## Rules that must not break
1. **Money is Decimal.** Only `acct_core.D()` converts input; it refuses float. DB columns are `NUMERIC`; JSON carries strings (`accounting.ser`). No float anywhere in `acct_*` (a test checks the schema).
2. **The core is pure.** Decisions about numbers live in `acct_core.py` (no I/O). The service loads data, calls the core and stores the outcome. The frontend only displays server values.
3. **Never adjust or invent.** Differences are issues; missing accounts, tax codes, rates or contacts are blocking issues, not defaults chosen silently. Role defaults are visible and labelled.
4. **Person approves.** Approval needs a user, a permission, the company policy and zero blocking issues; AI/system never calls it. Triggers back this up.
5. **Approved is immutable.** Fix with reversal/amendment. Never add UPDATE/DELETE grants on accounting tables.
6. **Company isolation.** Every query filters `company_id` (RLS alone also exposes the firm's other companies to an all-company user); every reference is a composite FK; ids from the client are verified through `get_*` helpers (404).
7. **Limits** only through `EntitlementService`; reaching a limit blocks new records, never alters existing ones.
8. **Tax is configuration** with verification status; no country constants in code; do not present rules as advice.

## Where things go
New transaction type → `acct_core.TXN_TYPES` + `build_journal` + migration CHECK + `accounting_txn` role maps + tests (journal must balance for every type). New validation → `acct_core` (pure test) then `rebuild` in `accounting_txn`. New route → `routers/accounting.py` with `Requires(...)`, then `make client` and the desktop `OPS` table in `core/src/acct.rs`.

## Testing
`tests/test_p8_core.py` (pure, includes seeded randomised balance checks) and `tests/test_p8_accounting.py` (API + DB triggers via the superuser engine). Synthetic data only. Pydantic model names must not collide with existing OpenAPI schemas.

## Not verified / not built
Oman tax pack correctness, posting, VAT returns, cash-basis accounting, FX revaluation, closing entries, invoice allocation, AI suggestions, configuration screens in the apps, the desktop UI on Windows.
