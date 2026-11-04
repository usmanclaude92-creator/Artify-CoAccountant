# Excel Engine and Reporting Design

Implemented in Phase 9 (templates, exports) with the configuration UI in the Control Center and operation in Desktop. Every template is **owned by one Company**.

## 1. Ownership model

```
Artify system presets / Organization presets  (libraries, optional)
         │  "use preset" = explicit COPY (new ids)       ← no live link
         ▼
Company ─ excel_templates ─ excel_template_versions (immutable) ─ excel_column_mappings
                                   │
                                   └─ excel_exports (what was exported, when, by whom, from which entries)
```

- `excel_templates` carry `organization_id`, `company_id`. A change to Company A's template never affects Company B's (no shared rows).
- **Presets** (`excel_template_presets`) are owned by an Organization (or Artify) and reusable only by explicit copy; the copy records `source_preset_id` as provenance only. An update to a preset never alters copies.
- Multiple templates per Company, multiple versions per template, and templates bound to **purposes** (e.g. purchase journal, sales journal, bank transactions) and optionally to workspace sections/categories (`excel_template_bindings`).

## 2. Template lifecycle

1. **Upload** `.xlsx` (macro-enabled `.xlsm` is accepted only if the Organization enables it; macros are preserved but never executed).
2. **Compatibility analysis** (OpenPyXL + OOXML inspection): sheets, defined names, tables, merged cells, data validations, conditional formats, formulas, charts, images, pivot tables, form controls, external links. Produces a report classifying each feature as *preserved*, *at risk*, or *unsupported* **before** the user invests in mapping.
3. **Mapping** (column mapping builder): choose worksheet, header row, first data row, target columns/cells ↔ source fields (see §3).
4. **Test**: run with sample/synthetic or selected approved entries into a scratch output; preview diff; validation of formulas not broken.
5. **Publish version**: immutable version (file stored in object storage, SHA-256, mapping JSON validated). Previous versions remain for history and re-export.

## 3. Mapping language (declarative; ADR-016)

A mapping binds a **target** (sheet, column or cell/named range) to a **source** drawn from a closed catalog of exportable fields and a whitelisted formatter. No user-supplied code or `eval` ever executes.

Source catalog (per export purpose): entry header (voucher number, date, narration, currency, source document reference), journal line (account code/name, debit, credit, VAT code, VAT amount, cost center…), invoice/expense/bank-transaction fields, **Company custom fields** (by field id), category/section display names (as snapshotted), counters.

Supported shaping:

| Need | Mechanism |
|---|---|
| Debit/credit | Separate debit and credit columns, or signed amount column, per template choice |
| VAT | Net/VAT/gross columns, tax code column, per-line or per-document |
| Narration, voucher number | Format tokens; voucher number sequence is Company-configured (`voucher_sequences`) |
| Dates / numbers | Excel number formats and date formats set per column; Decimal values written as numeric cells (not strings) with the template's format; rounding per currency minor unit |
| Row layout | **One row per journal line**, **one row per document** (multi-column debit/credit), or **grouped** with subtotal rows |
| Static cells | Header blocks (company name, period, run date) via tokens |
| Lookup | Account code ↔ external accounting software code mapping tables owned by the Company |

## 4. Export behavior

- **Append mode:** writes into the data region from `start_row`, extending after the last used row; copies row styles and **translates formulas** in formula columns (relative references) so totals and helper columns continue to work.
- **Fill mode:** writes into fixed named cells/ranges (e.g. report headers).
- **Formula preservation:** formulas in the template are retained; the engine never overwrites cells that contain formulas unless explicitly mapped; computed values are produced by Excel on open (the engine sets full-calc-on-load). Where authoritative numbers are needed they come from Artify's Decimal engine, not from Excel recalculation.
- **Formatting:** existing styles are preserved to the extent OpenPyXL supports; features flagged *at risk/unsupported* in the compatibility analysis are warned about at upload and again at publish.
- **Idempotency & traceability:** each run creates `excel_exports` (template version, requested by, parameters, output file hash) and `excel_export_rows` (entry/line id → sheet/row). Entries move to `exported` when the export is marked final; re-exporting requires an explicit re-export action (new export record, reason recorded). Duplicate-export prevention is the default.
- **Only approved entries** can be exported (the export service rejects anything else).
- **Sheets:** multiple worksheets per template; per-sheet purpose; selectable at export.
- **Preview:** rows to be written and cell values rendered in-app (from the same code path) before generating the file.
- **Large exports** run in the `export` queue; output delivered via pre-signed URL.

## 5. Fidelity risk and fallback

OpenPyXL rewrites workbook XML and does not preserve every Excel feature (known categories: charts and images in some cases, pivot tables, slicers, form controls, some conditional-format extensions, external data connections). Mitigations, in order: (1) detect and report at upload (§2); (2) restrict writes to mapped cells; (3) for templates with unsupported features, evaluate **direct OOXML patching** of the target sheet XML (editing only cell values/styles in the sheet part and leaving other parts untouched) in the Phase 9 spike; (4) otherwise block publish with a clear explanation rather than silently degrading the customer's file. The chosen approach is validated with a fixture library of representative templates (synthetic, built for tests).

## 6. Reporting configuration

`report_configurations` (Company-scoped) define: data source (a **governed dataset**: daily transactions, purchases, sales, expenses, VAT, bank transactions, entries — never raw SQL), columns (including custom fields), filters, grouping/subtotals, sort, layout, output format (XLSX via the same engine, CSV, PDF later), and which roles may run it. `report_runs` record parameters, requester, output hash and row counts. Datasets read through the same RLS-protected views, so a report can never cross Company boundaries. Report configurations may reuse Excel templates for layout.

## 7. Security

Template files are untrusted input: size limits, zip-bomb and XML-entity protections (defusedxml), no external link resolution, no macro execution, formula-injection protection when writing cell values that begin with `=`, `+`, `-`, `@` from document text (written as text with a prefix guard unless the target column is explicitly numeric), and output scanned for embedded external references.
