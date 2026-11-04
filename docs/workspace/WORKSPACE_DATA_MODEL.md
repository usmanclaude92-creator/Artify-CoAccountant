# Workspace Data Model

Migration `0006_workspace` (forward-only, hand-written SQL wrapped by Alembic). All company-owned tables carry `organization_id` + `company_id` with the composite FK `(organization_id, company_id) → companies (organization_id, id)`, forced row-level security with the ADR-024 company-scope policy, and `UNIQUE (organization_id, company_id, id)` so related rows reference each other with **three-column composite foreign keys** (a row can never point at another company’s row, even by a forged id). The application role has no `BYPASSRLS`. `test_every_table_is_classified_and_protected` and `test_tenant_tables_carry_tenant_columns` cover the new tables.

| Table | Purpose | Key constraints |
|---|---|---|
| `document_kinds` (reference, no RLS) | Artify-controlled catalog: key, name, accounting_domain, processing_type, creates_financial_records | synced from code by `artify-api seed`; app role is read-only |
| `company_configurations` (Phase 3, extended) | version owner: `config_version`, `workspace_state`, + `workspace_access_mode` (`open`/`restricted`), `applied_template_id/_version` (provenance only) | PK company_id |
| `workspace_nodes` | sections (roots) and folders (any depth) | `node_type` ⇔ `parent_id IS NULL`; composite parent FK; `depth = cardinality(path_ids)`, last path element = id, second-last = parent, unique elements (no cycles); `key` unique per company, immutable; sibling name unique case-insensitively among non-archived siblings; name rejects `/ \ : * ? " < > |`, control characters, `.`/`..` |
| `document_categories` | customer category → `document_kind` (NOT NULL, FK), accounting domain, processing type, allowed file types, optional default folder | unique key; unique name among non-archived |
| `custom_field_definitions` | key (immutable), label, type (10 types), required, default, validation (declarative JSON), reference target, order | unique key; unique label among non-archived; reference target ⇔ type `reference` |
| `custom_field_options` | dropdown/multi-select options; archived, never deleted | unique (field, value) |
| `custom_field_applicability` | field → category **or** node; no rows = applies everywhere | exactly one of category/node; unique |
| `naming_rules` | pattern, optional category | one live rule per category (partial unique index) and one company default |
| `filing_rules` | priority, JSON conditions, target node (+ optional category) | target FK composite; 1–20 conditions |
| `workspace_permissions` | explicit allow/deny grants (node or whole company; user or role subject) | unique grant; node FK composite |
| `workspace_templates` / `workspace_template_versions` | system (organization_id NULL) and organization templates; immutable versions with a JSON definition | RLS: system rows readable by all tenants, writable by none (seed runs as owner) |
| `workspace_revisions` | immutable history: version, action, changes (before/after), full snapshot, actor | app role has **SELECT, INSERT only**; unique (company, version) |

## Why relational, with JSON only where it earns its place

Core entities and relationships are tables. JSON is used for: `validation` (declarative field rules), `conditions` (a filing rule’s predicate list), `config`/`processing_settings` (extensible per-node/category settings, size-capped), template `definition` (a versioned document), and revision `changes`/`snapshot`.

## Hierarchy queries

`path_ids` (GIN-indexed) gives subtree queries (`path_ids @> ARRAY[:id]`) without recursion; the application recomputes `depth`/`path_ids` top-down after a move or restore (`_recompute_paths`). Row-level CHECKs guarantee local consistency; cross-row path correctness is maintained by the application inside the locked transaction.

## Deleting

Nodes are archived (`archived_at`, cascaded to the subtree with the same timestamp so restore is exact). Hard delete is allowed only when nothing ever referenced the node (no children, categories, rules, applicability, grants); foreign keys are plain `RESTRICT`. The acceptance test uses a stand-in table with a `RESTRICT` FK to prove the database refuses the delete; `documents` arrives in Phase 6 and must be added to `workspace.impact()`.

## Measurements

Per-company limits are plain `count(*)`/`max(depth)` queries under RLS (`entitlements.COMPANY_MEASURES`); `limit.workspace_templates` is measured by `app.measure_entitlement` (replaced in 0006; definer-read policy on `workspace_templates`).
