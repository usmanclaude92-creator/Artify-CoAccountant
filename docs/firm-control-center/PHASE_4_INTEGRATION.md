# Phase 4 Integration — Company Configuration Ownership

Phase 3 does **not** build the workspace builder. It establishes who owns a company’s configuration, how it is reached securely, and what Phase 4 must respect. Nothing below the “Integration point” heading is implemented unless stated.

## What exists in Phase 3

- **`company_configurations`** (migration 0005): one row per company, created in the same transaction as the company (and backfilled for companies that existed before). Columns: `company_id` (PK), `organization_id`, `workspace_state` (`not_configured` | `configured`), `config_version`, `created_at`, `updated_at`; composite FK `(organization_id, company_id)`; RLS enabled and forced with the ADR-024 company-scope pattern; the app role has SELECT/INSERT/UPDATE (no DELETE).
- **`GET /companies/{id}/configuration`** (`companies.read`, company-scoped, 404 outside scope) returns the record plus the list of areas Phase 4 will attach. The Company page shows it honestly: *Not configured*, no default sections or folders.
- **Independent settings** — each company’s currency, financial year, accounting basis, VAT and jurisdiction live on `companies` and never inherit from or write back to the organization or another company (tested: `test_each_company_owns_its_settings_and_organization_settings_never_overwrite_them`).
- **Permissions** — `companies.read/update`, `company_settings.manage`, `companies.archive` are company-scoped keys. Phase 4 should add its own keys (for example `workspace.configure`) to the catalog rather than reuse these.

## Integration point (rules for Phase 4)

1. **Reference the company, not the organization.** Every Phase 4 table (sections, nodes, categories, category mappings, field definitions, templates, workflow settings, folder mappings) must carry `organization_id` + `company_id` with the composite FK, forced RLS and the ADR-024 policy pattern, and be classified in `test_every_table_is_classified_and_protected`.
2. **Hang them off `company_configurations`.** Use `config_version` for optimistic concurrency and publishing: bump it when a configuration is published; set `workspace_state` to `configured` only when a company has a published configuration. Do not add a parallel “settings” table.
3. **No defaults.** Companies differ (4, 10 or 7 nested sections…). Nothing may seed a standard category or folder list. Templates are *copied* into a company, never linked (invariant 4). Internal logic keys off Artify-controlled **document kinds** through each company’s category mapping (invariant 3).
4. **Stable ids.** Nodes are referenced by id/key, never by display name; archive instead of delete once referenced.
5. **Limits** (`limit.workspace_sections`, custom fields, templates …) are enforced by `EntitlementService` using entitlement definitions that already exist as data; the subscription page already lists them as *Not metered / Not enforced yet* until Phase 4 starts counting.
6. **Audit** — configuration changes write `audit_events` with `company_id`; the company History tab and the organization Activity page will then show them without changes.
7. **Suspended and archived companies** are read-only (409 on edit); Phase 4 writes must honor the same rule.
8. **Onboarding hook** — the wizard’s step list is local to the web app; a Phase 4 step “Workspace” can be added after “Assign accountants” and must stay optional.

## Not done in Phase 3 (do not assume)

Workspace builder UI, any workspace API beyond the read-only configuration record, section/folder/field/template/workflow tables, local folder mapping, per-company feature flags, copying configuration between companies.
