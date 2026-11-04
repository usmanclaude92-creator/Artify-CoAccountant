---
name: multi-tenant-saas
description: Use when adding or changing database tables, migrations, RLS policies, grants, tenant/company-scoped queries, or anything that reads/writes data on behalf of an Organization or Company in the Artify repo. Covers table classification, the RLS context, the company-scope performance pattern and the isolation tests to extend.
---

# Multi-tenant data rules

## Adding a table (checklist)
1. Classify it: P platform, R reference, T tenant (`organization_id`), C company (`organization_id` + `company_id`), U user, D document-bound. See `docs/database/DATABASE_DESIGN.md` §2.
2. Create it in a **new** forward-only migration (`services/api/migrations/sql/NNNN_*.sql` + a thin revision in `migrations/versions/`). Phase 1 migrations are raw SQL; the runner escapes `%` for psycopg.
3. T/C/U/D tables: `ENABLE` and `FORCE ROW LEVEL SECURITY`, explicit `GRANT` to `artify_app`/`artify_platform` (least privilege; no DELETE on immutable data), and a policy per command. Company tables also get `FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id)`; intra-company parents expose `UNIQUE (company_id, id)`.
4. Append-only tables get `app.forbid_mutation()` triggers and no UPDATE/DELETE grants.
5. `test_every_table_is_classified_and_protected` and `test_tenant_tables_carry_tenant_columns` fail until you comply — extend them, never exempt a table without a documented reason.

## RLS context
The API sets `app.user_id`, `app.org_id`, `app.company_scope` (`all`|`list`) and `app.company_ids` per transaction with `set_config(..., true)` (`artify_api.db.apply_context`). Missing context ⇒ zero rows. Values come from validated memberships in `context.Requires`, never from request bodies.

## PERFORMANCE RULE (measured)
Do **not** call `app.can_see_company()` in policies on large tables. Inline:
`col = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'::uuid[]))` so it is an InitPlan (65 ms → 1.2 ms for 300 assigned companies; `infrastructure/dev/bench_rls.py`, guarded by `test_company_scope_policy_is_planned_once_per_query`).

## Pre-authentication lookups
Use the narrow `SECURITY DEFINER` functions in `0002_rls_and_grants.sql` (owned by `artify_definer`, with explicit read policies, pinned `search_path`). Add a new one only when there is truly no user context yet.

## Tests to extend
`services/api/tests/test_database_rls.py` (fail-closed, composite FK, platform role limits) and `test_isolation_api.py` (two orgs, accountant, employee). Every new company-owned resource needs: cross-tenant 404, cross-company 404, and an RLS-level test.
