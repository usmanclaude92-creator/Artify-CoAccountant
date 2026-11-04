# Tenant and Company Isolation in PostgreSQL

Implements ADR-002, ADR-003 and ADR-013. This is a **design**; no SQL has been executed in Phase 0. Phase 1 turns it into migrations and proves it with the isolation test suite (TS-ISO-*).

## 1. Threat being addressed

A bug, a forged client-supplied ID, a missed `WHERE organization_id = …`, a leaked token, or an over-broad support tool must not expose another tenant's or another Company's data. Application authorization is the primary control; the database is an independent second control.

## 2. Database roles

| Role | Used by | Privileges |
|---|---|---|
| `artify_owner` | Migrations only | Owns schema objects. Not used by running services |
| `artify_app` | API and tenant-realm workers | `SELECT/INSERT/UPDATE` (no `DELETE` on immutable tables); **no `BYPASSRLS`**; not the table owner |
| `artify_platform` | Platform-realm API | Access to platform tables and aggregate/metering views; tenant business tables only through support-grant policies |
| `artify_retention` | Retention/erasure job | Narrow delete rights on explicitly listed tables; audited |
| `artify_readonly_analytics` | Optional BI replica | Aggregate views only |

All tenant tables: `ALTER TABLE … ENABLE ROW LEVEL SECURITY; ALTER TABLE … FORCE ROW LEVEL SECURITY;` so even an owner-equivalent connection obeys policies.

## 3. Transaction context

The API sets context at the start of every transaction using `SET LOCAL` (transaction-scoped, safe with pgbouncer transaction pooling):

| Variable | Meaning |
|---|---|
| `app.realm` | `tenant` or `platform` |
| `app.user_id` | Authenticated user (or platform admin) |
| `app.org_id` | Active Organization (validated against membership) |
| `app.company_scope` | `all` (firm owner/practice admin) or `list` |
| `app.company_ids` | Comma/array list of accessible Company ids when scope is `list` (assignments, company memberships) |
| `app.section_grants` | (optional) section ids the user may see, for document policies |
| `app.support_grant_id` | Active support access grant, if any (platform realm) |

Values are produced **server-side** from validated memberships. They are never copied from request bodies, headers or token claims without the validation in `SYSTEM_ARCHITECTURE.md` §3. Missing variables resolve to NULL → policies match no rows (fail closed). Workers re-establish context from job payloads and verify the row's tenant before writing.

## 4. Policy patterns

Helper functions (`STABLE`, `SECURITY INVOKER`, defined in an `app` schema):

```sql
-- illustrative; final form is written and tested in Phase 1
CREATE FUNCTION app.org_id() RETURNS uuid
  LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$;

CREATE FUNCTION app.can_see_company(org uuid, comp uuid) RETURNS boolean
  LANGUAGE sql STABLE AS $$
    SELECT org = app.org_id()
       AND ( current_setting('app.company_scope', true) = 'all'
          OR comp = ANY (string_to_array(current_setting('app.company_ids', true), ',')::uuid[]) )
  $$;
```

**Tenant-scoped table** (e.g. `organization_memberships`, `workspace_templates`):

```sql
CREATE POLICY tenant_isolation ON organization_memberships
  USING      (organization_id = app.org_id())
  WITH CHECK (organization_id = app.org_id());
```

**Company-scoped table** (e.g. `documents`, `workspace_sections`, `excel_templates`):

```sql
CREATE POLICY company_isolation ON documents
  USING      (app.can_see_company(organization_id, company_id))
  WITH CHECK (app.can_see_company(organization_id, company_id));
```

**Section-restricted tables** (`documents`, extracted data derived from them) additionally require the document's `section_id` to be within `app.section_grants` unless the principal holds all-sections permission.

**Support access (platform realm):**

```sql
CREATE POLICY support_read ON documents FOR SELECT TO artify_platform
  USING (EXISTS (SELECT 1 FROM support_access_grants g
                 WHERE g.id = app.support_grant_id()
                   AND g.organization_id = documents.organization_id
                   AND (g.company_id IS NULL OR g.company_id = documents.company_id)
                   AND g.status = 'active' AND now() < g.expires_at
                   AND g.access_level IN ('read')));
```

There is **no** platform policy on business tables without a grant, so the Super Admin interface cannot read documents even if its code were wrong.

**Platform tables** (`subscription_plans`, `platform_settings`, `platform_audit_events`, …): policies for `artify_platform` only; `artify_app` has no privileges.

## 5. Structural guarantees beyond RLS

1. **Composite foreign keys.** `companies` has `UNIQUE (organization_id, id)`. Every company-owned table has `FOREIGN KEY (organization_id, company_id) REFERENCES companies (organization_id, id)`. Child → parent references inside a Company also carry `company_id` in the FK, e.g. `documents (company_id, section_id) → workspace_sections (company_id, id)`, so a document cannot reference a section of another Company even through a buggy write. This is why parents expose `UNIQUE (company_id, id)`.
2. **No cross-company links by design.** There are no tables joining objects of two different Companies; template/preset copies carry provenance ids only as nullable, non-FK columns, never joined at runtime.
3. **Immutability.** Triggers reject `UPDATE`/`DELETE` on `document_versions`, `audit_events`, `platform_audit_events`, `workflow_history`, `document_review_events`, approved `accounting_entries`/`journal_lines`, `excel_exports`, `excel_template_versions`, `workspace_configuration_versions`.
4. **Defaults fail closed.** Missing context → zero rows; `INSERT` with mismatching `organization_id` → policy violation.

## 6. Operational concerns

- **Migrations** run as `artify_owner`; a CI check verifies that every table with `organization_id` has RLS enabled and forced, and a policy for each command used (query catalog `pg_policies`). New tables fail CI until classified (see ownership classes in `DATABASE_DESIGN.md`).
- **Performance.** Policies use indexable predicates (`organization_id`, `company_id` leading columns). `app.can_see_company` is `STABLE`; very large `company_ids` lists are avoided by role-scope `all` or a membership-join policy variant; benchmarks on synthetic data in Phase 1.
- **Testing.** The isolation suite runs as `artify_app` with crafted contexts: wrong org, wrong company, empty context, forged IDs in bodies, direct SQL with no `WHERE`, cross-company FK insertion attempts, platform role without/with expired/revoked/other-tenant grants.
- **Break-glass DB access** by operators is a separate, audited, time-limited credential; routine operations never use `artify_owner`.
- **Backups/restores** are tenant-agnostic but restore procedures re-verify policies; per-tenant export/restore tooling is a Phase 12 item.
