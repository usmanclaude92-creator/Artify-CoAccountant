---
name: excel-template-engine
description: Use when changing Excel templates, mappings, workbook analysis, exports, export history or reports in the Artify repo (Phase 9: services/api xl_engine.py, xl_catalogue.py, xl_mapping.py, xl_writer.py, xl_data.py, excel.py, excel_tasks.py, routers/excel.py, migration 0010; apps/desktop src/screens/excel.tsx, core/src/xl.rs; Control Center /excel). Covers workbook safety, formula preservation, mapping rules, approved-only exports, immutability and what is not verified.
---

# Excel template engine

Read `docs/excel/EXCEL_ENGINE.md` and ADR-069..074 first.

## Rules that must not break
1. **The accountant's workbook is the product.** Never impose a layout; never hard-code categories, sheet names or folders. Template categories are free text and carry no behaviour. The export destination is chosen by the person (native Save as), not by code.
2. **Untrusted input.** Never execute macros, never evaluate formulas, never trust the extension alone (`inspect_upload`). Parse package XML with `defusedxml`. Text starting with `= + - @ TAB CR` is written as a string (`safe_text`). Any new write path must go through `set_value`.
3. **Formulas:** a cell that holds a formula is never overwritten unless the column says `allow_formula_overwrite`; row insertion must go through `rewrite_formula` (all sheets, defined names). Artify never recalculates; say so wherever totals are discussed.
4. **Mappings are declarative.** Extend the whitelist in `xl_catalogue` (`ROW_CALCS`, `AGGREGATES`, `CONDITION_OPS`) and `xl_mapping`; never add `eval`, scripts or SQL fragments from a definition. Validate in `xl_mapping.validate_mapping` and add a pure test.
5. **Governed datasets only.** A new report/dataset = a `Dataset` in `xl_catalogue` + one fixed parameterised statement in `xl_data.fetch`; unapproved data only via `include_unapproved` on transaction datasets and only with `excel.export_unapproved`. Always filter `company_id` explicitly.
6. **Approved data, honest status.** Exports are `completed` only after `verify_output` and storage succeeded (DB CHECK). Failure leaves no file and no `xl_export_rows`. Do not call a report statutory.
7. **Immutability.** Template versions, activated mappings and finished exports never change; changes are new versions/records. Never add UPDATE/DELETE grants for `xl_exports`, `xl_export_rows`, `xl_template_versions` UPDATE.
8. **Limits** only through `EntitlementService` (`quota.excel_exports_per_month`, `limit.excel_templates_per_company`, `limit.excel_template_versions_per_template`, `limit.custom_reports_per_company`, storage). Reaching a limit blocks new items, never deletes.
9. **Company isolation:** composite FKs, RLS, ids from the client verified under the company (404).

## Where things go
New field → `xl_catalogue` field + SQL column in `xl_data`. New route → `routers/excel.py` with `Requires(...)`, `make client`, and the `OPS` table in `core/src/xl.rs` + the `XlOp` union and mock. New fidelity warning → `FEATURE_PARTS` in `xl_engine`.

## Testing
`tests/test_p9_engine.py` (pure: upload safety, analysis, mapping, writer, injection, large export) and `tests/test_p9_excel.py` (API + PostgreSQL: approved-only, repeat warnings, immutability triggers/grants, tenancy, quotas, reports). Fixtures in `tests/xlfixtures.py` are synthetic. Desktop: `core/tests/xl.rs`, `src/excel.test.tsx`.

## Not verified / not built
Opening generated files in desktop Excel, the Windows desktop UI, real customer workbooks, antivirus scanning of templates, charts/pivots/slicers preservation, presets, voucher sequences, CSV output, scheduled exports, Control Center template administration (read-only overview only).
