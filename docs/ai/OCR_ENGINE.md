# OCR Engine (Phase 7)

How a stored document becomes recognised text. Code: `services/api/src/artify_api/ocr_engine.py`; orchestration in `processing.py`.

## Provider-independent
`OcrProvider` is a small protocol (`available()`, `version()`, `installed_languages()`, `recognise(image, languages) -> PageOcr`). The only implementation shipped is `TesseractProvider` (local `tesseract` binary, TSV output, subprocess with a timeout). Selected by `ARTIFY_OCR_PROVIDER`. If the binary is missing the job fails with a clear `ocr_unavailable` state and the Control Center shows "Not configured"; there are **no simulated results**. Another provider can be added by implementing the protocol; none has been integrated.

## Input handling
| Input | How it is read | Tested |
|---|---|---|
| Searchable PDF | Embedded text and character boxes via `pypdfium2`; OCR is skipped for pages that already carry text (`source = pdf_text`, confidence 100) | yes |
| Scanned PDF | Pages rendered at `ARTIFY_OCR_RENDER_DPI` (200) then OCR | yes |
| JPG / PNG / BMP | OCR | yes |
| TIFF (multi-page) | Each frame is a page, order preserved | yes |
| HEIC/HEIF | Accepted by the upload checks; decoded only if `pillow-heif` loads at runtime | **no HEIC fixture was tested** |

Limits: `ARTIFY_OCR_MAX_PAGES` (200), per-call `ARTIFY_OCR_TIMEOUT_SECONDS`. Corrupt, encrypted or oversized inputs fail with a stable error code and a message; they never crash the worker.

## Preprocessing (on a copy, never on the original)
Orientation detection (Tesseract OSD) and rotation, deskew (projection-profile skew estimate), denoise (median), contrast stretch, sharpen, resolution assessment (`low_resolution` flag) and blank-page detection (`is_blank`). The processed page is stored as a **derived storage object** (PNG, not billed against the document quota) and is the surface every bounding box refers to. The original version is never touched (tested: `test_original_documents_are_never_modified_by_processing`).

**Not implemented:** page-boundary detection/cropping (the whole page is read).

## What is stored
`ocr_results` (provider, engine version, languages, page count, mean confidence, job) → `ocr_pages` (size, source, rotation, deskew angle, quality flags, text, mean confidence, processed image) → `ocr_blocks` (line text, confidence, normalised bounding box `[x0,y0,x1,y1]` in 0..1). Re-running creates a **new** result; earlier results stay.

## Languages
English and Arabic (`eng+ara`; `osd` for orientation) when the Tesseract language data is installed on the server. Tested only with synthetic, rendered text (one document with both languages). Arabic-Indic digits are normalised during extraction. Accuracy on real Arabic scans, handwriting, stamps and low-quality photographs has **not** been measured; low confidence is surfaced as `needs_review`, never hidden.

## Measured limits
Quality tests (blur, noise, low resolution) assert only that processing completes and confidence drops; they do not assert recognition accuracy.
