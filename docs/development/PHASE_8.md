# Phase 8 — Accounting Intelligence Engine

**Status: NOT STARTED.** Requires decision D-6 (ledger scope).

## Objectives

Implement the deterministic accounting engine: Company chart of accounts, VAT/tax configuration, validators, financial records, entry preparation with human approval, reconciliation support — mapped through document kinds, never section names.

## Functional scope

- Company Accounting Configuration (Control Center module 8) and VAT/Tax Configuration (module 11): chart of accounts (import/edit), account role mappings, financial periods and locks, currencies/FX, vendors/customers, bank accounts, VAT configuration and tax codes with effective dating.
- Jurisdiction pack mechanism and the **initial Oman pack as data**, with `source_reference` and `verified_on`; engine contains no country constants. The pack must be reviewed by a qualified tax professional before being marked verified; the report states what verification occurred.
- Records: `invoices` (+ lines), `expenses`, `bank_transactions` created from reviewed extractions; validators from `AI_AND_DOCUMENT_PROCESSING.md` §5; duplicate detection (fuzzy).
- Entry preparation from rule templates by document kind + account role mappings + VAT; draft → pending approval → approved → exported; rejection; reversal entries.
- Human approval UI (Desktop): approval levels per workflow; separation of duties.
- Reconciliation: bank ↔ records/entries (suggestions as `ai_suggestions`, decisions as records).
- Traceability navigation (document ↔ record ↔ entry ↔ export).

## Technical implementation

`accounting-engine` pure-function core (Decimal, rounding policies), service layer with transactions, DB triggers for balance/immutability/state-actor, rule-template registry per kind, pack loader, period-lock enforcement, property-based tests, `accounting-engine`, `vat-compliance`, `financial-data-validation` skills.

## Database changes

Creates `currencies` data, `exchange_rates`, `chart_of_accounts`, `account_role_mappings`, `vat_configurations`, `tax_codes`, `vendors`, `customers`, `bank_accounts`, `bank_transactions`, `invoices`, `invoice_lines`, `expenses`, `accounting_entries`, `journal_lines`, `reconciliation_records`, `financial_periods` usage; triggers and constraints per `ENTITY_CATALOG.md`.

## API requirements

`/companies/{id}/accounting/chart`, `…/role-mappings`, `…/periods`, `…/vat`, `…/records`, `…/entries` (+ `submit|approve|reject|reverse`), `…/reconciliation`, `…/traceability/{id}`, `/reference/currencies`, `/reference/jurisdiction-packs`.

## Dependencies

Phases 4, 6, 7; D-6; qualified tax reviewer for the Oman pack.

## Security requirements

ADR-009 enforced in service and DB; AI components cannot call approval; period locks; approved immutability; entries audited; section permissions applied to financial records derived from restricted documents; override permissions separate and audited.

## Testing requirements

Executable: TS-ACC-01 through TS-ACC-11, TS-ENT (record counts if limited), golden-value tests for 3-decimal currencies and both rounding policies, property tests (balance, rounding), schema lint (no float), authorization matrix for approval routes.

## Acceptance criteria

1. Two Companies with differently named sections produce equivalent validation results for equivalent documents (TS-ACC-01).
2. Every entry balances; approved entries are immutable; corrections via reversal only.
3. No code path lets AI or system actors approve; verified by static and DB tests.
4. Switching jurisdiction pack changes VAT outcomes without code changes.
5. Full traceability from any entry to the original document and back.

## Completion checklist

- [ ] D-6 recorded
- [ ] Oman pack: verification status stated precisely
- [ ] Skills `accounting-engine`, `vat-compliance`, `financial-data-validation`
- [ ] `PHASE_8_COMPLETION_REPORT.md`

## Known risks

Regulatory accuracy and liability; rounding disputes across customer software; mapping diversity of charts of accounts; scope creep toward a full ledger; period/close edge cases.
