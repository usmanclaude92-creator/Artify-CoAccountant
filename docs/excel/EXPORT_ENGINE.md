# Export Engine

**Data:** approved accounting data only (`approved`, `posted`; `reversed` only in ledger-style datasets). `include_unapproved` is available to transaction datasets, requires `excel.export_unapproved`, is recorded on the export, shown as "Includes unapproved" in history, and the validation adds an `unapproved_included` warning.

**Steps:** `POST …/exports/validate` (template status, active mapping, row limit `excel_max_export_rows`, empty result, missing required values, repeat detection) → `…/exports/preview` (the same mapping code on the same rows; the desktop shows what will be written) → `POST …/exports` (**202**, idempotency key; same key returns the first export with **200**) → worker.

**Worker** (`run_export`, system actor, separate short transactions): mark running → download the template version → fetch datasets under RLS → generate → `verify_output` → put the file in the object store, check its size → insert the storage object, export rows and mark `completed` with filename, SHA-256, size, row counts, fidelity notes. Storage errors retry (`excel_export_max_attempts`); any other failure writes `failed` with a reason and leaves **no file and no export rows**. An export is never `completed` before validation and storage (DB `CHECK`).

**Duplicates:** exporting transactions already in a completed export of the same template returns **409** with the earlier exports; proceeding needs `acknowledge_repeat` and a reason. **Regenerate** creates a *new* export (same template version, mapping, parameters; `source_export_id`), requires a reason, and is not counted against the monthly quota. Originals are never replaced.

**Download:** `GET …/exports/{id}/file` streams from the store after the permission check; audited; `X-Export-Sha256` header; `POST …/verify` re-hashes the stored object. The desktop saves through a native "Save as…" dialog (no hard-coded folder) and discards a file whose checksum differs from the record.

**Quota:** `quota.excel_exports_per_month` counts exports that are not regenerations; when reached, new exports are refused (402) and existing files stay downloadable.
