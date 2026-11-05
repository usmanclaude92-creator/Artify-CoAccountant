# Phase 9 Completion Report — Excel Template & Reporting Engine

Status: **delivered for review.** Phase 10 has not been started. Nothing here has been opened in desktop Excel, run on Windows, or used with a real customer workbook (all fixtures are synthetic).

## 1. What was implemented
* **Schema** (migration 0010): `xl_templates`, `xl_template_versions`, `xl_mappings`, `xl_exports`, `xl_export_rows`, `xl_reports`, `xl_report_runs` (composite company FKs, forced RLS, immutability triggers/grants); storage purposes `template` and `export` are billable; `measure_entitlement` extended.
* **Engine** (`xl_engine`, `xl_mapping`, `xl_writer`, `xl_catalogue`, `xl_data`): safe upload inspection, workbook analysis, declarative mapping validation, in-place writer with formula/format preservation and read-back verification, governed datasets.
* **Service + API** (`excel.py`, `routers/excel.py`, 37 routes): template upload/versioning/status/duplicate/delete/download; analysis; mappings (draft, validate, activate, preview); export validate/preview/create/history/download/verify/regenerate/compare/transaction links; standard and custom reports; organization overview (`excel.monitor`). Async via Celery task `artify.excel_export` (in-process background when not in Celery mode).
* **Desktop** (`core/src/xl.rs`, `src-tauri/src/xl_commands.rs`, `src/screens/excel.tsx`): "Excel & Reports" page with Templates (native file dialog upload, new version, activate/deactivate/archive), Mapping builder, Generate export (validate, preview, repeat warning, unapproved marking, save-as with checksum check), Export history (verify, re-export), Standard reports, Custom report builder. The old "Reports — not available yet" placeholder was removed.
* **Control Center**: read-only "Excel & Reports" overview (templates by company, recent exports, usage, storage; no workbook content, no downloads).
* **Permissions/entitlements:** `excel.read`, `excel.templates.manage`, `excel.export`, `excel.export_unapproved`, `excel.reports.configure`, `excel.monitor`; `quota.excel_exports_per_month`, `limit.excel_templates_per_company`, `limit.excel_template_versions_per_template`, `limit.custom_reports_per_company`; existing flags `feature.report_builder`, `feature.macro_templates`.

## 2. Supported formats and template types
`.xlsx` always; `.xlsm` only with `feature.macro_templates` (macros kept, never run). Refused: `.xls`, `.xlsb`, CSV, encrypted/password-protected files, VBA inside `.xlsx`. Template categories are free text (nothing depends on them), so purchase/sales registers, VAT, bank, trial balance, P&L, balance sheet, management reports and custom layouts all use the same mechanism.

## 3. Mapping capabilities
Worksheet, header row, start row, column mappings, fixed header cells (company name, period, balances, net profit, totals…), static values, whitelisted per-row calculations (`add`, `subtract`, `concat`) and header aggregates (`sum`, `count`, `min`, `max`), conditional mappings (`present`, `absent`, `eq`, `neq`, `gt`, `lt`, `in` with an else value), several worksheets per template, number/date formats, required columns, explicit formula-overwrite permission. Validation blocks activation/export; drafts may be incomplete.

## 4. Preserved Excel features (tested with synthetic workbooks, re-read with openpyxl only)
Values, formulas (kept; translated down new rows; references shifted/extended across sheets and defined names), cell styles, row heights/column widths, merged ranges, freeze panes, autofilter, print area, tables, defined names, hidden sheets, basic conditional formats, `.xlsm` VBA container. **Not preserved (detected and reported):** charts, pivot tables, slicers, form controls, embedded objects, external links, data connections; images/threaded comments/Excel 2010+ conditional formats may change; protected sheets are refused.

## 5. Reports implemented
Governed datasets: transactions, transaction lines, journal lines and listing, trial balance, account balances, general and account ledger, profit and loss, balance sheet data, sales/purchase/expense/receipts/payments registers, customer/supplier ledgers, receivables/payables summaries, VAT summary and VAT transactions, bank transactions and summary, expense analysis, dimension summary. All are management information, `statutory = false`; the P&L / balance sheet show "Result for the period (not closed to equity)". Custom reports: choose fields, one grouping, sort, filters; optional template for Excel export. No arbitrary SQL.

