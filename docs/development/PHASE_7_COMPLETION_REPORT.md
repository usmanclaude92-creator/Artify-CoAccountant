# Phase 7 Completion Report — OCR & AI Document Intelligence

Status: **delivered for review.** Phase 8 has not been started. Nothing here was verified on Windows or against a real AI provider.

## 1. What was implemented (and exercised end to end)
* **Data model** (migration 0008): 15 tenant tables (jobs, attempts, OCR results/pages/blocks, classifications, extractions, fields, line items, bank statements/transactions, workspace and metadata suggestions, field corrections, split proposals, AI provider configuration) with composite company FKs, forced RLS and definer-read entitlement measurement.
* **Pipeline**: start → prepare pages → OCR → classify → extract → validate → suggest → await review; retries with backoff, timeouts, cancel, manual retry, idempotent requests and resume, one active job per version, history, reprocessing that keeps earlier results. Celery task `artify.process_document` plus an in-process fallback.
* **OCR**: local Tesseract 5 (eng+ara) and embedded PDF text; preprocessing on a derived copy; processed page images stored and served.
* **Understanding**: deterministic rules provider for classification, invoice/receipt/credit-debit note extraction and the bank-statement parser/validator; optional OpenAI-compatible provider behind consent and entitlement.
* **Suggestions**: company-specific workspace location (own company's nodes only, permission-checked, archived nodes never offered), custom-metadata values (validated, never invented), split proposals (proposal only).
* **API**: 26 routes under `/companies/{id}/…` and `/processing/…` (see `routers/processing.py`), permissions `documents.process|review|approve_extraction`, `processing.monitor`, `ai_providers.manage`; entitlements for documents, OCR pages, bank statements, concurrency, advanced AI.
* **Windows app**: Processing Queue; review workspace (page image with highlighted regions, thumbnails, zoom, view rotation, fields/line items/bank rows with confidence and origin labels, edit/confirm/reject/missing/unreadable, add bank row, checks, suggestions, split proposal, re-run OCR/classification/extraction, approve/reject); “Read with OCR / review” on a document; Rust passthrough with validated ids and fixed routes.
* **Control Center**: Document Processing page (real job counts, usage, per-company and failed lists, provider state, consent switch for external AI). Monitoring only.
* **Docs**: `docs/ai/*` (7 files), this report, ADR-058…ADR-062.

## 2. Providers
| Provider | State |
|---|---|
| Tesseract (local) | Integrated and tested with real OCR in this sandbox (v5.3.4, eng+ara+osd). Must be installed on every API/worker host (CI installs it). |
| Rules (local) | Integrated, default. |
| OpenAI-compatible HTTP | Integrated behind consent; tested **only against a local stub server**. Not verified with any real service. |

## 3. Document types and languages
Classification: the 18 types listed in [DOCUMENT_CLASSIFICATION.md](../ai/DOCUMENT_CLASSIFICATION.md) (keyword rules; synthetic tests). Extraction: invoices (all four invoice types), receipts, credit/debit notes, bank statements. Other types are classified only. Languages: English tested; Arabic tested only with one synthetic rendered fixture.

## 4. Not implemented / incomplete
* HEIC decoding relies on optional `pillow-heif`; **not tested**.
* Page-boundary detection/cropping; handwriting; stamps/signatures; barcode/QR reading.
* Admin UI to define custom document types (extensible in code only).
* No Mobile review (out of scope); no desktop offline processing.
* Desktop: the review UI is tested with the mock bridge and component tests; **not run in a real Tauri/Windows window**, and not connected live to the API in a desktop test (`make desktop-live` was not extended).
* Splitting: proposals are stored and can be approved/declined; **no split is executed** (files are never split or replaced).
* Statement checks do not cover dates outside the period or duplicate-looking rows.
* Real-document accuracy has not been measured; all fixtures are synthetic.

## 5. Tests (executed)
* API: full suite run on a real PostgreSQL; after fixing the one failure (missing composite company FKs on the new tables, caught by `test_tenant_tables_carry_tenant_columns`) the Phase 7 and RLS files pass. New files: `test_p7_extract.py` (18 pure tests) and `test_p7_processing.py` (OCR formats, rotation, skew, quality, Arabic, corrupt input, bank statements, review, reprocessing, idempotency, retries, quotas, workspace A vs B, security, provider consent).
* Desktop core: 5 new Rust tests (`core/tests/intel.rs`) pass; UI: 7 new component tests, lint and types pass (65/65 vitest).
* Web: types and lint pass; e2e `documents.spec.ts` (which now also covers the processing page) passes against the built app.
* OpenAPI: regenerated, 0 breaking changes. Early in development two new response models shared names with existing schemas; they were renamed (`Processing…`) before commit.
* The final full-suite re-run after the last FK fix and the CI result for the pushed commits are reported in the PR, not here.

## 6. Security limitations
See [AI_DATA_SECURITY.md](../ai/AI_DATA_SECURITY.md): no penetration test; OCR subprocess has a timeout but no OS sandbox; external provider unverified; the app holds opened page images in memory while a review is open.

## 7. Risks
OCR/rules accuracy on real documents is unknown and likely lower than on fixtures; tesseract must be present in production images; Celery routing for the new task is untested against a real broker (the task function and dispatch call are tested); large documents (200 pages) were not load-tested.

## 8. Run and test
`sudo apt-get install tesseract-ocr tesseract-ocr-ara tesseract-ocr-osd` · `make db-reset` · `make test-api` (or `pytest services/api/tests/test_p7_*.py`) · `make client` · `make desktop-test` · `make e2e`. Settings: `ARTIFY_OCR_*`, `ARTIFY_UNDERSTANDING_PROVIDER`, `ARTIFY_AI_HTTP_*`, `ARTIFY_PROCESSING_MODE`.
