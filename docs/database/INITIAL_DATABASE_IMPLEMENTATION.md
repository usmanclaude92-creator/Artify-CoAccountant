# Initial Database Implementation (Phase 1)

What exists in PostgreSQL 16 after `alembic upgrade head` + `artify-api seed`. Design intent: [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md), [`ENTITY_CATALOG.md`](ENTITY_CATALOG.md), [`ISOLATION_AND_RLS.md`](ISOLATION_AND_RLS.md). Where this document and the SQL differ, the SQL (`services/api/migrations/sql/`) wins.

## 1. Migrations

| Revision | File | Content |
|---|---|---|
| 0001 | `sql/0001_foundation.sql` | extension `citext`, all tables, constraints, indexes, partitioned audit/usage tables |
| 0002 | `sql/0002_rls_and_grants.sql` | DB roles, `app.*` context functions, role-scope and assignment triggers, immutability triggers, `SECURITY DEFINER` functions, RLS enable+force, policies, least-privilege grants |
| 0003 | `sql/0003_auth_audit_fanout.sql` | tenant-visible fan-out of user-level security events (ADR-025) |

Alembic revisions are thin wrappers executing the hand-written SQL. **Migrations are forward-only (no downgrade)** — a deliberate deviation from the brief's "rollback where supported" (DECISIONS ADR-020): the schema is RLS/trigger-heavy and an unreviewed automatic downgrade would be more dangerous than useful. Recovery path in development: `make db-reset`; in production: restore from backup / forward fix. Tests build the database from migration 0001 upward on every run; `Base.metadata.create_all()` is never used.

## 2. Tables

**Identity and sessions:** `users`, `auth_credentials`, `mfa_factors`, `mfa_recovery_codes`, `sessions`, `refresh_tokens`, `email_tokens`, `devices`, `platform_admins`.

**Tenancy and access control:** `organizations`, `organization_memberships`, `companies`, `company_memberships`, `accountant_assignments`, `invitations`, `roles`, `permissions`, `role_permissions`.

**Commercial / entitlements:** `subscription_plans`, `subscription_plan_versions`, `plan_entitlements`, `entitlement_definitions`, `subscriptions`, `entitlement_overrides`, `usage_counters`, `usage_records` (partitioned), `currencies`, `retention_policies` (schema only, D-8), `support_access_grants` (table only; workflow in Phase 2).

**Cross-cutting:** `audit_events` (partitioned, append-only), `platform_audit_events` (partitioned, append-only), `outbox_events`, `idempotency_keys`, `platform_settings`.

Not created (belong to later phases): workspaces/sections/folders, documents, categories, extraction, accounting records, templates, exports, devices sync state.

## 3. Core relationships

```
organizations 1──* organization_memberships *──1 users
     │                         │ role_id ──► roles (scope = organization)
     │1
     └──* companies 1──* company_memberships *──1 users     (role_id ──► roles, scope = company)
              │     └──* accountant_assignments *──1 users  (must be an active accountant/staff member)
              └── composite FK (organization_id, company_id) on every company-owned table
organizations 1──1 subscriptions ──► subscription_plan_versions ──* plan_entitlements
            └──* entitlement_overrides, usage_counters, usage_records
roles *──* permissions via role_permissions        invitations ──► (org role | company role)
```

## 4. Constraints that carry the security model

- **Composite foreign keys** `(organization_id, company_id) → companies(organization_id, id)` make it impossible to attach a company-owned row to a company of another organization (test `test_database_rls.py`).
- `UNIQUE (organization_id, user_id)`, `UNIQUE (company_id, user_id)`, `UNIQUE (organization_id, code)` on companies, `UNIQUE (company_id, user_id)` on assignments; `email` is `citext UNIQUE`; `slug` is `citext UNIQUE` with a format CHECK.
- Role-scope trigger: organization roles only in organization memberships, company roles only in company memberships/invitations; custom roles must belong to the same organization.
- Assignment trigger: only active `accountant`/`staff` members can be assigned.
- Status columns carry CHECK constraints (`active|suspended|archived` etc.). Companies are **archived, never deleted** (no DELETE grant on `companies`); memberships use `removed` status.
- Money: no monetary columns exist yet; the rule (`NUMERIC`, never float) is enforced by `test_no_floating_point_columns`, a schema lint over the catalog.
- `test_every_table_is_classified_and_protected` fails when a new table is unclassified or lacks (forced) RLS; `test_tenant_tables_carry_tenant_columns` checks the tenant columns.
- `audit_events` and `platform_audit_events`: INSERT/SELECT only for the application roles, plus triggers rejecting UPDATE/DELETE. `usage_records` is INSERT/SELECT only by grant (no immutability trigger yet).
- Primary keys are application-generated **UUIDv7** (`ids.new_id`); `row_version` on mutable aggregates for optimistic concurrency later.

## 5. Row-level security

- Four login roles, none with `BYPASSRLS`, none owning the tables: `artify_app` (tenant API), `artify_platform` (platform realm), `artify_dispatcher` (outbox), `artify_definer` (owns the few `SECURITY DEFINER` functions: pre-login lookups, signup subscription creation, entitlement measurement, user-event audit).
- Every request runs in one transaction whose context is set with `set_config(..., true)` (`app.user_id`, `app.org_id`, `app.company_scope`, `app.company_ids`); with no context every policy evaluates to no rows (**fail closed**). Pool reuse cannot leak a context because it is transaction-local.
- Company-scoped policies inline `col = ANY (COALESCE((SELECT app.visible_company_ids()), '{}'))` (an InitPlan) — never a per-row function call (ADR-024; measured 65 ms → 1.2 ms with 300 assigned companies).
- Application-level authorization (policy engine) runs **in addition**; RLS is the second line, not the only one.

## 6. Reference and development data

`artify-api seed` (idempotent, owner credential) synchronizes permissions, system roles, role-permission links, entitlement definitions, currencies and a `development_trial` plan whose limits are **data** (companies 5, users 20, …), never read from code at enforcement time. **No users, organizations, companies or financial records are seeded**; development accounts are created by sign-up with `@example.com` addresses. No financial transaction data exists anywhere in the repository.

## 7. Known limitations

- Performance was measured only for `companies`; every future company-scoped table must reuse the ADR-024 pattern and be benchmarked at realistic volumes.
- Partitions exist only as `DEFAULT` partitions; time-based partition creation/retention jobs are Phase 12 work.
- Tested on a single PostgreSQL 16 cluster with a superuser owner; a non-superuser owner role is documented (ADR-023) but not exercised.
- No generated ER diagram; §3 is hand-drawn.
