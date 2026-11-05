# AI Provider Architecture (Phase 7)

## Two provider kinds
| Kind | Default | Optional |
|---|---|---|
| OCR (`OcrProvider`) | `tesseract` (local) | none integrated |
| Understanding (`classify`, `extract`) | `rules` — deterministic, local, no model | `http_json` — OpenAI-compatible chat-completions endpoint |

## Configuration
Server settings only: `ARTIFY_AI_HTTP_BASE_URL`, `ARTIFY_AI_HTTP_MODEL`, `ARTIFY_AI_HTTP_API_KEY` (a `SecretStr`). The key is never stored in the database, never returned by any API, never logged, and never reaches the Control Center, Windows app or Mobile. Organization/company **choices** live in `ai_provider_configurations` (provider key, enabled, `allow_external_content`, optional company). `GET /processing/providers` returns state and choices, never secrets; `PUT` needs `ai_providers.manage`.

## Rules for the external provider
1. Used only if configured on the server **and** the organization recorded consent (`allow_external_content`) **and** the plan has `feature.advanced_ai`; otherwise the rules provider runs and the UI says so.
2. Receives recognised text only, not the original file.
3. Every returned value must appear in the OCR text (else `not_found_in_text`), money goes through the same Decimal parser, and the deterministic validators run on top.
4. Output is stored as suggestions with provider, model version and prompt version. Bank statements always use the deterministic parser.
5. Customer documents are not used for training by Artify; the external provider's own retention terms must be reviewed by the firm before consenting.

## Failure behaviour
Timeouts and 5xx are retried with backoff then reported (`provider_timeout`, `provider_error`); a missing configuration is reported as "not configured". Nothing is silently replaced by fabricated output.

## Verification status
The external path is exercised in tests only against a **local stub HTTP server**. It has never been run against a real AI service; treat it as unverified until it is.
