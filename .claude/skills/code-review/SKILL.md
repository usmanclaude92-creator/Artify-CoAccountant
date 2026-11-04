---
name: code-review
description: Use when reviewing a diff or pull request in the Artify repo. A focused checklist for invariants that automated linters do not catch: tenancy, authorization, money, approvals, migrations, contract drift and honesty of status claims.
---

# Review checklist

1. **Tenancy** — any new query on tenant data runs through `Requires`/RLS context? Any raw id from the client used before membership validation? New tables classified and protected (see `multi-tenant-saas`)?
2. **Authorization** — route declares permission; 404 vs 403 semantics preserved; new permission added to `permissions.py` and to the intended roles only.
3. **Money & numbers** — `Decimal`/`NUMERIC`, strings in JSON, no float. Rounding only at defined points.
4. **Approvals** — nothing lets `system`/`ai` actors approve (ADR-009).
5. **Entitlements** — limits via `EntitlementService`; no hard-coded numbers; guard before mutation, refresh after.
6. **Migrations** — new forward-only file, no edits to a released migration, RLS/grants/triggers included, applies on a clean DB (`make db-reset`).
7. **Errors/logging/audit** — problem+json with stable codes; no sensitive data in logs or audit; state changes audited.
8. **Contract** — `make client` run; generated files committed; no hand edits to `openapi.json`/`schema.d.ts`.
9. **Tests** — security-relevant change has a negative test (and ideally a mutation check). Skipped tests are called out.
10. **Docs & honesty** — docs/ADRs updated; PR text distinguishes implemented / documented / not done, and states which commands were run.
11. **Web** — no tokens in JS-visible storage; all strings via i18n (en + ar); logical CSS properties; keyboard focus and labels on every control.
