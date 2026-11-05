# Excel Template & Reporting Engine (Phase 9)

Each company keeps **its own Excel workbooks** as templates, maps governed accounting fields onto them, previews, and exports **approved** Phase 8 data into the accountant's existing layout. Artify adapts to the accountant's workflow; it does not impose a standard sheet.

## Layers
| Layer | Module | Decides |
|---|---|---|
| Upload safety + analysis | `xl_engine.py` (file I/O only, no database) | is this a safe `.xlsx`/`.xlsm`; what it contains; what may not survive |
| Catalogue | `xl_catalogue.py` (pure data) | the governed datasets, their fields and filters, header values, safe calculations |
| Mapping language | `xl_mapping.py` (pure) | validation of a mapping, conditions, aggregates, custom-report projection/grouping |
| Writer | `xl_writer.py` | fills the template in place, keeps formulas/formatting, verifies the output by reading it back |
| Data | `xl_data.py` | one fixed, parameterised SQL statement per dataset over **approved** accounting data |
| Service | `excel.py` | templates, versions, mappings, exports (async), history, reports, entitlements, audit |
| API | `routers/excel.py` (37 routes) | permissions and request validation |
| Worker | `excel_tasks.py` | Celery task `artify.excel_export` (in-process background when not in Celery mode) |

## Flow
```
Upload workbook (raw body, size-capped, streamed) → safety checks → analysis (advisory) → stored as an immutable template version
  → mapping draft (may be incomplete) → validation → activate (errors block; risky workbooks need an explicit acknowledgement)
  → export: validate → preview (same code path as the output) → create (idempotency key; repeat warning) → worker
  → fetch data → fill a COPY of the template → re-open and verify → store → mark completed (never earlier)
  → history record with template version, mapping, filters, row counts, checksum, transaction links
```

## What is stored (migration 0010)
`xl_templates`, `xl_template_versions` (immutable), `xl_mappings` (draft → active → superseded; active ones immutable), `xl_exports` (immutable once completed/failed), `xl_export_rows` (export ↔ transaction/journal links, append-only), `xl_reports`, `xl_report_runs`. All carry `organization_id` + `company_id`, composite FKs and forced RLS. Template and export files are `storage_objects` (`purpose` `template` / `export`) in the Phase 6 object store: no public location, authorised streaming downloads only.

## Honest limits
* Artify **never recalculates formulas**; the output is saved with "recalculate on open" so Excel does it. Opening the file in Excel is the check that totals are right.
* Charts, pivot tables, slicers, form controls, embedded objects, external links and data connections are **not preserved** (detected and reported before activation). Images, threaded comments and some Excel 2010+ conditional formats/sparklines may change.
* `.xls`, `.xlsb`, encrypted and password-protected workbooks are refused. Macros are never executed; `.xlsm` is only accepted when the plan has `feature.macro_templates`, and the macro code is carried through untouched.
* Not verified here: opening generated files in desktop Excel, the Windows desktop UI, real customer workbooks (synthetic only). See [the completion report](../development/PHASE_9_COMPLETION_REPORT.md).
