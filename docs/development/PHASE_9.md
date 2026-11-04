# Phase 9 — Excel Template and Reporting Engine

**Status: NOT STARTED.**

## Objectives

Implement Company-specific Excel templates, mappings, exports and configurable reports, preserving template fidelity as far as technically feasible and being explicit where it is not.

## Functional scope

- Template upload with compatibility analysis; multiple templates per Company, multiple immutable versions, bindings to purposes/sections/categories.
- Mapping builder (Control Center module 9): worksheet and header/start-row configuration, column/cell/named-range targets, source catalog (entries, lines, records, custom fields), formatters, debit/credit/VAT/narration/voucher mappings, layouts (row per line / per document / grouped), account-code lookup tables, voucher sequences.
- Test run and preview before export; publish version.
- Exports from Desktop and Control Center: only approved entries, idempotent export records, re-export with reason, row-level traceability.
- Presets: Organization/Artify preset library with copy-on-use semantics.
- Report configurations (module 10): governed datasets, columns, filters, grouping, layout, formats (XLSX via same engine, CSV), role-based run permissions; report runs recorded.

## Technical implementation

`excel-engine` library: OOXML inspector, OpenPyXL writer (translator for formulas), declarative mapping validator, preview renderer sharing the write path, formula-injection guard, defusedxml usage, export worker; direct-OOXML-patching fallback evaluated per Phase 6 spike results; fixture library of synthetic templates (merged cells, tables, validations, conditional formats, formulas, named ranges, protected sheets, charts/images for negative tests); `excel-template-engine` skill.

## Database changes

Creates `excel_template_presets`, `excel_templates`, `excel_template_versions`, `excel_column_mappings`, `excel_template_bindings`, `voucher_sequences`, `excel_exports`, `excel_export_rows`, `report_configurations`, `report_runs`; entitlement `limit.excel_templates_per_company` enforcement.

## API requirements

`/companies/{id}/excel/templates` (+ versions, analyze, test, publish), `…/excel/bindings`, `…/excel/exports` (+ preview, final, re-export), `…/reports/configs`, `…/reports/runs`, `/orgs/{id}/excel/presets` (+ copy-to-company).

## Dependencies

Phase 8 (approved entries, source catalog), Phase 6 spike results, Phase 3 UI foundations.

## Security requirements

`EXCEL_ENGINE_DESIGN.md` §7: untrusted template handling, no macro execution, XXE/zip-bomb defenses, injection guards; export downloads authorized and audited; reports read only through RLS-protected governed datasets.

## Testing requirements

Executable: TS-XLS-01 through TS-XLS-08, TS-ISO-03 (templates), TS-ACC-10 (traceability through exports), fidelity fixture suite (reported per feature: preserved / warned / blocked), 3-decimal formatting tests, export idempotency and concurrency tests, performance test for large exports.

## Acceptance criteria

1. Two Companies use entirely different templates for the same logical data and neither can see or alter the other's.
2. Templates with unsupported features are flagged before publish; no silent degradation.
3. Formulas outside mapped cells survive; formula columns extend correctly.
4. Every exported row traces back to the entry, record and original document.
5. Re-export is explicit and recorded; non-approved entries cannot export.

## Completion checklist

- [ ] Fidelity matrix published in the report
- [ ] Skill `excel-template-engine`
- [ ] `PHASE_9_COMPLETION_REPORT.md`

## Known risks

Template fidelity limits of OpenPyXL; infinite diversity of customer templates and accounting-software import formats; mapping UX complexity; large-file performance.