## 6. End-to-end exports verified
Through the API against PostgreSQL (`test_p9_excel.py`): upload → analysis → mapping → activation → export of approved purchase invoices into a purchase-register template, read back with openpyxl: values, footer formulas extended (`=SUM(F5:F7)`), cross-sheet reference shifted, template formula kept, merged title kept, hostile narration stored as text. Two companies with different templates and data. Unapproved export with permission and marking. Duplicate warning, reason, regenerate, idempotency. Failed generation (template file removed from the store) recorded with no file. Tamper detection via checksum. 5 000-row generation at the engine level.

## 7. Incomplete, simplified or deferred (honest list)
* **Never opened in desktop Excel / LibreOffice.** Compatibility is claimed only to the extent that openpyxl re-reads the output. Whether Excel shows a "repair" prompt for any real-world workbook is unknown.
* `verify_output` samples the first and last written row; it does not re-check every cell.
* Control Center is a **read-only overview**; template archive/restore/activation and export download are desktop-only (the brief allowed operations to stay in Desktop, but "Control Center administration" actions are not built).
* Not built: Organization/Artify preset library, voucher sequences (the voucher number is the Phase 8 transaction number), CSV output, scheduled/automatic exports, template bindings to workspace sections, multi-company bulk export, conditional-format/table-range extension beyond basic cases, merged ranges spanning an insert point (warned).
* Mapping builder is a form, not a visual grid: no on-sheet preview of the template; named-range targets are not supported.
* Antivirus scanning of uploaded workbooks is not applied (Phase 6 scanner interface not wired to templates).
* Database-level guard preventing deletion of a used template does not exist (service rule only; app role keeps `DELETE` on templates/versions/mappings).
* Export files have no automatic retention/expiry; they count towards storage.
* Desktop UI not run on Windows; the Tauri shell compiles (`cargo check`) but the installer is not built. Playwright e2e covers only the Control Center overview page.
* Standard reports reflect Phase 8's unverified Oman tax pack and are not statutory filings.

## 8. Tests
Executed and passed: `test_p9_engine.py` (24, pure) and `test_p9_excel.py` (30, API + PostgreSQL); an earlier full API run (before the final overview response model and a small cleanup of `excel.py`) passed with one skip; desktop Rust core tests (incl. 4 new in `tests/xl.rs`), clippy and fmt; desktop vitest 89 passed (incl. 13 new), eslint, tsc; web tsc/eslint/vitest; Playwright `documents.spec.ts` (includes the Excel overview page) passed; `cargo check` of the Tauri shell; ruff, mypy strict, import-linter, `check_docs`, OpenAPI regeneration with 0 breaking changes.
**Not re-run after the final edits:** the complete API suite (a last full run was still in progress when this was committed; only the Phase 9 tests were run after the cleanup). CI results on the PR are the arbiter.

## 9. Excel limitations to tell users
Formulas are recalculated by Excel when the file is opened, not by Artify. Charts/pivots/slicers/form controls/external links in a template are lost on export (flagged before activation). Macro-enabled files keep their macros but must be re-tested. Always open the first export of a new template in Excel and compare it with a manual one.

## 10. Security limitations
See [EXCEL_SECURITY.md](../excel/EXCEL_SECURITY.md): no workbook antivirus scan, no file signing, no external security review; limits are practical caps, not formal guarantees; the library (`openpyxl`) is a pure-Python parser of untrusted OOXML.

## 11. Run instructions
`make db-reset` then `make test-api` (includes `test_p9_engine.py`, `test_p9_excel.py`); `make desktop-test`; `make client` after API changes; `make check`. Exports run in-process by default (`ARTIFY_PROCESSING_MODE=background`); with `ARTIFY_PROCESSING_MODE=celery` they are sent to a Celery worker (the task `artify.excel_export` is registered through `worker.celery_app.conf.imports`; a real Celery/Redis worker was **not** run in this phase, only the in-process path is tested); settings `excel_max_template_bytes`, `excel_max_export_rows`, `excel_export_max_attempts`. Docs: [docs/excel/](../excel/EXCEL_ENGINE.md). Decisions: ADR-069..074.
