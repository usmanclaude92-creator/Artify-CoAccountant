# AI and Document Processing

Implemented in Phase 7 (OCR/extraction) and Phase 8 (accounting intelligence). Phase 0 fixes the architecture, safety rules and interfaces.

## 1. Pipeline

```
Upload (Mobile/Desktop/Inbox)
  → ingest: type/size validation, malware scan, page split, thumbnails, SHA-256, duplicate check
  → classify: propose category (customer-configured categories only; mapped to document kind)
  → OCR: text + layout (provider adapter)
  → extract: structured candidates per document-kind schema (provider adapter)
  → validate: deterministic accounting-engine checks (pure code)
  → suggest: account/VAT/vendor/match suggestions (ai_suggestions)
  → review: human accepts/edits (document_review_events)
  → financial record → entry preparation → approval (human) → export
```

Each stage is idempotent, keyed by `(document_version_id, stage, processor_version)`, resumable, and metered.

## 2. Provider-independent architecture

Interfaces owned by `document-engine`:

- `OcrProvider.recognize(pages, languages, hints) → OcrResult` (text blocks with bounding boxes, language, confidence).
- `ExtractionProvider.extract(ocr_result | images, schema, instructions) → ExtractionResult` (field → value, confidence, source spans).
- `ClassificationProvider.classify(document, candidate_categories) → ranked categories with confidence`.

Adapters (local engines, cloud services) are registered by configuration. Selection is by **tenant AI processing policy** (`organizations.ai_processing_policy`, decision D-5): `local_only`, `approved_cloud_providers[...]`, or `disabled`. Hybrid operation means: cheap/local steps first (page cleanup, OCR on-prem where quality is sufficient), cloud models only when policy allows and local confidence is low. Cloud processing is **off by default** until a data-processing agreement and the Organization's explicit opt-in exist. Provider id, model and version are recorded with every result for reproducibility.

Languages: English and Arabic (and mixed-language documents) are first-class requirements (D-9). Numeral systems (Arabic-Indic digits), right-to-left text and date formats (Gregorian/Hijri display) are part of the extraction test set.

## 3. Extraction schema by document kind

Schemas are defined per **document kind** (not per category). Example `purchase_invoice`: supplier name/tax id, invoice number, issue date, due date, currency, line items (description, quantity, unit price, line total, tax code/rate), subtotal, tax total, grand total, payment terms. Each field has `required_for_entry: bool` and type. Absent data stays `NULL` with a `not_found` flag; **the system never fabricates a value** (rule 13).

## 4. Confidence, provenance, and review

- Every extracted value stores confidence, page, bounding box/text span, extractor id and version.
- Low-confidence or validation-failing fields are highlighted; the review UI shows the source region next to the value.
- Review edits are `document_review_events` (old value → new value, user, time). The reviewed value, not the AI candidate, feeds financial records.
- **AI-originated proposals** (`ai_suggestions`): classification, vendor match, account, tax code, bank-line matching. Status `proposed → accepted | rejected | superseded`. Acceptance is a user action recorded with the user id; an accepted suggestion creates ordinary user-attributed data.

## 5. Deterministic validation (accounting-engine)

Pure, unit-tested, `Decimal`-based checks independent of AI:

- line totals = quantity × unit price (within currency rounding policy); subtotal = Σ lines; total = subtotal + tax − discounts;
- tax amount = base × configured rate under the Company's rounding policy (tolerance configurable); tax code valid for the date;
- currency known, amounts at allowed precision; dates plausible and inside an **open** financial period;
- required fields for the kind present;
- duplicates: exact (SHA-256) and fuzzy (vendor + number + date + amount via normalized keys / trigram similarity) → `duplicate_candidates`;
- bank statements: opening + Σ movements = closing balance; no overlapping/missing statement periods per account.

Findings have severity `blocking` / `warning` / `info`; blocking findings prevent advancing to approval until a human resolves or overrides with a recorded reason (override permission is separate).

## 6. AI safety controls

1. AI has no write path to approval or export functions and no database credentials; it communicates only via the typed provider interfaces. Outputs are schema-validated and size-bounded before storage.
2. **Prompt-injection hygiene:** document text is untrusted input. It is passed as data in a delimited slot; extraction prompts forbid following instructions found in documents; outputs are constrained to the schema (structured output), and any text claiming to be an instruction cannot change behavior because no tool/action surface exists.
3. **Data minimization:** only the pages/fields needed are sent; no cross-document or cross-tenant context is ever included in a request; provider requests are logged by metadata (ids, hashes, sizes), not content.
4. **Cost and abuse control:** per-tenant `quota.ai_units_per_month` checked before dispatch; per-document retry caps; circuit breakers per provider.
5. **Evaluation:** a versioned evaluation harness with **synthetic or explicitly consented, de-identified** documents measures field accuracy, per-kind and per-language; regressions block releases of extractor changes. No real customer documents are used in development.
6. **Model changes** are versioned; re-extraction of a document creates a new extraction run, never rewriting a reviewed value.

## 7. Failure and degradation

Provider outage → retries with backoff → `needs_attention` queue; manual data entry is always available. Low-quality scans → quality score prompts re-capture on Mobile. Partial extraction is acceptable and visible.

## 8. Usage metering

Each processing run emits a `usage_records` row (units by provider/step) feeding `quota.ai_units_per_month`, per-tenant cost visibility in Super Admin, and Control Center usage pages.

> **Implementation status (Phase 7):** OCR, classification, extraction, bank statements, review and suggestions are implemented as described in [docs/ai/](../ai/OCR_ENGINE.md); this document remains the design background. Items not built are listed in the [Phase 7 report](../development/PHASE_7_COMPLETION_REPORT.md).
