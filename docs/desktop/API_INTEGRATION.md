# API Integration

One gateway: `core/src/api.rs` (`ApiClient`). The UI never builds URLs or sees a token.

## Configuration

`ARTIFY_API_URL` (run time, else build time). HTTPS required except `localhost`/`127.0.0.1`; an unconfigured release build reports `api_not_configured` and disables sign-in instead of guessing.

## Behaviour

| Concern | Implementation |
|---|---|
| Timeouts | connect 5 s, request 20 s (configurable in `ApiConfig`) |
| Retries | **GET only**, up to 2 extra attempts, exponential backoff + jitter, for network errors, timeouts, 429, 502, 503, 504. Writes (POST/PATCH/DELETE) are never retried automatically. |
| Auth | `Authorization: Bearer` from memory; on 401 one single-flight refresh, then one repeat; refresh refused ⇒ session over |
| Organization claim | `X-Organization-Id` header (validated server-side) |
| Revalidation | `If-None-Match`/ETag for the workspace; 304 handled |
| Errors | RFC 9457 problem+json ⇒ `AppError{code,message,status,retryable,detail}`; only small structured extras (limit info, versions, validation errors) are kept — no raw body, headers or correlation ids reach the UI |
| Malformed answers | non-JSON or wrong-shaped success bodies ⇒ `malformed_response`, nothing trusted or cached |
| Offline | connection failures ⇒ `network_unavailable` (retryable); listings fall back to the sealed cache and are marked stale; opening a company needs the server |
| Cancellation | requests are plain futures; leaving a screen drops the pending result (no stale write to the UI). Explicit server-side cancellation is not available. |
| Logging | structured JSON lines, credentials redacted at write and read; only identifiers/outcomes are logged, never payloads |
| Types | UI types in `src/bridge/types.ts` mirror the Rust structs; backend contracts from the generated OpenAPI client (`@artify/api-client`) are the source for field names |

## Endpoints used

`POST /auth/login`, `/auth/mfa/verify`, `/auth/refresh`, `/auth/logout` · `GET /me` · `GET /orgs` · `GET /orgs/{org}/companies` · `GET /companies/{id}` · `GET /companies/{id}/workspace` · `GET /companies/{id}/assignments` · `POST /devices`, `POST /devices/{id}/session`, `GET /devices`, `DELETE /devices/{id}`.

## Backend changes made for Phase 5 (minimal, additive)

`POST /devices` binds the registering session to the new device; new `POST /devices/{id}/session` (bind current session + record activity; 404 for revoked or foreign devices); `DeviceOut` gained `registered_at` and `last_seen_at`. The OpenAPI diff has **0 breaking** changes. No business logic was duplicated in the app.

## Gaps in the backend that the desktop works around

No change-feed (polling with ETag instead), no `GET /me/capabilities`, no minimum-version enforcement, no company logo field, no per-device heartbeat other than `POST /devices/{id}/session`.
