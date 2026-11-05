---
name: financial-document-intelligence
description: Use when changing OCR, document classification, financial data extraction, bank-statement parsing, AI/rules providers, the processing pipeline, review/approval of extracted data, or workspace/metadata suggestions in the Artify repo (Phase 7 code in services/api ocr_engine.py, doc_extract.py, doc_taxonomy.py, understanding.py, processing.py, routers/processing.py; apps/desktop src/screens/intelligence.tsx, core/src/intel.rs). Covers invariants, where logic belongs, and what is and is not verified.
---

# Financial document intelligence

Read `docs/ai/*` and ADR-058..062 first.

## Rules that must not break
1. **Suggestions only.** Nothing from OCR, rules or an AI provider is approved, filed, split or posted by the system. Approval = `documents.approve_extraction` + `actor_type = user`. No accounting entries here (Phase 8).
2. **Never invent data.** Not found = `NULL` + flag (`missing`, `not_found`, `ambiguous_*`). Money is `Decimal`/`NUMERIC`/string; never `float`. Validators report differences; they never adjust values.
3. **Pure core.** `doc_extract.py` and `doc_taxonomy.py` do no I/O and import nothing from the app (import-linter). Add parsing rules there with a pure test in `test_p7_extract.py` first.
4. **Providers are interfaces.** `OcrProvider`, understanding provider. No provider SDK or URL outside `ocr_engine.py` / `understanding.py`. Credentials only from `Settings` (`SecretStr`); never in DB, logs, responses or clients. External use needs organization consent + `feature.advanced_ai`, text only, values must be grounded in OCR text.
5. **Originals are immutable.** OCR works on a derived page image; boxes are normalised to it. Reprocessing adds new results; old ones stay.
6. **Company workspace only.** Suggestions use the company's own nodes/categories/fields and the caller's permissions; archived nodes are never suggested; no default category names; no auto-created folders. Internal logic keys off document kinds.
7. **Pipeline discipline.** Stages commit separately; resume is idempotent; one active job per version; the pipeline is a system actor; quotas via `EntitlementService` only (`guard_quota`, `guard_concurrency`); never log recognised text or amounts.
8. **Tenant tables** carry `organization_id` + `company_id` with composite FK, forced RLS — `test_tenant_tables_carry_tenant_columns` enforces the direct company FK.

## Where things go
New field/type → `doc_taxonomy.py` (+ schema version) → extractor in `doc_extract.py` → persistence in `processing.py` → route in `routers/processing.py` (declare `Requires(...)`) → `make client` → desktop bridge (`core/src/intel.rs` fixed routes, `intel_commands.rs`, `bridge/*`) → screen. Name new Pydantic models with a `Processing` prefix (OpenAPI schema names are global).

## Testing
Synthetic documents only (`tests/docfixtures.py` draws pages with Pillow; real Tesseract is used and tests are skipped if it is missing). Reuse `firm` fixture for A-vs-B isolation. Never claim a provider works unless it ran: the HTTP provider has only been run against a stub.

## Not verified
HEIC input, real Arabic scans, handwriting, real-world bank layouts, a real AI provider, Celery against a broker, the desktop review UI in a real Windows window.
