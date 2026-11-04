---
name: co-accountant-architecture
description: Use when starting any task in the Artify Co-Accountant repo, choosing where code belongs, or when a request touches tenancy, workspaces, accounting meaning, AI approval, or Excel. Gives the vocabulary, the non-negotiable invariants and where the authoritative docs live. Not for step-by-step coding procedures (see the other project skills).
---

# Artify Co-Accountant — architecture entry point

Read `docs/architecture/GLOSSARY.md` (terms) and `docs/architecture/DECISIONS.md` (ADRs, open decisions) before designing anything. Do not invent terms; change the glossary first.

## Invariants (enforced by tests named in brackets; do not weaken them)
1. Tenant = **Organization**; a **Company** belongs to exactly one Organization. Company-owned rows carry `organization_id` and `company_id` with a composite FK to `companies (organization_id, id)`. [`test_tenant_tables_carry_tenant_columns`]
2. Never trust client-supplied tenant/company ids; resolve memberships server-side. Out-of-scope objects return 404. [`test_isolation_api.py`]
3. No hard-coded sections/folders/categories. Meaning comes from Artify-controlled **document kinds** via each category's mapping (docs/architecture/ACCOUNTING_DATA_MAPPING.md). Workspace nodes are referenced by id, never display name; templates are copied, never linked.
4. Money is `Decimal`/`NUMERIC`, strings in JSON, never float. [`test_no_floating_point_columns`]
5. AI suggests, humans approve: approval needs `actor_type = user` + permission (ADR-009). Never invent missing financial data.
6. Limits are enforced only by `artify_api.entitlements`; no limit constants in code. [`test_entitlements.py`]
7. Platform admins have no default access to business data; support grants only (ADR-013).

## Where things live
- API: `services/api/src/artify_api/` (`context.py` request auth/scope, `policy.py` authz, `entitlements.py`, `auth_service.py`, `routers/`).
- Database: `services/api/migrations/sql/*.sql` (schema, RLS), design in `docs/database/`.
- Web: `apps/web` (Next.js; browser talks only to its own BFF routes), `packages/ui`, `packages/api-client` (generated).
- Plans per phase: `docs/development/PHASE_N.md`. Open decisions needing the owner: `DECISIONS.md` (D-*).

## Working rules
Report honestly (implemented vs documented; executed vs specified). Update docs and ADRs in the same change as code. Run `make check` before declaring work done.
