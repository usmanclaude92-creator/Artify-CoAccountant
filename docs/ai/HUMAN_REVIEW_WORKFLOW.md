# Human Review Workflow (Phase 7)

## Pipeline stages (`processing_jobs.status`)
`queued → preparing_pages → ocr_processing → classifying → extracting → validating → suggesting → awaiting_review → approved`, with `failed`, `cancelled` and `needs_manual` as exits. Progress is computed from real page counts. Each stage runs in its own short transaction (`attempts` table, retry with backoff up to `ARTIFY_PROCESSING_MAX_ATTEMPTS`, timeouts, cancellation, idempotent resume: stored OCR/extraction results of the same job are reused). One active job per document version (partial unique index); a repeated request with the same `idempotency_key` returns the same job. Production dispatch is the Celery task `artify.process_document`; without Celery (`processing_mode = background`) it runs in-process.

## Who can do what
| Action | Permission |
|---|---|
| Start/retry/cancel/re-run | `documents.process` |
| Correct, confirm, reject, mark missing/unreadable, accept a suggestion | `documents.review` |
| Approve / reject the extraction | `documents.approve_extraction` and **actor = user** |
| Monitor | `processing.monitor` (organization) |
Document visibility still follows the workspace engine; a document you cannot see is `404`.

## Windows app
Processing Queue (waiting for review, running/failed jobs) and a review workspace: page image with highlighted source regions, page thumbnails, zoom, view rotation, fields with confidence and origin ("Suggested by the system", "Corrected by you", "Confirmed by you"), line items, bank rows, checks, suggested location/details, proposed split, re-run OCR/classification/extraction, Approve/Reject. Approval is blocked until every flagged item is resolved and validation errors are acknowledged with a note.

## Invariants
AI never approves: approval writes `approved_by` (DB CHECK) and requires a person. Suggestions (location, metadata, split) change nothing until accepted; a split is a **proposal** — the original is never replaced. No accounting entry is created anywhere. Reprocessing creates new versions of results and keeps the old.
