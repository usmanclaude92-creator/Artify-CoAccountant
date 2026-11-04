# Phase 7 — OCR and AI Document Intelligence

**Status: NOT STARTED.** Requires decision D-5 (AI/OCR provider and data policy).

## Objectives

Build the provider-independent OCR and extraction pipeline keyed by document kind, with confidence/provenance, human review UI, and an evaluation harness — with no AI path to approval.

## Functional scope

- `OcrProvider`, `ExtractionProvider`, `ClassificationProvider` interfaces and at least one local and one cloud adapter (as policy permits).
- Per-Organization `ai_processing_policy` enforcement (local-only / approved providers / disabled).
- Extraction schemas for kinds: `purchase_invoice`, `sales_invoice`, credit notes, `expense_receipt`, `bank_statement` (initial), others as needed.
- Classification suggestions to Company categories (never creating categories), confidence thresholds, optional auto-file per Company setting (still never approves).
- Review UI (Desktop): source viewer with bounding-box overlay, field editing, review events, accept/reject suggestions, keyboard-first.
- Processing Monitor (Control Center module 7 part 2): queue, failures, retries, throughput, per-stage times.
- Quality scoring for scans (feedback to Mobile later), Arabic/English support, numeral normalization.
- Metering to `usage_records` and `quota.ai_units_per_month` enforcement.
- Evaluation harness with synthetic and consented de-identified datasets.

## Technical implementation

Pipeline stages as idempotent Celery tasks keyed `(document_version_id, stage, processor_version)`; adapter registry; structured output validation; prompt-injection hygiene (data-slot, schema-constrained output); circuit breakers; cost controls; `financial-document-intelligence` skill; evaluation CI job producing per-kind/per-language accuracy tables (informational at first, gating later).

## Database changes

Creates `extracted_data`, `document_review_events`, `ai_suggestions`; extends `document_processing_jobs`; adds `ai_processing_policy` fields on organizations; usage metric definitions for AI units.

## API requirements

`GET …/documents/{id}/extractions`, `POST …/documents/{id}/reprocess`, `PATCH …/documents/{id}/reviewed-values`, `GET|POST …/suggestions/{id}/decision`, `GET …/processing/queue`, `GET …/processing/stats`, `GET|PUT /orgs/{id}/ai-policy`.

## Dependencies

Phase 6; D-5; evaluation dataset from Phase 6 spike.

## Security requirements

`AI_AND_DOCUMENT_PROCESSING.md` §6 in full; no cross-tenant context in any request; provider logs by metadata only; AI components have no DB credentials and no approval code path (static dependency test); cloud use only with opt-in and DPA.

## Testing requirements

Executable: TS-ACC-07 (never invent), TS-SEC-05 (prompt-injection fixtures), TS-ENT-06 (AI quota), provider-failure chaos tests, idempotency/replay tests, review-event audit tests, evaluation harness runs (results reported as measured, not as targets met unless they are).

## Acceptance criteria

1. Extraction runs through the provider interfaces; swapping a provider requires configuration, not core changes.
2. Missing data stays missing and flagged; reviewed values supersede AI candidates with full event history.
3. Injection fixtures cannot alter schema, approvals or other data.
4. Tenant policy `local_only`/`disabled` verifiably prevents external calls (test with network guard).
5. Measured accuracy per kind/language is reported honestly in the completion report.

## Completion checklist

- [ ] D-5 recorded; DPA/sub-processor list prepared (non-engineering)
- [ ] Evaluation results table in report
- [ ] Skill `financial-document-intelligence`
- [ ] `PHASE_7_COMPLETION_REPORT.md`

## Known risks

Arabic accuracy; provider cost/latency variance; data-policy complexity; reviewers trusting AI too much (UI must show provenance and confidence); model drift between versions.
