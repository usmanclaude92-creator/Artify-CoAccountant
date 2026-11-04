---
name: api-development
description: Use when adding or modifying FastAPI endpoints, request/response models, error handling, idempotency, audit events or the OpenAPI contract in services/api. Covers how a route declares its authorization, transaction behaviour, error format and client regeneration.
---

# API development

## A route must declare its authorization
Every route has exactly one of `Depends(Public("reason"))`, `Depends(Requires("<permission>", ...))`, `Depends(Requires(reason="..."))` (any authenticated user) or `Depends(PlatformRequires(...))` (`artify_api/context.py`). `test_every_route_declares_its_authorization` fails otherwise. Permissions are defined in `permissions.py` (catalog + system roles); evaluation is in `policy.py`.
- Org routes: `/orgs/{org_id}/...`. Company routes: `/companies/{company_id}/...` with `Requires(..., company=True)` and the `X-Organization-Id` header. Platform routes: `/platform/...`.
- Out-of-scope objects must be 404 (the dependency does this); lack of permission inside a visible scope is 403.

## Transactions
One transaction per request, committed **before** the response is sent (dependencies use `scope="function"`; guarded by `test_transaction_commits_before_the_response_is_sent`). Use `ctx.conn`; do not open other connections inside a handler. Events that must survive a rollback (denials, failed logins) go through `record_denial` / `_audit_security` in their own transaction.

## Conventions
- Errors: raise `ApiError` subclasses (`errors.py`) → RFC 9457 `application/problem+json` with a stable `code`. Never return raw exception text.
- Retried creates accept `Idempotency-Key`; use `idempotency.run_idempotent`.
- Write an audit event for every state change (`audit.write_audit`); sensitive keys are redacted automatically. Enqueue side effects with `outbox.enqueue` in the same transaction.
- Limited resources: call `entitlements.guard(...)` before the mutation and `refresh_counter` after (see `routers/companies.py`).
- Money in JSON is a string; models live next to routers or in `routers/schemas.py`. Give each route a stable `operation_id` (client method name).

## Contract
After changing routes/models: `make client` (regenerates `packages/api-client/openapi.json` and `src/schema.d.ts`). CI fails if either is stale (`make openapi-check`).
